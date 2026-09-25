# job 工作目录契约

本文落定调度侧为每个回测 job 分配的工作目录布局与**启动契约**。
上游依据是 `QuantTrading` 的 [`docs/backtest-run-contract.md`](../../QuantTrading/docs/backtest-run-contract.md)
（该文定义引擎侧契约，本文只补调度侧如何构造目录与拉起宿主）。

本文全部结论已在 Phase 0 实测验证，记录见末节。

---

## 1. 目录布局

```text
runs/<RunId>/
├── BackTest.json             # 引擎配置（引擎硬编码从此读）
├── Sessions.json             # 会话表（SessionFile 指向）
├── TestStrategyGrid.json     # 策略配置，名由 manifest 决定
├── grid_strategy.py          # 策略入口，原样复制自策略库
├── stdout.txt / stderr.txt   # runner 捕获的宿主输出
├── result.json               # 契约产物（引擎写）
├── BackTest_<RunId>.db       # 契约产物（17 表，SQLite）
├── Dump/<RunId>/t_*.csv      # 契约产物（17 个 CSV）
└── log/<策略名>.<时间戳>.log  # 引擎日志
```

`RunId` 由调度侧生成并经 `BackTest.json` 的 `RunId` 注入，同时用作目录名。
两者必须同值——这是调度侧一次生成时的唯一约束。

---

## 2. 路径相对性

引擎的全部输入与产物路径都**相对 CWD 解析**，故写路径必须给相对值。

| 配置项 | 性质 | 取值 |
| ---- | ---- | ---- |
| `DbHost` | **写** | **必须相对** |
| `DumpPath` | **写** | **必须相对** |
| `MdDataPath` | 读 | 允许绝对 |
| `DbInitHost` | 读 | 允许绝对 |
| `SessionFile` | 读 | `./Sessions.json` |

> **警告**：把 `DbHost` 或 `DumpPath` 写成绝对路径，会让「每 job 独立工作目录」
> 的隔离**静默失效**——两个并发 job 会写同一个库且不报错。

---

## 3. 启动契约

### 3.1 `argv[0]` 必须是无路径分隔符的裸文件名

这是 Phase 0 挖出的**硬约束**，它不由回测引擎决定，而由 Spark 的
`Utility::ParseProcessName`（`Spark/src/Core/Utility/Utility.cpp:13`）决定：

```cpp
const char* temp = strrchr(fullProcessName, '\\');   // 只找反斜杠
const char* dot = strchr(temp, '.');                 // 首个点截断扩展名
```

Windows 分支**只承认反斜杠为目录分隔符**，随后按**首个点**截断扩展名，
所得串被用作日志文件名 `log/<processName_>.<时间戳>.log`。于是：

| `argv[0]` | 日志路径 | 结果 |
| ---- | ---- | ---- |
| `D:/.../grid_strategy.py` | `log/D:/...` | 含 `:`，`fopen` 失败 |
| `D:\...\grid_strategy.py` | `log/grid_strategy.*.log` | 侥幸可用 |
| `grid_strategy.py` | `log/grid_strategy.*.log` | 稳 |

**关键推论**：这与绝对/相对**无关**，只与分隔符有关。正斜杠相对路径
（如 `strategies/grid/entry.py`）同样会炸。唯一在两种约定下都安全的形态是
**裸文件名**。

日志器在启动期打不开日志文件即**直接终止进程**（退出码 1），
故这不是"日志缺失"级别的降级，而是**启动失败**。

**因此 runner 必须：**

1. 把策略入口文件**原样复制**到 job 目录根部（保留原文件名）。
2. 以 `cwd=<job 目录>` 启动，`argv[0]` 传**裸文件名**。

> **注意**：复制到 job 目录根部也意味着入口文件名不得与引擎配置文件
> （`BackTest.json`、`Sessions.json`、`result.json`）撞名。v1 策略均为 `.py`，无此风险。

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

> **警告**：`SqliteWrapper` 未设 `busy_timeout`，**绝不可在运行中读结果库**。
> 读已完成的库是安全的（只读 + SHARED 锁可共存）。完成信号恒为「进程退出」。

---

## 6. Phase 0 实测记录（2026-09-25）

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
