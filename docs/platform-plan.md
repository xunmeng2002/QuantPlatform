# 量化回测平台实施计划

本文是 `QuantPlatform` 的总体实施计划：以 Vue 3 前端 + FastAPI 后端，包裹
`../QuantTrading` 的 C++ 回测引擎，做成一个**云上多用户**的回测平台。

计划的**前提不是本文新拟的**，而是承接 `QuantTrading` 仓内已定案的平台化结论
（`PROGRESS.md` 与归档 `D.48`，2026-09-18 用户拍板）。本文只解决那些定案中
「剩余全在调度侧」的部分，叠加上 2026-09-25 由用户拍板的多用户与上传决策。

---

## 1. 已定案的前提

以下八条来自 `QuantTrading` 归档 `D.48`，本平台**不得与之冲突**：

| 条目 | 定案 |
| ---- | ---- |
| 形态 | **调度层就是 web 后端**，不是另一个程序 |
| 部署 | web 后端**必须与引擎同机** |
| 通信 | 一次性进程 + 常驻调度层，**文件 + 退出码**，不走 RPC |
| RunId | **由调度侧经配置注入**，引擎不再自生成 |
| 隔离 | **每 job 独立工作目录** |
| catalog | **由调度侧写**，引擎零改动 |
| 策略 | **只收 Python 宿主**；平台拉起的是**用户策略程序** |
| 产物 | 本机磁盘 + SQLite，**不上对象存储** |

`QuantTrading/PROGRESS.md` 列的「上平台剩余四件」中：

- ① result 口径 —— **已了结**（第十六批）。
- ② job 工作目录的构造与 `.pyd` 查找 —— 标注为**硬钉子**，已由 P0 解除。
- ③ runner 本体 —— 拉进程、限时、收 stdout/stderr、读 `result.json`、写 catalog。
- ④ catalog 表结构 —— 见第 6 节。

2026-09-25 追加拍板（推翻同日更早的两项初判）：

| 条目 | 定案 |
| ---- | ---- |
| 用户 | **云上多用户**，现在就做多用户骨架 |
| 策略 | **由用户上传**，不再"仅内置策略" |
| 鉴权 | **要做**（推翻"不做，仅本机单用户"） |
| 隔离 | **目录级**起步，系统级为开放给不可信用户的前置门槛 |
| 可见性 | `private` / `shared` / `public` 三档 |
| 上云 | **本机先跑通，再迁 Windows 云主机** |

---

## 2. 实测确认的技术事实

本轮在开发机上实测所得，是后续所有设计的依据：

- 工具链：**Python 3.11.1**（正是 `.pyd` 的 `cp311`）、**Node 24.15 / npm 11.12**。
- 引擎产物：`result.json`、`BackTest_<RunId>.db`（17 张表）、
  `Dump/<RunId>/t_*.csv`（17 个）、`log/<名>.<时间戳>.log`。
- 权益曲线数据源现成：`Capital` 表含 `TradingDay, Balance, Available`，
  逐日一行，实测序列 `1000000.0 → 999549.73 → 999164.41`。
- 宿主契约：`python <策略>.py`，**从 CWD 读**两个硬编码名配置文件——
  策略配置（如 `TestStrategyGrid.json`）与引擎配置（`BackTest.json`），
  以 `sys.exit(main())` 走 0/1/2/3 退出码。
- 策略可用接口：10 个可覆写钩子（`on_start` / `on_tick` / `on_bar` /
  `on_trade` / `on_order` / `on_insert_order_rsp` / `on_cancel_order_rsp` /
  `on_session_begin` / `on_session_end` / `on_end`），若干下单与查询方法
  （`subscribe_tick` / `subscribe_bar` / `declare_bar_period` / `buy_open` /
  `sell_open` / `buy_close` / `sell_close` / `cancel_order` /
  `get_long_position` / `get_short_position` / `get_last_price`），
  模块级 `create_backtest_api` / `init_logger` / `shutdown_logger`。
- 行情数据根 `D:/MdBaoStock/Bar/` 存在，按 `Identity=<板块>/Year=<年>/`
  存放 `<年>_<周期>.parquet`。
- 既有栈惯例（`amies-data-platform`）：
  **FastAPI + SQLAlchemy + aiosqlite** / **Vue 3.5 + TS + Vite + Tailwind + Pinia**。

---

## 3. 硬钉子 ②：工作目录与 `.pyd` 查找（P0 已解）

`test/PythonStrategyGrid/grid_strategy.py` 靠 `__file__` 反推
`REPO_ROOT/bin/Release` 来找 `QuantTrading.cp311-win_amd64.pyd`。
**脚本一进独立工作目录就 import 失败。**

两条互补解法（**Phase 0 实测后已修正**，机制详见
[`job-workspace.md`](job-workspace.md) §3）：

1. **脚本原样复制到 job 目录根部，以裸文件名启动。**
   runner 把策略入口复制进 job 目录，以 `cwd=<job 目录>`、
   `argv[0]="grid_strategy.py"` 启动。复制是**原样**的，故现有
   `grid_strategy.py` **一行不改**即可运行。
2. **`PYTHONPATH` 指向引擎根——这是必需项，不是兜底。**
   脚本被复制后 `__file__` 指向 job 目录，凡靠 `__file__` 反推
   `bin/Release` 的写法都会算错，引擎根必须由环境变量显式给出。

原先"脚本留在原地、只改 CWD"的方案**已被实测否决**：引擎日志器（Spark 的
`Utility::ParseProcessName`）在 Windows 上**只认反斜杠为目录分隔符**，
拿到带路径的 `argv[0]` 会拼出非法日志路径，**启动期即终止进程（退出码 1）**。

> **提示**：`PYTHONPATH` 单独一条即可满足 `import QuantTrading` 及 `.pyd`
> 同目录依赖（`BackTest.dll`、`Core.dll`、`Network.dll` 等）——CPython 在
> Windows 以 `LOAD_WITH_ALTERED_SEARCH_PATH` 加载扩展模块。Phase 0 已实测。

**这条决定在多用户下更重要**：把策略入口复制进 job 目录，等于**冻结了当轮
实际执行的代码**。策略事后被改动或删除，历史 job 目录仍逐字自描述。

---

## 4. job 工作目录契约

契约全文（目录布局、启动契约、退出码、产物、Phase 0 实测记录）见
[`job-workspace.md`](job-workspace.md)。此处只复述两条决定后端设计的硬约束：

1. **写路径必须相对**：`DbHost` 与 `DumpPath` 一旦写成绝对路径，
   「每 job 独立工作目录」的隔离会**静默失效**——两个并发 job 会写
   同一个库且不报错。
2. **`argv[0]` 必须是无分隔符的裸文件名**：带正斜杠的路径会让引擎
   日志器拼出非法路径并**在启动期终止进程**（退出码 1）。这条与
   绝对/相对**无关**，只与分隔符有关。

---

## 5. 部署形态与隔离边界

### 5.1 部署

- **现在**：本机 Windows，监听 `127.0.0.1`，单实例。
- **将来**：**一台 Windows 云主机**。三点约束必须现在认下来：
  1. `.pyd` 是 `cp311-win_amd64`，**Linux 无解**，云主机只能是 Windows。
  2. 后端**必须与引擎同机**（读行情 parquet 磁盘 + 用 `.pyd` 拉起宿主），
     故横向扩展没有余地，扩容只能是纵向的。
  3. 行情数据要一并上云（现约 1.2 MB/年/板块，可接受，但需同步策略）。

### 5.2 隔离档位（已选：目录级）

平台要执行**用户上传的任意 Python**——这是设计的一部分（`D.48` 已承认
"in-process 任意代码执行不比独立 exe 安全"）。本平台把它放在**独立子进程**
里跑，不是在后端进程内，但**这不是沙箱**。

| 档位 | 做法 | 挡得住 | 挡不住 |
| ---- | ---- | ---- | ---- |
| 目录级（本期） | 每用户独立目录 + 运行时只暴露本人路径 + 全表行级过滤 | 误访问、界面越权 | **恶意读盘** |
| 系统级（上云前） | 每用户独立低权 OS 账号或容器 + ACL | 恶意代码 | —— |

> **警告**：目录级隔离**挡不住恶意读盘**——策略是任意 Python，带后端身份运行，
> 能自行按路径读他人的策略源码与产物，绕过全部 SQL 过滤。故本期**只对可信
> 用户开放**；开放给不可信用户之前，系统级隔离是必须的前置门槛。

**目录级隔离的落点**：策略与产物按用户分目录（见第 8 节），runner 构造 job
时**只挂载本人路径**，不把他人目录写进任何配置项传给宿主。

### 5.3 权限过滤是每张表都要的

不是"在策略表上加一列 `Strategies.OwnerUserId`"就完事：`Runs` 也必须按
`UserId` 过滤，否则甲能从运行列表里看到乙的成交明细。这类"漏了一处"的越权
很难靠人工审查发现，故实现上要求**查询入口统一收口**，不散落各处。

---

## 6. 数据模型

五张表。`runs` 的 `result.json` 镜像列的完整清单见
`QuantTrading/docs/backtest-run-contract.md` §2。

命名：表名与列名一律 PascalCase，Python 属性名 snake_case，两者由映射层对应
（`database-style.md` §8）。约束名不逐个手写，由命名约定统一生成：主键
`Pk<表>`、外键 `Fk<表><目标表>`、唯一 `Uq<表><列>`、索引 `Idx<表><列>`。

```sql
-- 用户
Users(
  Id PK, Username UNIQUE, PasswordHash, DisplayName,
  UserType,        -- admin / user
  Status,          -- active / disabled
  CreatedAt
)

-- 策略：逻辑实体（一个名字、一个归属）
Strategies(
  Id PK, OwnerUserId FK->Users, Name, Description,
  VisibilityType,  -- private / shared / public
  CreatedAt, UpdatedAt,
  DeletedAt,       -- 软删除；硬删会撞上历史运行的外键
  -- 名字只在未删行之间唯一（部分唯一索引），见 §6 修订 ③
  UNIQUE INDEX(OwnerUserId, Name) WHERE DeletedAt IS NULL
)

-- 策略版本：留档，append-only，永不改写
StrategyVersions(
  Id PK, StrategyId FK, VersionNo,
  EntryFilename,   -- 入口文件名，如 grid_strategy.py
  ConfigFilename,  -- 策略配置文件名，如 TestStrategyGrid.json
  ManifestJson,    -- 参数 schema + 支持的行情模式
  SourceHash,      -- sha256，兼作内容寻址
  StoragePath, UploadedAt, UploadedByUserId FK->Users,
  UNIQUE(StrategyId, VersionNo)
  -- 判重的键是 (SourceHash, ManifestJson) 两列，不加约束，见 §6 修订 ②
)

-- 策略授权：多对多（共享）
StrategyGrants(
  StrategyId FK, GranteeUserId FK,
  PermissionType,  -- run / read
  GrantedByUserId FK->Users, GrantedAt,
  PK(StrategyId, GranteeUserId)
)

-- 运行：钉版本，不钉策略
Runs(
  Id PK, UserId FK->Users,
  StrategyId FK, StrategyVersionId FK,  -- 前者供分组，后者供复现
  Status, SubmittedAt, StartedAt, FinishedAt, DurationMs,
  RunnerPid, Hostname, ExitCode,
  ParamsJson, BacktestConfigJson,
  -- result.json 的 25 个镜像列，列表页与对比页的排序/筛选取自此
  IsSuccess, ErrorId, ErrorMsg, SchemaVersion, MarketDataType,
  StartTradingDay, EndTradingDay, LastTradingDay, AccountId,
  Balance, Available, TotalCommission, TotalStampTax,
  TotalTransferFee, OrderCount, TradeCount, MdSubscribeCount,
  BarMarketDataCount, DepthMarketDataCount, InstrumentCount,
  CommissionMissingCount, CommissionZeroRateKeyCount,
  VolumeMultipleFallbackProductCount, BasicDataLoaded, HasCapital,
  -- 作业与输出
  WorkspacePath, DbPath, DumpPath, StdoutTail, StderrTail
)
```

**四条设计要点**：

1. **归属不是表**。策略属于谁由 `Strategies.OwnerUserId` 一列表达，
   一对一，建表即冗余。真正需要表的只有**共享**（多对多）。
2. **运行钉版本，不钉策略**。`Runs.StrategyId` 供"按策略分组看历史"，
   `Runs.StrategyVersionId` 供"逐字复现"。两者职责不同，**不是冗余**。
   只钉策略的话，策略改动后历史运行的参数与结果就对不上，对比不可信。
3. **镜像成列是必需的**。列表页与对比页要**按指标排序与筛序**，
   逐行去解数百个 `result.json` 不可行。`MissingRateKeys` 等长尾键
   留在文件里按需读。
4. **金额与指标列用 `Float`，不用 `Numeric`**。引擎报的是 float64
   （`Balance` 实测 `998951.45064649964`），存 `Numeric(18,6)` 会被舍入成
   `...450646`，**直接破坏 P0「与引擎逐位一致」的验收判据**。精度可议，
   但可复现性优先——这是本平台唯一不能退让的指标。
5. **策略软删除**（`Strategies.DeletedAt`）。计划原稿未提，实现时补入：
   硬删会与历史运行的外键冲突，而"删策略不影响历史运行"是既定要求。

**P2 实施中的三处修订**（上文 DDL 已按修订后的形态书写）：

① **`StrategyGrants` 的 `PK(StrategyId, GranteeUserId)` 不在 P2 落地**，
   授权接口推迟到 P2b。表已建，但 P2 不暴露写入路径。

② **`UNIQUE(StrategyId, SourceHash)` 撤销**。版本判重的键改为
   `(SourceHash, ManifestJson)` 这一对：同一份源码配不同 manifest（改了入口
   文件名、或增删了参数）是一份**新**版本。只钉 `SourceHash` 会把这种上传挡成
   完整性冲突，逼用户"改参数必须连源码一起改"——荒谬。判重挪到服务层做，
   那里判错也只是多一个目录，不伤完整性；`(StrategyId, VersionNo)` 仍是硬约束。

③ **`UNIQUE(OwnerUserId, Name)` 改成部分唯一索引**（`WHERE DeletedAt IS NULL`）。
   整表唯一会连已删策略的名字一起占住，而列表页与详情页都不再显示这条记录——
   用户看到的是"名字没人用，却说我重名"，且平台不提供硬删，没有任何接口能释放它。
   部分索引的语义正是"名字在**在用的**策略之间唯一"。**注意**：`sqlite_where`
   是方言选项，日后换库必须把同一条件带过去，否则索引会静默退化成整表唯一。

**artifact 不建表**：它是 `runs/<RunId>/` 下的文件，路径可由约定推出，建表即重复。

---

## 7. 策略上传与管理

### 7.1 上传形态

- **上传 `.py` 为必需**；manifest 可在网页表单填写，或上传 `manifest.json` 导入。
  两条路都落到 `strategy_versions.manifest_json`，故不必二选一。
- **不收 zip**：解压要逐条校验路径防 zip-slip，为一个单文件场景引入一整类
  漏洞面不划算。多文件策略（辅助模块）需要时再评估。
- 上传即建一个 `strategy_versions` 版本。`(source_hash, manifest_json)` 与
  该策略下**任一既有**版本相同则复用该版本，不重复占盘
  （**内容未变不产生新版本号**）。判重比的是"任一既有版本"而非"最新版本"：
  改了参数又改回去，就该命中那个老版本——版本号要能表达"内容变过几次"，
  不是"上传过几次"。
- **上传编码用 multipart**（`UploadFile`），`.py` 作文件部件，manifest 作一个
  文本字段：前端既可填表单，也可读入一份 `manifest.json` 再灌进同一字段，
  两条路落到同一处，服务端只有一条路径。Starlette 解析该编码需要
  `python-multipart`，已入 `requirements.txt`。

### 7.2 manifest 内容

| 字段 | 说明 |
| ---- | ---- |
| `entry_filename` | 入口文件名，决定 job 目录里的裸文件名与 `argv[0]` |
| `config_filename` | 策略配置文件名，平台据此渲染参数并写出该文件 |
| `supported_match_modes` | 该策略支持的行情模式，`Bar` / `Tick` |
| `params` | 参数列表：键 / 标签 / 类型 / 默认值 / 范围 / 枚举 |

**注意**：`params` 的类型枚举、取值范围、分组与联动**尚未定案**（见
`PROGRESS.md` ❓）。P2 只落地在任何方案下都成立的两条不变量——每项是一个 JSON
对象、且 `key` 非空且互不重复。参数项模型用 `extra="allow"`（未知键原样保留进
`ManifestJson`），manifest 顶层用 `extra="forbid"`：顶层键名写错（如 `param`
少个 s）会让参数整批静默落空，而参数项里的未知键此刻**正是**待定案的载体。
定案后补类型化字段即可，已上传的 manifest 不必改写。

### 7.3 为什么 manifest 必须声明行情模式

`QuantTrading` 有实据：Python 策略漏写 `on_bar` 时，引擎在 `MatchMode: Bar` 下
**静默 0 成交**——引擎报的 0 是忠实的，日志也正常，费率三项为 0 也是对的。
当时能潜伏数轮，是因为策略与配置由同一个人一次写好、无人交叉校验。

**上传之后这个前提消失了**：策略来自某个用户，运行配置来自提交表单，两者
不同时间、可能不同人。故平台必须**在提交时**校验：
`请求的 MatchMode ∈ manifest.supported_match_modes`，否则**直接拒绝**，
而不是等用户对着 0 笔成交去排查。

### 7.4 落盘与运行

```text
users/<user_id>/strategies/<strategy_id>/<version_no>/
├── entry.py          # 用户上传原文，平台永不改写
└── manifest.json     # 该版本的 manifest 快照
```

运行时把 `entry.py` 复制进 `runs/<RunId>/`，以裸文件名启动（见第 3 节），
并按 manifest 渲染参数写出 `<config_filename>`。

---

## 8. 目录结构

```text
QuantPlatform/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI 装配 + 启动恢复
│   │   ├── config.py          # 引擎根、runs 根、并发上限、超时
│   │   ├── auth/              # 登录、JWT、当前用户依赖
│   │   ├── catalog/           # db / models（五张表）/ schemas
│   │   ├── routers/           # auth / users / strategies / runs / artifacts
│   │   ├── scheduler/         # queue / workspace / runner / result / recovery
│   │   └── services/          # results_db / artifacts / strategy_store
│   ├── requirements.txt
│   └── data/catalog.db        # catalog（gitignore）
├── frontend/                  # 复刻 amies 的 api/views/stores/components
├── users/<user_id>/strategies/<strategy_id>/<version_no>/   # 策略库（gitignore）
├── runs/<RunId>/              # 每 job 独立工作目录（gitignore）
├── docs/{platform-plan.md, job-workspace.md}
└── PROGRESS.md / PROGRESS-archive.md
```

---

## 9. API 设计

除登录外，**全部端点要求已认证，且按当前用户过滤**。

| 方法 | 路径 | 说明 |
| ---- | ---- | ---- |
| `GET` | `/api/health` | 引擎自检：引擎根、`.pyd`、Python 版本、runs 根可写（**admin**） |
| `POST` | `/api/auth/login` | 登录换取 JWT |
| `GET` | `/api/auth/me` | 当前用户 |
| `GET` | `/api/users` | 用户列表（admin） |
| `POST` | `/api/users` | 建用户（admin） |
| `PATCH` | `/api/users/{id}/status` | 启用/停用（admin） |
| `GET` | `/api/strategies` | 可见策略：本人 + 授权共享 + public |
| `POST` | `/api/strategies` | 上传策略（`.py` + manifest） |
| `GET` | `/api/strategies/{id}` | 详情与版本列表 |
| `POST` | `/api/strategies/{id}/versions` | 上传新版本 |
| `PUT` | `/api/strategies/{id}/grants` | 授权共享（owner） |
| `DELETE` | `/api/strategies/{id}` | 删除（owner；历史 run 不受影响） |
| `POST` | `/api/runs` | 提交：校验模式 → 建工作目录 → 入队 → 返回 `run_id` |
| `GET` | `/api/runs` | 本人运行列表，按状态/策略筛选、按指标排序、分页 |
| `GET` | `/api/runs/{id}` | 元信息 + `result.json` 全文 |
| `GET` | `/api/runs/{id}/equity` | 权益曲线序列（读 `Capital` 逐日 `Balance`） |
| `GET` | `/api/runs/{id}/tables/{table}` | 结果表分页查询 |
| `GET` | `/api/runs/{id}/files` | 产物清单（名 + 大小） |
| `GET` | `/api/runs/{id}/files/{relpath}` | 产物下载 |
| `POST` | `/api/runs/{id}/cancel` | 终止进程 → 标 `interrupted` |
| `DELETE` | `/api/runs/{id}` | 删除 run（含工作目录） |
| `GET` | `/api/runs/compare?ids=a,b,c` | 多轮指标对比（仅本人的 run） |

> **修订（2026-09-25）**：`/api/health` 由本节原稿的**免认证**，经 P1 实施时
> 收为**需认证**，再于 P2 开工前收为**仅管理员**。理由是响应体携带引擎根与
> 运行根的绝对路径、缺失 DLL 的文件名与解释器版本——这是本机内部布局的清单，
> 而单机部署下不存在负载均衡这类匿名消费者。普通用户并非"少一道授权"，
> 而是**他所能做的动作里没有一项需要这份清单**，故给到他的只有泄漏面。
> 若日后多机部署确需匿名探活，应另开一个只回 `{"ready": true}` 的端点，
> 而不是放宽这一个。

> **P2 落地范围**：本节中 `POST /api/strategies`、`POST /api/strategies/{id}/versions`、
> `GET /api/strategies`、`GET /api/strategies/{id}`、`DELETE /api/strategies/{id}`
> 已实现。`PUT /api/strategies/{id}/grants` 推迟到 **P2b**——授权会牵出"被授权人
> 能对该策略做什么"的一整串判定，与上传落盘是两件事，混在一批里改，出问题时
> 分不清是哪半边。写操作（传新版本、删除）只对归属人开放，非归属人一律 404，
> 不区分"无权"与"不存在"——区分了等于确认该 id 存在。

**三条安全硬约束**（Harness §6）：

- `tables/{table}` 的表名走**白名单**，不得直接拼进 SQL。
- `files/{relpath}` 必须 `resolve()` 后校验仍在 run 工作目录内，**防目录穿越**。
- **每个查询入口统一收口**加 `user_id` 条件；不散落各处，避免漏一处即越权。

**结果读取方式**：对 `BackTest_<RunId>.db` 开**只读连接**（`?mode=ro`），
不解析 CSV。理由有两条：归档已核「读已完成的产物是纯只读、SHARED 锁可共存」；
而 `SqliteWrapper` **未设 `busy_timeout`**，**绝不许**在运行中读——完成信号恒为
「进程退出」，天然避开。CSV 仅用于打包下载。

---

## 10. 前端页面

| 路由 | 页面 | 内容 |
| ---- | ---- | ---- |
| `/login` | 登录 | 换取 JWT |
| `/runs` | 运行列表 | 状态徽章、耗时、RunId、关键指标列 |
| `/runs/new` | 新建回测 | 选策略 → 由 manifest 动态生成参数表单 → 引擎配置 |
| `/runs/:id` | 运行详情 | 概览 / 权益曲线 / 委托 / 成交 / 持仓 / 日志 / 产物 |
| `/compare` | 对比 | 多轮指标表 + 权益曲线叠加 |
| `/strategies` | 策略管理 | 列表、上传、版本历史、授权共享 |
| `/users` | 用户管理 | admin |
| `/settings` | 设置 | 引擎根、并发上限、超时、数据根，带 health 自检 |

图表库选用 **ECharts + vue-echarts**（量化领域事实标准：K 线、缩放、
大数据量折线开箱即用）。按需引入以控制体积。

---

## 11. 分期与验收

| 阶段 | 内容 | 验收 |
| ---- | ---- | ---- |
| **P0 地基探针** ✅ | 已按第 4 节契约跑通 | **2026-09-25 通过**：退出码 0，7 项指标与基准逐位一致 |
| **P1 后端骨架 + 多用户** ✅ | 五张表 / 登录 / JWT / 查询统一收口加 `user_id` | **2026-09-25 通过**：两账号互不可见；越权返回 404（175 项测试） |
| P2 策略上传 | 上传 `.py` + manifest / 校验 / 版本留档 / 落盘 | 上传后能跑通；manifest 缺模式或参数越界被拒 |
| **P2 上传核心** 🔄 | 建策略+首版 / 传新版本 / 列表详情 / 软删 | **2026-09-25 部分通过**：manifest 缺模式、文件名非法、参数 key 重复均被拒（261 项测试）。**「上传后能跑通」待 P3** |
| P2b 策略授权 | `PUT /{id}/grants`，`StrategyGrants` 写入路径 | 被授权人可见可跑；非授权人一律 404 |
| P3 runner 本体 | 队列 / subprocess / 结果回收 / 启动恢复 / 作业目录构造 | 轮询至 `succeeded`；重启后端须标 `interrupted` |
| P4 前端骨架 | Vue 3 + TS + Vite + Tailwind + Pinia | 完成「登录 → 上传策略 → 提交 → 看指标 → 下载」闭环 |
| P5 可视化 | 权益曲线 + 回撤、明细分页表 | 曲线与实测数据点吻合（`1000000.0 → 999549.73`） |
| P6 对比与模板 | 多轮对比、配置模板保存复用 | 同参不同 `GridStep` 的两轮指标并列且曲线叠加 |
| P7 加固 | 并发上限、超时、磁盘清理、日志轮转 | 并发上限内排队正确；超时轮标 `timeout` |
| P8 上云 | 迁 Windows 云主机 + 传行情数据 + 系统级隔离评估 | 系统级隔离到位后方可对不可信用户开放 |

P0 的验收里 `TradeCount>0` 是关键判据：`QuantTrading` 记载过 Python 宿主因漏写
`on_bar` 而**静默 0 成交**（引擎报的 0 是忠实的），故只看 `Success` 不足以判定打通。

P1 的越权验收用 **404 而非 403**：403 会泄漏"该 id 存在"这一事实，
多租户下应一律表现为"不存在"。

---

## 12. 风险与不做项

1. **绑死 Windows + Python 3.11**：`.pyd` 是 `cp311-win_amd64`，
   Linux 无解，云主机只能是 Windows；后端必须与引擎同机，**横向扩展无余地**。
2. **目录级隔离挡不住恶意读盘**（见 5.2）。开放给不可信用户前必须上系统级隔离。
3. **磁盘膨胀**：实测单轮 **3.0 MB**（结果库 + 17 个 CSV + 日志），
   多用户下增长更快，需按用户配额与清理策略（P7）。
4. **cancel 的进程树**：宿主是单进程（引擎以库形式进程内加载），
   `terminate()` 够用，不必上 `psutil`。
5. **输出编码**：引擎有 GBK 变体、日志含中文，runner 读 stdout/stderr 须
   `errors="replace"` 容错，否则收尾部时会崩。
6. **引擎日志名由 `argv[0]` 经 `ParseProcessName` 决定**：Windows 上只认
   反斜杠为分隔符、按首个点截断扩展名，故 `argv[0]` 必须传裸文件名，
   否则**启动期即终止进程**，详见 [`job-workspace.md`](job-workspace.md) §3.1。
7. **`BarPreces` 是引擎侧既有拼写**（非笔误，不可擅改），平台配置键须逐字一致。
8. **不做**：跨运行库级对比（`ATTACH` 上限 10，已决定暂不做）、盘后行情修正、
   C++ 策略宿主（三条再评估触发条件已留档）、zip 上传（见 7.1）。

---

## 13. 已锁定的实现选择

2026-09-25 由用户拍板，**含同日更早两项初判的修正**：

| 决策点 | 选择 | 备注 |
| ---- | ---- | ---- |
| 图表库 | ECharts + vue-echarts | |
| 并发形态 | 后端进程内 `subprocess` | |
| 用户模型 | 云上多用户，现在就做骨架 | |
| 策略来源 | 用户上传 | **修正**：原"v1 仅内置策略"已废弃 |
| 鉴权 | 做 | **修正**：原"不做，仅本机单用户"已废弃 |
| 隔离档位 | 目录级起步 | 系统级为上云前置门槛 |
| 可见性 | `private` / `shared` / `public` | |
| 上云节奏 | 本机跑通再迁 | |
| 上传形态 | `.py` 必需 + manifest 表单或文件 | 不收 zip |
