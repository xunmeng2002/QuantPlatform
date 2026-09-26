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

- 工具链：**Python 3.14.5**（正是 `.pyd` 的 ABI 标签 `cp314`；**2026-09-25 订正**，
  原记 3.11.1 / `cp311` 系早期误记）、**Node 24.16.0 / npm 11.13.0**
  （P4 实测订正，原记 24.15 / 11.12）。
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
  存放 `<年>_<周期>.parquet`。**只有 `Bar/`，没有 tick 数据目录。**
- 引擎单轮耗时（2026-09-25 P3 实测，`bin/Release` 下、5m 周期、单标的）：
  三个月 **约 1.7 秒**，2010–2024（58176 根 bar，678 笔成交）**约 3.8 秒**——
  比原估快得多，故"作业级超时"在真引擎上要靠**压时限**触发（见 §11 的 P3 行）。
- 既有栈惯例：**FastAPI + SQLAlchemy + aiosqlite** /
  **Vue 3.5 + TS + Vite + Tailwind + Pinia**。原文写的是"（`amies-data-platform`）
  的惯例"，但**该仓不在本机**（见 §8 的注）——P4 实际参照的是
  `ShopKit/frontend-platform` 与 `RMS/frontend-admin`，它们**不用 Tailwind**
  （SCSS + Element Plus），Tailwind 是本文档定的、不是照抄来的。

---

## 3. 硬钉子 ②：工作目录与 `.pyd` 查找（P0 已解）

`test/PythonStrategyGrid/grid_strategy.py` 靠 `__file__` 反推
`REPO_ROOT/bin/Release` 来找 `QuantTrading.cp314-win_amd64.pyd`。
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

> **2026-09-25 订正（P3 实测）**：此处原记「"脚本留在原地、只改 CWD"的方案
> **已被实测否决**：引擎日志器在 Windows 上只认反斜杠，拿到带路径的 `argv[0]`
> 会拼出非法日志路径，**启动期即终止进程（退出码 1）**」。**这条已不成立**：
> 以 `./grid_strategy.py`（正斜杠相对路径）与 `C:\...\Temp\<job>\grid_strategy.py`
> （反斜杠绝对路径）各跑一轮真引擎，**都是退出码 0、整轮回测跑完**。
> "脚本复制到 job 根部"仍是现行做法，但理由是它与其余契约自洽（配置文件与
> 产物同落 CWD、入口与 `cwd` 同处），**不是"否则起不来"**。

> **提示**：`PYTHONPATH` 单独一条即可满足 `import QuantTrading` 及 `.pyd`
> 同目录依赖（`BackTest.dll`、`Core.dll`、`Network.dll` 等）——CPython 在
> Windows 以 `LOAD_WITH_ALTERED_SEARCH_PATH` 加载扩展模块。Phase 0 已实测。

**这条决定在多用户下更重要**：把策略入口复制进 job 目录，等于**冻结了当轮
实际执行的代码**。策略事后被改动或删除，历史 job 目录仍逐字自描述。

---

## 4. job 工作目录契约

契约全文（目录布局、启动契约、退出码、产物、两轮实测记录）见
[`job-workspace.md`](job-workspace.md)。此处只复述两条决定后端设计的硬约束：

1. **写路径必须相对**：`DbHost` 与 `DumpPath` 一旦写成绝对路径，
   「每 job 独立工作目录」的隔离会**静默失效**——两个并发 job 会写
   同一个库且不报错。**P3 起这条在渲染器里是结构性的**：
   `render_engine_config()` 没有写路径形参，调用点传不进去绝对写路径。
2. **`argv[0]` 传裸文件名**（**不是硬约束，是固定选择**——
   2026-09-25 实测订正，见 §3 与 [`job-workspace.md`](job-workspace.md) §3.1）。
   带路径的形态实测同样能跑通，平台仍固定用裸文件名，因为它与
   "入口在 job 根、产物落 CWD"这套契约自洽。

---

## 5. 部署形态与隔离边界

### 5.1 部署

- **现在**：本机 Windows，监听 `127.0.0.1`，单实例。
- **将来**：**一台 Windows 云主机**。三点约束必须现在认下来：
  1. `.pyd` 是 `cp314-win_amd64`，**Linux 无解**，云主机只能是 Windows。
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
  VisibilityType,  -- private 仅归属人且授权表不生效 / shared 授权表内可见 / public 全员
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
  -- result.json 的 27 个镜像列（清单见 result.py 的 RESULT_MIRROR_COLUMN_NAMES），
  -- 列表页与对比页的排序/筛选取自此。结果文件里的 Success 落到 IsSuccess 列;
  -- 29 键里不镜像的两键是 RunId (即 Id 主键) 与 MissingRateKeys (长尾, 留在文件里)
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

① **`StrategyGrants` 的 `PK(StrategyId, GranteeUserId)` 已随 P2b 落地**，
   授权写入路径为 `PUT /api/strategies/{id}/grants`。P2 只建表不暴露写入路径，
   授权推迟到 P2b——它会牵出"被授权人能对该策略做什么"的一整串判定，
   与上传落盘是两件事，混在一批里改，出问题时分不清是哪半边。

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
| `supported_match_modes` | 该策略**声明**支持的行情模式，`Bar` / `Tick` |
| `run_field_keys` | 运行级字段在策略配置里的键名，见下 |
| `params` | 参数列表：键 / 标签 / 类型 / 默认值 / 范围 / 枚举 |

**`params` 的 schema 已于 2026-09-25（P3 开工前）定案**（原为 ❓，已结清）：

| 字段 | 必填 | 说明 |
| ---- | ---- | ---- |
| `key` | ✅ | 非空、≤64、`^[A-Za-z0-9_.-]+$`、同 manifest 内互不重复 |
| `label` | | 界面显示名，缺省回落 `key` |
| `type` | | `integer` / `number` / `string` / `boolean`，缺省 `string` |
| `default` | | **缺省即"提交方必须给出"**——不另设 `required` 标志 |
| `minimum` / `maximum` | | 仅 `integer` / `number` 允许 |
| `options` | | `[{"value": …, "label": …}]`，出现时取值必须落在其中 |
| `group` | | 分组名，纯供前端表单分区 |

- **为什么不设 `required`**：`required: true` 配 `default: 10` 是自相矛盾的组合，
  而"没有 `default` 就必须提交"已完整表达同一件事，且**写不出矛盾组合**。
- **为什么 `options` 不限类型**：`type=string + options`（周期）与
  `type=integer + options`（从 {1,5,10} 里选格数）都是真实需求，故 `options`
  是任何类型都可挂的约束，每项 `value` 按 `type` 校验。
- **默认值与提交值走同一个校验函数**，否则会出现"默认值自己不合法、
  提交方照抄默认值反被拒"。

**`run_field_keys`**（P3 新增）把三个运行级字段映射到策略配置里的键名，
键只允许 `exchange_id` / `instrument_id` / `bar_period`（`extra="forbid"` 收严，
写错键名立刻报错），值按与 `params[].key` 相同的模式校验，且**不得与任一
`params[].key` 重复**（否则同一份配置里有两个写入者，值以谁为准无从说起）。
三项均可省略，**省略即该字段不写进策略配置**。

它结清的是一类**静默失效**：`exchange_id` / `instrument_id` 引擎不认识，
只有策略的 `subscribe_tick` 用；而 `bar_period` **两处都要写**——引擎配置里的
`BarPreces`（实际聚合周期）与策略配置里的那个（`declare_bar_period` 的期望周期）
不一致时，策略收不到 bar、**静默 0 成交**。有映射时两者由平台写同一个值，
一致性从此是结构性的；**没有映射时平台只写引擎配置**，这份差异由策略作者承担。

参数项继续 `extra="allow"`（未知键原样保留），manifest 顶层继续 `extra="forbid"`
——顶层键名写错（如 `param` 少个 s）会让参数整批静默落空。

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

### 7.5 授权共享

`PUT /api/strategies/{id}/grants`，只对归属人开放，非归属人与不存在的策略
一律 404。请求体是**替换后的全集**（`{"grants": [{"grantee_user_id", "permission_type"}]}`），
空列表即撤销全部授权。整体替换而非增量：增量下"撤销谁"要另设一条删除路径，
而调用方手上的本来就是一份完整名单。

**授权驱动可见性**（2026-09-25 用户拍板）。这一条是本接口的语义核心：
`private` 下授权表**不生效**（见 §6 `Strategies.VisibilityType` 一行的注释），
而整个接口清单里**没有第二处**能把可见性改成 `shared`——建策略时的
`visibility_type` 表单字段是唯一的入口，建完就改不了。于是若授权与可见性各自为政，
在 `private` 策略上授权会**回 200 而无人获得访问权**：调用方看到的成败与事实相反，
且用户无从自救。故：

| 策略当前可见性 | 授权非空 | 授权清空 |
| ---- | ---- | ---- |
| `private` | 转 `shared` | 保持 `private` |
| `shared` | 保持 `shared` | 转 `private` |
| `public` | 保持 `public` | 保持 `public` |

`public` 一概不动：那份授权本就多余（全体登录用户已然可见），
而 `public` 转 `shared` 会把**没被逐一点名的用户静默挡在外面**——
收窄既有访问不是这条接口该做的事。

**错误文案只说明字段名与原因，不把调用方提交的被授权人 id 抄回响应体**
（同 §7.2 的既有规则：值会随响应体进接入层日志，而定问题靠字段名与原因）。

**`GrantedAt` 的语义**：整体替换会连未变动的条目一并删掉重建，
故它记的是"最近一次整体写入的时间"，不是"首次授权的时间"。要保留后者得改成
逐条增删改，那是另一套语义。

**被授权人按 `user_id` 指定，不按用户名**：按用户名的话，
"这个用户名不存在"与"授权成功"会构成一个**用户名存在性探针**，
而登录接口已经专门为此付过代价（账号不存在时也照跑一遍口令校验，
见 `auth.py`）。

**归属人怎么拿到同事的 `user_id`**（2026-09-25 用户拍板）：开放一条**受限
用户目录**端点，只回 `id` 与 `display_name`，供共享表单选人。
`GET /api/users` 是管理员专属，其理由是"用户清单本身即租户列表"——
受限目录**放宽了这一档**，故它必须自带边界面。原文见归档 `Q.03`。

**落地（P4，2026-09-25）**：`GET /api/users/directory`，鉴权用
`CurrentUserDependency`（**不是**管理员依赖），响应是房规分页信封，
每项只有 `id` 与 `display_name`。边界逐条定为：

| 项 | 定案 | 理由 |
| ---- | ---- | ---- |
| 谁可见 | 全体**已认证**用户 | 授权表单就在普通用户手上 |
| 列哪些账号 | 只列 `active` 且**显示名非空** | 停用账号登录不了，授权给他没有意义；显示名为空的号（P4 起已不可能新建）在表单上是一行空白 |
| 是否列自己 | **排除自己** | 授权给自己会被 `PUT /grants` 用 400 挡住，能选中一个必然失败的选项是白造死路 |
| 过滤 | 可选 `query` 子串（`contains(..., autoescape=True)`），**不按可见性过滤** | 全量枚举的代价已由下面的收口承担 |
| 排序 | `display_name ASC, id ASC` | 次序键 `id` 不能省：`display_name` 无唯一约束，并列行的顺序未定义，翻页会重复出条 |

**泄漏面（随端点一起评审，不另行挂账）**：

| 放宽了什么 | 收口到什么 |
| ---- | ---- |
| 全体登录用户可枚举"平台上有谁"——这正是 `GET /api/users` 限管理员的理由 | 只回**启用中**的账号，且只有 `id` 与 `display_name` 两个字段 |
| 显示名（真人可辨认的名字）对全体登录用户可见 | `username` **绝不出现**：登录接口专门为未知用户名跑一遍假校验（`DUMMY_PASSWORD_HASH`）防的就是用户名枚举，目录里回退 `username` 等于把那笔代价还回去 |
| —— | `user_type` 不出现：谁是管理员不该由目录暴露 |
| —— | `status` 不出现：只见启用中的账号，故"某人被停用了"也不可见 |

配套改动：`POST /api/users` 的 `display_name` **收紧为必填**
（空白回 400「显示名不能为空白」）——目录只列显示名非空的账号，
若建号时可以留空，管理员随手建的账号就会从目录里消失，而表单选不到人
正是本项目最忌讳的"点了没反应"。**已知缺口**：`display_name`
**没有唯一约束**，重名时表单分不清两个人（选错人 = 把策略授权给错的人）；
修法是加唯一约束，但那会新增一条 409 路径，见 §12.15。

---

## 8. 目录结构

```text
QuantPlatform/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI 装配 + 启动恢复
│   │   ├── config.py          # 引擎根、runs 根、并发上限、超时
│   │   ├── clock.py / ids.py / errors.py / dependencies.py / manifest.py
│   │   ├── bootstrap.py       # 首个管理员播种（无口令环境变量则启动即抛）
│   │   ├── auth/              # dependencies / passwords / tokens
│   │   ├── catalog/           # database / models（五张表）/ schemas /
│   │   │                      # enums / pagination / visibility（多租户唯一收口）
│   │   ├── routers/           # auth / users / strategies / runs / health
│   │   ├── scheduler/         # engine_config / result / output / workspace
│   │   │                      # registry / runner / recovery / scheduler (常驻循环)
│   │   └── services/          # run_submission / strategy_store / engine_probe /
│   │                          # result_database（P5：只读结果库与表名白名单）
│   ├── requirements.txt
│   └── data/catalog.db        # catalog（gitignore）
├── frontend/                  # 见下
├── users/<user_id>/strategies/<strategy_id>/<version_no>/   # 策略库（gitignore）
├── runs/<RunId>/              # 每 job 独立工作目录（gitignore）
├── docs/{platform-plan.md, job-workspace.md}
└── PROGRESS.md / PROGRESS-archive.md
```

**`frontend/`（P4 落地）**——纯逻辑与渲染分开，前者才是单测的对象：

```text
frontend/
├── index.html                # lang="zh-CN"
├── package.json / package-lock.json   # 锁文件入库
├── vite.config.ts            # vue + tailwind 插件；/api 代理与 vitest 配置同文件
├── tsconfig.json / tsconfig.app.json / tsconfig.node.json
└── src/
    ├── main.ts  App.vue  style.css    # style.css 内是 Tailwind 的 @theme 令牌
    ├── api/         client.ts（唯一出入口）/ types.ts（手写契约）/
    │                auth.ts / strategies.ts / runs.ts / users.ts
    ├── domain/      纯逻辑: manifest / run-form / run-status / format / labels /
    │                download / equity（P5：权益序列与回撤，纯函数）
    ├── stores/      session / strategy-catalog / user-directory
    ├── router/      index.ts（路由表 + 登录守卫 + 逐页懒加载）
    ├── components/  AppLayout / ParameterForm / ParameterField / DirectoryPicker /
    │                StrategyUploadForm / StrategyVersionUploadForm / ArtifactList /
    │                ConfirmDialog / PaginationBar / StatusBadge / ErrorBanner /
    │                EmptyNotice / LoadingNotice / FilePicker /
    │                EquityChartPanel / ResultTablePanel（P5）
    ├── composables/ usePolling.ts / useManifestTextSource.ts
    └── views/       LoginView / RunListView / RunSubmitView / RunDetailView /
                     StrategyListView / StrategyDetailView / UserAdminView /
                     NotFoundView
```

> **注意**：这里的纯逻辑目录叫 `domain/`（不叫 `lib/` 或 `utils/`）、运行页面是
> 扁平的 `views/RunListView.vue`（不在 `views/runs/` 下），**是仓库根
> `.gitignore` 逼出来的**：它从 Python 模板来，`lib/` `runs/` `users/`
> `build/` `target/` 等模式**不带 `/` 锚点、匹配任意层级**，用那些名字会让
> 源码写出来了却**静默不入库**（`git status` 里什么都没有）。拿不准时用
> `git check-ignore -v --no-index frontend/src/<路径>` 自查。
> 原计划的"复刻 `amies`"一句已删：**`amies`/`defect_tools` 不在本机**
> （全盘搜过，只有旧文档提到它且不给路径），实际参照的是本机的同族项目
> `D:/Gitee/ShopKit/frontend-platform` 与 `D:/Gitee/RMS/frontend-admin`
> （同作者、同栈；**它们的样式是 SCSS + Element Plus，且没有
> `variables.css`**——与本文档此前的描述不同）。

### 8.1 本地开发（两个终端）

```shell
# 终端 1：后端。空库首启必须给初始管理员口令，否则启动即 RuntimeError
cd backend
python -m app.main                            # 127.0.0.1:8000

# 终端 2：前端
cd frontend
npm install     # 首次
npm run dev     # http://localhost:5173/
```

**本地配置走 `backend/.env`**（该文件名已被仓根 `.gitignore` 覆盖，故口令不入库）：
`python -m app.main` 在启动期读它，`KEY=VALUE` 一行一条，`#` 只能在行首当注释
（取值里的 `#` 不算注释起点，免得口令被半个井号截断）。三条规则：

- **命令行上的真实环境变量优先**，文件只填还没设过的键——临时换一个口令不必改文件。
- 键名一律 `QUANT_` 前缀；别的名字会被记一条 `warning` 并忽略（多半是打错了）。
- 非法行**当场抛错**而不是跳过；文件不存在是正常情况（全部走默认值）。

排查时的两个现象：启动日志里有一行
`已从 …\.env 读入 N 项配置: … (取值不打印)`，看到它才说明文件真的被读了；
没有这一行而口令又没生效，先看是不是键名打错。

`backend/.env.example` 是**入库**的模板（`.env` 被忽略而它不被忽略），列了全部可配键
与默认值，另抄一份即可。**启动路径才读这个文件**：
`PlatformSettings.from_environment()` 仍是纯环境变量解析，测试直接调它、不受本机
`.env` 影响。

- 后端**没有 CORS 中间件**（`add_middleware` 全仓只出现在上传体积闸上），
  故 dev 期必须靠 Vite 的 `server.proxy` 把 `/api` 转发到
  `http://127.0.0.1:8000`，且**不重写路径**——后端路由无尾斜杠，
  `/api/strategies/` 会 307。
- **Vite 只绑 `[::1]:5173`**（IPv6 回环）：浏览器用 `http://localhost:5173/`
  正常，但 `http://127.0.0.1:5173` **连不上**——拿 `curl` 探活的人会以为服务
  没起。代理目标写的是显式的 `127.0.0.1`，故它打后端走 IPv4，两边不冲突。
- 手工验收想用**一次性库**（不碰项目自带的 `backend/data/` 与 `runs/`）：
  把 `QUANT_DATABASE_URL` / `QUANT_RUNS_ROOT` / `QUANT_USER_LIBRARY_ROOT`
  指到临时目录即可。
- 前端脚本：`dev` / `build`（`vue-tsc -b && vite build`）/ `preview` /
  `test`（`vitest run`，只跑 `src/**/*.spec.ts`、`environment: node`）/
  `type-check`。

### 8.2 手工验收闭环（P4 的验收项，走界面）

前置：两个终端都在跑（见 §8.1），且引擎与行情数据在位
（`../QuantTrading/bin/Release`、`D:/MdBaoStock/Bar`）。

1. 以管理员登录 → 「用户管理」建**两个**账号，**显示名都填**（建号页已强制）。
2. 甲登录 → 「策略管理」上传策略（`.py` + manifest 表单）→ 详情页确认版本与
   参数预览、确认可见性。
3. 甲在同页授权给**乙**：授权编辑器里按**显示名**从目录中选到乙（选不到自己，
   这是有意的）。
4. 乙登录 → 「新建回测」应能看到该策略；参数表单**按 manifest 生成**
   （类型/范围/选项/分组），且**没有行情模式选择**（Tick 不出现）。
5. 填运行范围后提交 → 运行列表自动轮询至 `succeeded`。
6. 运行详情：`TradeCount == 84`、`BarMarketDataCount == 2928`（与 P0 基线同
   口径，前提差异见 [`job-workspace.md`](job-workspace.md) §6）。
7. 在详情页下载 `result.json` 与一个 `t_*.csv`，**内容与磁盘上的原件一致**。
8. 另提交一轮 → 在它跑动时点「取消运行」→ 状态变 `interrupted`。
9. 回「新建回测」确认**表单里没有 Tick 选项**。

### 8.3 手工验收：权益曲线与明细表（P5 的验收项，走界面）

前置同上，且**手上要有一个 `succeeded` 的轮**（§8.2 第 5 步那个即可）。

1. 打开该轮的详情页，在「引擎数据镜像」下方应出现**权益曲线**与**回撤**两张图。
2. 概要四项核对：**初始权益 `1,000,000.00`**、**期末权益与该轮「绩效指标」里的
   「余额」逐位一致**（这是 P5 验收的自洽判据，见 §11 的订正）、最大回撤那一行
   带出发生日期、累计收益率与两者的比例对得上。
3. 权益曲线的**首点**应对着 `StartTradingDay`（即 `2024-10-01` 一类），
   末点对着「最后交易日」；拖动缩放条（或滚轮）能放大到单日。
4. 回撤曲线**恒在 0 及以下**，最大值那一点可在图上指认，且与概要里的
   「最大回撤」日期一致。
5. 「明细表」5 个页签逐个点开：**成交（84 行）/ 委托（654 行）/ 持仓（116 行）/
   持仓明细（290 行）** 与资金（62 行）都能翻页；`TradingDay` 列显示成
   `2024-10-01` 形态；换页签时页码回到第 1 页。
6. **未结束的轮**：提交一轮并在它跑动时打开详情页，应看到一句
   「运行结束后可查看权益曲线与明细表」，**两张图不出现**（后端对未结束的轮回
   409，前端干脆不请求）。

---

## 9. API 设计

除登录外，**全部端点要求已认证，且按当前用户过滤**。

| 方法 | 路径 | 说明 |
| ---- | ---- | ---- |
| `GET` | `/api/health` | 引擎自检：引擎根、`.pyd`、Python 版本、runs 根可写（**admin**） |
| `POST` | `/api/auth/login` | 登录换取 JWT |
| `GET` | `/api/auth/me` | 当前用户 |
| `GET` | `/api/users` | 用户列表（admin） |
| `GET` | `/api/users/directory` | 受限用户目录（**已认证即可**）：只回 `id` + `display_name`，见 §7.5 |
| `POST` | `/api/users` | 建用户（admin；`display_name` 必填，空白回 400） |
| `PATCH` | `/api/users/{id}/status` | 启用/停用（admin） |
| `GET` | `/api/strategies` | 可见策略：本人 + 授权共享 + public |
| `POST` | `/api/strategies` | 上传策略（`.py` + manifest） |
| `GET` | `/api/strategies/{id}` | 详情与版本列表 |
| `GET` | `/api/strategies/{id}/last-submitted-parameters` | 本人对该策略**最近一次提交**的参数（提交页预填；**无历史即 200 + 全空**，不是 404） |
| `POST` | `/api/strategies/{id}/versions` | 上传新版本 |
| `PUT` | `/api/strategies/{id}/grants` | 授权共享（owner） |
| `DELETE` | `/api/strategies/{id}` | 删除（owner；历史 run 不受影响） |
| `POST` | `/api/runs` | 提交：校验 → 落 `queued` 行 → 唤醒调度器 → 返回 `run_id`（**目录由调度侧构造**） |
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

> **P2 落地范围**：本节中策略相关的六个端点已全部实现——`POST /api/strategies`、
> `POST /api/strategies/{id}/versions`、`GET /api/strategies`、
> `GET /api/strategies/{id}`、`PUT /api/strategies/{id}/grants`、
> `DELETE /api/strategies/{id}`。三个写操作（传新版本、删除、改授权）只对归属人
> 开放，非归属人一律 404，不区分"无权"与"不存在"——区分了等于确认该 id 存在。
> 改授权的语义见 §7.5。

> **P3 落地范围**：`POST /api/runs`、`POST /api/runs/{id}/cancel`，
> 以及 **`GET /api/runs`（列表）与 `GET /api/runs/{id}`（详情）** 三项读端点
> 均已实现。**提交端点不碰文件系统**：它在 `commit()` 之后 `wake()` 调度器
> 就返回 201，作业目录构造属调度侧——把长 IO 塞进请求路径会让调用方拿到一个
> "目录还没建好"的运行，而目录构造失败要由调度侧把该轮标 `failed`。
>
> **订正（2026-09-25，P4 回写）**：本节原写"`/api/runs` 的其余读端点
> **不属 P3**、前者是 P5 的前置"，**这句是错的**——列表与详情在 P3 就已随
> 结果镜像一起落地并已测试（`test_run_queries.py`），P4 的"看指标"正因此
> 没有新端点。真正留给后面的是：权益曲线（`/equity`）与明细分页表
> （`/tables/{table}`）**属 P5**，`/compare` 属 P6，`DELETE /api/runs/{id}`
> 要连工作目录一起删、**留 P7**。
>
> **P4 落地范围**（前端前置的三处，都不新增依赖）：`GET /api/users/directory`
> （§7.5）、`POST /api/users` 的 `display_name` 必填、
> `StrategyVersionResponse` 增 `manifest_json`（**纯透传、不在读路径解析**：
> 解析要在读接口里多一条失败路径，存量行万一坏掉会让整个策略详情 500，
> 而前端本来就要 `JSON.parse`）、`GET /api/runs/{id}/files` 与
> `/files/{file_path:path}`。后两条的要点：作业目录**由运行行确定性重建**
> （`settings.runs_root / run.run_id`，不持久化新列、不翻调度器内存）、
> 目录不存在回 **404 而非 500**、路径 `resolve()` 后必须仍在作业目录内
> （防目录穿越，字符串判 `..` 挡不住符号链接与 Windows 大小写）、
> **一律 `attachment` 下发**（策略是任意 Python，可以往自己目录里写
> `.html`，同源内联渲染等于在平台域上执行它写的脚本）。

> **P5 落地范围**：`GET /api/runs/{id}/equity` 与 `GET /api/runs/{id}/tables/{table}`
> 已实现（`backend/app/services/result_database.py` + `routers/runs.py`）。
> 六条要点：
>
> 1. **表名白名单只有 5 张**：`Capital` / `Trade` / `Order` / `Position` /
>    `PositionDetail`，其余 12 张（含 2928 行的 `BarMarketData`）在界面上不可见。
>    校验与 `SELECT` 拼接**同处一个模块**，为的是让「先白名单、后拼接」在代码上
>    无法被绕过；表名一律加**双引号**——`Order` 是 SQLite 保留字，不加引号直接
>    `near "Order": syntax error`。列名不参与拼接（`SELECT *` + `PRAGMA table_info`）。
> 2. **只读连接**：`sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)`。
>    `as_uri()` 要求**绝对**路径，故先 `resolve()`,否则
>    `ValueError: relative paths can't be expressed as file URIs`。这一条被钉成
>    断言（对 contextmanager 出来的连接执行 `CREATE TABLE` → `OperationalError`），
>    而不是靠注释。
> 3. **运行中一律 409**（`TERMINAL_RUN_STATUSES` 之外），文案「运行尚未结束,
>    结果库正在被引擎写入」。这不是风格而是硬约束的落点：`SqliteWrapper` 没有
>    `busy_timeout`，运行中读会拿到 `SQLITE_BUSY` 或半份文件。
> 4. **`db_path` 视为不可信输入**：它是引擎侧写进 `result.json` 再由调度侧镜像入库的
>    值，而策略是任意 Python，**它同样可以被伪造**。故与产物路径同等对待——
>    `removeprefix("./")` → `(job_directory / rel).resolve()` → 必须
>    `is_relative_to(job_directory)` 且 `is_file()`，否则 404。失败的轮 `db_path`
>    是空串，同样 404「该运行没有结果库」。
> 5. **`asyncio.to_thread` 首例**：sqlite3 是阻塞 API 且连接不能跨线程，故把
>    「开连接 → 查询 → 关连接」整个放进同一个 worker。与 P4 直接内联文件 IO 的做法
>    **有意分叉**：一条 `COUNT(*)` + 一页 `SELECT` 的耗时随库长大，目标是云上多用户，
>    不该占着事件循环。
> 6. **回撤不在后端算**：端点只回 `Capital` 的原样逐日序列（`trading_day` /
>    `balance` / `available`），回撤与收益率由**前端纯函数**派生
>    （`frontend/src/domain/equity.ts`，进 vitest 单测）。理由：它是派生数据，
>    后端一旦算了，前端要点另一条曲线就再加一个字段，接口会随着图表变化。
>
> 排序恒为 `ORDER BY TradingDay, rowid`：前者在 5 张表里都存在（引擎契约），
> 后者破平局使翻页不重不漏；两者都是代码里的字面量，不是入参。分页用
> `LIMIT ? OFFSET ?` 参数绑定，`total` 由同表 `COUNT(*)` 取。

> **提交页预填端点（2026-09-26 增补）**：`GET /api/strategies/{id}/last-submitted-parameters`
> 已实现（`backend/app/services/run_prefill.py` + `routers/strategies.py`）。
> 六条要点：
>
> 1. **不新建表**：`Runs` 提交时本就写了两份渲染好的配置文本
>    （`ParamsJson` = 策略配置、`BacktestConfigJson` = 引擎 `BackTest.json`，见
>    `run_submission.py`）。「上一次用的参数」就是**该用户在该策略下最新那一轮**的
>    这两份文本，派生即可——这也天然满足「不论成败都算」：排在最前的就是最近一次
>    提交，与它成没成功无关。（`Base.metadata.create_all` 加不了列，故刻意不碰 schema。）
> 2. **归属过滤经 `catalog/visibility.py`**：用 `build_owned_run_query(current_user)`
>    （该模块禁止路由自己拼 `where`），排序照调度器的 `(submitted_at, id)` **双键兜平局**
>    ——Windows 上 `SubmittedAt` 只有毫秒精度，单键不确定。**粒度是 (用户, 策略)**，
>    共享策略下绝不把别人的参数填给你。
> 3. **引擎键名反查**：`MatchMode` → `engine_config.resolve_market_data_type`
>    （由 `MATCH_MODE_VALUES` 反查，未收录回 `None`）、`BarPreces` → `bar_period`
>    （**这是引擎侧既有拼写，不是笔误**）、`StartTradingDay` / `EndTradingDay` /
>    `InitialCapital` 直取。`MatchMode` 与 `InitialCapital` 用 `isinstance` 验类型，
>    坏值各自回空，**不整份作废**。
> 4. **`params` 由运行级键做差集**：用**该运行自己那个版本**的 manifest 的
>    `named_run_field_keys().values()` 剔除运行级键（manifest 已保证参数键不与运行级键撞名，
>    故差集精确）。余下的策略参数**原样（含类型）带回，后端不做范围过滤**——
>    前端为了渲染控件本就要逐项判「这个取值在这个控件上能不能用」，后端再滤一道就是
>    两处真相，还会静默吞键。
> 5. **坏 JSON 优雅降级**：`JSONDecodeError` 记 warning 后回 `None`（等价「没有记忆」），
>    绝不因一轮坏数据让提交页报错。
> 6. **无历史回 200 而不是 404**：`run_id=None` + 空 `params`。404 会与「策略不存在/
>    不可见」混为一谈，而首次使用这个策略走的就是这条路。可见性闸用
>    `load_visible_strategy`（别人共享给你的策略也要能用这个功能）。

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

**实况（P5 收尾时按落地改，标 ✅ 的已可访问）**：

| 路由 | 页面 | 内容 | 状态 |
| ---- | ---- | ---- | ---- |
| `/login` | 登录 | 换取 JWT 存 localStorage；失败文案用后端原文 | ✅ P4（观感层统一 2026-09-26） |
| `/` | —— | 重定向到 `/runs` | ✅ P4 |
| `/runs` | 运行列表 | 状态徽章 / 引擎判定 / 交易日区间 / 耗时 / 交易笔数 / 余额；按状态与策略筛选、按指标排序、分页；**存在非终态轮时 2 s 轮询**。**控件与表格已换成 Element Plus**（见 §12.18 与 §13 的 EP 拍板表） | ✅ P4（EP 化 2026-09-26，观感层统一 2026-09-26） |
| `/runs/new` | 新建回测 | 选策略 → 选版本（缺省最新）→ **按 manifest 动态生成参数表单** → 提交；`match_mode` 固定 `Bar`。**选中策略时按「你上次提交的那一份」预填**（策略参数 + 标的/日期/初始资金/周期），并给一个**「重置为默认值」**按钮（见 §11 的增补段）。**控件已换成 Element Plus，但表单外壳仍是原生 `<form>`**（见 §13） | ✅ P4（预填 2026-09-26 增补，EP 化 2026-09-26，观感层统一 2026-09-26） |
| `/runs/:id` | 运行详情 | 概览 / 绩效指标 / 引擎数据镜像 / **权益曲线与回撤曲线** / **5 张结果表的明细分页表** / 提交参数与引擎配置 / stdout·stderr 尾巴 / 产物清单与下载 / 取消；未结束时 2 s 轮询 | ✅ P5（观感层统一 2026-09-26；**结果库两节只在终态挂载**：未结束时后端回 409，前端干脆不请求，翻成终态由既有的 `watch(isTerminal)` 自动接上） |
| `/strategies` | 策略管理 | 列表 + 上传面板 + 可见性 + 归属（经用户目录映显示名） | ✅ P4（观感层统一 2026-09-26） |
| `/strategies/:id` | 策略详情 | 版本列表（含该版本 manifest 的参数预览）+ 传新版本 + **授权编辑器（目录选人）** + 软删 | ✅ P4（观感层统一 2026-09-26） |
| `/users` | 用户管理（admin） | 列表 / 建号（显示名必填）/ 启用停用 | ✅ P4（观感层统一 2026-09-26） |
| `/:pathMatch(.*)*` | 404 | 未知路径不白屏；**不需要登录**（登录前的错地址也该看得见它） | ✅ P4（观感层统一 2026-09-26） |
| `/compare` | 对比 | 多轮指标表 + 权益曲线叠加 | P6 |
| `/settings` | 设置 | 引擎根、并发上限、超时、数据根，带 health 自检 | P8（该端点目前是 **admin 专属**，普通用户的这一页看不到引擎自检） |

图表库选用 **ECharts + vue-echarts**（量化领域事实标准：K 线、缩放、
大数据量折线开箱即用），**P5 已装**，落地版本是 `echarts ^6.1.0` +
`vue-echarts ^8.3.0`（这是 npm 上唯一自洽的 peer 组合：vue-echarts 8 的 peer
写死 `echarts ^6.0.0`）。**按需引入**控制体积：`echarts/core` 的 `use([...])`
只注册折线 / 直角坐标系 / 浮层 / 缩放 / Canvas 渲染器，不用
`import * as echarts from 'echarts'` 的全量包；打包结果是 ECharts 只进
应用详情那一块 chunk（559 kB / gzip 190 kB），**按路由懒加载**，首屏不付这份钱。
P4 的依赖当时只有 `vue` / `vue-router` / `pinia` 三个（**无 UI 组件库**：
表格、分页、模态框、提示条、表单控件都是自己用 Tailwind 写的，见 §13 的
P4 拍板表）。**这一条只在 P4 成立**：2026-09-26 起引入 **Element Plus**，
`/runs` 与 `/runs/new` 两页的控件与表格先换（见 §12.18 与 §13 的 EP 拍板表），
其余页面分批跟进。**同日第二批（观感层）已把 8 个有界面的页面全部跟完**：
统一外壳与版面原语（`PageHeader` / `SurfaceCard` / `PaginationToolbar`）、
6 处手写提示条换 `el-alert`、2 处手写表换 `el-table`、404 换 `el-result`，
并补上全站此前**一件都没有**的动效与焦点层、以及成功/失败两条 toast 通道
（自绘 `ConfirmDialog.vue` 随之删除）。逐条见 §12.18 的第二段增补与 §13 的
「观感层拍板」表。

---

## 11. 分期与验收

| 阶段 | 内容 | 验收 |
| ---- | ---- | ---- |
| **P0 地基探针** ✅ | 已按第 4 节契约跑通 | **2026-09-25 通过**：退出码 0，7 项指标与基准逐位一致 |
| **P1 后端骨架 + 多用户** ✅ | 五张表 / 登录 / JWT / 查询统一收口加 `user_id` | **2026-09-25 通过**：两账号互不可见；越权返回 404（175 项测试） |
| **P2 策略上传** ✅ | 上传 `.py` + manifest / 校验 / 版本留档 / 落盘 | **2026-09-25 通过**：manifest 缺模式与参数越界均被拒；「上传后能跑通」由 P3 结清（同下行的「P2 上传核心」，本行是计划行、那行是落地行） |
| **P2 上传核心** ✅ | 建策略+首版 / 传新版本 / 列表详情 / 软删 | **2026-09-25 通过**：manifest 缺模式、文件名非法、参数 key 重复均被拒（261 项测试）。**「上传后能跑通」已于 P3 结清**（真引擎一轮 Bar 成功，见下） |
| **P2b 策略授权** ✅ | `PUT /{id}/grants`，`StrategyGrants` 写入路径 | **2026-09-25 通过**：「被授权人可见、非授权人 404」通过（287 项测试）。**「可跑」已于 P3 结清**：提交权限判定与可见性判定**逐字重合**，"授权即可跑"（含被授权人提交的用例） |
| **P3 runner 本体** ✅ | 队列 / subprocess / 结果回收 / 启动恢复 / 作业目录构造 / cancel | **2026-09-25 通过**：`POST /api/runs` 提交后轮询至 `succeeded`；重启后端把在跑的轮标 `interrupted`；并发上限、超时、取消、输出捕获、结果镜像齐备（**380 项测试**，其中 4 项真引擎验收默认不跑，见 `backend/tests/test_real_engine_acceptance.py`）。真引擎实测：`Success=true`、`TradeCount == 84`、`BarMarketDataCount == 2928`，与 P0 基线同口径（前提差异见 [`job-workspace.md`](job-workspace.md) §6） |
| **P4 前端骨架** ✅ | Vue 3 + TS + Vite + Tailwind + Pinia，`frontend/` 从零到闭环 | **2026-09-25 代码与链路通过**：闭环六步全部落地（47 个源文件 / 约 6850 行），后端 **406 项测试**（新增 26 项：目录 / 显示名必填 / manifest 透传 / 产物清单与下载），前端 `type-check` 无错 + `vitest` **58 项全绿** + `build` 成功（按路由分包）；代理链路冒烟 13 项（含 422 的数组信封、目录不泄漏 `username`、目录排除自己）。**「真引擎一轮真回测、全程走界面」的手工验收见 §8.2，须由用户在浏览器里走一遍** |
| **P5 可视化** ✅ | 权益曲线 + 回撤、明细分页表（后端两条读端点 + 前端两个面板 + `domain/equity` 纯函数） | **2026-09-26 代码与链路通过**：后端 **432 项测试**（新增 26 项：正向 / 越权 / 运行中 409 / 目录穿越 / 白名单 / 保留字 / 分页 / 只读），真引擎验收 4 项全过；前端 `type-check` 无错 + `vitest` **75 项全绿**（新增 `equity.spec.ts` 15 项）+ `build` 成功；代理链路冒烟 12 项。**曲线与实测数据点吻合的判据已订正**（原文的 `999549.73` 见下）：权益首点 `== 1000000.0`（`Capital` 种子行）、末点与该轮 `result.json.Balance` **逐位相等**（本机现存那轮是 `999377.0899999999`，62 点），`Trade` / `Order` / `Position` / `PositionDetail` 的 `total` 各为 84 / 654 / 116 / 290。**手工验收（打开一个 succeeded 的轮看曲线与 5 个页签）须由用户在浏览器里走一遍**。⚠️ 本行的四个表行数与末点余额是 **`GridStep` 改比例之前**的值，现值见下方「口径变更」段 |
| P6 对比与模板 | 多轮对比、配置模板保存复用 | 同参不同 `GridStep` 的两轮指标并列且曲线叠加 |
| P7 加固 | 并发上限、超时、磁盘清理、日志轮转 | 并发上限内排队正确；超时轮标 `timeout` |
| P8 上云 | 迁 Windows 云主机 + 传行情数据 + 系统级隔离评估 | 系统级隔离到位后方可对不可信用户开放 |

P0 的验收里 `TradeCount>0` 是关键判据：`QuantTrading` 记载过 Python 宿主因漏写
`on_bar` 而**静默 0 成交**（引擎报的 0 是忠实的），故只看 `Success` 不足以判定打通。

P1 的越权验收用 **404 而非 403**：403 会泄漏"该 id 存在"这一事实，
多租户下应一律表现为"不存在"。

P2b 的「可跑」所需的判定已于 P3 开工前（2026-09-25）**拍板**：**不区分 `read` /
`run`，授权即可跑；`public` 隐含可跑**。理由是提交权限的判定与可见性判定**逐字
重合**（`private` 下授权表本就不生效），于是跑权限检查就是那一次可见性取件，
既不新增 403 分支（D.02 起的"跨租户一律 404"原样保住），也不必为两档粒度另编
一套语义。`GrantPermission` 字段与 P2b 的端点契约**原样保留**，只是它不再被任何
判定读取——`enums.py` 的 docstring 已写明"已拍板不判定"，免得后人当缺陷去修。

P5 的验收判据**订正过一次**：「曲线与实测数据点吻合（`1000000.0 → 999549.73`）」里的
`999549.73` 是 **P0 有种子库那轮的基线，而那批产物已不在盘上**（`runs/` 已被清掉），
钉死它等于钉死一个取不到的数。改为**自洽形式**：首点恒为 `1000000.0`（`Capital`
的种子行 = 初始资金），末点与该轮 `result.json.Balance` 逐位相等——这条不依赖
种子库在不在，比一个常数更强，本轮实测即 `999377.0899999999`（无种子库变体）。

**2026-09-26 又追了一次口径变更**：策略的 `GridStep` 由**绝对价格**改为**比例**
（`0.01 = 1%`）。起因是用户实测：`GridStep=10` 在 `SZSE/000001`（锚价 11.94）上每一档都
远在市价之外，一轮 **610 笔委托、0 笔成交**；同一份参数在 `SSE/600519`（约 1558）上只有
0.64% 的间距，照常成交。改后同一批输入的成交密度随之变化，故 §11 各行的
`TradeCount` / `OrderCount` / `Balance` 与 §6 的两批基线**全部重取**（`34` / `629` /
`999257.8562340003`，无种子库变体；`BarMarketDataCount` 不变仍是 2928）——数字出处见
[`job-workspace.md`](job-workspace.md) §6.3，旧值只在 §6.1 / §6.2 的历史记录里保留。
本行的 `84 / 654 / 116 / 290` 是重取前的值，与 §6.3 的 `34 / 629 / 116 / 282` 不可混用。

**2026-09-26 增补：提交页按策略预填「上一次提交的参数」**（用户提出，不属任何分期）。
动机是纯重复劳动：每次回到提交页都要手敲标的、日期区间、初始资金、K 线周期与一整组策略参数。
**范围经用户拍板**：记住的 = **策略参数 + 运行级字段**；「上一次」= **最近一次提交**
（不论成败、是否还在跑）；另给一个**「重置为默认值」**按钮；粒度 **(用户, 策略)**，
绝不跨用户。端点契约见 §9 的增补段，**无新表、无 DB 变更、无新依赖**。

前端落点在 `RunSubmitView.vue` 与两个纯函数模块（`domain/manifest.createInitialParameterInputs`
扩一个 `rememberedValues` 形参、`domain/run-form.buildPrefilledRunFields`），判据一律**复用既有校验器**
（`coerceParameterInput` / `readTradingDay` / `readInitialCapital` / `readRunFieldValue`），
故「界面放行什么」这件事仍然只有一处真相。三处值得后人当心：

- **原来的 `watch(descriptors, …)` 已被删除**，换成版本下拉的 `@change` 处理器。预填要发一次请求、
  必然晚于 `descriptors` 变化，**保留 watcher 就一定会把填好的记忆清掉**。
- **运行级字段的回落值是「当前输入」而不是空串**：换版本时参数一定重建（未声明的键会被后端 400），
  而运行级字段只补不改——不静默抹掉用户已经敲进去的东西。`prefill` 为 `null` 时结果恒等于当前输入。
- 新增**竞态保护**（自增 `selectionToken`）：快速连着换两次策略时，先发的回包可能后到；
  这条今天对**策略详情**也是既有的隐患，顺手一起收口。

**验收（2026-09-26 通过）**：后端 **456 项全过**（新增 `test_run_prefill.py` 16 项：无历史回 200、
`BarPreces → bar_period`、`MatchMode 3 → Bar`、运行级键不出现在 `params`、多轮取最新、
`submitted_at` 平局按 `id`、别人的更新轮不采纳、两个用户各读各的、坏 JSON 降级、单个坏值只丢自己那一项、
不可见 404、匿名 401）；**三次变异检查**（去归属过滤 → 2 红、去 `id` 兜平局 → 1 红、
去运行级键差集 → 2 红）各自转红且只红对应用例，均已还原为逐字节相同；
`test_real_engine_acceptance.py` 的真回测里补了预填断言，**真引擎验收 4 项全过**；
前端 `type-check` 无错 + `vitest` **89 项全绿** + `build` 成功。本轮验收产物在
`backend/_acc_tmp/prefill-20260926/`。

**不做 / 已知缺口**：① **记忆不可删除**——它是从运行历史派生的，不是一份副本，按钮只表示"本轮不套用"；
要能真删就得新建一张表。② 不做「常用参数模板」（多套参数命名保存复用），那是 P6 的语义，
本轮只是「上一次」。③ **不预填行情模式**：表单本来就没有这个控件（Tick 不可提交），
端点上仍回 `match_mode` 供日后放开 Tick 时用。④ **manifest 没声明的约束照样能带出**：
如跨参数的 `0 < GridStep × GridCount < 1`——manifest 表达不了跨参数约束，
引擎构造期会拒并在 `stderr.txt` 写明。⑤ **不记「上次选的是哪一版」**：版本仍默认最新。

---

## 12. 风险与不做项

1. **绑死 Windows + Python 3.14**：`.pyd` 是 `cp314-win_amd64`，
   Linux 无解，云主机只能是 Windows；后端必须与引擎同机，**横向扩展无余地**。
2. **目录级隔离挡不住恶意读盘**（见 5.2）。开放给不可信用户前必须上系统级隔离。
3. **磁盘膨胀**：实测单轮 **3.0 MB**（结果库 + 17 个 CSV + 日志），
   多用户下增长更快，需按用户配额与清理策略（P7）。
4. **cancel 的进程树**：宿主是单进程（引擎以库形式进程内加载），
   `terminate()` 够用，不必上 `psutil`。
5. **输出编码**：引擎有 GBK 变体、日志含中文，runner 读 stdout/stderr 须
   `errors="replace"` 容错，否则收尾部时会崩。
6. **`argv[0]` 传裸文件名是固定选择，不是硬约束**（**2026-09-25 实测订正**）：
   带路径的形态（正斜杠相对、反斜杠绝对）同样能跑完整轮、退出码 0，
   原文"否则启动期即终止进程"**已不成立**。详见 §3 与
   [`job-workspace.md`](job-workspace.md) §3.1。
7. **`BarPreces` 是引擎侧既有拼写**（非笔误，不可擅改），平台配置键须逐字一致。
8. **不做**：跨运行库级对比（`ATTACH` 上限 10，已决定暂不做）、盘后行情修正、
   C++ 策略宿主（三条再评估触发条件已留档）、zip 上传（见 7.1）。

**P3 起新记的已知缺口**（明确不做，写在这里免得日后被当缺陷）：

9. **队列是纯 FIFO，没有配额**：一个用户连发 100 个作业会饿死其他人
   （`max_concurrent_runs=1` 时更明显）。本期不做配额。
10. **不做"重启后续跑"**：重启时 `queued` / `running` 一律标 `interrupted`。
    若日后要做，唤醒机制必须从"事件 + 看门狗"改成短轮询（看门狗会漏掉
    重启前的行，而它们那时不在队列里）。
11. **不做多 worker**：单实例是调度器的**硬前提**。`uvicorn --workers 2` 下，
    B 进程的启动恢复会把 A 正在跑的作业标 `interrupted`。已写进
    `recovery.py` 的 `logger.warning`。
12. **孤儿进程不自动清理**：不按 `RunnerPid` 杀（PID 回收后会杀错无关进程，
    且没有可验证的身份凭证）；孤儿的写入被"相对写路径"关在自己的作业目录里，
    伤害是资源泄漏而非正确性破坏。恢复时把 `(RunId, RunnerPid)` 写进日志供人工核对。
13. **Tick 模式不可提交**：manifest 仍可声明支持 `Tick`（那是策略作者的事），
    但提交侧一律 400。引擎的 tick 撮合有**三档**（`OrderBook:0` / `LastPrice:1` /
    `OppositePrice:2`），平台的 `MarketDataType` 只有两值，推不出那三档；
    且 `D:/MdBaoStock` 下根本没有 tick 数据，从未验证过。判据是具名常量
    `SUBMITTABLE_MATCH_MODES = frozenset({MarketDataType.BAR})`，开 Tick 时改它。
    **P4 的「新建回测」表单因此不显示 Tick 选项**（2026-09-25 拍板）——
    表单按 Bar 单模式生成，不做模式联动，也就不必先编出三档的界面语义。
14. **种子库 `BackTestInit.db` 不重建**：本机 `bin/Release` 下没有它，
    费用三项恒为 0、`CommissionMissingCount` 恒 84、`BasicDataLoaded` 恒 `false`。
    这是**输入缺失**而非缺陷，验收里已把这四条**钉成断言**（重建时会一起转红）。

**P4 起新记的已知缺口**：

15. **`display_name` 没有唯一约束**：重名时授权表单分不清两个人，
    **选错人就是授权给错人**。修法是加唯一约束，但那会新增一条 409 路径
    与"建号撞名怎么办"的界面语义（改名？加后缀？），超出 P4 的范围。
16. **授权被撤销后，运行列表与详情里的策略名回落成显示 id**：名字来自
    可见策略列表（`GET /api/strategies`），授权一撤该策略就不再可见，
    而运行行里只有 `strategy_id`。不另开"按 id 取名字"的端点——那等于
    给所有策略开一个存在性探针，与"跨租户一律 404"相冲。
17. **生产部署仍靠 Vite 代理**（两个终端），FastAPI 托管 `dist/` 或反向代理
    留 P8。**同一条里还有：前端没有 e2e**，闭环靠 §8.2 的手动验收——
    这本就是计划原本的验收方式，不是临时降级。
18. **不引 UI 组件库的代价**（**2026-09-26 起部分作废，见下方增补**）：
    表格、分页、模态框、提示条、表单控件都是自写的
    （14 个 `components/`）。这是"最小集"的直接后果；若日后要换
    Element Plus，那批里只有布局件需要留。另：Tailwind v4 是 **CSS 优先**，
    主题令牌写在 `src/style.css` 的 `@theme` 块里，**没有 `tailwind.config.js`**
    （照 v3 的教程去找那个文件会找不到）。

    **增补（2026-09-26，已完成第一批）**：本条当时的预判落地了——引入
    **Element Plus `^2.14.6`**，先做**地基 + 两个最脏页**：4 个纯展示共用件
    （`StatusBadge` / `EmptyNotice` / `ErrorBanner` / `PaginationBar`）换内部实现
    （对外 props/emits **一字未改**）、`/runs` 的筛选下拉 / 倒序 / 表格、
    `/runs/new` 与 `ParameterField` 的控件。**布局件如预判一样留下**
    （`AppLayout` / `FilePicker` / `DirectoryPicker` / `ResultTablePanel` /
    `EquityChartPanel`），`LoadingNotice` 也留（`role="status"`，EP 没有等价物）。
    逐条的取舍见 §13 的 EP 拍板表，四条跨层契约（参数下标字符串 / 分页 offset /
    `{label,tone}` / 校验语义归 `domain/`）在本批里全部原样存活。

    新增的代价（实测数字）：主 CSS **17,372 B → 382,097 B（gzip 52.5 kB）**——
    这是"显式 import + 全量 CSS"这个选择的已知代价，不是意外；ECharts 那块
    chunk 不受影响（559 kB → 559 kB）。想要小体积的退路是按需引
    `theme-chalk/el-<组件>.css`（本批用到的集合约 80 kB），**只需改 `main.ts` 一行**。
    另外三笔**仍是缺的**：`ElMessageBox`（模态框仍是 `ConfirmDialog`，形态是声明式
    与命令式之别）、`ElMessage` toast（全站今天没有 toast 通道，那是产品决策不是迁移）、
    页码按钮（原组件的既定决定：运行数上千之后再说）。两套样式语会**共存一段时间**，
    直到其余 5 个页面跟完。

    **第二段增补（2026-09-26，观感层那一批）**：第一批换完之后，用户看过界面
    （commit `83374ec`）的原话是「已经加了UI组件库了吗？看起来跟没加也没什么两样啊」。
    **这个判断是对的**，而原因不在"库没生效"——实测 dev server 的转译产物里 EP 的
    样式表 361,959 B 在的——在第一批只做了"换组件"这一个动作：`--el-*` 全被指回本仓
    那 9 个颜色令牌，EP 控件的出厂观感因此被抹平成我们自绘的样子；页头 / 卡片 / 区块
    标题 / 表壳仍是 Tailwind 手写类（页头类名串 6 处、卡片 14 处 9 种变体、表壳 5 种，
    换掉控件外壳也看不出来）；且全站 `transition-*` / `focus-visible:` / `ring-*` /
    `@keyframes` 实测都是 **0 次**，也没有 toast 通道。第二批补的正是这三层，
    并把 8 个有界面的页面一次性跟完（不再"共存两套样式语"）。体积：主 CSS
    382,097 → **383,310 B**，JS 合计 1,023,443 → **1,045,022 B**（+21,579 B；
    `el-message` / `el-message-box` / `el-skeleton` / `el-result` / `el-table`
    都落在新增的共享 chunk 上），ECharts 那块 **559.35 kB → 558.63 kB（不变）**。
    第一批欠的三笔至此结清两笔（`ElMessageBox`、`ElMessage`），**页码按钮仍未做**。
    逐条取舍见 §13 的「观感层拍板」表。

**P5 起新记的已知缺口**：

19. **另 12 张表在界面上看不到**（含 2928 行的 `BarMarketData` 等行情与统计表）：
    本轮只开 5 张交易与资金表。要看别表，要么同时改白名单与页签两处，
    要么日后补一个 `GET /runs/{id}/tables` 列表端点。
20. **表名清单是两处真相**：后端 `services/result_database.py:RESULT_TABLE_NAMES`
    是正本，前端 `api/types.ts:RESULT_TABLE_NAMES` 是**镜像**（照 `RUN_SORT_COLUMNS`
    的先例）。这是"不加列表端点"的直接代价——漏改一处不会报错，只会让某个页签
    点开是 404。
21. **盘中回撤不可得**：`Capital` 只有**逐日结算权益**（`Margin` / `MarketValue`
    在本样本里恒为 0），库里根本不存在日内曲线。图上画的是逐日权益与**逐日**回撤，
    不是"日内最高点之后回落多少"。
22. **失败的轮看不到部分结果**：`db_path` 只在引擎写 `result.json` **成功之后**
    才被镜像入库，取消 / 超时 / 启动失败的轮一律空串 → 404。要能看到"跑到一半
    写下的那些行"，得让调度侧在启动前就把 `DbPath` 镜像下来（留后续）。
    另：**明细表没有导出**（下载已有的产物即可）；K 线图、多轮曲线叠加（P6）、
    图表导出图片、大表虚拟滚动（一页最多 100 行）**明确不做**。

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
| 授权与可见性的衔接 | **授权驱动可见性** | 2026-09-25 P2b 开工前拍板；见 §7.5 |
| 上云节奏 | 本机跑通再迁 | |
| 上传形态 | `.py` 必需 + manifest 表单或文件 | 不收 zip |

**P3 开工前新增的拍板**（2026-09-25）：

| 决策点 | 选择 | 备注 |
| ---- | ---- | ---- |
| 授权粒度 | **不区分 `read` / `run`，授权即可跑** | 跑权限 = 可见性，不新增越权分支；`GrantPermission` 保留但不再被读；见 §11 |
| `public` 语义 | **隐含可跑** | 一次有意的权限放宽 |
| `params` schema | **本轮定案**（四类型 + `options` + 缺省即必填） | 见 §7.2 |
| manifest 运行级字段映射 | **新增 `run_field_keys`** | 结清"`bar_period` 两处不一致 → 静默 0 成交"；见 §7.2 |
| Tick 模式 | **本轮不开**（提交侧 400） | 三档撮合语义未定；见 §12.13 |
| 种子库 | **不重建**，验收按"缺失"断言 | 见 §12.14 |
| P3 范围 | 提交 + 队列 + 回收 + 恢复 **+ cancel** | 只读端点留 P5、`DELETE` 留 P7 |

**P4 开工前新增的拍板**（2026-09-25）：

| 决策点 | 选择 | 备注 |
| ---- | ---- | ---- |
| 前端样式 | **Tailwind**（按 §11 原文） | 参考项目 `defect_tools` 用纯 CSS，此处**有意分叉**；见归档 `Q.04` |
| 授权选人 | **受限用户目录**，只回 `id` + `display_name` | 端点已随 P4 落地，边界与泄漏面已在 §7.5 逐条收口 |
| Tick 显示 | **表单不显示**，提交侧继续 400 | 三档撮合语义仍未定，不阻塞 P4；见 §12.13 |

**Element Plus 开工前新增的拍板**（2026-09-26）：

| 决策点 | 选择 | 备注 |
| ---- | ---- | ---- |
| 组件库与版本 | **Element Plus `^2.14.6`** | 进 `dependencies`；peer 写 `vue ^3.3.7`，本仓 3.5.39 ✓ |
| 本批范围 | **地基 4 件 + 两个最脏页**（`/runs`、`/runs/new`） | 其余页面后续批次；"分批替换"是 §12.18 的预判 |
| 引入方式 | **显式 `import { ElButton } from 'element-plus'` + 全量 CSS**（`element-plus/dist/index.css`） | **不引** `unplugin-auto-import` / `unplugin-vue-components`——隐式全局与"名称即意图"相冲（D.07 曾对 `unplugin-auto-import` 有过同样的拒绝）。代价见 §12.18 的体积账 |
| 主题换肤 | **走 `--el-*` CSS 变量，不引 sass** | 五族（primary/success/warning/danger/info）**全对齐**：`@theme` 补 `danger`/`success`/`warning`/`info` 四个语义令牌，EP 的 7 档色阶用 `color-mix()` 生成 |
| 换肤块的选择器 | **`html:root`，不是 `:root`** | 我们的覆盖与 EP 自己的 `:root` **同为无层样式**，同权重下靠源序决胜，而源序取决于 Vite 打产物的顺序（未实测，不押）。`html:root` 是 (0,1,1)，压过 (0,1,0)，**与顺序无关** |
| 校验语义归谁 | **仍全部归 `domain/`**（**绝不用 `el-form` 的 rules**） | `el-form-item` 的错误是 `position:absolute`，会压在设计好的流内提示上；且 `required`/`rules`/`prop` 任一都会激活 EP 自己的校验，等于与 `visibleFieldErrors` 并列的第二处真相 |
| 测试环境 | **`jsdom` + `@vue/test-utils`**（本批安装） | 全局 `environment` 仍为 `node`，组件 spec 逐个在文件顶部写 `// @vitest-environment jsdom`——这正是 P4 拍板行"**要测组件时再加**"预设的那个条件 |
| 中文 locale | `ElConfigProvider :locale="zhCn"`，取 `element-plus/es/locale/lang/zh-cn` | 不做 `app.use(ElementPlus)` 全量注册（会废掉摇树），locale 只能走 provider；`dist/locale/zh-cn.mjs` **没有类型声明**，在 `vue-tsc -b` 下会报缺声明 |
| 本批不做 | `ConfirmDialog → ElMessageBox`、`ElMessage` toast、页码按钮、其余 5 个页面、`text-rose-600 → text-danger` 全仓重命名 | 逐条理由见 `PROGRESS.md` 的 `D.11` |

**观感层开工前新增的拍板**（2026-09-26，EP 之后的第二批）：

| 决策点 | 选择 | 备注 |
| ---- | ---- | ---- |
| 本批主题 | **全局观感层**：统一外壳 / 版面原语 / 动效与焦点 / 两条反馈通道 | 用户明确选**保持顶部导航**，不做侧边栏 |
| 反馈三类分流 | 表单自己的提交：成功 toast、失败**就地**留表单；表单之外的页面级动作：成败都 toast；加载类：`ErrorBanner` + 重试 | 一个 ref 不许同时承载"页面级加载失败"与"用户动作失败"，4 个视图逐处拆开 |
| 两条 toast | 成功约 2 s 自动消失；**失败不自动消失**且可关闭 | 用户拍板"成功与失败都弹" |
| 确认弹窗 | **`ElMessageBox` 取代自绘 `ConfirmDialog`**（`ConfirmDialog.vue` 随之删除） | 三点实现要点：显式中文按钮文案（函数式 API 拿不到 locale）、危险动作 `type:'warning'` + 确认键 danger 类 + **`autofocus: false`**（否则回车即执行删除）、保留"点遮罩即取消" |
| 弹窗忙碌态的去处 | 确认后弹窗立刻关闭，写入中的忙碌态挪到**触发它的那个按钮**的 `:loading` | 全批唯一的行为差（"处理中…"那句文案随之消失） |
| 每页控件 | 三个列表页统一到 `PaginationToolbar`（每页 `ElSelect` + 页码条） | 它自己承担"改每页 → offset 归零"，且**先 emit `update:limit` 再 emit `update:offset`(0)**——顺序是它唯一的隐式契约，有 spec 钉住 |
| 首屏与刷新 | 首屏 5 处用 `ContentSkeleton`（自带 `role="status"`）；轮询 / 级联 / 翻页不换骨架 | 骨架屏是**带动画**的，而本批判据是"列表数据刷新不加任何动画"，否则每次翻页闪一下灰条 |
| 焦点与动效 | 只由 `style.css` 出：`:where(a, button, summary, input, select, textarea)` 150 ms 过渡 + `:focus-visible` 品牌色描边 | `:where()` 把特异性压成 0，只靠源序压 EP；全局焦点规则**显式排除** `.el-input__inner` / `.el-textarea__inner`，否则输入框会出现双框 |
| 本批不做 | 深色模式、响应式/移动端、`text-rose-600 → text-danger` 全仓重命名、`ResultTablePanel` 换 `el-table`（列由服务端数据动态生成）、页码按钮与跳页、侧边栏/页脚/主题切换器 | 逐条理由见 `PROGRESS.md` 的 `D.12` |

**P4 交付时的实现选择**（2026-09-25）：

| 决策点 | 选择 | 备注 |
| ---- | ---- | ---- |
| 前端依赖 | **最小集**：原生 `fetch`，不引 axios / UI 库 / sass | 表格、分页、模态框、提示条自写；见 §12.18 |
| 前端测试 | **vitest 只测纯逻辑**（`environment: node`） | 不装 `@vue/test-utils` 与 jsdom——没有组件测试时它们就是无调用点的依赖；要测组件时连 jsdom 一起加 |
| 页面范围 | **闭环 + 最小 admin 用户页** | 不含 `/compare`、`/settings`、权益曲线与明细分页表 |
| 版本号 | **照抄同族项目的已验证组合**，不追最新 | router 5 / pinia 4 / vitest 5 / TS 7 都是主版本跳跃，日后单独评估 |
| 前端参照物 | 本机同族项目 `ShopKit/frontend-platform` 与 `RMS/frontend-admin` | `defect_tools`（`amies`）**不在本机**；且参照物的样式是 **SCSS + Element Plus**，不是纯 CSS + `variables.css` |
| 前端目录命名 | `domain/`（纯逻辑）、扁平的 `views/RunListView.vue` | 避开仓根 `.gitignore` 的无锚点模式（`lib/` `runs/` `users/` …），见 §8 |

**P5 开工前新增的拍板**（2026-09-26）：

| 决策点 | 选择 | 备注 |
| ---- | ---- | ---- |
| 图表库版本 | **`echarts ^6.1.0` + `vue-echarts ^8.3.0`** | 当前最新且**唯一自洽**的一对：vue-echarts 8 的 peer 写死 `echarts ^6.0.0`；已按需注册控制体积 |
| 明细表范围 | **精选 5 张**：`Capital` / `Trade` / `Order` / `Position` / `PositionDetail` | **不加列表端点**，表名清单在前端镜像一份；见 §12.19 / §12.20 |
| 回撤在哪算 | **前端纯函数派生**（`domain/equity.ts`），后端只回原样序列 | 端点不该随图表变化；前端这份是纯逻辑，正好进 vitest |
| 阻塞调用 | **`asyncio.to_thread`**（本仓首例） | sqlite3 阻塞且连接不能跨线程，"开→查→关"整个进同一 worker；与 P4 内联文件 IO 有意分叉 |
| 未结束的轮 | **409 + 前端不请求** | `SqliteWrapper` 无 `busy_timeout`，运行中读会拿到 `SQLITE_BUSY` 或半份文件 |
| 结果库两节的挂载 | **只在 `isTerminal` 时挂载** | 复用既有 `watch(isTerminal)`，翻成终态即取数，不必再等一次 2 s 轮询 |
