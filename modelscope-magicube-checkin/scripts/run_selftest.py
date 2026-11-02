#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自测驱动：起一个带调试端口的 Chrome → 跑 selftest.py → 关掉 Chrome。

为什么要单独一层：
  本机沙箱会回收「脱离当前任务」的子进程 ⇒ Chrome 必须和跑自测的
  Python 处在**同一个**后台任务里，否则刚起来就被收走（表现为
  Chrome 退出码 3、CDP 端口永远连不上）。
"""
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("SELFTEST_PORT", "9334"))
# 🚨 复用现成 Chrome 的端口（逗号分隔）。沙箱里新起的 Chrome 可能直接被收走
#    （退出码 3、无任何 stderr）⇒ 此时复用本机已有调试端口的 Chrome 更稳。
#    ⚠ 只新建/关闭**自己的 target**，绝不碰用户已有的页面。
REUSE_PORTS = [int(x) for x in
                os.environ.get("SELFTEST_REUSE_PORTS", "").split(",")
                if x.strip().isdigit()]


def _chrome_exe():
    """找一个能用的 Chrome 可执行文件。"""
    cands = [
        r"C:\Users\DELL\AppData\Local\Google\ChromeForTesting\chrome-win64\chrome.exe",
        r"C:\Users\DELL\AppData\Local\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    ]
    for c in cands:
        if os.path.exists(c):
            return c
    return None


def _run_against(port):
    """在指定端口的 Chrome 上跑 selftest.py（不负责起/停浏览器）。"""
    env = dict(os.environ)
    env["SELFTEST_PORT"] = str(port)
    r = subprocess.run([sys.executable, os.path.join(HERE, "selftest.py")],
                       cwd=HERE, env=env)
    print("[..] 自测退出码 = %s" % r.returncode)
    return r.returncode


def main():
    # ---- 优先复用现成的 Chrome 端口 ----
    for rp in REUSE_PORTS:
        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:%d/json/version" % rp, timeout=3) as r:
                r.read()
                print("[ok] 复用现成 Chrome 调试端口 %d" % rp)
                return _run_against(rp)
        except Exception:
            continue

    exe = _chrome_exe()
    if not exe:
        print("!! 找不到 Chrome 可执行文件")
        return 2
    prof = os.path.join(os.path.expanduser("~"),
                        "chrome-ms-selftest-%d" % os.getpid())
    env = dict(os.environ)
    env["LOCALAPPDATA"] = r"C:\Users\DELL\AppData\Local"
    env["ProgramFiles"] = r"C:\Program Files"
    env["ProgramFiles(x86)"] = r"C:\Program Files (x86)"

    cmd = [exe,
           "--remote-debugging-port=%d" % PORT,
           "--remote-debugging-address=127.0.0.1",
           "--user-data-dir=" + prof,
           "--no-first-run", "--no-default-browser-check",
           "about:blank"]
    print("[..] 启动 Chrome: %s" % exe)
    proc = subprocess.Popen(cmd, env=env,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    up = False
    for _ in range(25):
        time.sleep(1)
        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:%d/json/version" % PORT, timeout=3) as r:
                r.read()
                up = True
                break
        except Exception:
            if proc.poll() is not None:
                print("!! Chrome 退出，code=%s" % proc.poll())
                break
    if not up:
        print("!! CDP 端口没起来")
        try:
            proc.terminate()
        except Exception:
            pass
        return 3

    print("[ok] CDP 已就绪 (port=%d, pid=%d)" % (PORT, proc.pid))
    try:
        r = subprocess.run([sys.executable, os.path.join(HERE, "selftest.py")],
                           cwd=HERE)
        code = r.returncode
    finally:
        try:
            proc.terminate()
            time.sleep(1)
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass
        try:
            import shutil
            shutil.rmtree(prof, ignore_errors=True)
        except Exception:
            pass
    print("[..] 自测退出码 = %s" % code)
    return code


if __name__ == "__main__":
    sys.exit(main())
