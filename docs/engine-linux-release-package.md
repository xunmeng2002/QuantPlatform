# 引擎 Linux 发布包要求

本文是平台侧（QuantPlatform）对上游引擎包（QuantTrading）的**验收契约**：约定
Linux Release 包长什么样、怎么打、怎么自检。满足本文的包，平台侧只需把
`ENGINE_ROOT` 指向包内唯一目录即可挂载运行。

本文**只管 Linux**。Windows 包的对应契约见
[`engine-windows-release-package.md`](engine-windows-release-package.md)：两份文档同骨架、
同口径，差异只写在各平台真有差异的地方（依赖解析机制、文件清单、自检条目）。

与 [`backtest-run-contract.md`](backtest-run-contract.md) 并列：那份约定**怎么跑**
（一次性进程 + 退出码 + `result.json` + 相对写入路径），本源约定**怎么打包**。

> **注意**：本文只描述**部署运行包**，不描述链接用的开发包。两者的区别见 §3。

## 1. 一句话契约

平台只认一个目录。`ENGINE_ROOT` 指向的那个目录里，必须同时有：

- Python 扩展模块（`QuantTrading.*.so`）；
- **全部**运行时 `.so`（来自三个仓，见 §2）；
- `engine-version.txt`；
- 四个模板 json（引擎的 `BackTest.json`、`Sessions.json`，两个应用各一，见 §2）。

该目录之外，运行时不再有任何依赖。

目录里**另有**平台用不到的件（六个 API 中间层、`MdOffer` 与 `SimExchange` 两个应用，见 §2.1、
§2.3）：平台只按名字取它认的那几项，多出来的文件不妨碍挂载。

## 2. 发布包目录结构

运行时 `.so` **不来自单一仓**：引擎依赖 Spark 与 DbAdapters 的 `.so`，靠
`DT_NEEDED` 动态加载。打包必须把三个仓的产物**收拢到同一个目录**：

```text
engine/
├── QuantTrading.cpython-314-x86_64-linux-gnu.so   ← 引擎
├── libBackTest.so                                  ← 引擎
├── libMdApi.so  libMdGbkApi.so                     ← 引擎：行情接入面
├── libSimExchangeApi.so  libSimExchangeGbkApi.so   ← 引擎：模拟撮合接入面
├── libTraderApi.so  libTraderGbkApi.so             ← 引擎：交易接入面
├── MdOffer  MdOffer.json                           ← 引擎：行情分发应用
├── SimExchange  SimExchange.json                   ← 引擎：模拟撮合应用
├── libCore.so                                      ← Spark
├── libNetwork.so                                   ← Spark
├── libSerialization.so                             ← Spark
├── libAsyncDbWriter.so                             ← DbAdapters
├── libDuckdbWrapper.so                             ← DbAdapters
├── libSqliteWrapper.so                             ← DbAdapters
├── libduckdb.so                                    ← DbAdapters 转发
├── engine-version.txt
├── BackTest.json
└── Sessions.json
```

22 项（扩展模块按实际名计 1 项）。

| 文件 | 归属 | 角色 |
| :--- | :--- | :--- |
| `QuantTrading.*.so` | 引擎 | Python 扩展模块，解释器 import 的入口 |
| `libBackTest.so` | 引擎 | 回测主库，扩展模块的直接依赖 |
| `libMdApi.so`、`libMdGbkApi.so` | 引擎 | 行情接入面（UTF-8 / GBK 各一套），见 §2.1 |
| `libTraderApi.so`、`libTraderGbkApi.so` | 引擎 | 交易接入面，同上 |
| `libSimExchangeApi.so`、`libSimExchangeGbkApi.so` | 引擎 | 模拟撮合接入面，同上 |
| `MdOffer`、`MdOffer.json` | 引擎 | 行情分发应用与其配置，见 §2.3 |
| `SimExchange`、`SimExchange.json` | 引擎 | 模拟撮合应用与其配置，同上 |
| `libCore.so` | Spark | 基础库，**多个 `.so` 共用的直接依赖** |
| `libNetwork.so` | Spark | 网络，依赖 `libCore.so` |
| `libSerialization.so` | Spark | 序列化 |
| `libAsyncDbWriter.so` | DbAdapters | 异步落库，依赖 `libCore.so` |
| `libDuckdbWrapper.so` | DbAdapters | DuckDB 封装，依赖 `libCore.so` + `libduckdb.so` |
| `libSqliteWrapper.so` | DbAdapters | SQLite 封装，依赖 `libCore.so`，**按名 `dlopen`** |
| `libduckdb.so` | 三方 | 唯一非自家库，由 `libDuckdbWrapper.so` 加载 |
| `engine-version.txt` | 引擎 | 版本标识，一行文本，见 §6 |
| `BackTest.json` | 引擎 | 引擎主配置模板 |
| `Sessions.json` | 引擎 | 会话模板，平台逐作业拷贝 |

> **警告**：`libSqliteWrapper.so` 由 `BackTest.json` 的 `DbType` 决定是否
> 按名字 `dlopen` 加载，因此**不会出现在 `ldd` 的输出里**。清单必须
> 「`ldd` 结果 + 已知 dlopen 名单」两者合并，只看 `ldd` 会漏掉它。

### 2.1 六个 API 中间层：**进包**（2026-10-13 用户裁定改判）

`QuantTrading/bin/$<CONFIG>` 下的 6 个交易/行情 API 中间层：

```text
libMdApi.so  libMdGbkApi.so
libSimExchangeApi.so  libSimExchangeGbkApi.so
libTraderApi.so  libTraderGbkApi.so
```

**改判**：发布包不再只是「回测运行时闭包」，还要承载**外部程序接入面**——用户 2026-10-13：
「本仓的 API 肯定要随包，这个是供外部程序接入使用的」。故这 6 个库随包发运。
2026-10-09 的实测结论**没有作废，作废的是它的适用面**：

- 「不在回测闭包」至今成立：`libBackTest.so` 与扩展模块的链接行都不含这 6 个库，它们是
  **应用**的构建期 `DT_NEEDED`（2026-10-13 实测：`SimExchange` 只拉 `libMdApi.so`，
  `MdOffer` 一个都不拉，三个 `Test*Api` 应用各拉一套）。原结论是「回测包不含它们」，
  现在包的范围主动放大到「回测闭包 + 接入面 + 两个应用」，故结论由「排除」改为「收进」。
- 引擎源码树里**没有任何按名装载器**——`src/`、`include/`、`test/` 全树
  `dlopen` / `dlsym` / `LoadLibrary` / `GetProcAddress` **零命中**。本仓唯一可达的
  按名装载是 DbAdapters 的 `LoadDatabaseBackend`，它按 `DbType` 只拼得出
  `DuckdbWrapper` / `SqliteWrapper` / `MysqlWrapper` / `MariadbWrapper` 四个
  **数据库后端**名，没有分支能产出 `MdApi` / `TraderApi` / `SimExchangeApi`。
  故这 6 个库**没有任何自家代码会去加载**：它们是给**外部进程**链接的（含 C ABI 变体）。

> **警告**：它们与运行时闭包同处一个目录（§5 拍平的后果，现在正是如此），§7 的取用就
> **必须**是白名单。两种扫描都不行：「取该目录下全部 `.so`」会把
> `libMysqlWrapper.so` / `libMariadbWrapper.so` 一并收进来，而排掉一个库同样需要点名。

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

`MdOffer`（行情分发）与 `SimExchange`（模拟撮合）是**可执行文件**，各自读同目录同名的
`<应用名>.json` 模板。用户 2026-10-13：「`MdOffer`、`SimExchange` 可以先放进去，不过不是当前
主要使用的方向」——平台侧不碰它们（§1：平台只按名字取它认的那几项），它们服务于人工起停与外部联调。

| 应用 | 配置 | 加载期依赖（2026-10-13 实测） |
| :--- | :--- | :--- |
| `SimExchange` | `SimExchange.json` | 包内自含：`libMdApi.so` + Spark 三件 + DbAdapters 的 `libAsyncDbWriter.so` |
| `MdOffer` | `MdOffer.json` | 同上但**不含** `libMdApi.so`，**另加** CTP 的 `libthostmduserapi_se.so` |

`MdOffer` 那个 CTP 依赖**不随包**，由目标机自备：把 `libthostmduserapi_se.so` 放进发布目录、
或让它出现在 `LD_LIBRARY_PATH` 里，`MdOffer` 即可启动。两平台口径一致——Windows 侧
`MdOffer.exe` 同样由目标机自备 `thostmduserapi_se.dll`（`engine-windows-release-package.md` §2.3）。

> **2026-10-14：修掉一条真缺陷（原记作「已知缺陷」）**。CTP 的导入库没有 SONAME，CMake 对
> imported SHARED 库默认按**完整路径**链接，`ld` 于是把 `/home/<构建机用户>/.vs/Libs/Ctp/…`
> 写进了 `DT_NEEDED`。带斜杠的 `DT_NEEDED` 由 `ld.so` 按该路径**直接**加载、**不走 `RUNPATH`**，
> 后果是：同机照跑、**换机必挂**，且往包目录里放一份同名库也救不回来（绝对路径优先，
> `$ORIGIN` 轮不到）。修法是给 CTP 的两个导入目标置 `IMPORTED_NO_SONAME`
> （`CMakeLists.txt` 的 Linux 分支），让链接改走 `-l` 形式，`DT_NEEDED` 落成**裸名**；此后按
> 上一段的「目标机自备」即能起。该属性由 `CMAKE_PLATFORM_USES_PATH_WHEN_NO_SONAME` 驱动，而它
> 只由 `Modules/Platform/Linux.cmake` 设定，故**在 Windows 上不可达**，Windows 产物零影响。

本包**不复制** CTP 的两个 `.so`（`libthostmduserapi_se.so` 5.0 MB、`libthosttraderapi_se.so`
5.6 MB）：2026-10-13 定的再分发边界不变——第三方 SDK 的责任不由本包背。故 §8 的自检把这两个
名字写进 `NotPackagedDependencyNames` 显式放行，**未列名的包外依赖一律不合格**。

`SimExchangeInit`（模拟撮合初始化工具，同样依赖 CTP 交易 API）与 `BackTestInit.*`
暂不进包；需要时按本节口径补名进白名单即可。

## 3. 只有一类产物进发布包

同一份源码会产出三类东西，**只有第三类**进发布包：

| 产物 | 内容 | 给谁用 | 进发布包 |
| :--- | :--- | :--- | :--- |
| 链接用 | `include/` 头文件、`lib/cmake/*.cmake` | 编译别的项目 | 否 |
| 调试用 | `*.pdb`（Windows）、`*.a` 静态库 | 符号调试 / 静态链接 | 否 |
| **运行时** | `*.so`、扩展模块、json | `ld.so` 与解释器 | **是** |

头文件、CMake 配置、静态库只服务于**编译期**；引擎一旦编出 Release 包，
它们的使命就结束了。部署目录里出现它们即为打包错误。

## 4. 每个 `.so` 必须自带 `RUNPATH: $ORIGIN`

### 4.1 为什么必须逐个设

`RUNPATH` **不传递**。`ld.so` 解析某个对象的 `DT_NEEDED` 时，只用
**发起加载的那个对象自己**的 `RUNPATH`，不继承调用者的：

| 对象 | 它的直接依赖 | 用谁的 `RUNPATH` 找 |
| :--- | :--- | :--- |
| 扩展模块 | `libBackTest.so`, `libCore.so` | 扩展模块自己的 |
| `libBackTest.so` | `libCore`, `libNetwork`, `libSerialization`… | **`libBackTest.so` 自己的** |
| `libNetwork.so` | `libCore.so` | **`libNetwork.so` 自己的** |

所以「扩展模块带了 `$ORIGIN`」帮不到 `libBackTest.so` 去找它的兄弟。
**每一个有非系统依赖的 `.so` 都必须自带 `RUNPATH: $ORIGIN`**。

### 4.2 怎么设（一处，全体生效）

`CMakeCommon.cmake` 顶部已加入家规，**两条并用**：

```cmake
set(CMAKE_BUILD_RPATH_USE_ORIGIN ON)
if(UNIX AND NOT APPLE)
    add_link_options(-Wl,-rpath,$ORIGIN)
endif()
```

- 只有第二条对本仓产物有效。`CMAKE_BUILD_RPATH_USE_ORIGIN` 只把
  **CMake 二进制目录（`out/build/...`）之内**的库换算成 `$ORIGIN` 相对形式；
  本仓把产物输出到 `${CMAKE_SOURCE_DIR}/bin/...`（二进制目录之外），三方库又来自
  `${CMAKE_SOURCE_DIR}/../Libs` 安装树，两处都在其管辖之外。**2026-10-09 实测**：
  一轮完整 Release 构建后 8 个产物 RUNPATH 全是绝对路径，`$ORIGIN` 一个都没有。
- 第二条显式追加一个 `$ORIGIN` 条目，与 CMake 自动算出的构建机绝对路径
  **并存**（实测值形如 `[$ORIGIN:/home/<用户>/...]`）。取舍：构建树内照旧可直接运行
  （`bin/Release` 下的宿主与 `import` 都不需要 `LD_LIBRARY_PATH`），发布包在目标机上
  靠 `$ORIGIN` 解析同目录的兄弟库，那些绝对项不存在时被 `ld.so` 跳过。

> **注意**：`RUNPATH` 是链接期写进 ELF 的字段，**必须重新链接**才生效
> （`.o` 复用；2026-10-09 实测一次「只重链接、无重编译」的构建即拿到新 RUNPATH）。三个产品仓都在各自
> `CMakeLists.txt` 顶部 `include(submodules/CMakeCommon/CMakeCommon.cmake)`，
> 故第二条对本仓所有 target 一体生效（第一条只管二进制目录内的产物）。版本要求
> `CMake ≥ 3.13`（`add_link_options`），
> 三家现状 3.20 / 3.25 满足。

### 4.3 怎么验

```shell
cd engine
for so in *.so; do
    printf '%-40s' "$so"
    readelf -d "$so" | grep -o 'RPATH.*\|RUNPATH.*' || echo '  (无 RPATH/RUNPATH)'
done
```

判据按产物归属分档（2026-10-09 裁定）：

| 产物 | 判据 | 缺 `$ORIGIN` 时 |
| :--- | :--- | :--- |
| 引擎自己的产物（扩展模块、`libBackTest.so`、六个 API 中间层、两个应用） | **必须**含 `$ORIGIN` | 不合格 |
| Spark / DbAdapters 安装树来的 ship 库 | 暂记**警告**（见 §4.4） | 不阻断 |

- 判「含」不判「恰为」：绝对项按 §4.2 的取舍保留，`$ORIGIN` 在最前即可。
- **2026-10-09 实测**（补家规并重链接后）：引擎 8 个产物的首个条目均为 `$ORIGIN`；
  7 个 ship 库中只有 `libDuckdbWrapper.so` 带 `$ORIGIN`，其余 6 个**无任何** `RPATH/RUNPATH`。
- **2026-10-13 实测**（22 项重打后）：引擎侧 10 个产物（原 8 个 + `MdOffer`、`SimExchange`）
  首个条目均为 `$ORIGIN`；7 个 ship 库中 **6 个**已带上 `$ORIGIN`，只剩 `libduckdb.so` 无
  `RPATH/RUNPATH`——它只依赖系统库，单独 `ldd` 无缺失，故不构成问题。
- **附带缺陷（实测确认）**：RUNPATH **末尾带一个空元素**（形如 `.../Spark/x64-linux/lib:`）
  的产物，2026-10-09 是 1 项，2026-10-13 是 **7 项**（`libBackTest.so` + 六个 API 中间层，
  随家规新增的产物把安装树导出的尾随冒号一并继承了下来）。glibc 把空元素当作**当前工作
  目录**搜索——2026-10-09 用构造 `.so` 实测：依赖只放在 cwd 时能解析，换一个 cwd 立刻
  `not found`。来源不在本仓（全树 `rpath` / `link_directories` 零命中），是 Libs 安装树
  导出的链接片段带尾随冒号；修它要动 Spark / DbAdapters 的导出配置，不在本批范围。实际影响
  有限：包内 `DT_NEEDED` 的库都能先被 `$ORIGIN` 解析到，空元素轮不到；但 `dlopen` 裸名加载
  （§2 的 `libSqliteWrapper.so`）会把作业的工作目录纳入搜索范围。

### 4.4 谁必须重建（实测，2026-10-09；2026-10-13 复核）

| 产物 | 绝对 `RUNPATH` | `$ORIGIN` | 结论 |
| :--- | :--- | :--- | :--- |
| 引擎 10 个产物（2026-10-13 起含两个应用） | 有（指向构建机 `Libs/...`） | 补家规前**无**，补后**有** | 必须重新链接（2026-10-09 已完成） |
| Spark / DbAdapters 的 ship 库 | 无 | 2026-10-09：6 个无、1 个有；**2026-10-13：6 个有、1 个无** | 无需重打 tag（家规已随各自下次构建生效） |

Spark 与 DbAdapters 的 ship 库**无需重建即可工作**，但原因是两个巧合，
而非它们更"干净"：

1. 它们**没有**绝对 `RUNPATH`；
2. 它们唯一的非系统依赖 `libCore.so` 恰好是**扩展模块的直接 `DT_NEEDED`**，
   必先映射（`libDuckdbWrapper.so` 找 `libduckdb.so` 则靠它**已自带**的 `$ORIGIN`）。

第 2 条的代价是**`ldd` 单独分析会假阳性**：`libNetwork.so`、`libAsyncDbWriter.so`、
`libSqliteWrapper.so` 在发布目录里单独 `ldd` 会报 `libCore.so => not found`——它们自己
没有 `RUNPATH`，`ld.so` 不会去看同目录的兄弟库。真实加载路径里 `libCore.so` 由扩展模块
先映射好，故 §8 第 3 条（`import`）才是**决定性判据**，实测通过。

第 2 条本身是**加载顺序的巧合**：一旦依赖图变动即可能碎于运行时、且无声。统一
`$ORIGIN` 后即消除。故 **Spark / DbAdapters 不需要现在重打 tag**——家规随
各自下次构建生效即可；届时上表右列转绿，§8 的分档警告自动消失。

**2026-10-13 复核**：两条巧合都已消失——6 个 ship 库已带上 `$ORIGIN`，「单独 `ldd`
假阳性」不再复现。只剩 `libduckdb.so` 无 `RPATH/RUNPATH`，而它只依赖系统库，没有
「找不到兄弟库」这回事。

## 5. 拍平：`bin/` 与 `lib/` 合成一个目录

### 5.1 改前的两层结构

引擎原先输出到两个目录（`QuantTrading/CMakeLists.txt:61-63`）：扩展模块进
`bin/$<CONFIG>`，7 个 `.so` 与 15 个 `.a` 进 `lib/$<CONFIG>`。如此，`$ORIGIN` 会推出
`$ORIGIN/../lib/Release`，包成两层，且 `.a` 混在同一目录里。

### 5.2 改法：共享库目录指向 `bin/`（2026-10-09 已落地）

把**共享库**输出目录也指向 `bin/$<CONFIG>`：

```cmake
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_SOURCE_DIR}/bin/$<CONFIG>)
set(CMAKE_LIBRARY_OUTPUT_DIRECTORY ${CMAKE_SOURCE_DIR}/bin/$<CONFIG>)
set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY ${CMAKE_SOURCE_DIR}/lib/$<CONFIG>)
```

- 效果：`bin/$<CONFIG>` 里只剩扩展模块与全部 `.so`，`.a` 仍留在 `lib/`，
  天然被发布包排除；原先进 RUNPATH 的那条「仓内库目录」不再需要，改由 §4.2 的
  `$ORIGIN` 条目承担（构建机绝对项按 §4.2 的取舍仍在，实测见附录 A.1）。
- **Windows 不受影响**：`CMAKE_LIBRARY_OUTPUT_DIRECTORY` 只作用于
  非 DLL 共享库（Linux `.so` / macOS `.dylib`）；Windows 的 `.dll` 走
  `RUNTIME_OUTPUT_DIRECTORY`，本就落在 `bin/`。
- 已查证无消费者按 `lib/$<CONFIG>` 取库：`Configs/*.json` 无库路径字段，
  `test/` 无按路径寻库。
- 注意：`$ORIGIN` 只覆盖**引擎构建树内的**库。Spark / DbAdapters 的
  `.so` 来自各自安装树，构建期仍靠链接目录的绝对路径寻址；**拍平发生在
  §7 的打包脚本**——把三处的 `.so` 一并收进发布目录。

## 6. `engine-version.txt` 必须在 Linux 侧产出

平台用它标识「这一轮跑的是哪个引擎构建」（取首个非空行）。引擎侧现行写法
（`QuantTrading/CMakeLists.txt:29`）：

```cmake
file(GENERATE OUTPUT ${CMAKE_SOURCE_DIR}/bin/$<CONFIG>/engine-version.txt CONTENT "${PROJECT_VERSION}\n"
    NEWLINE_STYLE UNIX)
```

**两平台字节一致（2026-10-14）**：`file(GENERATE)` 不写 `NEWLINE_STYLE` 时按平台换行风格落盘——
同一个版本号在 Windows 包里是 CRLF、Linux 包里是 LF。两侧读者都取「首个非空行 + 去空白」，故
此前无故障；写死 `UNIX` 是把这份一致性从「读者恰好宽容」变成「文件本身相同」。该选项自
CMake 3.20 起可用，与本仓 `cmake_minimum_required(VERSION 3.20)` 正好对齐，不抬高下限。

**2026-10-09 实测结案**：此前 Linux 构建树与 Windows `bin/Debug` 里都没有该文件，
原因是**这两棵树在该行落地之后还没重新 configure**——不是写法问题。对同一个
`WSL-GCC-Release` 预设重跑一次构建（configure 段随之重跑）后，WSL 侧即产出
`bin/Release/engine-version.txt`（内容 `1.0.0`）；**代码不动**。

原先揣测的两个候选**均被实测否证**：

1. `$<CONFIG>` 在 Ninja（单配置生成器）下取 `CMAKE_BUILD_TYPE`，实测路径正确
   落在 `bin/Release/`；
2. `file(GENERATE)` 会自行创建缺失的父目录，写入不会静默失败。

> **注意**：该文件由**配置期**产生，故「改完不重跑 configure 就构建」看不到它——
> 这与「`.o` 复用、只重链接」的情形不同，是本次误诊的根源。

缺失时平台退回「扩展模块 + 运行时库」的内容摘要，仍能跑，但拿不到
人可读版本号——Release 包应避免走到这条兜底。

## 7. 打包脚本要求

打包脚本把**三处**产物平铺进发布目录。取用必须是**显式白名单**——不可写成
「取该目录下全部 `.so`」，理由见 §2.1 的警告：

| 来源 | 取用（白名单，逐条列名） |
| :--- | :--- |
| `QuantTrading/bin/$<CONFIG>` | 扩展模块 `QuantTrading.*`（后缀按 §2.2 认）、`libBackTest.so`、六个 API 中间层（§2.1）、`MdOffer` 与 `SimExchange` 两个应用（§2.3）、`engine-version.txt`、`BackTest.json`、`Sessions.json`、`MdOffer.json`、`SimExchange.json` |
| `Libs/Spark/x64-linux/lib` | `libCore.so`、`libNetwork.so`、`libSerialization.so` |
| `Libs/DbAdapters/x64-linux/lib` | `libAsyncDbWriter.so`、`libDuckdbWrapper.so`、`libSqliteWrapper.so`、`libduckdb.so` |

- 白名单共 **22 项**（扩展模块按实际名计 1 项），与 §2 的清单一一对应；
- 排除：`*.a`、`*.pdb`、`include/`、`lib/cmake/`、任何 `.so.*` 调试拆分；
- 排除：`libMysqlWrapper.so`、`libMariadbWrapper.so`（各 11–15 MB；2026-10-13 用户裁定
  「数据库只需要 sqlite 和 duckdb」，故连「按需另定」的余地也不留）；
- 排除：测试程序（`Test*`）、`SimExchangeInit`、`BackTestInit.*`、跑测残留（`*.db`、`log/`、
  `Dump/`）——它们既不服务平台，也不服务接入面；
- 排除：CTP SDK 的两个 `.so`（再分发边界不背第三方 SDK；`MdOffer` 由目标机自备即可，理由见 §2.3）；
- 输出：**一个目录**，无子目录（这是**发布目录**的形态，即下面 `--zip` 解压出来的那一层）；
- 白名单任一文件缺失即**非零退出并点名**，不得静默少文件；
- 版本号只从 `engine-version.txt` 读，脚本不得手写（见 §6）。

**归档形态（可选，`--zip`）**：把发布目录打成
`QuantTrading-<版本>-linux-<架构>.zip`，包内是**一个**顶层目录（同名去 `.zip`），§2 清单的
全部文件平铺其下。
本条不改变上面「一个目录、无子目录」的要求——zip 只是那一层目录的封装，解压后仍是扁平单目录。
平台侧「换版按版本留目录、不原地覆盖」的约定因此天然满足：解压即得一层可直接
`QUANT_ENGINE_ROOT` 指过去的目录（`QuantPlatform` 的 `docs/platform-plan.md`）。归档一律在 §8
自检**全过之后**才产出。

> 引擎侧实现：仓根 `PackageLinuxRelease.sh`（`--config` / `--output` / `--libs` / `--zip` / `--force`）。
> 它把上表与「排除」两节逐条写成白名单，缺失即点名且非零退出，并内嵌 §8 的判据；
> 默认拒绝写非空输出目录，`--force` 只清它自己那个输出目录。`--zip` 的打包与包内校验（可读、
> 条目数一致、无嵌套目录）只用解释器自带的 `zipfile`，不引入 `zip(1)` / `unzip(1)` 依赖。

## 8. 引擎侧自检（全过才算合格）

```shell
cd engine

# 1. 依赖齐全: 应无任何输出（引擎自有库与两个应用都要查）
ldd ./*.so MdOffer SimExchange | grep -i "not found"

# 2. RUNPATH 里应含 $ORIGIN: 按 §4.3 分档判定
for artifact in *.so MdOffer SimExchange; do
    printf '%-40s' "$artifact"; readelf -d "$artifact" | grep -o 'RPATH.*\|RUNPATH.*' || echo '  (无)'
done

# 3. 解释器能 import: 打印出的路径应在发布目录内
python -c "import QuantTrading; print(QuantTrading.__file__)"

# 4. 版本文件在: 应打印一行版本号
cat engine-version.txt

# 5. 无绝对路径 DT_NEEDED: 应无任何输出（2026-10-14 起是硬判据）
readelf -d ./*.so MdOffer SimExchange | grep -E '\(NEEDED\).*\[/'
```

判据按 §4.3 / §4.4 **分档**（2026-10-09 裁定）：

| 条目 | 引擎自己的产物 | Spark / DbAdapters ship 库 |
| :--- | :--- | :--- |
| 第 1 条（依赖解析） | 有缺失即不合格；解析到包外且未列名也不合格 | 缺 `libCore.so` 只记警告 |
| 第 2 条（含 `$ORIGIN`） | 缺即不合格 | 缺只记警告 |
| 第 5 条（绝对 `DT_NEEDED`） | 有即不合格 | 无此情形 |

- 第 3 条是**决定性**的：它同时验证扩展模块能找到 `libBackTest.so`、
  `libBackTest.so` 能找到它的兄弟——即整套 `$ORIGIN` 链是否真的成立。
  **但它不能替代第 2 条**：在构建机上那些绝对路径还在，`import` 即便不靠
  `$ORIGIN` 也会成功；只有 `readelf` 能证明 `$ORIGIN` 确实写进了 ELF。
  反过来，在目标机上第 3 条通过即终局通过。
- 第 1 条对 ship 库报 `libCore.so => not found` 是**假阳性**（§4.4）：它们没有
  `RUNPATH`，单独分析时看不见同目录的兄弟库；真实加载路径里兄弟库由扩展模块先映射。
- `ldd` 对 `dlopen` 类库（§2 的 `libSqliteWrapper.so`）不可见，第 1 条只覆盖
  `DT_NEEDED` 闭包；§2.1 那 6 个 API 中间层不是 `dlopen` 库——它们在 `SimExchange`
  的 `DT_NEEDED` 里，第 1 条照常查得到（但也只有 `SimExchange` 拉其中一个，
  §2.1 的「没有自家代码会去加载」说的是引擎源码树，别把两句记混）。
- **第 1 条自 2026-10-14 起多判一层**：光「`ldd` 无缺失」在**构建机上**说明不了什么——构建机的
  `RUNPATH` 里本来就带着 `Libs` 安装树的绝对目录，包外依赖在这台机器上照样解析成功。故脚本另判
  「解析到了**哪里**」：引擎自有产物的每条依赖，路径要么在发布目录内、要么是系统库目录
  （`/lib`、`/lib64`、`/usr/lib`、`/usr/lib64`；`/usr/local/lib` 不在内——那里装的是本机自建
  软件，目标机上未必有），要么在 `NotPackagedDependencyNames` 里列着。未列名却落在包外即不合格。

> **引擎侧实现**：仓根 `PackageLinuxRelease.sh` 已内嵌这些判据（分档同上）。
> 注意它的「五条自检」与本节 1–5 **不是同一组编号**，对应关系见下表。
> 2026-10-09 实测：12 项文件、四条全过，另有 9 条警告（6 条 ship 库无 `$ORIGIN`、
> 3 条 ship 库单独 `ldd` 假阳性），exit 0。
> **2026-10-13 实测**（扩到 22 项之后重打）：22 项文件、自检全过，另有 **1 条已知缺陷**
> （`MdOffer` 的 CTP 绝对路径）与 **1 条警告**（`libduckdb.so` 无 `$ORIGIN`），exit 0，
> zip 28,203,609 B。同批顺带证实：Spark / DbAdapters 的 ship 库这轮已带上 `$ORIGIN`，
> 2026-10-09 那 6 条警告与 3 条 `ldd` 假阳性随之消失。
> **2026-10-14 实测**（修掉 `MdOffer` 的绝对路径 `DT_NEEDED` 之后重打）：22 项文件、自检全过，
> 只有 **1 条警告**（`libduckdb.so` 无 `$ORIGIN`）；「已知缺陷」那一条归零，zip 28,203,076 B、
> sha256 `c679898705688ca880bb7ae738beea9edfb0a98b030efd2f30e7c169c151144a`，exit 0。
> 同批验证：新加的「解析到包外」判据**确实认出了** `libthostmduserapi_se.so` 解析到构建机的 CTP
> 安装树，因在 `NotPackagedDependencyNames` 里而放行——是显式列名放行，不是没看见（探针见附录 B）。

脚本的自检（执行顺序）与本节手工验法的对应：

| 脚本内嵌的检查 | 对应本节 |
| :--- | :--- |
| 包内容（§7 白名单与排除项 + 文件数 22 + 无子目录） | 本节未列，手工看 §7 |
| 依赖解析（`ldd` 无缺失；解析路径须在包内 / 系统库 / 列名之内） | 第 1 条 |
| RUNPATH 含 `$ORIGIN` | 第 2 条 |
| 绝对路径 `DT_NEEDED`（一律不合格） | 第 5 条 |
| 解释器 `import` | 第 3 条 |

「`engine-version.txt` 读不到版本行」在脚本里**先于自检**按硬失败处理（exit 3），
即本节第 4 条不合格时脚本不会走到自检，而是直接中止——故脚本口头报的
「自检全过」不含第 4 条，版本文件是它的前置条件。

## 9. 依赖来源与版本（包内只放产物，来源记在这里）

运行时闭包**必须随包**（§2）：它们是 `DT_NEEDED` 加载期依赖，系统包管理器里没有，且 soname
不带版本号（是 `libCore.so` 而不是 `libCore.so.1`）——**错配是静默的**，链接器与 `ld.so`
都不报错，只在运行时给出错误结果。所以**文档不替代打包**：下面这张表管的是「这一包是哪几个
提交、哪几个版本编出来的」，用于复现与追责，不是「去哪儿装依赖」。

| 依赖 | 来源 | 版本锚点（2026-10-13 实测） |
| :--- | :--- | :--- |
| 引擎 QuantTrading | 仓 `xunmeng2002/QuantTrading` | `1.0.0`；发布构建源为 WSL 克隆 `~/.vs/QuantTrading`，HEAD `91c4459`（2026-10-10）；2026-10-14 那一版另含当日改动（`CMakeLists.txt` 的两处、`PackageLinuxRelease.sh` 的自检加固，见附录 B.1） |
| Spark | 仓 `xunmeng2002/Spark` | `v1.0.0-17-g881d744`（`881d744`，2026-10-09） |
| DbAdapters | 仓 `xunmeng2002/DbAdapters` | `v1.0.0-5-gf57e9b8`（`f57e9b8`，2026-10-09） |
| duckdb | 预构建树 `~/.vs/Libs/duckdb`（**不走 vcpkg**） | `v1.5.3`（`duckdb.hpp` 的 `DUCKDB_VERSION`，`.so` 内同串） |
| CTP SDK | 上期技术官方 SDK，`~/.vs/Libs/Ctp`（**不随包**，理由见 §2.3） | `6.7.9`（`CTPConfig.cmake` 注释与两个 `.so` 内的版本串一致） |
| 工具链 | 构建机 | GCC `15.2.0`（Ubuntu 15.2.0-16ubuntu1）、Python `3.14.4`、triplet `x64-linux` |

- 三棵安装树（`~/.vs/Libs/{Spark,DbAdapters,x64-linux}`）有 `install_manifest.txt` 坐实来源；
  安装物 mtime 晚于各自 HEAD 的提交时间，此后两仓无源码提交——即**这几棵树就是上表版本编出来的**。
- C++ ABI 由**编译器与标准库**决定：这一包只保证与 GCC 15.2.0 编出的同族产物互通。换编译器
  （或换发行版）另编一份 Spark / DbAdapters 装进来**不会有任何报错**，只会在运行时出怪结果——
  故换版整包换，不要只换其中一个 `.so`。
- vcpkg 那 15 个包（sqlite3 `3.50.4`、zlib `1.3.1`、lz4、zstd、openssl `3.5.2`、
  protobuf `5.29.5`、abseil、rapidjson、utf8-range，以及为数据库后端备的 libmariadb /
  mysql-connector-cpp）是**构建期静态链入**：发布目录里三方 `.so` 只有 `libduckdb.so` 一个
  （§2 的清单即全部），运行期不需要它们中的任何一个；MySQL / MariaDB 那两个连接器对应的
  封装库本轮不进包（§7）。

---

## 附录 A：实测依赖关系（2026-10-09，WSL）

`NEEDED` 列只列非系统库；`RUNPATH` 列标注**是否已正确**。

| 对象 | 归属 | `DT_NEEDED`（非系统） | `RUNPATH` |
| :--- | :--- | :--- | :--- |
| 扩展模块 | 引擎 | `libBackTest.so`, `libCore.so` | ✗ 绝对 |
| `libBackTest.so` | 引擎 | `libAsyncDbWriter`, `libDuckdbWrapper`, `libSerialization`, `libNetwork`, `libCore` | ✗ 绝对 |
| `libMdApi` / `libMdGbkApi` | 引擎 | `libNetwork`, `libCore`, （`libSerialization`） | ✗ 绝对 |
| `libSimExchangeApi` / `libSimExchangeGbkApi` | 引擎 | 同上 | ✗ 绝对 |
| `libTraderApi` / `libTraderGbkApi` | 引擎 | 同上 | ✗ 绝对 |
| `SimExchange`（应用） | 引擎 | `libMdApi`, `libAsyncDbWriter`, `libSerialization`, `libNetwork`, `libCore` | ✓ `$ORIGIN`（2026-10-13 实测） |
| `MdOffer`（应用） | 引擎 | 上一行的依赖去掉 `libMdApi`，**另加 CTP 的 `libthostmduserapi_se.so`（绝对路径）** | ✓ `$ORIGIN`，缺陷见 §2.3 |
| `libCore.so` | Spark | （仅系统） | 无（可接受） |
| `libNetwork.so` | Spark | `libCore.so` | 无 |
| `libSerialization.so` | Spark | （仅系统） | 无（可接受） |
| `libAsyncDbWriter.so` | DbAdapters | `libCore.so` | 无 |
| `libDuckdbWrapper.so` | DbAdapters | `libCore.so`, `libduckdb.so` | ✓ `$ORIGIN` |
| `libSqliteWrapper.so` | DbAdapters | `libCore.so`（**且按名 dlopen**） | 无 |
| `libduckdb.so` | 三方 | `libdl`, `libpthread` | 无（可接受） |

> **提示**：本表由 `readelf -d` 实测得出；`libSqliteWrapper.so` 的 "按名
> dlopen" 是手工补注——它属 dyld 盲区，见 §2 的警告。

### 附录 A.1 补家规并重链接后的复测（2026-10-09）

上表 `RUNPATH` 列的 ✗ 是**补家规前**的状态。给 `CMakeCommon.cmake` 追加
`-Wl,-rpath,$ORIGIN` 后重新链接，引擎 8 个产物全部转正（两个应用由 2026-10-13 的
复核补记在表末）：

| 对象 | 重链接后的 `RUNPATH` |
| :--- | :--- |
| 扩展模块 | `[$ORIGIN : Libs/Spark/... : Libs/duckdb/... : Libs/DbAdapters/...]` |
| `libBackTest.so` | `[$ORIGIN : Libs/duckdb/... : Libs/DbAdapters/... : Libs/Spark/...]` |
| 六个 API 中间层 | `[$ORIGIN : Libs/Spark/...]`（2026-10-13 起随包发运） |
| `MdOffer` / `SimExchange`（应用） | `[$ORIGIN : …]`（2026-10-13 实测，同批进包） |
| Spark / DbAdapters ship 库 | 2026-10-09：与上表一致；**2026-10-13：6 个已带上 `$ORIGIN`**，只剩 `libduckdb.so` 无 |

两处实测细节：

- 绝对项**未被清除**，`$ORIGIN` 只是排在最前——见 §4.2 的取舍说明；
- 2026-10-09：12 项发布文件里有 1 项 RUNPATH 末尾带**空元素**；2026-10-13 扩到 22 项后是
  7 项（`libBackTest.so` 与六个 API 中间层）。glibc 视作当前工作目录；来源是 Libs 安装树
  导出的链接片段，详见 §4.3 的附带缺陷段。

## 附录 B：一次实测留档（原始输出）

```text
扩展模块  RUNPATH = [.../QuantTrading/lib/Release : .../Libs/Spark/x64-linux/lib
                     : .../Libs/duckdb/x64-linux/lib : .../Libs/DbAdapters/x64-linux/lib]
libBackTest.so  RUNPATH = [.../Libs/duckdb/... : .../Libs/DbAdapters/... : .../Libs/Spark/...]
libMdApi.so 等  RUNPATH = [.../Libs/Spark/x64-linux/lib]
libDuckdbWrapper.so  RUNPATH = [$ORIGIN]
libCore.so / libNetwork.so / libSerialization.so / libAsyncDbWriter.so
      / libSqliteWrapper.so / libduckdb.so  → 无 RUNPATH
```

结论：**带绝对 `RUNPATH` 的只有引擎自己的产物**；Spark / DbAdapters 的 ship
库要么无 `RUNPATH`、要么已正确 `$ORIGIN`。

补家规（`-Wl,-rpath,$ORIGIN`）并重新链接后，同日复测：

```text
bin/Release 的 8 个产物   → 每条 RUNPATH 均以 $ORIGIN 开头（绝对项保留在后）

bash PackageLinuxRelease.sh --force
  发布目录：~/.vs/QuantTrading/dist/linux-engine
  引擎版本（取自 engine-version.txt）：1.0.0
  文件数：12
  自检四条全过（契约 §8），另有 9 条警告（不阻断）
  exit=0

探针：把 libNetwork.so 临时改判为「引擎自有」后重跑
  [不通过] libNetwork.so 的依赖未解析：	libCore.so => not found
  [不通过] libNetwork.so 的 RUNPATH 未含 $ORIGIN：无 RPATH/RUNPATH
  exit=4（期望 4）——分档的硬失败侧确实会拦下
```

2026-10-13，白名单扩到 22 项（六个 API 中间层 + `MdOffer` / `SimExchange` 两个应用）后重打：

```text
bash PackageLinuxRelease.sh --zip --force
  覆盖：清空输出目录 ~/.vs/QuantTrading/dist/linux-engine
  [警告] libduckdb.so 无 $ORIGIN（三方 ship 库，本批次暂可接受）：无 RPATH/RUNPATH
  [提示] 10 个产物保留了构建机绝对路径（$ORIGIN 在最前，目标机上不存在即跳过）
  [提示] 7 个产物的 RUNPATH 含空元素（等于当前目录，属 Libs 安装树遗留）
  [已知缺陷] MdOffer 记着 CTP SDK 的绝对路径依赖（同机可跑，跨机不可用）：
             /home/xunmeng/.vs/Libs/Ctp/x64-linux/lib/libthostmduserapi_se.so

  发布目录：~/.vs/QuantTrading/dist/linux-engine
  引擎版本（取自 engine-version.txt）：1.0.0
  文件数：22
  zip 包：dist/QuantTrading-1.0.0-linux-x86_64.zip（包内顶层目录同名，22 个文件）
  zip 大小：28,203,609 字节
  自检全过（契约 §8），另有 1 条已知缺陷（契约 §2.3，不阻断），另有 1 条警告（不阻断）
  exit=0
```

同批发现：`libduckdb.so` 是这轮唯一缺 `$ORIGIN` 的产物（它只依赖系统库，无碍）；
2026-10-09 那 6 条 ship 库警告与 3 条 `ldd` 假阳性都没有复现——Spark / DbAdapters 的安装树
已带上家规。

### 附录 B.1 修掉绝对路径 `DT_NEEDED` 后重打（2026-10-14，WSL）

改 `CMakeLists.txt` 给 CTP 的两个导入目标置 `IMPORTED_NO_SONAME` 后，只重链接两个目标
（`cmake --build out/build/WSL-GCC-Release --target MdOffer SimExchangeInit`，无重编译）：

```text
readelf -d bin/Release/MdOffer | grep NEEDED | grep thost
  (NEEDED)  Shared library: [libthostmduserapi_se.so]      ← 裸名，斜杠没了

readelf -d bin/Release/MdOffer bin/Release/SimExchangeInit | grep -E '\(NEEDED\).*\[/'
  （无输出）

ldd bin/Release/MdOffer | grep -i thost
  libthostmduserapi_se.so => /home/xunmeng/.vs/Libs/Ctp/x64-linux/lib/libthostmduserapi_se.so
```

第三行正是「构建机绿、目标机未必绿」的实证：裸名在**构建机上**被 `RUNPATH` 里那条绝对项命中
（真加载时才轮到 `$ORIGIN`）。故 §8 第 1 条另判「解析到了哪里」——同目录下单独跑一遍该判据：

```text
（只看既不在发布目录、也不在系统库目录的项）
libthostmduserapi_se.so /home/xunmeng/.vs/Libs/Ctp/x64-linux/lib/libthostmduserapi_se.so
在 NotPackagedDependencyNames 里的命中数：1
```

重打发布包：

```text
bash PackageLinuxRelease.sh --force --zip
  覆盖：清空输出目录 ~/.vs/QuantTrading/dist/linux-engine
  [警告] libduckdb.so 无 $ORIGIN（三方 ship 库，本批次暂可接受）：无 RPATH/RUNPATH
  [提示] 10 个产物保留了构建机绝对路径（$ORIGIN 在最前，目标机上不存在即跳过）
  [提示] 7 个产物的 RUNPATH 含空元素（等于当前目录，属 Libs 安装树遗留）

  文件数：22
  zip 大小：28,203,076 字节
  自检全过（契约 §8），另有 1 条警告（不阻断）
  exit=0
```

`[已知缺陷]` 那一行没再出现；zip 比上一版少 7 字节（`MdOffer` 重链接后体积微变，
其余 21 项字节不变）。同日 Windows 侧以同一版 `CMakeLists.txt` 重新 configure 后重打：
23 项、13,660,618 字节（比上一版少 1 字节，正是 `engine-version.txt` 由 CRLF 改 LF 的那一字节），
两侧 `engine-version.txt` 的 sha256 相同（`59854984…ce03`）。
