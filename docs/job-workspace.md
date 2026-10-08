# job 工作目录契约

本文落定调度侧为每个回测 job 分配的工作目录布局与**启动契约**。
上游依据是 `QuantTrading` 的 [`docs/backtest-run-contract.md`](../../QuantTrading/docs/backtest-run-contract.md)
（该文定义引擎侧契约，本文只补调度侧如何构造目录与拉起宿主）。

本文全部结论已在 Phase 0 与 Phase 3 两轮真引擎实测中验证，记录见末节 §6。
两轮的**前提不同**（种子库在不在盘上），各自成表，不可互相覆盖。

---

## 1. 目录布局

```text
runs/<RunId>/
├── BackTest.json             # 引擎配置（引擎硬编码从此读）
├── Sessions.json             # 会话表（SessionFile 指向）
├── TestStrategyGrid.json     # 策略配置，文件名与内容取自上传的那一份
├── grid_strategy.py          # 策略入口，原样复制自策略库
├── BackTestInit.db           # 引擎种子库（DbInitHost 指向；**按轮现生成**，见 §1.1）
├── stdout.txt / stderr.txt   # runner 捕获的宿主输出
├── result.json               # 契约产物（引擎写）
├── BackTest_<RunId>.db       # 契约产物（17 表，SQLite）
├── Dump/<RunId>/t_*.csv      # 契约产物（17 个 CSV）
└── log/<策略名>.<时间戳>.log  # 引擎日志
```

`RunId` 由调度侧生成并经 `BackTest.json` 的 `RunId` 注入，同时用作目录名。
两者必须同值——这是调度侧一次生成时的唯一约束。

调度侧**构造**这个目录时写入五个输入（`BackTest.json` 渲染、`Sessions.json` 复制、
策略入口复制、策略配置渲染、**种子库生成**），并顺带建一个空的 `Dump/`。**`Dump/` 不必预建**：
P3 实测（2026-09-25，目录里只有那四个输入文件）引擎会自己建出 `Dump/` 与
`Dump/<RunId>/`。预建是便宜的冗余，不是前提。`result.json` **绝不在构造阶段出现**
——它的缺席是"本轮没走到收尾"的唯一信号。

### 1.1 种子库是这一轮现生成的第五个输入

`BackTestInit.db` 由调度侧在**起进程之前**从 catalog 现造（`reference_data/seed_database.py`），
与其余四个文件一起写进**暂存目录**、再经 `rename` 原子发布——读侧永远看不到半个作业。
它只含**该轮用到的合约**的行（费率的合约 / 品种 / 交易所三级规则在生成时被摊平），故不同轮
的这个文件内容不同，不可跨轮复用。任何一步失败都让**该轮失败**，不静默降级——降级的后果是
费用全 0 而回测照跑完。生成口径与三级语义见 `platform-plan.md` §14.2；该文件随目录一起被
删除 / 保留清理覆盖，**不需要单独的清理路径**。

**整个目录的生命周期**（P7 落定）：上面这棵树是**一次性的**——`DELETE /api/runs/{id}` 与保留
清理（`services/run_retention.py`）都是**整目录移除**，不是逐文件挑着删。于是有几件事不必各写
一遍：`log/` 里的引擎日志、`stdout.txt` / `stderr.txt`、`Dump/` 下的 17 个 CSV 与结果库随目录
一起消失，**平台不另做日志轮转**。顺序恒为"先目录、后行"：目录名就是 `RunId`，而记着它的只有
`Runs` 那一行，行先没了就再没有任何东西能找到这个目录（见 `platform-plan.md` §9 的 P7 段）。
反过来，**运行中的轮不属于任何一条清理路径**：删除要求终态，保留清理的候选集合里非终态行
永不入选——它们正被调度器或引擎握着。

---

## 2. 路径相对性

引擎的全部输入与产物路径都**相对 CWD 解析**，故写路径必须给相对值。

| 配置项 | 性质 | 取值 |
| ---- | ---- | ---- |
| `DbHost` | **写** | **必须相对** |
| `DumpPath` | **写** | **必须相对** |
| `MdDataPath` | 读（**job 目录之外**） | 允许绝对 |
| `DbInitHost` | 读（**job 目录之内**） | **必须相对** |
| `SessionFile` | 读 | `./Sessions.json` |

> **警告**：把 `DbHost` 或 `DumpPath` 写成绝对路径，会让「每 job 独立工作目录」
> 的隔离**静默失效**——两个并发 job 会写同一个库且不报错。

`DbInitHost` 与上一行的 `SessionFile` 同类：**读**，但读的文件就在作业目录里（种子库由
调度器按轮生成后**搬进作业目录**，见 §1.1）。故相对值恰好指对，而绝对路径是把"文件放哪"
这件事从调度侧复制一份到提交侧——两处各算一次迟早会分叉，症状是引擎读不到种子库而整轮照
跑完（费用三项恒为 0、合约乘数全部退化），`result.json` 仍报成功。2026-10-08 由绝对路径
改为常量 `./BackTestInit.db`（此前它是唯一一处被写成绝对的 job 目录内路径）。

**这条约束在渲染器里是结构性的**（P3，2026-09-25）：`render_engine_config()` 的形参
只有运行级字段与**唯一一个** job 目录之外的**读**路径（`market_data_path`），
写路径与 `DbInitHost` 在函数体内是常量 `./BackTest.db` / `./Dump` /
`./BackTestInit.db`。调用点**没有入口**能传入绝对路径，故"隔离静默失效"从"靠测试发现"
变成"写不出来"。判据落在
`tests/test_engine_config.py::test_the_renderer_takes_no_write_path_parameter`。

**派生行为**：`DbHost` 恒写 `./BackTest.db`，引擎自己派生成
`BackTest_<RunId>.db`（已实测）。平台若先拼一份 RunId 进去，会得到
`BackTest_<RunId>_<RunId>.db`——不报错，只是与 `result.json.DbPath` 不符。

**策略配置文件里的运行级字段**：策略配置（`TestStrategyGrid.json` 一类）除策略自己的
参数之外还要拿到 `exchange_id` / `instrument_id` / `bar_period` 三个字段，键名**固定**为
`ExchangeId` / `InstrumentId` / `BarPreces`（`app/strategy_configuration.py` 的三个常量），
由平台覆写——模板里没有就新增，有就覆写。策略作者那一份说明见
[`strategy-configuration.md`](strategy-configuration.md)。

`bar_period` **两处都有，而两处不是同一件事**：`BackTest.json` 的 `BarPreces` 是**落盘
精度**，恒为平台常量 `5m`，只决定读哪一族 parquet；策略配置那份是策略的**订阅周期**
（`declare_bar_period` 的期望周期），是用户在提交页选的。详见 `platform-plan.md` §7.3。

**两者不一致时不是"静默 0 成交"**（这是本节原稿的说法，2026-10-01 订正）：引擎按
`BackTest.json.BarPreces` 过滤，磁盘上没有那一族时一行都读不到，它在**装载期**就判
`ErrorMarketDataNotExist` 拒掉整轮——报错收场，那一轮白跑。而平台上这条走不到：订阅周期
只给清单内的值（5m 的整数倍），聚合不出来的目标在提交页就挡住了。

---

## 3. 启动契约

### 3.1 `argv[0]` 传裸文件名（**不是硬约束**，是固定选择）

> **2026-09-25 订正**：本节原记「带路径的 `argv[0]` 会在启动期被日志器 `fopen`
> 失败打死（退出码 1）」，**该结论已被实测推翻**，原文与订正理由见
> `PROGRESS-archive.md` 的 `D.01`。订正后的实测事实如下。

在 P0 所用引擎构建（`QuantTrading.cp314-win_amd64.pyd`）上复测三种形态，
各自跑完**整轮 Bar 回测**、退出码均为 `0`：

| `argv[0]` | 实测结果 |
| ---- | ---- |
| `grid_strategy.py`（裸文件名） | 退出码 0，全轮通过 |
| `./grid_strategy.py`（正斜杠相对路径） | 退出码 0，全轮通过 |
| `C:\...\Temp\<job>\grid_strategy.py`（反斜杠绝对路径） | 退出码 0，全轮通过 |

即：**日志器并没有因为拿到路径而打死进程**。`Utility::ParseProcessName`
（`Spark/src/Core/Utility/Utility.cpp:13`）仍只以 `strrchr(..., '\\')` 找分隔符、
再按首个点截断扩展名，但这条拼出来的日志名显然不是启动期的硬闸。

**平台仍固定用裸文件名**，理由是它与其余契约自洽，而不是"否则起不来"：

1. 入口文件**原样复制**到 job 目录根部（保留原文件名），`cwd=<job 目录>`。
   策略读自己的配置文件（`TestStrategyGrid.json`）、引擎写全部产物，都相对 CWD
   解析——入口在 job 根让这三件事落在同一处。
2. 传给 `create_subprocess_exec` 的是 `sys.executable` + **裸文件名**
   （Windows 的 `CreateProcess` 不认文件关联，直接拿 `.py` 当可执行文件会得到
   `WinError 2`，已实测），配 `cwd=` 解析。

> **注意**：复制到 job 目录根部也意味着入口文件名不得与引擎配置文件
> （`BackTest.json`、`Sessions.json`、`result.json`）撞名。v1 策略均为 `.py`，无此风险。

> **对后人的提醒**：这条订正的意思是"多一种可行形式不构成改设计的理由"，
> 不是"可以随便传绝对路径"。契约的其余部分（相对写路径、`PYTHONPATH`、
> 目录隔离）都建立在上面的形态上；要改形态，得重新走一遍真引擎验收。

### 3.2 `PYTHONPATH` 必须指向引擎根

策略被复制到 job 目录后，其 `__file__` 指向 job 目录，
凡依赖 `__file__` 反推 `bin/Release` 的写法都会算出**错误路径**。
故必须显式给出引擎根：

```text
cd runs/<RunId>
set PYTHONPATH=D:\Gitee\QuantTrading\bin\Release
python grid_strategy.py
```

`PYTHONPATH` 单独一条即可满足 `import QuantTrading` 及 `.pyd` 的同目录依赖
（`BackTest.dll`、`Core.dll`、`Network.dll` 等）——CPython 在 Windows 以
`LOAD_WITH_ALTERED_SEARCH_PATH` 加载扩展模块，依赖随之解析。Phase 0 已实测。

**注意**：`MysqlWrapper` / `MariadbWrapper` 两项已改为**按配置 `DbType` 运行时装载**，
不在 `.pyd` 的导入表里，故引擎包可以不发运它们；只有 `DbType` 取 `2` / `3` 时才需要
把对应模块摆在**引擎根目录**下。`SqliteWrapper` / `DuckdbWrapper` 仍是硬依赖，缺一不可。

**取不到适配器时（模块缺失，或 `DbType` 是个不认识的取值）引擎的收场方式**：写一条点名
`DbType`、并列明全部合法取值的 ERROR，然后交出**空适配器**，由引擎既有的判空通路接管 ——
`SimExchange::Init()` 返回失败，宿主以 `ExitCodeHostInitFailed`（实测 `1`）收场。
**不是**进程终止，也**不是**抛异常：适配器是在 `SimExchange` 构造函数里建的，抛出点不在宿主的
`try` 作用域内，异常会以 `std::terminate` 收场；而 Windows 上的 `abort` 既不 flush stdio 缓冲、
也不走日志器线程的 `ThreadExit`（日志器是后台线程 + 缓冲，落盘在 `ThreadExit`），
**写在抛出前的那条日志会随进程一起消失**（实测退出码 `0xC0000409`、日志 0 字节）。
故这里只剩「日志 + 失败退出」一条路，本节 Python 宿主与四个 `.exe` 宿主行为一致。

---

## 4. 退出码

| 码 | 含义 |
| ---- | ---- |
| `0` | 成功 |
| `1` | 宿主启动失败 |
| `2` | 结果文件缺失或不可解析 |
| `3` | 引擎报告失败 |

策略宿主以 `sys.exit(main())` 返回该码，runner 直接取进程退出码。

> **警告**：MSVC 下 `abort()` 与未捕获异常同样返回 3，与「引擎报告失败」同码。
> 消歧只能靠文件：码 3 **且**存在可解析的 `result.json` 即引擎报告失败；
> 码 3 且无文件即崩溃。

---

## 5. 产物

| 产物 | 路径 | 用途 |
| ---- | ---- | ---- |
| 指标 | `<job>/result.json` | 落 catalog 的镜像列 |
| 结果库 | `<job>/BackTest_<RunId>.db` | 明细查询（只读连接） |
| 明细 CSV | `<job>/Dump/<RunId>/t_*.csv` | 打包下载 |
| 日志 | `<job>/log/<策略名>.*.log` | 排障 |
| 输出 | `<job>/stdout.txt`、`stderr.txt` | 排障 |

上表五类产物**共享同一个生命周期**：它们都在 `<job>/` 这一棵目录里，而删除与保留清理都是
**整目录移除**（见 §1），故"日志轮转"在这一层没有落点——单轮的引擎日志随该轮一起消失。唯一的
例外是**表本身还在**：指标已镜像进 `Runs` 的列（目录清掉之后列表与 `/compare` 照常显示），
结果库与 CSV 则只有目录还在时才读得到。

> **警告**：`SqliteWrapper` 未设 `busy_timeout`，**绝不可在运行中读结果库**。
> 读已完成的库是安全的（只读 + SHARED 锁可共存）。完成信号恒为「进程退出」。

---

## 6. 实测记录

> ⚠️ **§6.1 / §6.2 / §6.3 三份基线都是「平台尚未产种子库」时代的口径，待本次验收重取**
> （2026-10-07）：此后种子库由 QuantPlatform **按轮现生成**到作业目录里（见
> [`platform-plan.md`](platform-plan.md) §14.2 与本文 §1.1），故"种子库在不在盘上"不再是
> 碰运气，而是**每一轮都有、且内容随该轮合约而定**。三份基线的**输入前提因此全变了**：
> §6.1 那份带种子库的数字是手工 `makeseeddb.py` 造的旧库、且是**绝对步长**口径；
> §6.2 / §6.3 两份是"库里没有那个文件"口径。**重取之前，三份数字只可用于对照趋势，不可与
> 新产物混算。** 重取后的落点见 `acceptance-checklist.md` §16。

### 6.1 Phase 0（2026-09-25 上午，种子库 `BackTestInit.db` **在盘上**）

**方法**：在 `runs/probe/` 按本文契约构造工作目录，以裸文件名启动
`QuantTrading` 仓内的 `grid_strategy.py`（**未做任何修改**）。

**结果**：退出码 `0`，`result.json` 的 `Success=true`、`ErrorId=0`、
`RunId='probe'`，且下列七项与引擎自有机工作目录（`bin/Release`）下的
基准轮**逐位一致**：

| 指标 | 基准与实测 |
| ---- | ---- |
| `BarMarketDataCount` | 2928 |
| `OrderCount` | 654 |
| `TradeCount` | 84 |
| `Balance` | 998951.4506464996 |
| `TotalCommission` | 420.0 |
| `TotalStampTax` | 4.297395 |
| `TotalTransferFee` | 1.3419585 |

其余产物：`Dump/probe/` 下 17 个 CSV、`BackTest_probe.db`、
`log/grid_strategy.20260925-154847.log`。**单轮总体积 3.0 MB。**

**结论**：硬钉子 ② 已解——隔离到独立工作目录不改变回测结果，
且 `RunId` 配置注入生效。基准取自 `QuantTrading/bin/Release/result.json`
（Python 宿主补齐 `on_bar` 之后的一轮）。

### 6.2 Phase 3（2026-09-25 晚，种子库 `BackTestInit.db` **不在盘上**）

**方法**：不再手工构造目录，改走**完整链路**——策略经上传接口落盘、作业目录由
`app/scheduler/workspace.py` 构造、引擎由 runner 启动、结果由收尾镜像进库。
用例见 `backend/tests/test_real_engine_acceptance.py`（标 `real_engine`，默认不跑）。

**与 6.1 的已知差异只有一处前提**：本机 `bin/Release` 下**没有** `BackTestInit.db`，
而引擎对它的缺失是**优雅降级**（`SimExchange.cpp` 只做 `exists` 检查，缺失仅
Warning + `BasicDataLoaded=false`，继续跑完）。故费用三项退化成 0、余额因此
**高出**费用的总和：

| 指标 | 6.2 实测（无种子库） |
| ---- | ---- |
| `BarMarketDataCount` | 2928（与 6.1 同） |
| `OrderCount` | 654（与 6.1 同） |
| `TradeCount` | 84（与 6.1 同） |
| `Balance` | **999377.0899999999** |
| `TotalCommission` / `TotalStampTax` / `TotalTransferFee` | 0.0 / 0.0 / 0.0 |
| `CommissionMissingCount` | 84 |
| `BasicDataLoaded` | `false` |
| `DbPath` | `./BackTest_<RunId>.db` |

两份基线的差额 `999377.0899999999 − 998951.4506464996 = 425.6393535003`，与
6.1 费用三项之和 `420.0 + 4.297395 + 1.3419585 = 425.6393535` 相符（末位差异来自
浮点求和次序）。**故引擎行为未变，变的只是输入**——两份记录并存，各自注明前提，
不要用新数字覆盖旧基线。

> 本节（含 §6.1）的数字都在 **`GridStep` 还是绝对价格（`10.0`）**时测得。
> 2026-09-26 步长改为比例后，量本身变了，基线已按 §6.3 重取；此处保留原文，
> 因为「费用三项 = 两份基线的差」这个判据正是从这一对数字里读出来的。

另外三条本轮实测：

- **引擎比预期快得多**：三个月的 5m 回测约 **1.7 秒**；2010–2024（58176 根 bar）约 **3.8 秒**。
  故"制造一个跑得够久的作业"只能靠压时限，不能靠拉长时间范围。
- **`Dump/` 父目录不必预建**（见 §1）。
- **`DbHost` 派生**：写入 `./BackTest.db`，引擎落盘为 `BackTest_<RunId>.db`，
  与 `result.json.DbPath` 逐字相符（见 §2）。

**结论**：P2「上传后能跑通」与 P2b「被授权人可跑」至此结清；§2 的隔离与 §3.2 的
`PYTHONPATH` 在真引擎上复验通过。

### 6.3 重取基线（2026-09-26，步长由绝对价格改为比例）

**为什么重取**：`GridStep` 原本是**绝对价格**（旧值 `10.0`），用户实测它在低价标的上
完全失效——`SZSE/000001`（锚价 11.94）的每一档都远在市价之外，一轮下来 **610 笔委托、
0 笔成交**；同一份参数在 `SSE/600519`（约 1558）上则是 0.64% 的间距，照常成交。
策略已改为**比例**（`0.01 = 1%`，档位价 `锚价 × (1 ∓ 步长 × 档号)`，平仓价
`开仓成交价 × (1 ± 步长)`），故 §6.1 / §6.2 两批基线所用的输入已不存在，基线必须重取。

**方法**：与 §6.2 同一套完整链路（`pytest -m real_engine`），只换 `GridStep` 为 `0.01`。
本轮产物在 `backend/_acc_tmp/ratio-20260926/`。

| 指标 | 6.3 实测（比例 0.01，无种子库） | 与 6.2 的差异 |
| ---- | ---- | ---- |
| `BarMarketDataCount` | 2928 | 不变（只由行情范围决定） |
| `OrderCount` | 629 | 654 → 629 |
| `TradeCount` | 34 | 84 → 34 |
| `Balance` | **999257.8562340003** | 999377.0899999999 → 本轮值 |
| `CommissionMissingCount` | 34 | 等于成交笔数 |
| `Capital` 行数 | 62（首行种子 `1000000.0`、末行 `20241231`） | 不变 |
| `Trade` / `Order` / `Position` / `PositionDetail` | 34 / 629 / 116 / 282 | `PositionDetail` 290 → 282 |

成交变少是**预期**：旧值在 `600519` 上等于 0.64% 的间距，新值 1% 更宽。同一批输入下
**C++ 孪生**（`TestStrategyGrid.exe`，`bin/Debug`）跑出 `OrderCount=629`、
`TradeCount=34`、`Balance=999257.8562340003`，与 Python 宿主**逐位相同**。

⚠️ §6.1 那个带种子库的余额 `998951.4506464996` 是**旧口径**下的值（本机种子库不在盘上，
无法重取），它与 6.3 的余额不可相减——「费用三项 = 两份基线的差」这个判据在旧口径下成立
（差 `425.6393535003`）。重建种子库后，带种子库的那一份必须按比例步长重测。
