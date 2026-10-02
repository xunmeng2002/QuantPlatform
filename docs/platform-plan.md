# 量化回测平台实施计划

本文是 `QuantPlatform` 的总体实施计划：以 Vue 3 前端 + FastAPI 后端，包裹
`../QuantTrading` 的 C++ 回测引擎，做成一个**云上多用户**的回测平台。

计划的**前提不是本文新拟的**，而是承接 `QuantTrading` 仓内已定案的平台化结论
（`PROGRESS.md` 与归档 `D.48`，2026-09-18 用户拍板）。本文只解决那些定案中
「剩余全在调度侧」的部分，叠加上 2026-09-25 由用户拍板的多用户与上传决策。

---

## 1. 已定案的前提

**产品名：薪火量化**（2026-09-26 用户拍板）。界面文案、浏览器标签页标题与主页主视觉用这个名字；
仓库名 `QuantPlatform`、包名、目录名与**本文档标题**不动（H1 仍是
`# 量化回测平台实施计划`，那是有意保留的，不是漏改）。

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
- 行情数据根 `<仓根>/market-data/Bar/` 存在，按 `Identity=<板块>/Year=<年>/`
  存放 `<年>_<周期>.parquet`。**只有 `Bar/`，没有 tick 数据目录。**
  （2026-10-01 由 `D:/MdBaoStock` 迁入仓内，逐文件 SHA-256 校验过；原目录保留未删。）
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

**引擎不进本仓，按路径引用**（2026-09-27 定）：`QUANT_ENGINE_ROOT` 指向
`../QuantTrading/bin/Release`，平台**不复制** `.pyd` 与三个运行时 DLL——它们靠
`PYTHONPATH` + `LOAD_WITH_ALTERED_SEARCH_PATH` 就地加载（见第 3 节），job 目录里只有
平台自己产的四个文件，引擎侧唯一被复制的是 `Sessions.json`。故换版 = 换那个目录，
不改本仓任何代码。

**换版请按版本留目录，不要原地覆盖**：

```text
D:/Gitee/QuantTrading/bin/Release-2026.09
D:/Gitee/QuantTrading/bin/Release-2026.11
```

再把 `QUANT_ENGINE_ROOT` 指过去、重启后端。理由是可追溯：每一轮在提交那一刻就把引擎标识
冻进 `Runs.EngineVersion`（详情页「引擎版本」一行、`/api/health` 一处），原地覆盖会让历史轮与
新轮在库里长得**一模一样**——而 `StrategyVersionId` 那条"供逐字复现"的承诺要三样（策略版本、
参数配置、引擎与行情），少了引擎这一半就复现不出来。这与 D.06 吃过的教训同类：口径变了而库里
没有线索，旧数字就再也不能与新数字相减。

标识取两层，**文件优先**：

| 优先级 | 来源 | 取值形态 |
| ---- | ---- | ---- |
| 1 | `<引擎根>/engine-version.txt` 首个非空行 | 人写的版本号，如 `2026.09.26` |
| 2 | `.pyd` + 三个运行时 DLL 的内容摘要 | 如 `sha256:a1b2c3d4e5f6` |
| 3 | 两者都取不到 | 空串（"不知道"，不另造哨兵值） |

> **注意**：`sha256:` 前缀是**有意**的——两种形态**不可比较**。兜底摘要只覆盖那四个文件，
> 引擎包里别的文件变了它**不会**变；它是"读不出人写版本号"时的降级，不是等价替代。故
> `engine-version.txt` 由 QuantTrading 侧产出（本仓不生成），上云前随引擎包一并给。

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

### 5.4 登录失败节流与反向代理

`POST /api/auth/login` 是唯一不需要令牌的端点，也是唯一能被无限次尝试的入口；而口令校验是
同步的 PBKDF2，无限次尝试同时是一次廉价的资源消耗。节流按**用户名**与**来源地址**两个维度
各自计数：任一维度连续失败 5 次（`QUANT_LOGIN_THROTTLE_MAXIMUM_FAILURES`）即锁 15 分钟
（`QUANT_LOGIN_THROTTLE_LOCK_MINUTES`），期间一律 `429` 并带 `Retry-After`。

| 取舍 | 取值 | 理由 |
| ---- | ---- | ---- |
| 计数存放 | 进程内存 | 单实例是调度器的硬前提（§12.11），故无"多 worker 各记一份、阈值被放大 N 倍"的问题；代价是**重启即清空** |
| 锁定期间拿对口令 | 仍然拒绝 | 若放行，攻击者在 5 次之后就能继续无限猜——猜对了就进去了，防爆破当场作废 |
| 未知用户名是否计数 | 计数 | 只对存在的账号计数的话，`429` 本身就变成用户名枚举接口，与登录那处假校验（§7.5）自相矛盾 |
| 停用账号的 `401` | 不计入 | 它只在口令**已正确**时才走得到，记成凭据失败语义上就是错的 |

**取"来源地址"必须配可信代理，否则这一维会失效。** 后端直接暴露时 `request.client.host` 就是
真实客户端；前面一旦有反向代理，它变成代理自己的地址，全部用户会挤进同一个桶——任意 5 次失败
就能把所有人一起锁住。故 `QUANT_TRUSTED_PROXY_ADDRESSES` 要按拓扑填：

| 部署拓扑 | 取值 |
| ---- | ---- |
| 后端直连公网 | 留空（默认；谁都不信） |
| 同机 nginx | `127.0.0.1` |
| 两层（CDN + nginx） | 两层地址都列上 |

规则是：**对端地址不在名单里就完全不看 `X-Forwarded-For`**；在名单里则从最右往左跳过可信项，
第一个不可信的就是客户端。之所以不按"代理跳数"配，是因为那个数填小了会造成**静默**的伪造缺口
（客户端每次换一个自造的 header 就换一个桶，永远发现不了），而白名单的默认值在任何拓扑下都安全。
地址先按原始条目取、再解析，**取到解析不出来的项就地回退对端地址，绝不继续往左**——左边是
请求方可写的区域，这处写错就等于把伪造值当成客户端。

> **风险**：知道用户名的人每 15 分钟发 5 次错口令，即可让该账号持续登不进来。影响面限于"被针对
> 账号的登录"（拿不到数据、冒充不了、改不了状态，已签发的令牌不受影响），且锁定事件会记
> warning 日志以便发现；来源地址维度让单 IP 攻击同时锁掉自己的地址，但多出口可绕过这层。

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
  EntryFilename,        -- 上传的 .py 裸文件名，如 grid_strategy.py
  ConfigFilename,       -- 上传的 .json 裸文件名，如 TestStrategyGrid.json
  ConfigurationJson,    -- 策略配置模板原文；NULL = 改形态之前的旧版本
  ManifestJson,         -- **历史列**，改形态后恒为空串，见 §7.2
  SourceHash,           -- sha256，兼作内容寻址
  StoragePath, UploadedAt, UploadedByUserId FK->Users,
  UNIQUE(StrategyId, VersionNo)
  -- 判重的键是 (SourceHash, ConfigurationJson) 两列，不加约束，见 §6 修订 ②
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
   `(SourceHash, ConfigurationJson)` 这一对：同一份源码配不同配置模板（改了参数、
   或换了入口文件名）是一份**新**版本。只钉 `SourceHash` 会把这种上传挡成
   完整性冲突，逼用户"改配置必须连源码一起改"——荒谬。判重挪到服务层做，
   那里判错也只是多一个目录，不伤完整性；`(StrategyId, VersionNo)` 仍是硬约束。
   （该列原叫 `ManifestJson`，2026-10-01 改形态时另起 `ConfigurationJson`，
   见 §7.2。）

③ **`UNIQUE(OwnerUserId, Name)` 改成部分唯一索引**（`WHERE DeletedAt IS NULL`）。
   整表唯一会连已删策略的名字一起占住，而列表页与详情页都不再显示这条记录——
   用户看到的是"名字没人用，却说我重名"，且平台不提供硬删，没有任何接口能释放它。
   部分索引的语义正是"名字在**在用的**策略之间唯一"。**注意**：`sqlite_where`
   是方言选项，日后换库必须把同一条件带过去，否则索引会静默退化成整表唯一。

**artifact 不建表**：它是 `runs/<RunId>/` 下的文件，路径可由约定推出，建表即重复。

---

## 7. 策略上传与管理

### 7.1 上传形态

- **两份文件，都是必需**：策略源码 `.py`，以及**它在启动时真正去读的那份配置** `.json`。
  multipart 两个文件部件（`source` / `configuration`）——配置模板必须带自己的文件名，
  而纯文本字段没有名字，作业目录里要按这个名字生成它。
- **不收 zip**：解压要逐条校验路径防 zip-slip，为一个单文件场景引入一整类
  漏洞面不划算。多文件策略（辅助模块）需要时再评估。
- 两个文件名都必须是**裸文件名**且各带后缀（`.py` / `.json`），校验在
  `app/strategy_configuration.py`——纯函数，不碰库也不碰盘。见 §7.2。
- 上传即建一个 `strategy_versions` 版本。`(source_hash, configuration_json)` 与该策略下
  **任一既有**版本相同则复用该版本，不重复占盘（**内容未变不产生新版本号**）。
  判重比的是"任一既有版本"而非"最新版本"：改了参数又改回去，就该命中那个老版本——
  版本号要能表达"内容变过几次"，不是"上传过几次"。

### 7.2 策略配置模板

> **2026-10-01 改形态**：本节取代原「manifest 内容」。策略作者那一份说明见
> [`strategy-configuration.md`](strategy-configuration.md)，配一份可直接用的
> [`strategy-configuration.example.json`](strategy-configuration.example.json)。

**那份配置 JSON 的键就是参数**：每个键在提交页渲染一个控件，**键集由文件固定**，不可增删。
平台不认识任何参数声明——没有标题、没有范围、没有可选项、没有分组，**参数合法性由策略自己守**。

| 事项 | 口径 |
| ---- | ---- |
| 控件形态 | 按该键**当前取值的 JSON 类型**：布尔 / 数值 / 字符串 |
| 初值 | 文件里该键的取值 |
| 渲染 | 模板 + 用户编辑 + **覆写**三个运行级键 |
| 键集 | 不可增删；提交只改值 |

**三个运行级键**由平台覆写，键名固定，与引擎自带的 `Configs/TestStrategyGrid.json`
同拼写：`ExchangeId`（`SSE` / `SZSE`）、`InstrumentId`、`BarPreces`（**引擎侧既有拼写，
非笔误**，见 §12）。模板里没有就新增，有就覆写；它们**不进参数区**——参数区再给一次，
只会让用户以为改得动。

**形状约束只有四条**（键数 ≤ 200、键名 ≤ 64 字符且非空、UTF-8 文本 ≤ 64 KiB、
不得含 `NaN` / `Infinity` / `-Infinity`），都与"表单能不能渲染"直接相关：一个键一个控件，
而键名是那格控件的唯一标识。顶层必须是 JSON 对象——数组或标量渲染不出任何控件，却照样会被
原样写进策略配置（`app/strategy_configuration.py:parse_configuration_template`）。
非有限数那一类不能省：`json.loads` 默认收下这几个**非 JSON 字面量**，落进配置后引擎读到的
是一个不可比的数——那一轮结果再怎么看都正常，只是永远算不对。

**取值是数组 / 对象 / `null` 的键**渲染不出控件，平台**原样透传**：提交页把它们列出来但改不了。
硬造一个控件只会引入"存得进去、读不出来"的一类失败。

**参数值类型写错是新的一类静默风险**：把 `GridStep` 写成字符串 `"0.01"`，表单上就是个文本框，
策略拿到的是字符串。类型以文件里的取值为准。旧 manifest 曾挡掉一批"键名写错就静默落空"的
错误（顶层的 `extra="forbid"`），新形态下模板即真相，那类错误消失，代价换成这一条。

`ManifestJson` 是**历史列**：改形态后新写的一律是空串，"这个版本是旧形态"由
`ConfigurationJson` 为 `NULL` 表达——那是**结构性**的判据，不是靠去猜旧 manifest 里的键长什么样。
新形态下三个字段——入口文件名、配置文件名、配置模板——都必有值，故这一列既不必删、也无从
回收；`catalog/migrations.py` 只加列不删列，保留它不花任何代价。

### 7.3 两种周期不是一件事，行情模式只有 `Bar`

- **落盘精度**：磁盘上的行情只有 5m 一档，引擎那份 `BackTest.json.BarPreces` 恒为平台常量
  `5m`（`app/config.py` 的 `MARKET_DATA_PRECISION`），由平台写死，用户改不动。它只决定
  读哪一族 parquet。
- **订阅周期**：提交页选的 `5m` / `15m` / `30m` / `60m` 写进**策略配置**的 `BarPreces`，
  是策略声明的聚合目标（`declare_bar_period` 的期望周期）。引擎在运行时把 5m 聚合成它
  ——`BarAggregator` 收同精度且 `targetSeconds % inputSeconds == 0` 的目标。

把一个值写进这两处，正是 2026-10-01 修掉的那个类别错误：选 15m 时引擎按 `Preces = '15m'`
去过滤，磁盘上没有这一族，一行都读不到。**它是报错不是静默 0 成交**——引擎在装载期就判
`ErrorMarketDataNotExist` 拒掉整轮（`SimExchange.cpp` 那段注释写着"不必等首根 bar 才发现
不可聚合"）。但那一轮已经废了，而详情报的是通用的"引擎报告本轮回测失败"，用户看不出是周期
选错了，故提交页只给清单内的值。

**行情模式固定 `Bar`**：可提交的模式只有 `Bar`（`engine_config.MATCH_MODE_VALUES`），
提交页因此**没有**行情模式选择。旧设计要策略在 manifest 里声明 `supported_match_modes`
并逐轮校验，是因为当年有实据：Python 策略漏写 `on_bar` 时，引擎在 `MatchMode: Bar` 下
0 笔成交，而**引擎报的 0 是忠实的**，日志也正常，费率三项为 0 也是对的——只看 `Success`
不足以判定打通。新形态下这份声明没有地方可放，也不需要：`MatchMode` 由平台写死，
不是用户可选项，"请求的模式不在策略声明的清单里"这件事写不出来。Tick 仍未开放，见 §12。

### 7.4 落盘与运行

```text
users/<user_id>/strategies/<strategy_id>/<version_no>/
├── <entry_filename>     # 用户上传的 .py 原文，平台永不改写
└── <config_filename>    # 用户上传的配置 JSON 原文，平台永不改写
```

两者都按**上传时的裸文件名**落盘。运行时把它们复制进 `runs/<RunId>/`，以裸文件名启动
（见第 3 节），并在提交时以**该版本的配置模板为底稿**渲染出 `<config_filename>`。

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
│   │   ├── clock.py / ids.py / errors.py / dependencies.py
│   │   ├── strategy_configuration.py   # 上传的两份文件与配置模板的形状校验
│   │   ├── bootstrap.py       # 首个管理员播种（无口令环境变量则启动即抛）
│   │   ├── auth/              # dependencies / passwords / tokens
│   │   ├── catalog/           # database / models（五张表）/ schemas /
│   │   │                      # enums / pagination / visibility（多租户唯一收口）
│   │   │                      # migrations（只补新增列，不是迁移框架）
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
    ├── domain/      纯逻辑: strategy-configuration / run-form / run-status / format / labels /
    │                download / equity（P5：权益序列与回撤，纯函数）
    ├── stores/      session / strategy-catalog / user-directory
    ├── router/      index.ts（路由表 + 登录守卫 + 逐页懒加载）
    ├── components/  AppLayout / ParameterForm / ParameterField / DirectoryPicker /
    │                StrategyUploadForm / StrategyVersionUploadForm / ArtifactList /
    │                ConfirmDialog / PaginationBar / StatusBadge / ErrorBanner /
    │                EmptyNotice / LoadingNotice / FilePicker /
    │                EquityChartPanel / ResultTablePanel（P5）
    ├── composables/ usePolling.ts / useConfigurationFileSource.ts
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

前置：两个终端都在跑（见 §8.1），且引擎、行情数据与行情组件都在位
（`../QuantTrading/bin/Release`、`market-data/Bar`、`../QuoteHub`）。

1. 以管理员登录 → 「用户管理」建**两个**账号，**显示名都填**（建号页已强制）。
2. 甲登录 → 「策略管理」上传策略（`.py` + 配置 `.json` 两份文件）→ 详情页确认
   版本与参数预览、确认可见性。
3. 甲在同页授权给**乙**：授权编辑器里按**显示名**从目录中选到乙（选不到自己，
   这是有意的）。
4. 乙登录 → 「新建回测」应能看到该策略；参数表单**按配置模板的键生成**
   （控件形态由该键取值的类型决定），且**没有行情模式选择**（Tick 不出现）。
5. 填运行范围后提交 → 运行列表自动轮询至 `succeeded`。
6. 运行详情：`TradeCount == 84`、`BarMarketDataCount == 2928`（与 P0 基线同
   口径，前提差异见 [`job-workspace.md`](job-workspace.md) §6）。
7. 在详情页下载 `result.json` 与一个 `t_*.csv`，**内容与磁盘上的原件一致**。
8. 另提交一轮 → 在它跑动时点「取消运行」→ 状态变 `interrupted`。
9. 回「新建回测」确认**表单里没有 Tick 选项**。

另见 [`acceptance-checklist.md`](acceptance-checklist.md)：2026-09-26 那六批观感与外观改动
（D.12 – D.17）的验收项，已按页面与操作顺序合并成一份清单，前置同 §8.1。本节与 §8.3 仍是
P4 / P5 的功能闭环。

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
| `POST` | `/api/strategies` | 上传策略（`.py` + 配置 `.json` 两份文件） |
| `GET` | `/api/strategies/{id}` | 详情与版本列表 |
| `GET` | `/api/strategies/{id}/last-submitted-parameters` | 本人对该策略**最近一次提交**的参数（提交页预填；**无历史即 200 + 全空**，不是 404） |
| `GET` | `/api/strategies/{id}/run-templates` | 本人在该策略下保存的配置模板（**P6**，见下） |
| `POST` | `/api/strategies/{id}/run-templates` | 存为模板；同策略重名 409（**P6**，见下） |
| `PATCH` | `/api/strategies/{id}/run-templates/{template_id}` | 改模板名（**P6**，见下） |
| `DELETE` | `/api/strategies/{id}/run-templates/{template_id}` | 删模板（**P6**，见下） |
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
| `DELETE` | `/api/runs/{id}` | 删除 run（连工作目录一起，**P7**，见下） |
| `GET` | `/api/runs/compare?ids=a,b,c` | 多轮指标对比（仅本人的 run；**P6**，见下） |

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
> 要连工作目录一起删、**留 P7**。（**2026-09-28 结清**：这四项至此全部落地——
> 前两项在 P5、后两项在 P6/P7，落点见本节末的「P6/P7 落地范围」。）
>
> **P4 落地范围**（前端前置的三处，都不新增依赖）：`GET /api/users/directory`
> （§7.5）、`POST /api/users` 的 `display_name` 必填、
> `StrategyVersionResponse` 增 `configuration_json`（**纯透传、不在读路径解析**：
> 解析要在读接口里多一条失败路径，存量行万一坏掉会让整个策略详情 500，
> 而前端本来就要 `JSON.parse`；该字段原先叫 `manifest_json`，改形态时改名，
> 见 §7.2）、`GET /api/runs/{id}/files` 与
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
> 3. **两个来源不能互换**：撮合模式与两个交易日取自**引擎那份**（`MatchMode` →
>    `engine_config.resolve_market_data_type`，由 `MATCH_MODE_VALUES` 反查，未收录回
>    `None`；`StartTradingDay` / `EndTradingDay` / `InitialCapital` 直取），合约与订阅
>    周期取自**策略那份**（`ExchangeId` / `InstrumentId` / `BarPreces`，**这是引擎侧既有
>    拼写，不是笔误**）。尤其 `bar_period` 只能从策略配置取——引擎那份的 `BarPreces`
>    是平台常量（落盘精度），从那里读会让每一次预填都报 `5m`。`MatchMode` 与
>    `InitialCapital` 用 `isinstance` 验类型，坏值各自回空，**不整份作废**。
> 4. **`params` 是常量差集**：策略配置减去 `PLATFORM_KEY_NAMES` 那三个键即可
>    （渲染与解码引用的是**同一份常量**，两处不可能不一致；旧形态要回头去查该运行用的
>    那个版本的 manifest，见 §7.2）。余下的策略参数**原样（含类型）带回，后端不做范围
>    过滤**——前端为了渲染控件本就要逐项判「这个取值在这个控件上能不能用」，后端再滤
>    一道就是两处真相，还会静默吞键。
> 5. **坏 JSON 优雅降级**：`JSONDecodeError` 记 warning 后回 `None`（等价「没有记忆」），
>    绝不因一轮坏数据让提交页报错。
> 6. **无历史回 200 而不是 404**：`run_id=None` + 空 `params`。404 会与「策略不存在/
>    不可见」混为一谈，而首次使用这个策略走的就是这条路。可见性闸用
>    `load_visible_strategy`（别人共享给你的策略也要能用这个功能）。

> **P6/P7 落地范围**（2026-09-28）：`GET /api/runs/compare`、`DELETE /api/runs/{id}`、
> 四个配置模板端点，以及**保留清理**。
>
> **配置模板（P6）**——新建 `routers/run_templates.py` 与 `RunTemplates` 表：
>
> 1. **作用域是策略域**：行上同时有 `OwnerUserId` 与 `StrategyId`，唯一约束
>    `(OwnerUserId, StrategyId, Name)`；而**可见性仍只按归属人**——授权与公开只影响
>    "能不能给这个策略存模板"，不影响"能看见谁的模板"，与运行记录同一条口径
>    （被授权人能跑，不等于能看别人的运行）。
> 2. **读走 `load_visible_strategy`，写走 `load_owned_run_template`**：后者同时按归属人与
>    策略过滤（实现只在 `catalog/visibility.py`），取不到即 404 `模板不存在`。
>    **改名与删除不再要求策略此刻可见**：模板是我的，就该一直改得动、删得掉——要求可见性会让
>    "策略被撤了共享之后，我的模板连同删掉它的接口一起消失，而它们在库里还在"。
> 3. **重名 409 以唯一约束为准**（捕 `IntegrityError`），不做"先查后插"：后者要两次往返，
>    且两次之间并发的同名创建照样会一起落库。
> 4. **校验与提交共用一份判据**：`run_submission.py` 的三个校验器
>    （`_normalize_run_field_value` / `_validate_trading_day` / `_validate_initial_capital`）
>    与 `MAXIMUM_RUN_FIELD_VALUE_LENGTH` 提到 `services/run_configuration.py` 并改为公开名，
>    提交侧改 import。**HTTP 契约一字不变**，属搬位置。两处各写一份的话，"能存下的取值"与
>    "能提交的取值"就不是同一个集合，症状是"存得下、提交时 400"且只在特定取值上出现。
> 5. **参数按该策略最新版本的配置模板校验，但模板不绑版本**：模板是策略级的，换版本后仍应
>    可用。代价是"某个参数在新版本里被删掉"要等到套用或提交时才暴露——绑版本换来的
>    "存模板时就报错"会让每一次正常迭代作废全部旧模板。参数**键集不属于模板**（它由配置
>    文件固定，见 §7.2），故换版本后旧模板可能带着新版本没有的键，提交侧以
>    `UNKNOWN_PARAMETER_MESSAGE` 拒。
> 6. **没有 apply 端点**：套用要与当前配置模板派生出的控件、当前表单已填的值一起决定，
>    是纯前端动作；放服务端等于把 `createInitialParameterInputs` /
>    `buildPrefilledRunFields` 抄一份，而抄出来的那份迟早与界面上真正跑的那份不一致。
> 7. **硬删，无 `DeletedAt`**：没有任何外键指向模板，删它不影响历史运行——那一轮的取值在提交
>    时就烤进了 `Runs.ParamsJson`。护栏 `MAXIMUM_TEMPLATES_PER_STRATEGY = 50`（超了 400），
>    它是**护栏而不是不变量**：计数与插入之间没有锁，多存一个模板的代价是几十字节，为它加锁
>    不划算；列表不分页（个人级小集合）。
>
> **`GET /api/runs/compare`（P6）**：
>
> 1. **必须声明在 `@router.get("/{run_id}")` 之前**：两者形状完全一样，声明晚了就被当成
>    `run_id="compare"` 吞掉，且不报任何错——只会得到一句"运行不存在"。
> 2. **归属与不存在一起判，粒度是整请求**：一次 `IN` 取件，有任何一个 id 取不到就整请求 404
>    （文案与单轮详情逐字相同）。不降级成"少一列"——那正是多租户要消除的可分辨信号：
>    拿一组 id 去试就能问出"这个运行号存不存在"。
> 3. **曲线逐轮降级**（未结束 / `db_path` 为空 / 结果库文件不在 / 库读不出来），只把**那一列**
>    的曲线置空并附一句原因，指标与其余列照常出。每条协程各自 `try/except`，
>    `logger.warning` 只带 `run_id`、**不带路径**。
> 4. **指标一律取 `Runs` 的镜像列**，绝不重读 `result.json`：那 27 列的立项理由就是"列表与对比
>    不逐行解文件"；这同时是 **P7 保留清理的前提**——目录被清掉之后，历史轮的指标必须还在。
> 5. **`RunSummaryResponse` 增 `params_json`**（纯增量、无破坏性）：验收句要求看见"同参不同
>    `GridStep`"到底差在哪，而参数此前只在详情响应里。
> 6. `ids` 逗号分隔 → 去空白 → 丢空项 → **保序去重**；空即 400；上限
>    `MAXIMUM_COMPARISON_RUNS = 6`，**按去重之后判**（传 8 个而其中 3 个重复，实际画 5 列，
>    没有理由拒）。上限的三条依据：ECharts 默认调色板 9 色、URL 长度、每轮一次结果库打开的
>    可估开销。
>
> **`DELETE /api/runs/{id}`（P7）**：
>
> 1. **先删作业目录、后删行**。顺序不能反：行是那个目录**唯一**的句柄（目录名就是 `RunId`，
>    而知道它的只有这一列），行先没了就再没有任何东西能找到那个目录，磁盘泄漏变成永久且
>    不可观测的。反过来，目录删干净了而行还在，只是一行指向空目录的记录，下一次删除会幂等地
>    把它收掉。
> 2. **守卫只有一份**（`services/run_storage.py:resolve_run_directory`）：由运行行确定性重建
>    作业目录（`settings.runs_root / run.workspace_path`），四道校验——非空、`resolve()` 后再判
>    `is_relative_to(runs_root)`、不得等于运行根本身、**目录名必须等于 `run.id`**。最后一条把
>    契约"`RunId` 同时用作目录名"变成可执行判据，挡住"A 轮的目录名写到 B 轮的行上"这种越权读写。
>    **失败方向恒为拒绝**（fail-closed）。读路径原用的 `_resolve_job_directory` 一并改为复用
>    它再补 `is_dir()` 前置——读要求目录真在，删除**不能**要求，复用同一份就等于把删除也卡在
>    `is_dir()` 上（那正是"目录删得掉"的正常情形）。
> 3. **权限只走 `load_owned_run`**（运行只有提交人本人可见，**管理员无旁路**，跨租户 404）；
>    状态闸**直接复用 `_require_finished_run`**（未结束 → 409，既有文案）——想让排队轮消失走
>    既有的取消端点。界面上删除按钮只在终态显示（复用 `isTerminal`）。
> 4. **幂等**：目录已不在但行还在 → 200 `运行已删除`；行已不在（重复调用）→ 404（同
>    `DELETE /api/strategies/{id}` 的先例）。目录**删不掉**时返回 409 且**保留行**，文案不含
>    路径：行在，下次还能再试；而报成功等于把磁盘泄漏变成看不见的。
> 5. 响应体是 `MessageResponse`，**不用 204**（JSON 通路才统一）；`shutil.rmtree` 阻塞，
>    故走 `asyncio.to_thread`。
>
> **保留清理（P7）**——新建 `services/run_retention.py`，默认**关**：
>
> 1. 两个配置项：`QUANT_RUN_RETENTION_ENABLED`（默认 `false`）、
>    `QUANT_RETAINED_RUNS_PER_USER`（默认 50，`__post_init__` 校验 `>= 1`——0 会被算法读成
>    "一个不留"，必须在这里拦住）。布尔读取新增同形兄弟 `read_boolean_environment`，
>    非法值**抛错**而不是静默回落：写 `=enable` 的人想要的是"开着"，按"非 true 即 false"
>    处理会得到一个安静地什么都不删的后端，与"这项没生效"无法区分。
> 2. **默认关**的理由：它删的是历史轮的结果库明细、对比曲线与逐字复现要读的东西，不可逆，
>    而且**第一次打开就会把存量历史一口气收干净**——这件事只能由人明确要求。
> 3. 算法三段，**不把文件 IO 夹在决定与落库之间**：① 选候选——按用户取终态轮，
>    `order_by(submitted_at DESC, id DESC).offset(retained_runs_per_user)`，即"第 N+1 新及
>    之后"，不需要窗口函数；**非终态永不入选**（这是与在飞运行的关系的结构性答案）；
>    `protected_run_ids` 一律排除。② 逐行 `to_thread(remove_run_directory, ...)`。③ 只有
>    `ROW_DELETABLE_OUTCOMES` 里的才删行，最后显式 `commit()`。
> 4. **与端点共用同一个 `remove_run_directory`**：守卫、日志、fail-closed 的方向，以及"什么
>    算删干净了"这个判据因此都只有一份。两处各写一套删除逻辑的话，迟早有一份先漏掉守卫。
> 5. 何时跑：**启动时**（`main.py` 的 lifespan 里，`recover_interrupted_runs` **之后**——
>    恢复会把残留的 `queued` / `running` 改写成 `interrupted`，改写后才进候选集合；
>    调度器 `start()` **之前**——此刻没有作业在跑）整段包 `except (OSError, SQLAlchemyError)`
>    → `logger.error` 后**继续启动**（维护性动作的失败不升级成可用性故障，是有意的取舍）；
>    **某一轮落终态之后**，调度器起一个**游离任务**（绝不 await，免得拖住槽位释放路径），
>    注入方式是仅关键字、默认 `None` 的 `retention_sweep` 形参——默认 `None` 意味着既有测试
>    构造的调度器行为不变。单飞护栏是 `self._retention_task`，"判 + 建"之间没有 `await`，
>    在事件循环里是原子的，不需要锁。
> 6. **不引入锁文件或心跳**：单实例是调度器的硬前提（§12.11）。加锁不会让多实例变安全，
>    只会把"多开了一个进程"从"删错"变成"看着有保护其实没有"。

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
| `/` | 主页 | 原文为「重定向到 `/runs`」；**2026-09-26 起改为公开主页**（五节落地页，见下方订正段） | ✅ P4（主页 2026-09-26 增补） |
| `/runs` | 运行列表 | 状态徽章 / 引擎判定 / 交易日区间 / 耗时 / 交易笔数 / 余额；按状态与策略筛选、按指标排序、分页；**存在非终态轮时 2 s 轮询**。**控件与表格已换成 Element Plus**（见 §12.18 与 §13 的 EP 拍板表） | ✅ P4（EP 化 2026-09-26，观感层统一 2026-09-26） |
| `/runs/new` | 新建回测 | 选策略 → 选版本（缺省最新）→ **按该版本的配置模板动态生成参数表单** → 提交；`match_mode` 固定 `Bar`。**选中策略时按「你上次提交的那一份」预填**（策略参数 + 标的/日期/初始资金/周期），并给一个**「重置为默认值」**按钮（见 §11 的增补段）。**「配置模板」区：下拉选一份已存模板 →「套用」/「存为模板」**（弹窗输名字，见 §11 的 P6 增补段）。**控件已换成 Element Plus，但表单外壳仍是原生 `<form>`**（见 §13） | ✅ P4（预填 2026-09-26 增补，EP 化 2026-09-26，观感层统一 2026-09-26，**模板 2026-09-28 P6**） |
| `/runs/:id` | 运行详情 | 概览 / 绩效指标 / 引擎数据镜像 / **权益曲线与回撤曲线** / **5 张结果表的明细分页表** / 提交参数与引擎配置 / stdout·stderr 尾巴 / 产物清单与下载 / 取消 / **加入对比**；未结束时 2 s 轮询。**「取消运行」与「删除运行」按终态互斥**（未结束只能取消，已结束才能删） | ✅ P5（观感层统一 2026-09-26；**结果库两节只在终态挂载**：未结束时后端回 409，前端干脆不请求，翻成终态由既有的 `watch(isTerminal)` 自动接上；**加入对比与删除 2026-09-28 P6/P7**） |
| `/strategies` | 策略管理 | 列表 + 上传面板 + 可见性 + 归属（经用户目录映显示名） | ✅ P4（观感层统一 2026-09-26） |
| `/strategies/:id` | 策略详情 | 版本列表（含该版本配置模板的参数预览）+ 传新版本 + **授权编辑器（目录选人）** + 软删 | ✅ P4（观感层统一 2026-09-26） |
| `/users` | 用户管理（admin） | 列表 / 建号（显示名必填）/ 启用停用 | ✅ P4（观感层统一 2026-09-26） |
| `/:pathMatch(.*)*` | 404 | 未知路径不白屏；**不需要登录**（登录前的错地址也该看得见它） | ✅ P4（观感层统一 2026-09-26） |
| `/compare` | 对比 | 多轮指标表 + 权益曲线叠加。**`?ids=` 是这一页唯一的状态源**（勾选框只是它的另一种写法）：去空白、丢空项、保序去重、最多 6 轮，选满后其余勾选框置灰；候选只列**终态**轮；取数一次性**不轮询**（另有「刷新」按钮） | ✅ P6（2026-09-28） |
| `/settings` | 设置 | 引擎根、并发上限、超时、数据根，带 health 自检 | P8（该端点目前是 **admin 专属**，普通用户的这一页看不到引擎自检） |

**订正（2026-09-26，第十三批 主页与改名）**：

- `/` 不再是重定向，而是一个**公开的一屏落地页**（`views/HomeView.vue`）：主视觉 → 平台能做什么
  → 一次回测的完整流程 → **引擎与运行环境** → 收尾一个「当前边界」卡。访客看到介绍与「登录」入口，
  拿到用户后同一页换成「进入回测运行」与「新建回测」。落地页**没有任何请求**，不产生新的反馈路径。
- **第四节只讲引擎侧**（同日追加，用户看过主页后提出）：原本叫「技术底座」、8 条，含前端框架、
  后端框架与「一次性进程 + 常驻调度层」的接入方式，用户原话「这里 web 的技术方案没什么可介绍的」。
  已砍掉那三行，留回测引擎 / 运行环境 / 行情模式 / 一轮的产物 / 实测速度 5 条，**标题改
  「引擎与运行环境」**；站点自身的实现取向不再出现在这一页。
- **外壳显隐从「是否公开」解耦**：`meta.isPublic` 此前兼着「不需要登录」与「不挂外壳」两件事
  （主页公开但**要**顶栏，访客得从那里登录），故新增一个抑制型字段 `meta.hidesHeader`，**只写在
  `/login` 与 404 两条路由上**；`isPublic` 的原义一行不改，`main.ts` 的 401 处理器判据不变。
- **带顶栏的公开页要补一次身份**：守卫在 `hidesHeader !== true` 且 `currentUser === null` 时补一次
  `loadCurrentUser()`（`/login` 与 404 不挂外壳，没有消费者，不白付一次往返）；补失败时**公开页
  不跳转**，只清会话并停在原页——令牌过期的访客从公开主页被弹到登录页会让「公开」名不副实。
- **产品名**：界面文案、浏览器标签页标题与主页主视觉改为「薪火量化」（详见 §1）；后端只改
  `APPLICATION_DESCRIPTION` 一句人类可读的字符串，`APPLICATION_TITLE` 保留 `QuantPlatform`。
- **`/compare`（P6）与 `/settings`（P8）当时都未实现**，那一批不做任何预告，主页的边界卡只说「尚未提供」；
  `/settings` 的 **admin 专属**限定维持原样。（**2026-09-28 订正**：`/compare` 已随 P6 落地，
  见上表；主页边界卡里"多轮对比尚未提供"那一句**同批改掉**，`/settings` 仍未实现。）

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
| **P6 对比与模板** ✅ | 多轮对比、配置模板保存复用 | **2026-09-28 通过**（验收句）：同参不同 `GridStep` 的两轮各占一列、指标并排、参数节里两格的取值是 `0.01` / `0.02`，曲线叠加在一张图上（`test_run_comparison.py` 的验收用例 + `RunCompareView.spec.ts` 同一条）。后端 **558 项**（新增 55 项：模板 17 / 对比 20 / 删除 9 / 保留 9），前端 `type-check` 无错 + `vitest` **24 文件 204 项全绿** + `build` 成功。**交互层（套用模板、加入对比）只能由用户在浏览器里走一遍**，见 `docs/acceptance-checklist.md` §11 |
| **P7 加固** ✅ | 并发上限、超时、**磁盘清理**、~~日志轮转~~ | **2026-09-28 通过**：并发上限与超时 P3 已交付（本行原验收句自此结清），本批新增**删除端点**与**自动保留策略**（默认关）。`DELETE` 的判据是"目录先删、行后删"且幂等（目录已不在即 200；行已不在即 404；删不掉即 409 且保留行）。**日志轮转不做**：整目录移除已覆盖 `log/`（见 `job-workspace.md` §1/§5）。真引擎验收 4 项全过（21.7 s），证明改动没碰引擎侧契约 |
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

前端落点在 `RunSubmitView.vue` 与两个纯函数模块（`domain/strategy-configuration.createInitialParameterInputs`
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
端点上仍回 `match_mode` 供日后放开 Tick 时用。④ **平台认不了的约束照样能带出**：
如跨参数的 `0 < GridStep × GridCount < 1`——平台只看键值、不看键之间的关系，
引擎构造期会拒并在 `stderr.txt` 写明。⑤ **不记「上次选的是哪一版」**：版本仍默认最新。

**2026-09-27 增补：引擎版本可追溯**（用户提出，不属任何分期）。起因是**换引擎那一刻库里没有
任何线索**：升级 = 原地覆盖 `bin/Release`，此后历史轮与新轮在 `Runs` 表里逐列相同。范围经用户
拍板为**只做可追溯**（不收引擎包、不做启用 / 停用 / 删除、不按轮选引擎，理由见 §12.23）。
落点四处：`services/engine_probe.read_engine_version()` 取标识（两层口径，见 §5.1）、
`RunModel.engine_version` 在**提交时**冻结（与 `backtest_config_json` 同一形态）、
`RunDetailResponse` 与 `EngineHealthResponse` 各暴露一处、前端详情页多一行「引擎版本」。
**列表页不带该字段**（与 `hostname` 同形，多一个既不排序也不筛选的字段就是白带的字节）。

**新列落到既有库的那一步是本批唯一的新机制**：`create_all` 对已存在的表**一列都不改**，于是
"加一列模型"与"库里那 10 行真实运行记录"直接冲突——补不上就只能重建库。新增
`catalog/migrations.py:add_missing_columns()`，只做一件事：比对 `Base.metadata` 声明的列与
实际表结构，缺哪个补哪个。三处边界写进模块 docstring：

- **列的 DDL 从模型渲染**（`CreateColumn(column).compile(dialect=...)`），**类型不另写一份**——
  否则就是"两处真相"（§12.20 的表名两处真相是同类缺陷）。
- **只补新增列**。改类型 / 删列 / 加约束一律不做，遇到就记一条 warning 点名"需重建库"。
  NOT NULL 且默认值渲染不进 DDL 的列（如 `default=utc_now`）也走这条，**拒绝得静默**的话症状
  会推迟到第一次写入，变成一句与原因隔着一层的英文报错。
- **幂等**：启动期每次都会跑它。

**验收（2026-09-27 通过）**：后端 **480 项全过**（新增 `test_engine_probe.py` 13 项、
`test_catalog_migrations.py` 6 项、`test_health.py` 3 项、`test_run_submission.py` 1 项、
`test_run_queries.py` 1 项）；迁移先在 `catalog.db` 的**副本**上试过（补列日志出现、
`EngineVersion` 列到位、10 行 Runs 一行不少且取值为 `''`）才落到真库；前端 `type-check` 无错 +
`vitest` 170 项全绿 + `build` 成功。**未做**：不引迁移框架（Alembic 之类）、不写
`engine-version.txt`（QuantTrading 侧的产物，上云前随引擎包一并给）、不给引擎根加新环境变量
（版本文件名是平台侧约定常量，不是配置项）。

**2026-09-28 增补：P6（对比与模板）与 P7（删除与保留清理）**。两期的设计与判据见 §9 的
「P6/P7 落地范围」与 §13 的 P6/P7 拍板表，这里只记**与计划有出入的地方**与验收证据。

**与计划的三处命名/落点出入**（都是实施中发现计划那一版不够准确，不是打折扣）：

1. **`services/run_field_values.py` → `services/run_configuration.py`**。计划里那个名字只说了
   "运行级字段的校验"，而这个模块实际收的是**一份运行的完整配置怎么校验**：运行级字段之外还有
   模式闸（`validate_match_mode`）、交易日区间、初始资金，以及策略参数怎么收成引擎取值
   （`build_parameter_configuration`）。它的两个消费者是提交端点与模板端点，名字对准"运行配置"
   才对得上这两处。
2. **`domain/run-metrics.ts` 的 `buildHostMetricRows` → `buildDetailMetricSections`**。计划里那个
   名字暗示返回值是"几行"，实际返回的是**整节**（节标题 + 行定义），且详情页三节共用一份定义。
   名字跟着返回值走。
3. **新增 `domain/run-comparison.ts`（计划里没有这个文件）**。对比表的造型（列 = 轮、行 =
   `节标题·行名`、参数行取各列并集）在视图里是看不见的，抽成纯函数才进得了 vitest——
   计划把这件事留在 `RunCompareView` 里，等于让它只靠 jsdom 间接覆盖。

**两处前端契约加宽**（都是纯增量）：

- `domain/run-form.ts` 新增 `RunFormPrefill` 类型，`buildPrefilledRunFields` 的**第一个形参换成它**。
  `LastSubmittedParameters` 与 `RunTemplate` 都结构化满足它，于是"套用模板"与"按上次提交预填"
  在两个函数眼里是同一个动作，取数路径不必分支；既有调用点全都照旧编译得过。
- 详情页的三节行定义改调 `domain/run-metrics.ts`，**DOM 结构逐字不变**（`run-metrics.spec.ts`
  钉的就是这一点）。

**两处 unlayered-CSS 陷阱**（新图表组件踩到过，第二条对全站成立）：

- `x-vue-echarts { height: 100% }` 与 `.el-alert { margin: 0 }` 都在**无层样式**里，特异性之外还
  压过 Tailwind 的 `@layer utilities`。故**图表高度只能给外层包裹 `div`**（给组件本身设
  `h-*` 会被那条 100% 顶掉，表现为图高 0），**纵向间距要加在我们自己的包裹元素上**。
- 与 P5 的 `EquityChartPanel` 同一条：这个陷阱不是"再写一遍就能避开"，而是**每个新图表容器都要
  重新避一次**。

**测试侧的两次收口**（都应写进后续批次的默认动作）：

- `backend/tests/run_helpers.py` 收三个共用动作：`write_job_directory` / `read_run_or_none` /
  `open_result_database_for_writing`。四个新测试文件都建作业目录、都读回运行行、都要造结果库，
  各写一遍就是四份会各自漂移的夹具。
- **验收证据**：后端 **558 项全过**（新增 55 项：`test_run_templates.py` 17 / `test_run_comparison.py`
  20 / `test_run_deletion.py` 9 / `test_run_retention.py` 9；随之改动的既有用例是 `test_config.py`
  （新增两个设置项）与 `test_run_submission.py`（搬位置后的 import 与 `assert_rejected` 改从
  `tests/helpers.py` 取）），`test_run_artifacts.py` 与 `test_run_results.py`
  **改后仍全绿**——这是"`_resolve_job_directory` 换成共用守卫后契约不变"的证明；
  真引擎验收 **4 项全过**；前端 `type-check` 无错 + `vitest` **24 文件 / 204 项全绿**
  （新增 `run-comparison.spec.ts` 8 项、`run-metrics.spec.ts` 5 项、`RunCompareView.spec.ts` 7 项，
  `equity.spec.ts` 与 `run-form.spec.ts` 各自扩充）+ `build` 成功。
- **一处越批的文案订正**：主页「当前边界」卡里"多轮对比与设置页尚未提供"这句随 P6 落地**删掉了
  前半句**（现为"设置页尚未提供"）。这是主页那三条自我约束之一自己要求的：页面一落地，
  边界卡那句就要跟着撤，否则公开页在说一件已经不成立的事。

---

## 12. 风险与不做项

1. **绑死 Windows + Python 3.14**：`.pyd` 是 `cp314-win_amd64`，
   Linux 无解，云主机只能是 Windows；后端必须与引擎同机，**横向扩展无余地**。
2. **目录级隔离挡不住恶意读盘**（见 5.2）。开放给不可信用户前必须上系统级隔离。
3. **磁盘膨胀**：实测单轮 **3.0 MB**（结果库 + 17 个 CSV + 日志），
   多用户下增长更快，需按用户配额与清理策略（P7）。**P7 已做其中一半**：
   `DELETE /api/runs/{id}` 与保留清理（默认关，`QUANT_RETAINED_RUNS_PER_USER` 默认 50）。
   **这个 3.0 MB 就是 N 的取值依据**：50 轮 × 3.0 MB ≈ 每人 150 MB，十几个人也在 2 GB 量级，
   是单机磁盘能长期承受的数；反过来设成 500 就意味着一人 1.5 GB。**仍缺的是配额**——
   保留轮数管的是"每人留多少轮"，管不了"一共多少人"，总量控制留 P8 与系统级隔离一起看。
4. **cancel 的进程树**：宿主是单进程（引擎以库形式进程内加载），
   `terminate()` 够用，不必上 `psutil`。
5. **输出编码**：引擎有 GBK 变体、日志含中文，runner 读 stdout/stderr 须
   `errors="replace"` 容错，否则收尾部时会崩。
6. **`argv[0]` 传裸文件名是固定选择，不是硬约束**（**2026-09-25 实测订正**）：
   带路径的形态（正斜杠相对、反斜杠绝对）同样能跑完整轮、退出码 0，
   原文"否则启动期即终止进程"**已不成立**。详见 §3 与
   [`job-workspace.md`](job-workspace.md) §3.1。
7. **`BarPreces` 是引擎侧既有拼写**（非笔误，不可擅改），平台配置键须逐字一致。
   它在两份配置里各出现一次而**语义不同**：引擎 `BackTest.json` 那份恒为落盘精度 `5m`，
   策略配置那份是用户选的订阅周期，见 §7.3。
8. **不做**：跨运行库级对比（`ATTACH` 上限 10，已决定暂不做）、盘后行情修正、
   C++ 策略宿主（三条再评估触发条件已留档）、zip 上传（见 7.1）。
   **P6 的对比绕开了这个限制而不是解开它**：对比**不 `ATTACH`**——逐轮分别开只读连接、
   `asyncio.gather` 并行读（`routers/runs.py:_read_comparison_equity`），因此 6 轮这个上限与
   那 10 库无关；失败粒度是**逐轮降级**（只有归属/不存在才整请求 404）。

**P3 起新记的已知缺口**（明确不做，写在这里免得日后被当缺陷）：

9. **队列是纯 FIFO，没有配额**：一个用户连发 100 个作业会饿死其他人
   （`max_concurrent_runs=1` 时更明显）。本期不做配额。
10. **不做"重启后续跑"**：重启时 `queued` / `running` 一律标 `interrupted`。
    若日后要做，唤醒机制必须从"事件 + 看门狗"改成短轮询（看门狗会漏掉
    重启前的行，而它们那时不在队列里）。
11. **不做多 worker**：单实例是调度器的**硬前提**。`uvicorn --workers 2` 下，
    B 进程的启动恢复会把 A 正在跑的作业标 `interrupted`。已写进
    `recovery.py` 的 `logger.warning`。**它同时是 P7 保留清理的前提**：清理**不引锁文件或
    心跳**（§9 的 P7 段），直接假定"只有一个进程在删"。加锁不会让多实例变安全，
    只会把"多开了一个进程"从"删错"变成"看着有保护其实没有"。
12. **孤儿进程不自动清理**：不按 `RunnerPid` 杀（PID 回收后会杀错无关进程，
    且没有可验证的身份凭证）；孤儿的写入被"相对写路径"关在自己的作业目录里，
    伤害是资源泄漏而非正确性破坏。恢复时把 `(RunId, RunnerPid)` 写进日志供人工核对。
    **代价在 P7 显形**：孤儿还占着作业目录时，Windows 上 `rmtree` 报 `WinError 32`，
    删除端点回 **409 且保留行**、保留清理**跳过该行**等下次。这是"删不掉就是删不掉"的正确
    结果（行是目录唯一的句柄，保留行才谈得上重试），**不要当缺陷修**——重启后端或结束那个
    进程后重试即可。
13. **Tick 模式不可提交**：提交侧一律 400。引擎的 tick 撮合有**三档**（`OrderBook:0` /
    `LastPrice:1` / `OppositePrice:2`），平台的 `MarketDataType` 只有两值，推不出那三档；
    且行情根 `market-data/` 下根本没有 tick 数据，从未验证过。判据是具名常量
    `SUBMITTABLE_MATCH_MODES = frozenset({MarketDataType.BAR})`，开 Tick 时改它。
    **P4 的「新建回测」表单因此不显示 Tick 选项**（2026-09-25 拍板）——
    表单按 Bar 单模式生成，不做模式联动，也就不必先编出三档的界面语义。
    （旧形态允许策略在 manifest 里声明支持 `Tick`，声明了也一样 400；改形态后
    §7.2 的那份配置没有地方放这份声明，判断一字未变。）
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
    另：**明细表没有导出**（下载已有的产物即可）；K 线图、图表导出图片、
    大表虚拟滚动（一页最多 100 行）**明确不做**。**订正**：原文把"多轮曲线叠加（P6）"
    也列在这里，那是 P5 当时的缺口清单；**P6 已把它做掉**——`/compare` 的叠加图就是它，
    只是**按日对齐、缺日留断口**（`domain/equity.ts`），不是 K 线。

**引擎版本批次起新记的已知缺口**：

23. **平台不做引擎包管理**：不收引擎包、不做启用 / 停用 / 删除、不按轮选引擎。理由是引擎包
    70–120 MB 且**平台不知道它的依赖闭包**——收包就得校验完整性，校验不了就不该收。留档与
    换版归部署流程（§5.1 的按版本留目录）。平台只做**可追溯**：记下这一轮跑的是哪个标识，
    不去管那个标识对应的目录还在不在、更不去校验它。**代价**：`Runs.EngineVersion` 是提交时
    读到的**探测值**，不是校验过的版本号——引擎根坏了它是空串，引擎包被人在原地换掉它也不会
    知道（作业入队后、起进程前换掉即属此列；"作业在跑时换 `.pyd`"则根本不受支持，Windows 上
    已加载的扩展模块处于锁定状态，覆盖会失败）。
24. **`Runs.Hostname` 是一条死列，界面上却看得见**（2026-09-27 实测）：全仓
    **没有任何代码路径写它**（生产环境恒为空串），而运行详情页有一行「执行主机」按它渲染。
    它当初是为"多机部署时能分辨哪台机跑的"留的，而本平台的结构是**后端与引擎必须同机**、
    横向扩展无余地（§5.1），故这一列今天没有语义。**本批不动它**——删一列属于"删除既有
    导出符号"，须另行确认。

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
| 上传形态 | **`.py` + 配置 `.json` 两份文件**（2026-10-01 改） | 原为「`.py` + manifest」；不收 zip，见 §7.1 |

**P3 开工前新增的拍板**（2026-09-25）：

| 决策点 | 选择 | 备注 |
| ---- | ---- | ---- |
| 授权粒度 | **不区分 `read` / `run`，授权即可跑** | 跑权限 = 可见性，不新增越权分支；`GrantPermission` 保留但不再被读；见 §11 |
| `public` 语义 | **隐含可跑** | 一次有意的权限放宽 |
| `params` schema | **本轮定案**（四类型 + `options` + 缺省即必填） | 见 §7.2 |
| manifest 运行级字段映射 | **新增 `run_field_keys`**（2026-10-01 随 manifest 一起删除） | 当时按"`bar_period` 两处不一致 → 静默 0 成交"记；**该说法已于 2026-10-01 订正**为"引擎装载期即拒、报错收场"，见 §7.3 |
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

**主页与改名开工前新增的拍板**（2026-09-26，第十三批）：

| 决策点 | 选择 | 备注 |
| ---- | ---- | ---- |
| 主页可见性 | **公开**：`/` 不需要登录 | 访客看到介绍 + 「登录」入口；拿到用户后同一页换成工作入口，判据是 `session.hasCurrentUser` 而不是"有没有令牌" |
| 主页形态 | **一屏落地页**（主视觉 / 能做什么 / 流程 / 引擎与运行环境 / 当前边界） | 正文窄栏 `max-w-5xl`；全页只有一个 `h1`；卡与步骤一律 `v-for` 遍历模块级数组。第四节原为「技术底座」8 条，同日按用户意见砍掉 web 栈三行后改此名 |
| 主页第四节的取材 | **只讲引擎侧**，不介绍站点自己的实现 | 读这一页的人要判断策略跑不跑得动、多快、产物是什么；用什么框架写的与他无关 |
| hero 自绘 | **不套 `PageHeader`** | 那是"工作页页头"原语（`text-xl` + 右侧动作），落地页有五个同级分节 |
| 外壳显隐 | **新增抑制型 `meta.hidesHeader`**，只写在 `/login` 与 404 上 | `isPublic` 的原义不动（它只管鉴权）；取抑制极性是为了"漏写时多一个顶栏"这种肉眼可见的失败，而不是静默少掉导航 |
| 带顶栏的公开页 | **补一次 `loadCurrentUser()`，失败时公开页不跳转** | 不补就会出现"登录后 F5 停在 `/`、顶栏空着、主页还在问要不要登录"的半登录态；失败仍跳转会让"公开"名不副实 |
| 身份就绪的判据 | **`session.hasCurrentUser`**（唯一出处） | `isAuthenticated` 只看令牌；渲染身份的地方（顶栏、主页两态入口）都只读前者 |
| 改名范围 | **界面文案 + 浏览器标题**：顶栏品牌区、登录页标题、主页主视觉、`index.html` 的 `<title>` | 仓库名 / 包名 / 目录名 / 文档标题 / `APPLICATION_TITLE` **不动** |
| 品牌区链接 | 由 `runs` 改指 **`home`** | 品牌区是"回首页"的惯例语义，导航项才是"你在哪个工作面"；**不新增「主页」导航项** |
| 路由改名后仍落 `/runs` | **有意不改** | 登录后的下一步是干活；主页由品牌区到达。404 的「回到回测运行」同理保留 |
| 本批不做 | 按路由改标签页标题（`meta.title` + 一处 watch 是一套新机制）、页脚、深色模式、移动端专版、markdown 渲染与插画资产 | 逐条理由见 `PROGRESS.md` 的 `D.13`；日后标签页标题的正确落点是守卫里统一 `document.title = meta.title` |

**P6/P7 开工前新增的拍板**（2026-09-28）：

| 决策点 | 选择 | 备注 |
| ---- | ---- | ---- |
| 模板的作用域 | **策略域**：行上同时有 `OwnerUserId` 与 `StrategyId`，可见性**只按归属人** | 参数只在一个策略的键命名空间里有意义；纯用户域会让跨策略套用"只套一半"（运行级字段生效、参数被静默忽略），正是本仓到处在防的静默失败 |
| 模板有无 apply 端点 | **无**：只有 list / create / rename / delete | 套用要与当前配置模板派生出的控件、当前表单已填值一起决定，是纯前端动作；放服务端等于把 `createInitialParameterInputs` / `buildPrefilledRunFields` 抄一份 |
| 模板的删除形态 | **硬删，无 `DeletedAt`** | 没有任何外键指向模板；软删会引入"名字被占住又无接口释放"的老问题，而改名/删除**不要求策略此刻可见**正是为了不留这种死角 |
| 模板校验的来源 | **按该策略最新版本的配置模板校验，但不绑版本** | 绑版本会让每次正常迭代作废全部旧模板；代价是"参数被删掉"要到套用或提交时才暴露 |
| compare 的失败粒度 | **归属/不存在 → 整请求 404**；**只对"这一轮还没有曲线"逐轮降级** | "少一列"是越权与不存在的可分辨信号，与多租户规则冲突；而未结束的轮不该让整页变白（同 `RunDetailView` 面板级降级的先例） |
| compare 的指标来源 | **只取 `Runs` 的镜像列**，绝不重读 `result.json` | 那 27 列的立项理由就是"列表与对比不逐行解文件"；也是保留清理的前提——目录清掉后指标必须还在 |
| 对比轮数上限 | **6**（`MAXIMUM_COMPARISON_RUNS`），**按去重后判** | 三条依据：ECharts 默认调色板 9 色、URL 长度、每轮一次结果库打开的可估开销 |
| 删除的先后 | **先删作业目录，后删行** | 行是目录**唯一**的句柄（目录名就是 `RunId`）；反了就再没有任何东西能找到那个目录，磁盘泄漏变成永久且不可观测的 |
| 删除的状态闸 | **要求终态**（直接复用 `_require_finished_run`，未结束 409） | 运行中的轮其目录正被引擎占着；想让排队轮消失走取消端点 |
| 目录删不掉时 | **保留行 + 409**（文案不含路径） | 行在，下次还能再试；报成功等于把磁盘泄漏变成看不见的。孤儿进程占用是已知触发条件（§12.12） |
| 保留策略默认值 | **默认关**（`QUANT_RUN_RETENTION_ENABLED=false`） | 不可逆；默认开启会在升级那次启动就批量删历史，而删掉的正是对比曲线与逐字复现要读的结果库明细。磁盘风险真实（§12.3：单轮 3.0 MB），但应由运维显式打开并配 N |
| 保留策略的时机 | **启动一次（恢复之后、调度器之前）+ 每轮落终态后一个游离任务** | 启动那次负责收存量；终态钩子**绝不 await**，免得拖住槽位释放路径；`retention_sweep` 形参默认 `None`，既有测试构造的调度器行为不变 |
| 保留策略的同步原语 | **不加锁、不引锁文件/心跳** | 单实例是硬前提（§12.11）；加锁只会把"多开了一个进程"从"删错"变成"看着有保护其实没有" |
