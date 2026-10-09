# 引擎 Linux 发布包要求

本文是平台侧（QuantPlatform）对上游引擎包（QuantTrading）的**验收契约**：约定
Linux Release 包长什么样、怎么打、怎么自检。满足本文的包，平台侧只需把
`ENGINE_ROOT` 指向包内唯一目录即可挂载运行。

> **注意**：本文只描述**部署运行包**，不描述链接用的开发包。两者的区别见 §3。

## 1. 一句话契约

平台只认一个目录。`ENGINE_ROOT` 指向的那个目录里，必须同时有：

- Python 扩展模块（`QuantTrading.*.so`）；
- **全部**运行时 `.so`（来自三个仓，见 §2）；
- `engine-version.txt`；
- 两个模板 json。

该目录之外，运行时不再有任何依赖。

## 2. 发布包目录结构

运行时 `.so` **不来自单一仓**：引擎依赖 Spark 与 DbAdapters 的 `.so`，靠
`DT_NEEDED` 动态加载。打包必须把三个仓的产物**收拢到同一个目录**：

```text
engine/
├── QuantTrading.cpython-314-x86_64-linux-gnu.so   ← 引擎
├── libBackTest.so                                  ← 引擎
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

| 文件 | 归属 | 角色 |
| :--- | :--- | :--- |
| `QuantTrading.*.so` | 引擎 | Python 扩展模块，解释器 import 的入口 |
| `libBackTest.so` | 引擎 | 回测主库，扩展模块的直接依赖 |
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

### 2.1 引擎另有一组「按需装载」的 `.so`

`QuantTrading/lib/$<CONFIG>` 下还有 6 个交易/行情 API 中间层：

```text
libMdApi.so  libMdGbkApi.so
libSimExchangeApi.so  libSimExchangeGbkApi.so
libTraderApi.so  libTraderGbkApi.so
```

它们**不出现在任何已知 `DT_NEEDED` 里**，属引擎 loader 按名装载的一类
（同 §2 的 `libSqliteWrapper.so`）。**只跑回测是否需要它们，需引擎侧确认**；
确需则一并进包，不需则明确排除。

> **注意**：若进包，它们同样带绝对 `RUNPATH`（指向构建机的 Spark 安装树），
> 与 §4 一并处理。

### 2.2 扩展模块后缀不写死

扩展模块的后缀随解释器而定，**不要硬编码**：

| 平台 | 后缀 |
| :--- | :--- |
| Windows | `.cp314-win_amd64.pyd` / `.pyd` |
| Linux | `.cpython-314-x86_64-linux-gnu.so` / `.abi3.so` / `.so` |

平台侧的判据与 import 机制同源，取 `importlib.machinery.EXTENSION_SUFFIXES`。
引擎侧打包时按实际产物命名即可，平台会按当前解释器的后缀表去认。

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

`CMakeCommon.cmake` 顶部已加入家规：

```cmake
set(CMAKE_BUILD_RPATH_USE_ORIGIN ON)
```

三个产品仓（QuantTrading / Spark / DbAdapters）都在各自 `CMakeLists.txt`
第 2 行 `include(submodules/CMakeCommon/CMakeCommon.cmake)`，故此开关对
所有 target 一体生效。版本要求 `CMake ≥ 3.14`，三家现状为 3.20 / 3.25，
满足。

> **注意**：`RUNPATH` 是链接期写进 ELF 的字段，**必须重新构建**才生效。
> 是「重新链接」而非「重新编译」，`.o` 复用，秒级完成。

### 4.3 怎么验

```shell
cd engine
for so in *.so; do
    printf '%-40s' "$so"
    readelf -d "$so" | grep -o 'RUNPATH.*' || echo '  (无 RUNPATH)'
done
```

期望：每个 `.so` 都命中，且值为 `[$ORIGIN]`。

### 4.4 谁必须重建（实测，2026-10-09）

带**绝对** `RUNPATH` 的**全部是引擎自己**的产物——扩展模块与
`libBackTest.so` 等 7 个 `.so`，指向构建机的 `Libs/{Spark,Duckdb,DbAdapters}`
安装树。**这些必须重建**（本次 Release 即覆盖）。

Spark 与 DbAdapters 的 ship 库**无需重建即可工作**，但原因是
两个巧合，而非它们更"干净"：

1. 它们**没有**绝对 `RUNPATH`；
2. 它们唯一的非系统依赖 `libCore.so` 恰好是**扩展模块的直接 `DT_NEEDED`**，
   必先映射（`libDuckdbWrapper.so` 找 `libduckdb.so` 则靠它**已自带**的 `$ORIGIN`）。

第 2 条是**加载顺序的巧合**：一旦依赖图变动即可能碎于运行时、且无声。统一
`$ORIGIN` 后即消除。故 **Spark / DbAdapters 不需要现在重打 tag**——家规随
各自下次构建生效即可。

## 5. 拍平：`bin/` 与 `lib/` 合成一个目录

### 5.1 现状

引擎当前输出到两个目录（`QuantTrading/CMakeLists.txt:59-61`）：

```cmake
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_SOURCE_DIR}/bin/$<CONFIG>)
set(CMAKE_LIBRARY_OUTPUT_DIRECTORY ${CMAKE_SOURCE_DIR}/lib/$<CONFIG>)
set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY ${CMAKE_SOURCE_DIR}/lib/$<CONFIG>)
```

扩展模块进 `bin/$<CONFIG>`，7 个 `.so` 与 15 个 `.a` 进 `lib/$<CONFIG>`。
如此，`$ORIGIN` 会推出 `$ORIGIN/../lib/Release`，包成两层，且 `.a`
混在同一目录里。

### 5.2 改成一行

把**共享库**输出目录也指向 `bin/$<CONFIG>`：

```cmake
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_SOURCE_DIR}/bin/$<CONFIG>)
set(CMAKE_LIBRARY_OUTPUT_DIRECTORY ${CMAKE_SOURCE_DIR}/bin/$<CONFIG>)
set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY ${CMAKE_SOURCE_DIR}/lib/$<CONFIG>)
```

- 效果：`bin/$<CONFIG>` 里只剩扩展模块与全部 `.so`，`.a` 仍留在 `lib/`，
  天然被发布包排除；每个 `.so` 的 `RUNPATH` 退化为纯 `$ORIGIN`。
- **Windows 不受影响**：`CMAKE_LIBRARY_OUTPUT_DIRECTORY` 只作用于
  非 DLL 共享库（Linux `.so` / macOS `.dylib`）；Windows 的 `.dll` 走
  `RUNTIME_OUTPUT_DIRECTORY`，本就落在 `bin/`。
- 改后需确认没有别处（测试、json 配置）按 `lib/$<CONFIG>` 路径去取库。
- 注意：`$ORIGIN` 只覆盖**引擎构建树内的**库。Spark / DbAdapters 的
  `.so` 来自各自安装树，构建期仍靠链接目录的绝对路径寻址；**拍平发生在
  §7 的打包脚本**——把三处的 `.so` 一并收进发布目录。

## 6. `engine-version.txt` 必须在 Linux 侧产出

平台用它标识「这一轮跑的是哪个引擎构建」（取首个非空行）。现状：
Windows 侧有，WSL / Linux 构建树里**整个缺失**。

引擎侧现行写法（`QuantTrading/CMakeLists.txt`）：

```cmake
file(GENERATE OUTPUT ${CMAKE_SOURCE_DIR}/bin/$<CONFIG>/engine-version.txt CONTENT "${PROJECT_VERSION}\n")
```

**缺失原因尚未证实**，需引擎侧在 WSL 上实测确认。两个候选：

1. `$<CONFIG>` 在 Linux 构建下的求值——预设用的是单配置生成器 Ninja，
   `$<CONFIG>` 应取 `CMAKE_BUILD_TYPE`，但值得打印确认；
2. `bin/$<CONFIG>` 目录在 `file(GENERATE)` 执行时是否存在——若该命令
   不自行创建目录，写入会静默失败。

任一为真时，改用不依赖生成器求值的写法即可：

```cmake
# 配置期直接写; 适用于单配置生成器 (本项目预设即 Ninja)
file(WRITE ${CMAKE_SOURCE_DIR}/bin/${CMAKE_BUILD_TYPE}/engine-version.txt "${PROJECT_VERSION}\n")
```

> **提示**：`file(WRITE)` 会自行创建父目录，故第 2 个候选在它这里不成立。

缺失时平台退回「扩展模块 + 运行时库」的内容摘要，仍能跑，但拿不到
人可读版本号——Release 包应避免走到这条兜底。

## 7. 打包脚本要求

打包脚本把**三处**产物平铺进发布目录：

| 来源 | 取用 |
| :--- | :--- |
| `QuantTrading/bin/$<CONFIG>` | 扩展模块、全部 `.so`、`engine-version.txt`、两个 json |
| `Libs/Spark/x64-linux/lib` | `libCore.so`、`libNetwork.so`、`libSerialization.so` |
| `Libs/DbAdapters/x64-linux/lib` | `libAsyncDbWriter.so`、`libDuckdbWrapper.so`、`libSqliteWrapper.so`、`libduckdb.so` |

- 排除：`*.a`、`*.pdb`、`include/`、`lib/cmake/`、任何 `.so.*` 调试拆分；
- 排除：`libMysqlWrapper.so`、`libMariadbWrapper.so`（各 11–15 MB，按需另定）；
- 输出：**一个目录**，无子目录；
- 入口：打包脚本与 `engine-version.txt` 同步产出，勿手写版本号。

## 8. 引擎侧自检（四条，全过才算合格）

```shell
cd engine

# 1. 依赖齐全: 应无任何输出
ldd ./*.so | grep -i "not found"

# 2. RUNPATH 全为 $ORIGIN: 每个 .so 都应命中
for so in *.so; do
    printf '%-40s' "$so"; readelf -d "$so" | grep -o 'RUNPATH.*' || echo '  (无)'
done

# 3. 解释器能 import: 打印出的路径应在发布目录内
python -c "import QuantTrading; print(QuantTrading.__file__)"

# 4. 版本文件在: 应打印一行版本号
cat engine-version.txt
```

第 3 条是**决定性**的：它同时验证扩展模块能找到 `libBackTest.so`、
`libBackTest.so` 能找到它的兄弟——即整套 `$ORIGIN` 链是否真的成立。

> **提示**：`ldd` 对 `dlopen` 类库（§2 的 `libSqliteWrapper.so`、§2.1 的
> API 中间层）不可见，第 1 条只覆盖 `DT_NEEDED` 闭包。

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
| `libCore.so` | Spark | （仅系统） | 无（可接受） |
| `libNetwork.so` | Spark | `libCore.so` | 无 |
| `libSerialization.so` | Spark | （仅系统） | 无（可接受） |
| `libAsyncDbWriter.so` | DbAdapters | `libCore.so` | 无 |
| `libDuckdbWrapper.so` | DbAdapters | `libCore.so`, `libduckdb.so` | ✓ `$ORIGIN` |
| `libSqliteWrapper.so` | DbAdapters | `libCore.so`（**且按名 dlopen**） | 无 |
| `libduckdb.so` | 三方 | `libdl`, `libpthread` | 无（可接受） |

> **提示**：本表由 `readelf -d` 实测得出；`libSqliteWrapper.so` 的 "按名
> dlopen" 是手工补注——它属 dyld 盲区，见 §2 的警告。

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
