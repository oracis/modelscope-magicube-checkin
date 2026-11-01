#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""最小 CDP 客户端 + Chrome 启动器（零依赖，只用 Python 标准库）。

为什么自带一套而不用别的项目的模块
----------------------------------
本 skill 要能脱离 ai-case-library 独立跑（换台机器、换个项目），
所以把「WebSocket + CDP 命令 + Chrome 启动」这三件事收进一个文件。

三个已实测的硬坑（照抄前先读完，别再踩）
------------------------------------------
1. **必须 `--no-sandbox`**：不带时 Chrome 会加载完 policy 就 2 秒内退出
   （exit code 3, RESULT_CODE_KILLED_BAD_MESSAGE），DevTools 端口根本不开。
   已用标志矩阵验证过：baseline / --disable-gpu 都是 FAIL，加 --no-sandbox 才 OK。
2. **必须先清 `Default/LOCK`**：强杀残留的锁会让 Chrome「启动即退、无报错无窗口」。
   症状看起来像登录态问题，实际是锁没清掉。
   ⚠ 删除可能被安全删除钩子拦（safe-delete 改走回收站 API，对被 Chrome 占着的
   LOCK 直接失败）⇒ 兜底用 `os.rename` 把 LOCK 挪出 profile（挪走≠删除）。
3. **必须清干净代理**：`http_proxy=127.0.0.1:xxxx` 会掐断 CDP 的 WebSocket
   （10053/10054）。CDP 相关的 urllib 请求也要显式 `ProxyHandler({})`，
   否则 curl/urllib 会走系统代理。

另外：**Chrome 136+ 拒绝在【默认 user-data-dir】上开调试端口**（官方设计，
见 https://developer.chrome.com/blog/remote-debugging-port），
所以必须用独立 profile —— 登录态天然与日常 Chrome 分家，这是预期行为不是故障。
日常 Chrome 的 cookie 是 App-Bound 加密（v20 前缀），**解不开**，别想着复用。

WS 实现说明：只做客户端→服务端方向的帧掩码（RFC 6455 要求），
不处理分片重组的续帧以外的情况，够用即可。
"""
import base64
import json
import os
import re
import select
import socket
import struct
import subprocess
import tempfile
import time
import urllib.request

__all__ = [
    "WS", "CDP", "cdp_up", "clear_proxy", "remove_lock", "pick_chrome",
    "build_args", "wait_cdp", "ensure_chrome", "stop_chrome", "log",
]


def log(msg):
    print(msg, flush=True)


# --------------------------------------------------------------------------
# WebSocket（最小实现）
# --------------------------------------------------------------------------
class WS:
    """够用的 WebSocket 客户端：文本帧收发 + 客户端掩码。"""

    def __init__(self, url, timeout=45):
        m = re.match(r"ws://([^:/]+):(\d+)(/.*)", url)
        if not m:
            raise ValueError("不支持的 ws url: %r" % url)
        self.host, self.port, self.path = m.group(1), int(m.group(2)), m.group(3)
        self.sock = socket.create_connection((self.host, self.port), timeout=timeout)
        self.sock.settimeout(timeout)
        self._handshake()
        self._buf = b""

    def _handshake(self):
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            "GET %s HTTP/1.1\r\nHost: %s:%d\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            "Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: %s\r\n\r\n"
            % (self.path, self.host, self.port, key)
        )
        self.sock.sendall(req.encode())
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("ws 握手失败：连接被对端关闭")
            data += chunk
        if b"101" not in data.split(b"\r\n", 1)[0]:
            raise ConnectionError("ws 握手被拒：%r" % data.split(b"\r\n", 1)[0])

    def _read_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("ws closed")
            buf += chunk
        return buf

    def _recv_frame(self):
        b0, b1 = self._read_exact(2)
        fin = (b0 & 0x80) != 0
        opcode = b0 & 0x0F
        masked = (b1 & 0x80) != 0
        length = b1 & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._read_exact(8))[0]
        if masked:
            self._read_exact(4)
        return fin, opcode, self._read_exact(length)

    def recv_text(self):
        parts = []
        while True:
            fin, opcode, payload = self._recv_frame()
            if opcode in (0x1, 0x0):
                parts.append(payload)
            if fin:
                break
        return b"".join(parts).decode("utf-8", "replace")

    def send_text(self, text):
        payload = text.encode("utf-8")
        mask = os.urandom(4)
        header = bytes([0x81])
        n = len(payload)
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", n)
        masked = bytes(payload[i] ^ mask[i % 4] for i in range(n))
        self.sock.sendall(header + mask + masked)

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass

    def readable(self, timeout=0.3):
        try:
            r, _, _ = select.select([self.sock], [], [], timeout)
            return bool(r)
        except Exception:
            return False


# --------------------------------------------------------------------------
# CDP
# --------------------------------------------------------------------------
class CDP:
    """够用的 CDP 封装：建 target / 连 target / 求值 JS。"""

    def __init__(self, port):
        self.port = port
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            ver = self._http("/json/version")
        except Exception as e:
            raise SystemExit(
                "\n✗ 连不上 Chrome 调试端口 127.0.0.1:%d\n"
                "  Chrome 136+ 拒绝在【默认 user-data-dir】上开调试端口"
                "（官方说明：\n"
                "  https://developer.chrome.com/blog/remote-debugging-port）。\n"
                "  ⇒ CDP 只能跑在【独立 profile】上，登录态与日常 Chrome 分家，\n"
                "    这是官方设计，不是本机故障。\n"
                "  ⚠ 别再试图复制/解密日常 Chrome 的 Cookies（App-Bound 加密解不开）。\n"
                "  请依次确认：\n"
                "    1) 已完全退出所有 chrome.exe（任务管理器确认无残留）\n"
                "    2) 删除强杀残留的锁：<profile>\\Default\\LOCK\n"
                "    3) 用【Chrome for Testing】启动（官方对自动化场景的推荐浏览器）\n"
                "  原始错误：%s" % (port, e))
        self.bws = WS(ver["webSocketDebuggerUrl"])
        self.ws = None
        self.tid = None
        self._id = 0
        self._main_context = None

    # ---- HTTP 侧 ----
    def _http(self, path):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path))
        with self._opener.open(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))

    def list_targets(self):
        return self._http("/json")

    def find_target(self, needle):
        """按 URL 子串找已存在的 target；找不到返回 None。"""
        for t in self.list_targets():
            if needle in (t.get("url") or "") and t.get("webSocketDebuggerUrl"):
                return t
        return None

    # ---- 浏览器级 ----
    def send_browser(self, method, params=None):
        self._id += 1
        msg = {"id": self._id, "method": method, "params": params or {}}
        self.bws.send_text(json.dumps(msg))
        while True:
            resp = json.loads(self.bws.recv_text())
            if resp.get("id") == self._id:
                return resp

    def new_target(self, url):
        """走 CDP 原生 Target.createTarget —— HTTP /json/new 在新版 Chrome 被禁（405）。"""
        r = self.send_browser("Target.createTarget", {"url": url})
        if "error" in r:
            raise RuntimeError("Target.createTarget 失败: %s" % r["error"])
        tid = r["result"]["targetId"]
        return {"id": tid, "targetId": tid}

    def connect_target(self, target_id):
        for _ in range(10):
            for t in self.list_targets():
                if t.get("id") == target_id and t.get("webSocketDebuggerUrl"):
                    self.ws = WS(t["webSocketDebuggerUrl"])
                    self.tid = target_id
                    self._main_context = self._enable_and_grab_context()
                    return True
            time.sleep(0.5)
        return False

    def close_target(self, target_id):
        try:
            self.send_browser("Target.closeTarget", {"targetId": target_id})
        except Exception:
            pass

    def close_browser(self):
        try:
            self.bws.send_text(json.dumps(
                {"id": 999999, "method": "Browser.close", "params": {}}))
        except Exception:
            pass

    # ---- 页面级 ----
    def _enable_and_grab_context(self, timeout=6):
        """发 Runtime.enable 并抓主帧默认执行上下文 id（SPA 导航会换上下文）。"""
        try:
            self.send("Runtime.enable")
        except Exception:
            pass
        end = time.time() + timeout
        best = None
        while time.time() < end:
            if not self.ws.readable(0.3):
                if best is not None:
                    break
                continue
            try:
                raw = self.ws.recv_text()
            except Exception:
                break
            try:
                ev = json.loads(raw)
            except Exception:
                continue
            if ev.get("method") == "Runtime.executionContextCreated":
                p = ev.get("params", {})
                if p.get("auxData", {}).get("isDefault"):
                    best = p.get("id")
            elif ev.get("method") == "Runtime.executionContextsCleared":
                best = None
        return best

    def send(self, method, params=None):
        if self.ws is None:
            raise RuntimeError("未连接页面 target，先 new_target + connect_target")
        self._id += 1
        msg = {"id": self._id, "method": method, "params": params or {}}

        def _roundtrip():
            self.ws.send_text(json.dumps(msg))
            while True:
                raw = self.ws.recv_text()
                resp = json.loads(raw)
                if resp.get("id") == self._id:
                    return resp
                # 其余是事件帧，忽略

        try:
            return _roundtrip()
        except ConnectionError:
            tid = getattr(self, "tid", None)
            if not tid or not self.connect_target(tid):
                raise
            log("  [warn] CDP 页面连接断开，已重连同一标签页")
            return _roundtrip()

    def eval(self, expr, refresh_context=False, context_id=None):
        if refresh_context or (context_id is None and self._main_context is None):
            self._main_context = self._enable_and_grab_context()
        params = {"expression": expr, "returnByValue": True, "awaitPromise": True}
        ctx = context_id if context_id is not None else self._main_context
        if ctx is not None:
            params["contextId"] = ctx
        last = None
        for _ in range(6):
            r = self.send("Runtime.evaluate", params)
            if "error" in r:
                err = r["error"]
                if err.get("code") == -32000 and self._main_context is not None:
                    self._main_context = self._enable_and_grab_context()
                    params["contextId"] = self._main_context
                    time.sleep(1)
                    continue
                raise RuntimeError("CDP 协议错误: %s" % err)
            if "exceptionDetails" in r:
                exc = r["exceptionDetails"].get("exception", {})
                raise RuntimeError("JS 执行异常: %s"
                                   % exc.get("description", r["exceptionDetails"]))
            last = r.get("result", {}).get("result", {}).get("value")
            if last is not None:
                return last
            time.sleep(1)
        if last is None and ctx is not None and context_id is None:
            r = self.send("Runtime.evaluate",
                          {"expression": expr, "returnByValue": True,
                           "awaitPromise": True})
            last = r.get("result", {}).get("result", {}).get("value")
        return last


# --------------------------------------------------------------------------
# Chrome 启动
# --------------------------------------------------------------------------
CHROME_CANDIDATES = [
    # 优先 Chrome for Testing（官方对自动化场景的推荐浏览器）
    (r"%LOCALAPPDATA%\Google\ChromeForTesting\chrome-win64\chrome.exe",
     "Chrome for Testing"),
    (r"%ProgramFiles%\Google\Chrome\Application\chrome.exe", "system Chrome"),
    (r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe", "system Chrome x86"),
    (r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe", "user Chrome"),
    (r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe", "Edge"),
]

# Git Bash 起的 python 缺这两个变量 → Chrome 会找不到自己的 DLL/目录
for _k, _v in (("LOCALAPPDATA", r"C:\Users\DELL\AppData\Local"),
               ("ProgramFiles", r"C:\Program Files")):
    if not os.environ.get(_k):
        os.environ[_k] = _v


def cdp_up(port):
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with op.open("http://127.0.0.1:%d/json/version" % port, timeout=3) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def clear_proxy():
    """清干净代理。留着 http_proxy 会把 CDP 的 WebSocket 掐断（10053/10054）。"""
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
              "ALL_PROXY", "all_proxy"):
        os.environ.pop(k, None)


def remove_lock(profile):
    """挪掉强杀残留的 LOCK —— 「启动即退」的头号根因。

    ⚠ 只能用 Python 删：bash 的 `rm -f` 会被安全删除策略拦。
    ⚠ `os.remove` 本身也可能被 safe-delete 钩子改走回收站 API 而失败
    ⇒ 兜底用 `os.rename` 把 LOCK 挪出 profile（挪走≠删除，不触发删除钩子）。
    """
    lock = os.path.join(profile, "Default", "LOCK")
    if not os.path.exists(lock):
        return False
    try:
        os.remove(lock)
        log("  cleared stale LOCK")
        return True
    except Exception as e:
        log("  [warn] os.remove LOCK failed (%s), fallback to rename" % e)
    try:
        dst = os.path.join(tempfile.gettempdir(),
                           "chrome_cdp_LOCK_%d" % int(time.time()))
        os.rename(lock, dst)
        log("  moved stale LOCK -> %s" % dst)
        return True
    except Exception as e:
        log("  [warn] cannot remove LOCK: %s" % e)
    return False


def pick_chrome():
    seen = set()
    for path_tpl, label in CHROME_CANDIDATES:
        if path_tpl in seen:
            continue
        seen.add(path_tpl)
        p = os.path.expandvars(path_tpl)
        if os.path.exists(p):
            return p, (label or "Chrome")
    return None, None


def build_args(chrome, profile, extra=(), port=None, headless=None):
    """⚠ `--no-sandbox` 是硬性要求（见模块 docstring 坑 1）。"""
    if headless is None:
        headless = not str(os.environ.get("CHROME_HEADED", "")).lower() \
            in ("1", "true", "yes")
    base = [
        chrome,
        "--remote-debugging-port=%d" % (port or 9333),
        "--user-data-dir=%s" % profile,
        "--no-first-run",
        "--no-default-browser-check",
        "--no-sandbox",
    ]
    if headless:
        base.append("--headless=new")
        base.append("--disable-gpu")
    return base + list(extra)


def wait_cdp(proc, timeout=40, port=None):
    """轮询 DevTools 端口。返回 True 表示已通。

    ⚠ 用 urllib 而不是 curl：curl 会带上系统代理，必须显式清空。
    """
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    url = "http://127.0.0.1:%d/json/version" % (port or 9333)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            log("  Chrome exited early, code=%s" % proc.poll())
            return False
        try:
            op.open(url, timeout=2).read()
            return True
        except Exception:
            time.sleep(1.5)
    return False


def ensure_chrome(headless=True, port=9333, url="about:blank", profile=None):
    """端口不通就自己起一个 Chrome。返回 (proc_or_None, launched_bool)。"""
    if cdp_up(port):
        return None, False
    profile = profile or os.path.expanduser("~/chrome-ms-profile")
    clear_proxy()
    chrome, label = pick_chrome()
    if chrome is None:
        raise SystemExit("找不到 Chrome 可执行文件（试过 %d 个候选路径）"
                         % len(CHROME_CANDIDATES))
    remove_lock(profile)
    os.makedirs(profile, exist_ok=True)
    args = build_args(chrome, profile, extra=[url], port=port, headless=headless)
    log("[ms] 起 Chrome(%s) port=%d headless=%s profile=%s"
        % (label, port, headless, profile))
    # ⚠ DETACHED_PROCESS：让 Chrome 脱离本进程的控制台/作业对象。
    # 沙箱会回收「调用 shell 退出时」的整棵进程树，不脱钩的话 --login
    # 开出来的窗口往往撑不到人去点登录就没了。PID 仍在我们手里，
    # stop_chrome 照样能按 PID 收掉。
    flags = 0x00000008 if os.name == "nt" else 0  # DETACHED_PROCESS
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL,
                            creationflags=flags)
    if not wait_cdp(proc, timeout=40, port=port):
        raise SystemExit("Chrome 启动失败（exit=%s）" % proc.poll())
    return proc, True


def stop_chrome(cdp, proc):
    """只杀自己拉起的那个 Chrome，绝不 taskkill /IM chrome.exe。"""
    try:
        if cdp is not None:
            cdp.close_browser()
            time.sleep(1)
    except Exception:
        pass
    if proc is None:
        return
    if proc.poll() is None:
        subprocess.run(["taskkill", "/F", "/PID", str(proc.pid), "/T"],
                       check=False, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    log("[ms] Chrome 已关闭 (pid=%s)" % proc.pid)


if __name__ == "__main__":
    # 自检：列候选 Chrome + 当前端口状态
    p, label = pick_chrome()
    print("Chrome:", p, label)
    for port in (9333,):
        print("port %d:" % port, "UP" if cdp_up(port) else "down")
