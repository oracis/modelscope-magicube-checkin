#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自测：用本地假页面验证「找领取按钮 / 点领取 / 余额判据」三段逻辑。

为什么需要它
------------
真实链路里最难验的一段是「点按钮」—— 真账号一天只能领一次，
按钮点完就没了，没法反复回归。而这段恰恰最容易坏：
  - 父容器 innerText 会把整段页面带进来 ⇒ 必须只认短文案**叶子**按钮
  - disabled 按钮要能被排除（`<button disabled>` 的 className 是空的，
    只看 class 判不出来 ⇒ 得看 el.disabled 属性 + 祖先链）
  - 一天有两个「领取」（登录 200 / 绑阿里云 50）⇒ 必须循环点，不能只点第一个
  - 已点过的按钮要能被跳过 ⇒ 用 data-ms-clicked 标记
⇒ 用一个本地 HTML 假页面把这段单独钉死，改完 JS 立刻能回归。

用法：python selftest.py
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ms_cdp as M      # noqa: E402
import ms_checkin as C  # noqa: E402

SELFTEST_PORT = 9334

FIXTURE = """<!doctype html>
<html><head><meta charset="utf-8"><title>fake</title></head>
<body>
  <div class="header">我的魔粒</div>
  <div id="bal">100</div>
  <div class="task"><span>注册并登录</span><button id="b1">领取</button></div>
  <div class="task"><span>绑定阿里云</span><button id="b2">领取</button></div>
  <div class="task"><span>已完成的项</span><span>今日已完成</span></div>
  <div class="task"><button id="b3" disabled>领取</button></div>
  <div class="noise"><button>领取魔粒奖励中心入口这类长文案不算按钮</button></div>
  <div class="task"><button id="b4">领取</button></div>
  <script>
    function add(n) {
      document.getElementById('bal').textContent =
        String(Number(document.getElementById('bal').textContent) + n);
    }
    // b1/b2：领完后文案变成「今日已完成」（真实页面行为）
    function done(id) {
      add(50);
      document.getElementById(id).textContent = '今日已完成';
    }
    document.getElementById('b1').addEventListener('click', function () { done('b1'); });
    document.getElementById('b2').addEventListener('click', function () { done('b2'); });
    // b4：领完文案不变 ⇒ 只能靠 data-ms-clicked 标记去重
    document.getElementById('b4').addEventListener('click', function () { add(50); });
  </script>
</body></html>
"""

FAILED = []


def check(name, cond, extra=""):
    if cond:
        print("  [ok]   %s" % name)
    else:
        print("  [FAIL] %s %s" % (name, extra))
        FAILED.append(name)


def main():
    # 假页面写到脚本同目录下的固定文件名（不是系统临时目录），
    # 跑完删掉。选固定路径是为了让「写了什么、在哪」一目了然。
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "_selftest_fixture.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(FIXTURE)
    url = "file:///" + path.replace("\\", "/")

    # 连接一个已经开着调试端口的 Chrome（端口 9334）。
    # 若没开，ensure_chrome 会打印可直接复制的启动命令并退出。
    proc, launched = M.ensure_chrome(port=SELFTEST_PORT, url="about:blank")
    cdp = M.CDP(port=SELFTEST_PORT)
    try:
        t = cdp.new_target(url)
        cdp.connect_target(t["id"])
        time.sleep(2)

        # 1) 余额判据
        info = json.loads(cdp.eval(C.JS_AUTH, refresh_context=True) or "{}")
        check("余额解析=100", C._balance_of(info) == 100.0, str(info))
        check("未误判未登录", info.get("loginBtn") is False, str(info))

        # 2) 找按钮：只认 3 个短文案「领取」叶子按钮。
        #    要排除：disabled 的 b3、长文案按钮、两个包着 button 的 div。
        find = json.loads(cdp.eval(C.JS_FIND_CLAIM) or "{}")
        check("认到 3 个领取按钮（排除 disabled/长文案/容器）",
              find.get("total") == 3, str(find))
        check("首个按钮有下标", (find.get("index") or -1) >= 0, str(find))

        # 3) 点第一个 → 余额 +50（b1 文案变「今日已完成」）
        click = json.loads(
            cdp.eval(C.JS_CLICK_CLAIM % (C._JS_PRELUDE, int(find["index"])))
            or "{}")
        check("点击返回 clicked", click.get("clicked") is True, str(click))
        time.sleep(0.4)
        info2 = json.loads(cdp.eval(C.JS_AUTH, refresh_context=True) or "{}")
        check("点击后余额=150", C._balance_of(info2) == 150.0, str(info2))

        # 4) 下一个按钮换成 b2（b1 文案已变），证明能连续点多个
        find2 = json.loads(cdp.eval(C.JS_FIND_CLAIM) or "{}")
        check("第二个按钮下标不同（能连点）",
              find2.get("index") != find.get("index"), str(find2))
        cdp.eval(C.JS_CLICK_CLAIM % (C._JS_PRELUDE, int(find2["index"])))
        time.sleep(0.4)
        info3 = json.loads(cdp.eval(C.JS_AUTH, refresh_context=True) or "{}")
        check("连点两次后余额=200", C._balance_of(info3) == 200.0, str(info3))

        # 5) b4 文案不变，只有 marker 能去重 ⇒ 点完应找不到了
        find3 = json.loads(cdp.eval(C.JS_FIND_CLAIM) or "{}")
        cdp.eval(C.JS_CLICK_CLAIM % (C._JS_PRELUDE, int(find3["index"])))
        time.sleep(0.4)
        find4 = json.loads(cdp.eval(C.JS_FIND_CLAIM) or "{}")
        check("已点过的按钮被 marker 跳过",
              find4.get("index") == -1, str(find4))

        # 6) 「今日已完成」文案能被认出来
        txt = str(cdp.eval(C.JS_CLAIMED_TEXT) or "")
        check("认得「今日已完成」", "今日已完成" in txt, repr(txt))

        # 7) 域名白名单：只有魔搭的 host 才算数
        #    （这是安全边界：连上 tab 之后会在那个页面上点按钮）
        check("白名单放行 www.modelscope.cn",
              M._host_matches("https://www.modelscope.cn/magicube/usage",
                               C.ALLOWED_HOSTS) is True)
        check("白名单放行裸域 modelscope.cn",
              M._host_matches("https://modelscope.cn/x", C.ALLOWED_HOSTS) is True)
        check("白名单拦截第三方站点",
              M._host_matches("https://evil.example.com/magicube",
                              C.ALLOWED_HOSTS) is False)
        # 8) 后缀拼接攻击：evil-modelscope.cn 不是 modelscope.cn
        check("白名单拦截后缀拼接 evil-modelscope.cn",
              M._host_matches("https://evil-modelscope.cn/magicube",
                              C.ALLOWED_HOSTS) is False)
        check("白名单拦截子域劫持 attacker.modelscope.cn.evil.com",
              M._host_matches("https://attacker.modelscope.cn.evil.com/x",
                              C.ALLOWED_HOSTS) is False)
        check("白名单拦截非 http 伪协议 javascript:",
              M._host_matches("javascript:alert(1)//modelscope.cn",
                              C.ALLOWED_HOSTS) is False)

        # 9) 点击前的页面归属校验：非魔搭页面必须否决
        check("页面归属校验：魔搭页放行",
              C._assert_page_is_magicube(
                  {"url": "https://www.modelscope.cn/magicube/usage"}) is True)
        check("页面归属校验：第三方页否决",
              C._assert_page_is_magicube(
                  {"url": "https://evil.example.com/magicube"}) is False)
        check("页面归属校验：空 url 否决",
              C._assert_page_is_magicube({"url": ""}) is False)

        # 10) 成功判据：只有「余额真的增加」才算 CLAIMED
        st, dl, _ = C.classify(100.0, 150.0, True, "", "", ["领取"])
        check("余额增加 ⇒ CLAIMED", st == "CLAIMED", st)
        check("余额增加 ⇒ delta=50", dl == 50.0, str(dl))
        # 🚨 关键：点了按钮 + 文案变成「今日已完成」但余额没变 ⇒ **不算成功**
        st, dl, dt = C.classify(100.0, 100.0, True, "", "今日已完成", ["领取"])
        check("余额没变（哪怕文案变了）⇒ 不是 CLAIMED", st != "CLAIMED", st)
        check("余额没变 ⇒ 记 UNKNOWN", st == "UNKNOWN", st)
        check("余额没变 ⇒ detail 明说未核实", "未核实" in dt, dt)
        st, _, _ = C.classify(100.0, 100.0, True, "", "", ["领取"])
        check("点了但余额没变且无文案 ⇒ UNKNOWN", st == "UNKNOWN", st)
        # 没点到按钮 + 页面写着今日已完成 ⇒ ALREADY（不是新领取）
        st, _, _ = C.classify(100.0, 100.0, False, "今日已完成", "今日已完成", [])
        check("没点按钮 + 已完成文案 ⇒ ALREADY", st == "ALREADY", st)
        # 余额减少不能算成功
        st, _, _ = C.classify(100.0, 50.0, True, "", "", ["领取"])
        check("余额减少 ⇒ 不是 CLAIMED", st != "CLAIMED", st)
        # 读不到余额也不能算成功
        st, _, _ = C.classify(None, None, True, "", "今日已完成", ["领取"])
        check("余额读不到 ⇒ 不是 CLAIMED", st != "CLAIMED", st)
    finally:
        M.stop_chrome(cdp, proc)
        try:
            os.remove(path)
        except Exception:
            pass

    print("")
    if FAILED:
        print("✗ %d 项未过：%s" % (len(FAILED), ", ".join(FAILED)))
        return 1
    print("✓ 自测全过（%d 项）" % 28)
    return 0


if __name__ == "__main__":
    sys.exit(main())
