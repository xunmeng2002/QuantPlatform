# 平台部署

一句话：把平台发布包解压成部署根，把引擎包与行情组件包解压到它下面，写一份 `backend/.env`，
建一次虚拟环境，跑 `start-platform`，打开 `http://<主机>:8000/`。全程**只有一个进程**在跑。

## 1. 部署形态

发布包里带着**前端构建产物**，后端因此同源托管前端与 API：一个进程、一个端口，不需要 Node，
不需要反向代理，也不存在跨域（后端没有任何 CORS 中间件，同源是它成立的前提）。

引擎与行情组件**不在这个包里**。它们各自有发布包，平台按路径引用：

| 件 | 谁提供 | 平台怎么用它 |
| ---- | ---- | ---- |
| 平台 | 本发布包 | 后端一个进程 + 前端静态产物 |
| 引擎 | `QuantTrading-<版本>-<平台>.zip` | 按 `QUANT_ENGINE_ROOT` 就地引用，**不复制进平台** |
| 行情组件 | `QuoteHub-v<版本>-src-<日期>.zip` | 按 `QUANT_QUOTE_HUB_ROOT` 跑它的命令行、读它的库 |

> **注意**：行情组件的命令行由**后端自己的解释器**启动（`services/quote_hub.py` 传的是
> `sys.executable`），所以平台的虚拟环境必须同时装上它那四个依赖（`baostock` / `numpy` /
> `pandas` / `pyarrow`）。包里的 `requirements.deploy.txt` 已经把两份依赖清单并起来冻结过，
> 照它装即可；只装平台自己的 `backend/requirements.txt` 不会报错，直到下载行情那一步才炸。

## 2. 目标机前提

- **Python 3.14 x64**。引擎的扩展模块带 ABI 标签（Windows 是 `cp314-win_amd64`），解释器小版本
  必须与它一致，`chmod` 一样改不了的硬约束。
- 平台包与**对应平台**的引擎包各一份（平台包两个平台通用，引擎包分平台）。
- 目标机能联网（装依赖那一步）。回测读本地 parquet 不需要网络，但首次下载行情需要。
- 首启要用一个初始管理员口令，否则空库启动会直接 `RuntimeError`——这是有意为之，不是故障。

## 3. 目录布局

平台包的顶层目录固定叫 `QuantPlatform`（**不带版本号**），所以解压到 `D:\` 正好落在
`D:\QuantPlatform`。它以 `PLATFORM_ROOT` 的身份解析全部默认路径：

```text
D:\QuantPlatform\                              ← 部署根（解压平台包即得这一层）
├── backend\
│   ├── app\                                   包内：后端源码
│   ├── reference_seed\                        包内：三份初始化 CSV（A 股品种码 + 默认费率，首次播种用）
│   ├── requirements.txt  pyproject.toml  .env.example
│   ├── .env                                   部署时写（不在包里）
│   └── data\catalog.db  data\jwt_secret.key   首次启动自建（不在包里）
├── frontend\dist\                             包内：前端构建产物
├── docs\                                      包内：本文件、验收清单与作业目录契约
├── requirements.deploy.txt  RELEASE-INFO.txt
├── start-platform.ps1  start-platform.sh
├── .venv\                                     部署时建（不在包里）
├── QuantTrading-1.0.0-windows-x86_64\         引擎包解压（**目录名不改**，换版按版本留）
├── QuoteHub\                                  行情组件包解压
│   └── stock_data.db                          在它仓根跑一次 init_database.py --create
├── market-data\                               行情落地。**先建出来**：平台不替它建，见 §4
├── runs\                                      首轮作业时自建
└── users\                                     首次上传策略时自建
```

`market-data` / `runs` / `users` **不用配**：它们的默认值就在部署根内，前两者用到时才自建，
`market-data` 则要手工建（缺了提交会被 400 挡下，报「行情数据根目录不存在」）。只有引擎与
行情组件在部署根**之内**而又不叫默认名，故必须显式指定（见 §5）。WSL 上把 `D:\QuantPlatform`
换成 `/opt/quant-platform` 即可，布局一字不变。

## 4. 一次部署

1. 解压平台包到部署根（`D:\`，或 WSL 的 `/opt`）。
2. 解压引擎包与行情组件包到同一个部署根下，得到 `QuantTrading-<版本>-<平台>\` 与 `QuoteHub\`。
3. 建出 `market-data\`（在部署根下 `mkdir market-data`，内容不必手工铺，行情由平台的下载功能
   经行情组件落进去）。平台**不会**替你建这一层，缺了在提交那一刻被 400 挡下。
4. 建虚拟环境并装依赖（用**包内冻结的**锁文件，它钉死了当时那套版本）：

   ```shell
   # Windows（在部署根下）
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.deploy.txt

   # WSL
   python3 -m venv .venv
   ./.venv/bin/python -m pip install -r requirements.deploy.txt
   ```

5. 初始化行情组件的库（**唯一一处要跨仓同步的动作**）。这件事有**两半**，缺了第二半平台
   照常启动、但**跑不起任何一轮**：

   ```shell
   cd QuoteHub

   # 5a. 建库：24 张表，全部是空的
   ../.venv/Scripts/python.exe init_database.py --create    # WSL 用 ../.venv/bin/python

   # 5b. 引导同步：填 Securities（合约清单）与 TradeDates（交易日历）
   ../.venv/Scripts/python.exe bao_stock.py --sync-securities
   ../.venv/Scripts/python.exe bao_stock.py --update-trade-dates
   ```

   **5b 不能省，且必须在这个入口跑。** 平台自己调的是组件**另一个**入口 `bao_stock_parquet.py`，
   它只写 `MinuteBars` / `QueriedBarSpans`，**不碰** `Securities` / `TradeDates`；而提交页的合约
   下拉框读的正是 `Securities`。空库上于是形成一个**闭环缺口**：下拉框空 → 选不出合约 → 提交不了
   → 触达不到平台那次下载 → `Securities` 永远是空的。两条命令都要 BaoStock 联网登录（实测全市场
   7430 只，落到平台侧可选的股票 5214 只）。

6. 按 §5 写 `backend/.env`。
7. 起服务：`.\start-platform.ps1`（WSL 用 `./start-platform.sh`）。
8. 打开 `http://localhost:8000/`，用初始管理员登录。

## 5. `backend/.env` 必改项

其余键见 `backend/.env.example`（随包交付，与代码同源）。**这三项必须按本机实际路径给**：

```ini
# Windows：引擎目录名照解压出来的原文写，不要改成 engine —— 换版时它才是「按版本留目录」的那一层
QUANT_ENGINE_ROOT=D:/QuantPlatform/QuantTrading-1.0.0-windows-x86_64
QUANT_QUOTE_HUB_ROOT=D:/QuantPlatform/QuoteHub
QUANT_INITIAL_ADMIN_PASSWORD=换成你自己的口令
```

```ini
# WSL
QUANT_ENGINE_ROOT=/opt/quant-platform/QuantTrading-1.0.0-linux-x86_64
QUANT_QUOTE_HUB_ROOT=/opt/quant-platform/QuoteHub
QUANT_INITIAL_ADMIN_PASSWORD=换成你自己的口令
```

三条容易踩的：

- **默认值指向部署根的上一级**。`QUANT_ENGINE_ROOT` 默认是 `<部署根>/../QuantTrading/bin/Release`，
  `QUANT_QUOTE_HUB_ROOT` 默认是 `<部署根>/../QuoteHub`。开发机的仓是同级的，部署根里它是子目录，
  故这两行不写就是错的——而现象是「每一轮都失败」，不是启动报错。
- **要让别的机器访问，得把 `QUANT_HTTP_HOST` 从 `127.0.0.1` 改成 `0.0.0.0`**。改之前先读
  `platform-plan.md` §5.2：平台执行的是**用户上传的任意 Python**，本期只有目录级隔离，
  **挡不住恶意读盘**。只对可信网络开放；开放给不可信用户之前，系统级隔离是前置门槛。
  真有反向代理在前面，还要把 `QUANT_TRUSTED_PROXY_ADDRESSES` 配对，否则全部用户挤进同一个
  登录节流桶，任意 5 次失败就能把所有人一起锁住。
- **交给不可信的人用（演示、对外托管），把 `QUANT_STRATEGY_UPLOAD_ALLOWED` 设成 `false`**。
  它只放行管理员上传，此后新入库的策略便全部出自你手（**存量不受影响**）——这是系统级隔离
  到位之前，唯一能把 §5.2 那条风险关掉的做法。它管不着**运行**那一侧：库里已有的策略仍以
  平台身份执行，故不是系统级隔离的替代品。默认是 `true`（常规用法就是用户带策略来）。

## 6. 判据

最省事的一条是读 `/api/health`——它一次把本进程与三件外部件（引擎、行情数据、会话文件）的
关系全报出来，`ready` 只是这几项的总闸。浏览器里进「设置」页看的是同一份数据；要在命令行看：

```shell
# 取管理员令牌（口令就是写进 .env 的那个）
curl -s -X POST http://127.0.0.1:8000/api/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<初始口令>"}'

# 把返回里的 access_token 填进 <令牌>
curl -s http://127.0.0.1:8000/api/health -H 'Authorization: Bearer <令牌>'
```

要看的几项：

| 字段 | 期望 | 不是这样说明什么 |
| ---- | ---- | ---- |
| `engine_root_exists` | `true` | `QUANT_ENGINE_ROOT` 指错 |
| `engine_version` | 版本号（如 `1.0.0`） | 显示 `sha256:…` 摘要 = 引擎包缺 `engine-version.txt`（仍能跑） |
| `missing_runtime_filenames` | `[]` | 引擎包解压不完整 |
| `session_file_exists` | `true` | 引擎包里的 `Sessions.json` 没解压出来 |
| `market_data_root_exists` | `true` | `market-data\` 没建（§4 第 3 步） |

再走一遍功能面：

- 启动日志里有 `已从 …\.env 读入 N 项配置`（没有它说明 `.env` 根本没被读到），
  以及 `前端构建产物在 …, 由本进程同源托管`。
- 打开首页是**登录页**而不是一串 JSON；随便点两条路由再按 F5，不出现 404
  （前端是 history 模式，靠后端那一条 `index.html` 回退接住深链接）。
- 提交一轮回测跑到 `succeeded`。这一条同时证了引擎、行情数据与行情组件三者在位。
- 登管理员进「基础数据」页：品种表里已有 13 个 A 股品种码（沪深主板 / 科创板 / 创业板），费率
  明细里有 1 号组「A股默认费率」的 SSE / SZSE 两行（交易所级、双向：佣金 0.01%、平仓印花税
  0.05%、过户费 0.001%、佣金下限 5 元）。**全新安装不必手工录任何费率**——随包的三份 CSV 在首次
  启动时播种一次，此后要改就在这一页改，下一轮即生效。
  这三项按 2026 年的现行标准给：印花税 0.05% 卖出单边，过户费 0.001% 买卖双向（沪深已统一），
  佣金那一列按**全佣**填（含经手费与证管费），最低 5 元是监管硬性规定。佣金是三项里唯一可协商的
  ——0.01%(万1) 是优惠账户的常见档，你的券商不是这个数就在这一页改。
- 平台自身的功能验收走 `docs/acceptance-checklist.md`。

> **提示**：本节与 §4 的步骤在 2026-10-14 用 `QuantPlatform-0.1.0-2026-10-14.zip` 原样走过一遍
> （引擎与行情组件用开发机那份，只改路径），启动日志、首页、深链接、未知 `/api` 路径、
> `/api/health` 逐条符合预期。行情组件那条下载链路不在这次演练内。
>
> **2026-10-15 补记**：上一条演练用的是开发机上**已经填好**的 QuoteHub，故没撞上 §4 的 5b——
> 那一版本节让读者直接「提交一轮回测」，而在空机器上这一步**到不了**。本轮部署到空机才暴露，
> 缺口已就地补上；「提交一轮到 `succeeded`」这条判据现在可达。
## 7. 升级

**引擎**：解压新版本到一个**新目录**（名字里带版本），把 `QUANT_ENGINE_ROOT` 指过去、重启。
旧目录**留着**——每一轮在提交那一刻就把引擎标识冻进了 `Runs.EngineVersion`，留着旧目录，历史轮
才复现得出来（`platform-plan.md` §5.1）。

**平台**：部署根里除了包内文件，还住着运行数据与两个外部件，故**不能整目录替换**。步骤：

1. 停服务。
2. 把 `backend/data` 与 `backend/.env` 移出到临时处（这两样是 `backend/` 里唯一的运行数据）。
3. 删掉包内那几件：`backend/`、`frontend/`、`docs/`、`start-platform.*`、`requirements.deploy.txt`、
   `RELEASE-INFO.txt`、`.venv/`。
4. 解压新平台包到部署根，把第 2 步那两样移回 `backend/`。
5. 重装依赖（`requirements.deploy.txt` 可能与上一版不同），再起服务。

> **注意**：别拿「解压覆盖」当升级。覆盖不会删掉新版本里**已经删掉**的文件，于是旧模块继续在
> 盘上、继续被 import，症状是「改了的地方没生效」。

`runs/`、`users/`、`market-data/`、引擎目录、`QuoteHub/` 全程不动。

## 8. 备份与保留

| 对象 | 要不要备份 | 为什么 |
| ---- | ---- | ---- |
| `backend/data/catalog.db` | **要** | 账号、策略元数据与全部运行记录，删了不可再生 |
| `backend/data/jwt_secret.key` | **要** | 丢了不影响数据，但已签发的令牌全部失效（所有人重新登录） |
| `users/` | **要** | 用户上传的策略源码与配置，属用户资产 |
| `runs/` | 视需要 | 每轮产物；可重跑，但重跑要相同环境与时间 |
| `market-data/` | 不必 | 可重下，只是费时间 |
| `frontend/`、`backend/app/`、`.venv/` | 不必 | 全部来自发布包 |

**保留策略默认是关的**（`QUANT_RUN_RETENTION_ENABLED`）。打开后每人只留最新 N 轮，更旧的轮
**连作业目录一起删**，且不可逆——结果库明细、对比曲线、逐字复现要读的东西都在那个目录里。

## 9. 常见故障

| 现象 | 多半是 |
| ---- | ---- |
| 首页给一串 JSON 404，但 `/api/...` 正常 | `frontend/dist/index.html` 不在（包不全，或 `QUANT_FRONTEND_DIST_ROOT` 指错） |
| 首页打得开，点进某页再刷新就 404 | 不该发生：深链接由后端回退到 `index.html`。若真出现，看托管是否根本没挂上（比对上一行） |
| 页面白屏、控制台一堆资源 404 | 浏览器缓存了旧的 `index.html`（强刷一次）；或前端产物与源码对不上 |
| 启动即 `RuntimeError`（初始管理员口令） | 空库且没给 `QUANT_INITIAL_ADMIN_PASSWORD` |
| 提交回测 400「行情数据根目录不存在」 | `market-data/` 没建（§4 第 3 步；它不会自建） |
| 提交页的**合约下拉框是空的** | 没跑 §4 的 5b：`Securities` 是空的。平台自己那条下载命令填不上它 |
| 重启后**费率明细仍是空的** | 库里的组号不含 `1`（费率种子指着 1 号组），播种跳过该表并在启动日志留一行 WARNING。按库里实际有的组号手工补录，或在「基础数据」页把费率建到那个组下 |
| 提交回测 400「会话表文件不存在」 | 引擎包没解压全：`Sessions.json` 是它包里的件 |
| 每一轮都失败，提示行情组件不在位 | `QUANT_QUOTE_HUB_ROOT` 指错，或组件的库没建（§4 第 5 步） |
| 上传策略返回 403「本部署不接受用户上传策略」 | 这台部署把 `QUANT_STRATEGY_UPLOAD_ALLOWED` 设成了 false，且当前账号不是管理员（§5 第三条）。要传就用管理员，或把那项改回 true |
| 下载行情那一步 `ModuleNotFoundError: baostock` | venv 里没装组件依赖（没照 `requirements.deploy.txt` 装） |
| `/api/health` 的 `ready` 为假 | 逐项对着 §6 那张表看：引擎路径 / 运行时库 / `Sessions.json` / `market-data` |
| 别的机器打不开 | `QUANT_HTTP_HOST` 还是 `127.0.0.1`（改之前先读 §5 的第二条） |
