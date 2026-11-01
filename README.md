# modelscope-magicube-checkin

> 魔搭（ModelScope）每日魔粒自动领取 Skill —— 真点页面、以余额变化为唯一成功判据。

魔粒（Magicube）是魔搭 API-Inference 的算力额度。**登录魔搭不等于到账** —— 必须到魔粒页点「领取」，额度才会进余额。
本 Skill 用 Chrome DevTools Protocol 真点页面按钮，并**以余额前后差值作为唯一成功判据**。脚本零第三方依赖（只用 Python 标准库）。

- **判据不造假**：必须「点了 + 余额真的变了」才算领到。只判「页面读得到余额」是典型的假绿（日志天天 OK，额度一分没多）
- **零依赖**：只用 Python 标准库（自带最小 WebSocket + CDP 客户端），无需 `pip install`
- **不碰凭据**：复用你自己浏览器里的一次性登录态，脚本不存密码、不做网络登录
- **两按钮连点**：一天有「注册并登录 200」+「绑定阿里云 50」两个领取按钮，只点一个会漏领
- **四态可判**：`CLAIMED` / `ALREADY` / `NO_LOGIN` / `UNKNOWN`，退出码区分，漏跑无副作用
- **带自测**：9 项本地自测钉死「找按钮 / 点按钮 / 余额判据」三段逻辑，改 JS 判据后可回归

## 效果

```
$ python modelscope-magicube-checkin/scripts/ms_checkin.py
[ms] 打开魔粒页...
[ms] 余额 before = 430
[ms] 命中领取按钮：领取
[ms] 点击后等待到账...
[ms] 余额 after = 630
[ms] 状态 = CLAIMED（+200 粒）
```

已领过的一天：

```
[ms] 页面显示「今日已完成」
[ms] 状态 = ALREADY（今天已领过，不重复领）
```

## 安装

### 方式一：作为 WorkBuddy Skill（推荐）

```bash
git clone https://github.com/oracis/modelscope-magicube-checkin.git
cd modelscope-magicube-checkin

# Windows (Git Bash)
cp -r modelscope-magicube-checkin ~/.workbuddy/skills/

# macOS / Linux
cp -r modelscope-magicube-checkin ~/.workbuddy/skills/
```

或用附带的安装脚本：

```bash
bash install.sh              # macOS / Linux / Git Bash
./install.ps1                # PowerShell
```

脚本会自动：备份旧版本 → 安装 → 跑一次**只读自检**（`--status`，不点任何按钮）。

### 方式二：直接跑脚本（不装成 Skill）

```bash
python modelscope-magicube-checkin/scripts/ms_checkin.py --status
```

## 首次使用：人工登录一次

魔搭登录要验证码/短信，绕不过，必须人工一次：

```bash
python modelscope-magicube-checkin/scripts/ms_checkin.py --login
```

会弹出**有头** Chrome 窗口 → 登录 modelscope.cn → 脚本每几秒探一次登录态，**登录成功自动退出**并打印余额。

> ⚠ 登录态存在**独立 profile**（默认 `~/chrome-ms-profile`），与日常 Chrome 分家 —— 这是 Chrome 136+ 的官方设计（默认 user-data-dir 不允许开调试端口），不是故障。

之后每天无头跑即可：

```bash
python modelscope-magicube-checkin/scripts/ms_checkin.py
```

## 参数

| 参数 | 作用 |
|---|---|
| 无参数 | 每日领取：无头开站点 → 点「领取」→ 比对余额 → 写流水 |
| `--login` | 起有头 Chrome 供人工登录，并自动轮询验证登录结果 |
| `--status` | 只查登录态与余额，**不点任何按钮** |
| `--probe` | 打印页面判据 / 魔粒文案 / 正文，排查站点改版用 |
| `--headed` | 本次用有头模式 |
| `--keep` | 跑完不关 Chrome |
| `--wait N` | `--login` 时等待登录的秒数（默认 300） |
| `--port N` | CDP 端口（默认 9333） |
| `--profile PATH` | Chrome profile 目录（默认 `~/chrome-ms-profile`） |
| `--json` | 额外输出一行 JSON 结果 |

## 状态判据

| 状态 | 含义 | 退出码 |
|---|---|---|
| `CLAIMED` | 点了「领取」且**余额真的增加** | 0 |
| `ALREADY` | 页面写着「今日已完成」 | 0 |
| `LOGGED_IN` | `--status` 专用：登录态正常 | 0 |
| `NO_LOGIN` | 页面仍有「登录 / 注册」入口 | 2 |
| `UNKNOWN` | 没拿到判据（站点改版 / 页面没渲染完） | 3 |

## 定时任务

脚本幂等：同一天重复跑会覆盖当天流水，不会重复领。适合配每日定时任务。
`--login` **不要**放进定时任务（需要人工交互）。

## 依赖

- Python 3.8+（只用标准库）
- Chrome 或 Chrome for Testing（自动探测常见安装路径）

## 自测

```bash
python modelscope-magicube-checkin/scripts/selftest.py
```

9 项本地自测，用本地假页面钉死「找按钮 / 点按钮 / 余额判据」三段逻辑，**不碰真实账号**。改过 `scripts/ms_checkin.py` 里的 JS 判据后请跑一次。

## 故障速查

| 现象 | 处置 |
|---|---|
| 退出码 2（`NO_LOGIN`） | 跑一次 `--login` 人工登录 |
| 退出码 3（`UNKNOWN`） | 跑 `--probe` 看页面实际返回；多半是站点改版，按探针输出改 `JS_FIND_CLAIM` 正则 |
| 连不上 CDP 端口 | 查 `tasklist \| findstr chrome` 有无残留；强杀残留的锁由脚本自动清理 |
| Chrome 启动即退 | 脚本已强制带 `--no-sandbox`；其次是 profile 锁残留（脚本自动清） |
| 页面一直转圈 | 代理问题。脚本启动 Chrome 前会清 `http_proxy` 等环境变量（留着会掐断 CDP 的 WebSocket） |
| 点了但余额没变 | 可能延迟到账，人工到页面确认一次 |

## 背景：魔粒体系

- 单价：轻量 0.5 粒 / 主流 1 粒 / 旗舰 2 粒每次
- 每日额度：登录 200 粒；做任务最高 500/天
- 有效期：短期粒 24h、长期粒 90 天，自动先扣临近过期的
- 满额 500 粒 ⇒ 旗舰 250 次 / 主流 500 次 / 轻量 1000 次每天
- 另有账号级 2000 次/天、单模型动态约 200 次/天两道上限

官方文档：`modelscope.cn/docs/magicube/intro`

## 已知边界

- **没有官方签到 API**。任何自动签到脚本都是模拟网页操作 ⇒ 站点改版就失效。这是绕不开的前提，不是本 Skill 的缺陷
- **绑定阿里云那 50 粒**需要你自己在页面上完成 OAuth 绑定，脚本不代做
- 首次登录必须人工一次（验证码/短信）

## License

MIT
