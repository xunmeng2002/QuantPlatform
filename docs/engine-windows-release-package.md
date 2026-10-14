# 引擎 Windows 发布包要求

本文是平台侧（QuantPlatform）对上游引擎包（QuantTrading）的**验收契约**：约定
Windows Release 包长什么样、怎么打、怎么自检。满足本文的包，平台侧只需把
`ENGINE_ROOT` 指向包内唯一目录即可挂载运行。

本文与 [`engine-linux-release-package.md`](engine-linux-release-package.md) **同骨架、同口径**，
差异只写在各平台真有差异的地方：**依赖解析机制**（§4）、**文件清单**（§2）、**自检条目**（§8）。
平台侧要「同一套代码对待两个包」，需按平台分派的只有三处：扩展模块后缀（§2.2）、文件数
（Windows 23 / Linux 22）、产物后缀（`.dll` / `.so`）。**包形态本身一致**——两侧都是「一个顶层
目录 + 全部文件平铺」，由共享模块 `release_zip.py` 钉死（§7）。

与 [`backtest-run-contract.md`](backtest-run-contract.md) 并列：那份约定**怎么跑**
（一次性进程 + 退出码 + `result.json` + 相对写入路径），本源约定**怎么打包**。

> **注意**：本文只描述**部署运行包**，不描述链接用的开发包。两者的区别见 §3。

## 1. 一句话契约

平台只认一个目录。`ENGINE_ROOT` 指向的那个目录里，必须同时有：

- Python 扩展模块（`QuantTrading.cp314-win_amd64.pyd`）；
- **全部**运行时 `.dll`（来自三个仓，见 §2）；
- `engine-version.txt`；
- 四个模板 json（引擎两个 + 两个应用各一）。

该目录之外，运行时只剩两层依赖，都由目标机提供：系统 API 集与 **MSVC 运行库**（§9）。

目录里**另有**平台用不到的件（六个 API 中间层、`MdOffer.exe` 与 `SimExchange.exe` 两个应用，
见 §2.1、§2.3）：平台只按名字取它认的那几项，多出来的文件不妨碍挂载。

## 2. 发布包目录结构

运行时 `.dll` **不来自单一仓**：引擎依赖 Spark 与 DbAdapters 的 `.dll`，靠 PE 导入表加载。
打包必须把三个仓的产物**收拢到同一个目录**：

```text
engine/
├── QuantTrading.cp314-win_amd64.pyd  ← 引擎
├── BackTest.dll                      ← 引擎
├── MdApi.dll  MdGbkApi.dll           ← 引擎：行情接入面
├── SimExchangeApi.dll  SimExchangeGbkApi.dll   ← 引擎：模拟撮合接入面
├── TraderApi.dll  TraderGbkApi.dll   ← 引擎：交易接入面
├── MdOffer.exe  MdOffer.json         ← 引擎：行情分发应用
├── SimExchange.exe  SimExchange.json ← 引擎：模拟撮合应用
├── Core.dll                          ← Spark
├── Network.dll                       ← Spark
├── Serialization.dll                 ← Spark
├── AsyncDbWriter.dll                 ← DbAdapters
├── DuckdbWrapper.dll                 ← DbAdapters
├── SqliteWrapper.dll                 ← DbAdapters
├── duckdb.dll                        ← DbAdapters 转发
├── sqlite3.dll                       ← 三方
├── engine-version.txt
├── BackTest.json
└── Sessions.json
```

23 项（扩展模块按实际名计 1 项）。

| 文件 | 归属 | 角色 |
| :--- | :--- | :--- |
| `QuantTrading.*.pyd` | 引擎 | Python 扩展模块，解释器 import 的入口 |
| `BackTest.dll` | 引擎 | 回测主库，扩展模块的直接依赖 |
| `MdApi.dll`、`MdGbkApi.dll` | 引擎 | 行情接入面（UTF-8 / GBK 各一套），见 §2.1 |
| `TraderApi.dll`、`TraderGbkApi.dll` | 引擎 | 交易接入面，同上 |
| `SimExchangeApi.dll`、`SimExchangeGbkApi.dll` | 引擎 | 模拟撮合接入面，同上 |
| `MdOffer.exe`、`MdOffer.json` | 引擎 | 行情分发应用与其配置，见 §2.3 |
| `SimExchange.exe`、`SimExchange.json` | 引擎 | 模拟撮合应用与其配置，同上 |
| `Core.dll` | Spark | 基础库，**多个 `.dll` 共用的直接依赖** |
| `Network.dll` | Spark | 网络，依赖 `Core.dll` |
| `Serialization.dll` | Spark | 序列化 |
| `AsyncDbWriter.dll` | DbAdapters | 异步落库，依赖 `Core.dll` |
| `DuckdbWrapper.dll` | DbAdapters | DuckDB 封装，依赖 `Core.dll` + `duckdb.dll` |
| `SqliteWrapper.dll` | DbAdapters | SQLite 封装，依赖 `Core.dll` + `sqlite3.dll` |
| `duckdb.dll` | 三方 | 由 `DuckdbWrapper.dll` 加载 |
| `sqlite3.dll` | 三方 | 由 `SqliteWrapper.dll` 加载，见 §4 |
| `engine-version.txt` | 引擎 | 版本标识，一行文本，见 §6 |
| `BackTest.json` | 引擎 | 引擎主配置模板 |
| `Sessions.json` | 引擎 | 会话模板，平台逐作业拷贝 |

> **警告**：数据库后端（`DuckdbWrapper` / `SqliteWrapper`）由 `BackTest.json` 的 `DbType` 决定
> 是否按名 `LoadLibrary` 加载，因此**不进 PE 的静态导入表**。清单必须「导入表 + 已知按名加载名单」
> 两者合并，只看导入表会漏掉它们。本包两者都带（§2.1 的裁定），故不影响清单。

### 2.1 六个 API 中间层：**进包**（2026-10-13 用户裁定改判）

`QuantTrading/bin/$<CONFIG>` 下的 6 个交易/行情 API 中间层：

```text
MdApi.dll  MdGbkApi.dll
SimExchangeApi.dll  SimExchangeGbkApi.dll
TraderApi.dll  TraderGbkApi.dll
```

**改判**：发布包不再只是「回测运行时闭包」，还要承载**外部程序接入面**——用户 2026-10-13：
「本仓的 API 肯定要随包，这个是供外部程序接入使用的」。故这 6 个库随包发运，理由与 Linux 侧
逐字相同（`engine-linux-release-package.md` §2.1）。要点：引擎源码树里**没有任何按名装载器**
（`src/`、`include/`、`test/` 全树 `LoadLibrary` / `GetProcAddress` 零命中），故这 6 个库
**没有任何自家代码会去加载**，它们是给**外部进程**链接的。

> **警告**：它们与运行时闭包同处一个目录（§5），§7 的取用就**必须**是白名单。
> 「取该目录下全部 `.dll`」会把 `MysqlWrapper.dll` / `MariadbWrapper.dll` 连同
> `mariadbcpp.dll` / `mysqlcppconnx-2-vs14.dll` / `libmariadb.dll` 一并收进来。

### 2.2 扩展模块后缀不写死

扩展模块的后缀随解释器而定，**不要硬编码**：

| 平台 | 后缀 |
| :--- | :--- |
| Windows | `.cp314-win_amd64.pyd` / `.pyd` |
| Linux | `.cpython-314-x86_64-linux-gnu.so` / `.abi3.so` / `.so` |

平台侧的判据与 import 机制同源，取 `importlib.machinery.EXTENSION_SUFFIXES`。
引擎侧打包时按实际产物命名即可，平台会按当前解释器的后缀表去认。

两个平台的次版本由 `CMakePresets.json` 的 `QUANTTRADING_PYTHON_VERSION` 钉死
（Windows `3.14`、WSL/Linux `3.14`），表内示例即现状产物；改版本时以 preset 为唯一来源。

### 2.3 两个应用与它们的配置（2026-10-13 起进包）

`MdOffer.exe`（行情分发）与 `SimExchange.exe`（模拟撮合）是**可执行文件**，各自读同目录同名的
`<应用名>.json` 模板。用户 2026-10-13：「`MdOffer`、`SimExchange` 可以先放进去，不过不是当前
主要使用的方向」——平台侧不碰它们（§1：平台只按名字取它认的那几项），它们服务于人工起停与外部联调。

| 应用 | 配置 | 加载期依赖 |
| :--- | :--- | :--- |
| `SimExchange.exe` | `SimExchange.json` | 包内自含：`MdApi.dll` + Spark 三件 + DbAdapters 的 `AsyncDbWriter.dll` |
| `MdOffer.exe` | `MdOffer.json` | 同上但**不含** `MdApi.dll`，**另加** CTP 的 `thostmduserapi_se.dll` |

`thostmduserapi_se.dll`（CTP 行情 SDK）**不随包**：第三方 SDK 的再分发责任不由本包背
（2026-10-13 就 Linux 侧定的同一条边界）。目标机自备——把该 DLL 放进发布目录即可，
PE 按同目录解析（§4），**Windows 侧本就没有 Linux 那条「绝对路径绕开搜索」的毛病**。

两平台口径因此对称：Windows 自备 `thostmduserapi_se.dll`，Linux 自备
`libthostmduserapi_se.so`（`engine-linux-release-package.md` §2.3）。

## 3. 只有一类产物进发布包

同一份源码会产出三类东西，**只有第三类**进发布包：

| 产物 | 内容 | 给谁用 | 进发布包 |
| :--- | :--- | :--- | :--- |
| 链接用 | `include/` 头文件、`lib/cmake/*.cmake` | 编译别的项目 | 否 |
| 调试用 | `*.pdb`、`*.lib` 导入库、`*.exp`、`*.ilk` | 符号调试 / 编译期链接 | 否 |
| **运行时** | `*.dll`、`*.exe`、扩展模块、json | PE 加载器与解释器 | **是** |

头文件、CMake 配置、导入库只服务于**编译期**；引擎一旦编出 Release 包，它们的使命就结束了。
部署目录里出现它们即为打包错误。

## 4. PE 没有 `RPATH`：依赖按「与镜像同目录」解析

这是两个平台**最本质的差异**，也是本包比 Linux 包多一项 `sqlite3.dll` 的成因。

ELF 可以在镜像里写一条 `RUNPATH`（`engine-linux-release-package.md` §4 的 `$ORIGIN`），
PE **没有对应物**——可执行文件与 DLL 没有任何内嵌的搜索路径字段。Windows 加载器的搜索顺序是：

1. 已加载模块的同名命中；
2. **发起加载的那个镜像所在目录**（可执行文件即其自身目录）；
3. 系统目录（`System32` 等）；
4. `PATH`。

发布包把这套规则变成了便宜：清单里全部文件**平铺在一个目录**（§7），而入口是包内的
`MdOffer.exe` / 解释器进程，于是「镜像所在目录」就是发布目录本身，包内 `.dll` 全部自动可见
——不需要任何环境变量、不需要 `LD_LIBRARY_PATH` 式的配置。

> **提示**：扩展模块（`.pyd`）由 CPython 以 `LOAD_WITH_ALTERED_SEARCH_PATH` 加载，于是搜索从
> **`.pyd` 自身所在目录**起算，而不是宿主解释器所在目录。包内全部文件平铺在一起，两者本就是同一
> 目录，故这一细节不改变任何结论；写出来是为了说明第 2 条在 `.pyd` 上为何照样成立。

代价是反向的：**任何非系统的 `.dll` 都必须进包**，没有「目标机系统里早有」这条退路。
故 Spark 三件、DbAdapters 四件、`duckdb.dll`、`sqlite3.dll` 全在清单里；Linux 侧同一批依赖
要么由系统提供、要么在构建期静态链入，那份清单只管 `libduckdb.so` 一个三方 `.so`。

> **注意**：第 2 条只覆盖**静态导入表**里的依赖。按名 `LoadLibrary("SqliteWrapper")` 这类裸名
> 加载走的是另一条路：Windows 对裸名同样先查**宿主进程可执行文件所在目录**（再是系统目录、
> `PATH`），故包目录里照样找得到——平台侧挂载时 `ENGINE_ROOT` 即发布目录本身，故成立。
> 这一条 Linux 侧同样有（那儿是 `dlopen` 与 `RUNPATH`）。

## 5. 产物已平铺在 `bin/<配置>`

Windows 侧**无需**任何额外处理：`CMakeLists.txt` 已把 Spark / DbAdapters 的 DLL 拷进
`bin/$<CONFIG>`（`CMAKE_RUNTIME_OUTPUT_DIRECTORY` 对 DLL 天然生效），发布脚本只有一个来源目录
`bin/<配置>`。对照 Linux 侧：那里要先把 `bin/` 与 `lib/` 拍平，且要分别去两个安装树取件
（`engine-linux-release-package.md` §5）。

## 6. `engine-version.txt`

平台用它标识「这一轮跑的是哪个引擎构建」（取首个非空行）。该文件由**配置期**的
`file(GENERATE)` 产出（`QuantTrading/CMakeLists.txt:29`），与 `project() VERSION` 同源，
故「改完不重跑 configure 就构建」看不到它。

**两平台字节一致（2026-10-14）**：写出时钉了 `NEWLINE_STYLE UNIX`，故同一版本号在 Windows 包
与 Linux 包里连行尾都相同（`1.0.0\n`，不是 `1.0.0\r\n`）。两侧读者都取「首个非空行 + 去空白」，
故此前无故障；钉死是为了不把这种一致性留给调用方的宽容。详见
`engine-linux-release-package.md` §6。

缺失时平台退回「扩展模块 + 运行时库」的内容摘要，仍能跑，但拿不到人可读版本号——Release 包
应避免走到这条兜底。

## 7. 打包脚本要求

打包脚本把**一个**来源目录（`bin/<配置>`，§5）的产物平铺进发布目录。取用必须是
**显式白名单**——不可写成「取该目录下全部 `.dll`」，理由见 §2.1 的警告：

| 来源 | 取用（白名单，逐条列名） |
| :--- | :--- |
| `QuantTrading/bin/$<CONFIG>` | 扩展模块 `QuantTrading.*`（后缀按 §2.2 认）、`BackTest.dll`、六个 API 中间层（§2.1）、`MdOffer.exe` 与 `SimExchange.exe` 两个应用（§2.3）、Spark 三件（`Core.dll` / `Network.dll` / `Serialization.dll`）、DbAdapters 五件（`AsyncDbWriter.dll` / `DuckdbWrapper.dll` / `SqliteWrapper.dll` / `duckdb.dll` / `sqlite3.dll`）、`engine-version.txt`、四个 json |

- 白名单共 **23 项**（扩展模块按实际名计 1 项），与 §2 的清单一一对应；
- 排除：`*.pdb`、`*.lib`、`*.exp`、`*.obj`、`*.ilk`、`include/`、`lib/cmake/`；
- 排除：`MariadbWrapper.dll`、`MysqlWrapper.dll`、`mariadbcpp.dll`、`mysqlcppconnx-2-vs14.dll`、
  `libmariadb.dll`（2026-10-13 用户裁定「数据库只需要 sqlite 和 duckdb」，故连「按需另定」的
  余地也不留）；
- 排除：测试程序（`Test*`）、`SimExchangeInit`、`BackTestInit.*`、跑测残留（`*.db`、`log/`、
  `Dump/`）——它们既不服务平台，也不服务接入面；
- 排除：CTP SDK 的 `thostmduserapi_se.dll`（再分发边界不背第三方 SDK；`MdOffer.exe` 由目标机
  自备即可，理由见 §2.3）；
- 输出：**一个目录**，无子目录（这是**发布目录**的形态，即下面 `--zip` 解压出来的那一层）；
- 白名单任一文件缺失即**非零退出并点名**，不得静默少文件；
- 版本号只从 `engine-version.txt` 读，脚本不得手写（见 §6）。

**归档形态（可选，`--zip`）**：把发布目录打成 `QuantTrading-<版本>-windows-x86_64.zip`，包内是
**一个**顶层目录（同名去 `.zip`），§2 清单的全部文件平铺其下。本条不改变上面「一个目录、无子目录」
的要求——zip 只是那一层目录的封装。归档一律在 §8 自检**全过之后**才产出。

> **引擎侧实现**：仓根 `package_windows_release.py`（`--config` / `--output` / `--zip` /
> `--force`）。zip 的创建与包内校验与 Linux 侧**共用同一份模块** `release_zip.py`（函数给 Python
> 调用方、命令行给 bash 调用方），故两侧包形态由同一段代码保证；条目顺序也在模块内统一排序，
> 与调用方无关。默认拒绝写非空输出目录，`--force` 只清它自己那个输出目录。

## 8. 引擎侧自检（全过才算合格）

```powershell
cd engine

# 1. 解释器能 import: 打印出的路径应在发布目录内
py -3.14 -c "import QuantTrading; print(QuantTrading.__file__)"

# 2. 版本文件在: 应打印一行版本号
type engine-version.txt

# 3. 包内容: 应只有 23 个文件、无子目录、无 *.pdb / *.lib
dir
```

判据三条，与脚本内嵌的检查一一对应：

| 条目 | 判据 | 不合格 |
| :--- | :--- | :--- |
| 包内容 | 禁项不在、无子目录、无调试产物、文件数恰为 23 | 报出并拦下交付 |
| 导入表覆盖 | 包内每个 `.dll` / `.exe` / `.pyd` 的**静态导入名**，若在构建目录 `bin/<配置>` 里存在，就必须已进包（`python314.dll` 与 `thostmduserapi_se.dll` 例外，见下） | 报出并拦下交付 |
| 解释器 `import` | `import QuantTrading` 成功，且 `QuantTrading.__file__` 落在发布目录内 | 报出并拦下交付 |

- 第 3 条是**决定性**的：它同时验证扩展模块、`BackTest.dll` 与它们的三方依赖在发布目录里
  真能加载起来（与 Linux 侧 §8 第 3 条同构）。
- 第 2 条只覆盖**静态导入表**，不含延迟导入（delay-import）与按名 `LoadLibrary`；后者的公开面
  很小且都在白名单里（§2.1、§2），故够用。
- `python314.dll` 与 `thostmduserapi_se.dll` 是**刻意不进包**的两项，自检认得它们、不误报：
  前者随宿主解释器进程已在内存里，包内再放一份无用且会在补丁版本不一致时被优先命中；后者是
  目标机自备的第三方 SDK（§2.3）。
- 第 2 条判不了系统层依赖（`KERNEL32.dll`、`api-ms-win-crt-*.dll`、MSVC 运行库等）：它们不在
  `bin/<配置>` 里，脚本按「构建目录里没有即跳过」处理。故这一层单列成**前提**（§9）。

### 8.1 为什么这里没有 Linux 那两条自检

Linux 侧 §8 有「每个 `.so` 必须含 `RUNPATH: $ORIGIN`」与「`DT_NEEDED` 不得出现绝对路径」
两条硬判据，**Windows 侧没有对应物**：

- PE 没有 `RPATH` / `RUNPATH` 字段（§4），不存在「该不该带 `$ORIGIN`」这一问；
- 依赖名在导入表里**只能是 DLL 名**，没有「把路径写进去」这种形态，故也不存在绝对路径
  `DT_NEEDED` 这一类跨机缺陷（2026-10-14 Linux 侧已用 `IMPORTED_NO_SONAME` 修掉的那一条，
  在 Windows 上从未存在，也不可能出现）。

## 9. 依赖来源与版本 + 目标机前提

运行时闭包**必须随包**（§2）：PE 加载器没有包外的退路（§4）。所以**文档不替代打包**：下面这张表
管的是「这一包是哪几个提交、哪几个版本编出来的」，用于复现与追责，不是「去哪儿装依赖」。

| 依赖 | 来源 | 版本锚点 |
| :--- | :--- | :--- |
| 引擎 QuantTrading | 仓 `xunmeng2002/QuantTrading` | `1.0.0`（取自 `engine-version.txt`）；发布构建源为本仓工作区 |
| Spark | 仓 `xunmeng2002/Spark`（`x64-windows` 安装树） | 与 Linux 侧同源，见 Linux 契约 §9 |
| DbAdapters | 仓 `xunmeng2002/DbAdapters`（`x64-windows` 安装树） | 同上 |
| duckdb / sqlite3 | vcpkg `x64-windows`（dll 随包）、预构建 duckdb 树 | 见 Linux 契约 §9 的版本表 |
| CTP SDK | 上期技术官方 SDK（**不随包**，理由见 §2.3） | `6.7.9` |

**目标机前提**（本包不含、也不该含）：

| 前提 | 内容 | 怎么满足 |
| :--- | :--- | :--- |
| MSVC 运行库 | `MSVCP140.dll`、`VCRUNTIME140.dll`、`VCRUNTIME140_1.dll` | 装 Visual C++ 2015-2022 可再发行包，或装过带它的程序 |
| 系统 API 集 | `KERNEL32.dll`、`api-ms-win-crt-*.dll` 等 | 随 Windows 提供 |
| Python 解释器 | CPython **3.14**（扩展模块按次版本 ABI 锁定） | 与包同次版本的 `python314.dll` 宿主 |

- C++ ABI 由**编译器与运行库**决定：这一包只保证与同族 MSVC 编出的 Spark / DbAdapters 互通。
  换编译器另编一份装进来**不会有任何报错**，只会在运行时出怪结果——故换版整包换。
- 扩展模块与解释器必须同为 CPython 3.14：`.pyd` 的 ABI 随次版本变，换 3.15 须重编（两个平台
  同一处钉死，见 §2.2）。
