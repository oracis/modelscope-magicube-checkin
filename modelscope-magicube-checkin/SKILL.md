---
name: modelscope-magicube-checkin
slug: modelscope-magicube-checkin
displayName: 魔搭魔粒每日自动领取
version: 1.0.0
visibility: public
category: dev-programming
subCategories: [dev-script]
description: 自动领取魔搭（ModelScope）每日发放的魔粒（Magicube）算力额度。走 CDP 真点页面「领取」按钮并比对余额前后，不调逆向接口、不依赖登录即到账的假设。含一次性人工登录（登录态存独立 Chrome profile，之后每天无头复用）、四态判据机、故障处置表与自测脚本。当用户说「领魔粒」「魔搭签到」「魔粒余额」「魔搭自动登录」「ModelScope checkin」「魔粒够不够」时使用。
agent_created: true
---

# 魔搭魔粒每日自动领取

## 概览

魔粒（Magicube）是魔搭 API-Inference 的算力额度。每日登录魔搭后，**必须到魔粒页点「领取」** 额度才会到账。

本 skill 用 Chrome DevTools Protocol 真点页面按钮，并**以余额变化作为唯一成功判据**。脚本零第三方依赖（只用 Python 标准库），随 skill 一起分发。

## 何时使用

- 用户说「领魔粒 / 魔搭签到 / 魔粒余额 / 魔搭自动登录 / ModelScope checkin」
- 用户问「今天领了吗 / 还剩多少魔粒 / 够不够跑一轮核验」
- 定时任务触发每日领取

## 执行方式

脚本在 skill 目录下的 `scripts/`。拼出 skill 基准目录后执行（Windows 示例）：

```bash
python "<SKILL_DIR>/scripts/ms_checkin.py"
```

| 参数 | 作用 |
|---|---|
| 无参数 | 每日领取：无头开站点 → 点「领取」→ 比对余额 → 写流水 |
| `--login` | 起**有头** Chrome 供人工登录，并自动轮询验证登录结果 |
| `--status` | 只查登录态与余额，不点任何按钮 |
| `--probe` | 打印页面判据 / 魔粒文案 / 正文，排查改版用 |
| `--headed` | 本次用有头模式（想亲眼看页面） |
| `--keep` | 跑完不关 Chrome |
| `--wait N` | `--login` 时等待登录的秒数（默认 300） |
| `--port N` | CDP 端口（默认 9333） |
| `--profile PATH` | Chrome profile 目录（默认 `~/chrome-ms-profile`） |
| `--json` | 额外输出一行 JSON 结果，便于程序消费 |

环境变量：`MS_CDP_PORT`、`MS_CDP_PROFILE`、`MS_CHECKIN_LOG`（流水路径，默认 `~/.workbuddy/data/modelscope-magicube/checkin_log.json`）。

**推荐 Python**：`C:\Python315\python.exe`（本机 `WindowsApps\python.exe` 是 App Execution Alias 存根，直跑退 9009）。

## 状态判据（核心，汇报时别弄错）

| 状态 | 含义 | 退出码 | 汇报口径 |
|---|---|---|---|
| `CLAIMED` | 点了「领取」且**余额真的增加** | 0 | 「领到了，+N 粒」 |
| `ALREADY` | 没点到按钮，但页面写着「今日已完成」 | 0 | 「今天已经领过了」——**不是新领到，别说成领到了** |
| `LOGGED_IN` | `--status` 专用：登录态正常 | 0 | 「已登录，余额 N」 |
| `NO_LOGIN` | 页面仍有「登录 / 注册」入口 | 2 | 「登录态失效了，需要人工登一次」 |
| `UNKNOWN` | 没找到领取入口 / 点了但余额没变 / 页面没渲染完 | 3 | 「没拿到判据」，**别当失败也别当成功**，让用户看 `--probe` |

🚨 **判据必须是「点了 + 余额变了」**。只判「页面读得到余额」等于只登录不领取也报成功 —— 典型的假绿：日志天天 OK，魔粒一分没多。

## 首次使用：人工登录一次

1. 执行 `python "<SKILL_DIR>/scripts/ms_checkin.py" --login`
2. 在弹出的 Chrome 窗口里登录 modelscope.cn（验证码/短信都在那个窗口里）
3. 脚本每几秒探一次登录态，**登录成功后自动退出**并打印当前余额，无需手动关窗口

⚠ **`--login` 会在本机弹有头浏览器窗口**，属于需要人工介入的步骤，先告知用户再跑。
⚠ 该命令会等待最多 300 秒（`--wait` 可调），**用后台任务方式跑**（例如 `run_in_background`），别在 120 秒超时的前台调用里干等。
⚠ 登录态存在**独立 profile**（`~/chrome-ms-profile`），与日常 Chrome 分家，这是 Chrome 136+ 的官方设计，**不是故障**。

## 故障处置

| 现象 | 处置 |
|---|---|
| `NO_LOGIN` / 退出码 2 | 跑一次 `--login` 人工登录 |
| `UNKNOWN` / 退出码 3 | 跑 `--probe` 看页面到底返回了什么；多半是站点改版或按钮文案变了，按探针输出改 `scripts/ms_checkin.py` 里的 `JS_FIND_CLAIM` 正则 |
| 连不上 CDP 端口 | 先查 `tasklist \| findstr chrome` 是否有残留；确认没有 chrome.exe 占着 profile；有强杀残留的锁就先清 `<profile>\Default\LOCK` |
| Chrome 启动即退、无窗口 | 90% 是没带 `--no-sandbox`（本 skill 已强制带上）；其次是 `Default\LOCK` 残留（脚本会自动清） |
| 页面一直转圈 / 打不开 | 代理问题。脚本启动 Chrome 前会清 `http_proxy` 等环境变量（留着会掐断 CDP 的 WebSocket）；若本机访问外网必须走代理，改 `scripts/ms_cdp.py` 的 `clear_proxy()` |
| 找不到 Chrome | 装 Chrome for Testing（官方对自动化场景的推荐），或改 `CHROME_CANDIDATES` 加路径 |
| 点了但余额没变 | 可能延迟到账，去页面人工确认一次；确认已到账就把当天流水状态改成 `CLAIMED` |

## 定时任务

脚本幂等：同一天重复跑会覆盖当天流水，不会重复领。适合配每日定时任务（RRULE 用 `FREQ=DAILY;BYHOUR=9;BYMINUTE=0`）。
`--login` 不要放进定时任务（需要人工交互）。

## 已知坑（改代码前先读完）

1. **没有官方签到 API**。魔粒的「每日发放」只有社区逆向出来的余额查询接口，任何自动签到脚本都是模拟网页操作 ⇒ 站点改版就失效。所以本脚本走 CDP 真点页面。
2. **不是「登录即到账」，要在魔粒页点「领取」**。这是 2026-10-29 用户侧调研纠正的前提。
3. **日常 Chrome 的 cookie 解不开**。Chrome 127+ 是 v20 前缀 App-Bound 加密，解密要过 Chrome 自己的 elevation service。且 Chrome 136+ 拒绝在默认 user-data-dir 上开调试端口。⇒ 只能用独立 profile。
4. **一天有两个「领取」按钮**（注册并登录 200 + 绑定阿里云账号 50）。只点第一个会漏领 ⇒ 脚本循环点，最多 5 轮。绑定阿里云那 50 粒需要用户自己在页面上完成 OAuth 绑定，**脚本不代做**。
5. **按钮识别只认短文案的叶子元素**。`<div class="task"><button>领取</button></div>` 里那个 div 的 innerText 也正好是「领取」，会被误当成按钮点掉；`<button disabled>` 的 className 是空的，只看 class 判不出来 ⇒ 必须查 `el.disabled` 属性 + 祖先链。
6. **已点过的按钮靠 `data-ms-clicked` 属性去重**，因为点完按钮文案可能不变（真实页面会变成「今日已完成」）。
7. **只关本次自己拉起的 Chrome**。端口上本来就有 Chrome 时绝不 `close_browser` —— 那会把用户自己的窗口一起干掉。
8. **沙箱会回收后台进程树**（约 30 秒量级）。`--login` 必须与等待任务串在同一个后台任务里，否则浏览器窗口会被收走。

## 脚本清单

| 文件 | 作用 |
|---|---|
| `scripts/ms_checkin.py` | 主流程（领取 / 登录 / 状态 / 探针） |
| `scripts/ms_cdp.py` | 自包含的最小 CDP 客户端 + Chrome 启动器（零第三方依赖） |
| `scripts/selftest.py` | 自测：用本地假页面钉死「找按钮 / 点按钮 / 余额判据」三段逻辑 |

改动 JS 判据后跑一次自测：`python "<SKILL_DIR>/scripts/selftest.py"`（9 项，不碰真实账号）。

## 背景：魔粒体系

- 官方中文名「魔粒」/ 英文 Magicube，文档 `modelscope.cn/docs/magicube/intro`
- 单价：轻量 0.5 粒 / 主流 1 粒 / 旗舰 2 粒每次
- 每日额度：登录 200 粒；做任务最高 500/天（绑阿里云 +50、发 AIGC 作品 ×10 +100、收藏/喜欢 ×20 +40、评论 ×2 +10、完善信息 +50）
- 有效期：短期粒 24h、长期粒 90 天，自动先扣临近过期的
- 满额 500 粒 ⇒ 旗舰 250 次 / 主流 500 次 / 轻量 1000 次每天
- 另有账号级 2000 次/天、单模型动态约 200 次/天两道上限，实际可用 = min(额度、单模型、账号级)

## 与 ai-case-library 的关系

项目内同源入口：`D:\ai-case-library\scripts\ms_checkin.py`（早期版本，依赖项目的 `wechat_publish.py` / `chrome_cdp_launch.py`）。
本 skill 版更完整：自包含 CDP、登录轮询验证、两个领取按钮循环、`--status`、自测脚本。两者判据逻辑已同步。
