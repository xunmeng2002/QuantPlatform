# QuantPlatform 进度

## 项目定位

本仓是 `QuantPlatform`——一个**云上多用户**的量化回测平台：Vue 3 前端 +
FastAPI 后端，包裹 `../QuantTrading` 的 C++ 回测引擎（`BackTest.dll`），
以「一次性进程 + 退出码 + `result.json`」的方式驱动回测，**运行用户上传的
Python 策略**。

**产品名：薪火量化**（2026-09-26 用户拍板）。界面文案、浏览器标签页标题与主页主视觉用这个名字；
**仓库名 `QuantPlatform`、包名、目录名与文档标题一律不动**（`docs/platform-plan.md` 的 H1 仍是
`# 量化回测平台实施计划`，那是有意保留的）。

定位与形态**承接 `QuantTrading` 的已定案结论**，不自拟：

- 依据：`../QuantTrading/PROGRESS.md` 与归档 `D.48`（2026-09-18 用户拍板）。
- 要点：**调度层就是 web 后端**；后端**必须与引擎同机**；每 job **独立工作目录**；
  RunId **由调度侧配置注入**；catalog **由调度侧写**，引擎零改动；
  **只收 Python 宿主**，平台拉起的是**用户策略程序**；产物落本机磁盘 + SQLite。
- 总体计划全文见 [`docs/platform-plan.md`](docs/platform-plan.md)。
- job 工作目录契约见 [`docs/job-workspace.md`](docs/job-workspace.md)。

---

## 归档索引

已关闭条目的原文移入 [`PROGRESS-archive.md`](PROGRESS-archive.md)。本表一行一条
ID ＋ 主题 ＋ 判据注记；**引用主文件未载的结论前，必须先按 ID 或关键词 grep 归档取原文核实**，
不得凭记忆断言。归档里的条目**可删可改、以简洁为准**（2026-09-18 用户修订），
但改写必须保住 **ID、原日期、原结论连同「为什么」**——理由一丢，下个会话就会把已关闭的事
重新议一遍，那才是这条真正要防的损失。

| ID | 主题 |
| ---- | ---- |
| D.01 | P0 地基探针：硬钉子 ② 已解（**其中 `argv[0]` 结论已推翻**，见 D.06） |
| D.02 | P1 后端骨架与多用户隔离（五张表 / 登录 JWT / 查询统一收口加 `user_id`） |
| D.03 | P2 上传核心 + `/api/health` 收窄为仅管理员（落盘布局 / 版本判重 / 两道体积闸） |
| D.04 | P2b 策略授权共享（`PUT /{id}/grants` / 授权驱动可见性；审核修正见 D.05） |
| D.05 | P2b 审核修正与测试补强（上限判据 / 可见性回归 / 两处文案订正；**一条残余风险与一处行宽待办**见原文） |
| D.06 | P3 runner 本体 + `params` schema 定案（`argv[0]` 结论订正 / 两处 CAS 覆盖缺口 / P3 六项已知缺口；短版见 ✅ 区） |
| D.07 | P4 前端骨架 + 三处后端前置端点（受限用户目录 / 显示名必填 / `manifest_json` 透传 / 产物清单与下载；含 Vite 只绑 `[::1]`、仓根 `.gitignore` 静默吞源码等踩坑记录） |
| D.08 | P5 可视化：权益曲线与回撤 + 5 张结果明细表（`vue-echarts` 按需注册 / 结果库两节只在终态挂载 / 5 张表的表名两处真相；**D.11 加 UI 库后主文件越过 50 KB，整条移出**） |
| D.09 | 网格步长由绝对价格改为比例（档位价与平仓价公式 / `0 < GridStep × GridCount < 1` / 构造期拒启 / **重取后的真引擎基线**，旧新两套口径的数不可相减） |
| D.10 | 提交页按策略预填「上一次提交的参数」（记住 = 策略参数 + 运行级字段，取自该用户在该策略下最新那一轮的两份配置文本 / 无历史回 200 而非 404 / 前端 `selectionToken` 竞态保护；**D.12 后主文件再越 50 KB，整条移出**） |
| D.11 | 引入 Element Plus：地基 4 件 + 两个最脏页（`/runs`、`/runs/new` / 主题五族色阶经 `html:root` 覆盖对齐 / 筛选改 placeholder + × 清空；**D.13 后主文件再越 50 KB，整条移出**） |
| D.12 | 观感层：统一外壳 / 版面原语 / 动效与焦点 / 两条反馈通道（顶栏用不透明底色而非毛玻璃 / 反馈策略与表单类例外 / `confirmAction` 四处实现要点 / 全站唯一出处 `use-feedback.ts`；**D.14 后主文件再越 50 KB，整条移出**） |
| D.13 | 公开主页「薪火量化」+ 界面改名（`/` 由裸重定向改为公开主页 / 外壳显隐解耦为 `meta.hidesHeader` / 带顶栏的公开页补一次 `loadCurrentUser` 且失败不跳转 / 改名范围与不动的三样；**D.15 后主文件再越 50 KB，整条移出**） |
| D.14 | 修「切换登录状态后空白页, 要按 F5」（`<Transition mode="out-in">` 要求单个元素孩子 / 前导注释让组件渲染成片段 / `@vue/test-utils` 默认 stub 掉 `Transition` 会假绿；**D.18 后主文件再越 50 KB，整条移出**） |
| D.15 | 观感层之二：把 Element Plus 的令牌桥补完（文字阶 / 填充阶 / 表格 / 阴影四族；EP 把表格与骨架屏的变量声明在 `.el-table` 上而**不是** `:root`，覆盖必须写 `html:root .el-table`；**D.18 后主文件再越 50 KB，整条移出**） |
| D.16 | 主页第一屏的数字带 + 标签页图标（三个数全部取自已有实测值 / 图标是纯几何标记、不排汉字 / SVG 里的 `#1d4ed8` 是写死的；**顶栏标记那笔欠账已由 D.17 收口**；**D.19 后主文件再越 50 KB，整条移出**） |
| D.17 | 顶栏标记换成与标签页同一份几何标记 + 五批验收清单合并成一份（静态 `src="/favicon.svg"` 会让 `@vitejs/plugin-vue` 改写成 `import`，Vitest 在 Windows 上直接炸 / 合并后的 37 条见 `docs/acceptance-checklist.md`；**D.19 后主文件再越 50 KB，整条移出**） |
| D.18 | 策略 manifest 的作者向说明 + 一份可直接用的示例（作者向说明与样例落在 `docs/` / 平台**不读**策略自带配置文件、渲染结果 ≡「已映射字段 ∪ 已声明参数」/ `bar_period` 不映射会**静默 0 成交** / manifest 与 `.py` 放一起按版本传；**D.19 后主文件再越 50 KB，整条移出**；留在主文件的短版 stub 于 **D.23 后随"已完成区滚动"一并移出**，结论现只在归档） |
| D.19 | 引擎版本可追溯（`Runs.EngineVersion` 提交时冻结 / `catalog/migrations.py` 只补新增列 / `.pyd` + 三 DLL 的 `sha256:` 降级不可与人写版本号相比 / 顺手发现 `Runs.Hostname` 是死列；**2026-10-01 随 D.24 写完后主文件越 50 KB，短版也移出**，唯一欠的手工验收项已并入 `docs/acceptance-checklist.md` §6） |
| D.20 | 引擎的 MySQL / MariaDB 适配器改为按配置运行时装载（引擎包可以不发运这两系 / `$<TARGET_RUNTIME_DLLS>` 不再拷它们；**2026-10-01 随 D.24 移出**，其唯一的未决项本就与 ❓ 段那条重复） |
| D.21 | 取不到数据库适配器时的收场口径订正（不是进程终止，而是 ERROR 日志 + 空适配器 + `ExitCodeHostInitFailed` 退 `1`，runner 侧零改动 / 未识别的非空 `DbType` 不再静默退化 / **补记：`DbType` 由字符串改为 `int`**，平台写入点是 `engine_config.SQLITE_DATABASE_TYPE = 1`；**旧字符串写法装不上**——jsoncpp `asInt()` 抛 `LogicError`，实测退 `0xC0000409` 且日志 0 字节，手工改配置须写裸整数，详见 `job-workspace.md` §3.2；**2026-10-01 随 D.24 移出**） |
| D.22 | 引擎侧四个后端收成一条装载路 + Linux 链接补齐（跨两仓、**本仓零改动** / 四个后端全走 `LoadDatabaseBackend`，Linux `libMysqlWrapper.so` 补 OpenSSL、zlib、resolv 链接；**2026-10-01 随 D.24 后主文件越 50 KB 移出**） |
| D.24 旁支 | 真引擎基线漂移的控制实验与判据改法（证与本批无关 / 判据由硬断言降为观察值 / 种子库在不在盘上由用例自查；**未决部分仍在主文件 ❓ 段**） |
| Q.01 | manifest 的 `params` schema 细节未定（**已定案**，见 D.06） |
| Q.02 | `permission_type` 判定语义未定（**已拍板不判定**，见 D.06） |
| Q.03 | 归属人从哪儿得知同事的 `user_id`（**已拍板：受限用户目录**） |
| Q.04 | P4 前端样式 Tailwind 还是纯 CSS（**已拍板 Tailwind**，与 `defect_tools` 有意分叉） |
| Q.05 | 受限用户目录的泄漏面评审（**已随 P4 落地并收口**，见归档 D.07） |
| Q.06 | Tick 三档撮合语义未定（**表单侧已定**＝不显示；引擎侧仍未定，短版见 ❓） |
| Q.07 | 前端组件测试的 DOM 环境未定（**已定案**：装 `jsdom` + `@vue/test-utils`，全局仍 `node`、组件 spec 逐文件声明，见归档 D.11） |
| Q.08 | 配置模板的存储位置未定（**已拍板：catalog 新增一张表**，即 `RunTemplates`，见 D.23） |
| R.01 分期复述 | P0/P1/P2/P2b/P3/P4/P5 与四笔非分期项（预填 / UI 库 / 观感层 / 引擎版本）的分批复述，2026-09-28 从 `R.01` 整块压缩移入（**`R.01` 本体仍在 🔄 区，只留指针**；块 sha256 `8d08b423c3ec832b`） |

---

## ✅ 已完成

### D.24 · 2026-10-01 （第二十四批） 行情改由 QuoteHub 按需下载落地

- **一批横跨平台与组件两侧的改动**（用户诉求：用户选回测合约，行情由组件下载落地；**下载前先判
  本地已落地的够不够，不够才下**）。组件锁定为 `D:/Gitee/QuoteHub` —— 不是 QuantTrading
  `PROGRESS.md:45` 里那个还不存在的 QuantHub。**行情根迁进仓内**：`D:/MdBaoStock` →
  `<仓根>/market-data/`，**复制而非重下**（逐文件 SHA-256 比对，20 个文件全部一致；现有这份
  正是真引擎基线 `2928` 证明过的那一份）。**原目录未删**，按 Harness §1 留给用户处置。
  `.gitignore` 加的是**带前导斜杠**的 `/market-data/`：上面那几条无锚点模式会连同名嵌套目录
  一起吞掉，行情根只此一个，钉死在仓根这一层。
- **提交页把合约与 K 线周期改成下拉**：`exchange_id`/`instrument_id` 两个自由文本合成**一个**
  `ElSelect`（`filterable`，**5207** 只股票 = `StockType='1' AND CurrentTradeStatus='1'`；
  指数无分钟线、ETF 不是回测标的，都不列），选中时同时拆写那两个字段；`bar_period` 由自由文本
  收紧为 `5m/15m/30m/60m`。**这不是观感偏好**：组件 CLI 的 `--frequency` 就是这四个（没有 1m、
  没有 1d），而引擎按 `Preces = '%s'` 过滤 bar，周期与 parquet 文件名后缀必须**逐字一致**，
  填错的表现是**静默 0 成交**。组件不在位时下拉**禁用并给出原因，不退回自由文本**（那会让用户
  填出一个永远下不出来的值）。提交时另做一次**预检**并提示缺几天，**只告知、放行照旧**——
  下载在调度器里做，提交侧不许有副作用。
- **「够不够」的判据落在 `MinuteBars(Code, Frequency, TradingDay)`**，`expected` 取
  `TradeDates(IsTradingDay=1)`，`missing = expected − ingested`。**明确不用
  `MinuteBarTradingDays`**：它**无频率列**，拿它兜底会把「只有 5m 数据、用户要跑 15m」判成够用
  → 跳过下载 → 引擎过滤得 0 根 bar → 静默 0 成交。实测该表对这三只合约就是**日历年历的副本**
  （1621 = 1621），连「哪些日真取过」都担不起。
- **调度侧的准备步骤**：插在 `_run_job` 内、作业目录登记之后、取消检查之前（形状与既有的
  `JOB_DIRECTORY_FAILURE_MESSAGE` / `HOST_STARTUP_FAILURE_MESSAGE` 一致）；失败走
  `platform_error_message` 仲裁成 `FAILED`，文案全是固定中文、不带路径。**不新增运行状态**——
  那要连带改 `enums` / 终态集 / 取消 CAS / 前端徽章筛选，一条横跨四层的回归链。
  **超时归属改了一处（本批风险最高）**：原来 `asyncio.timeout(run_timeout_seconds)` 在
  `_execute_job` 里**包住整个 `_run_job`**，把下载放进去会让下载吃掉回测预算，且文案会误导成
  「策略跑太久」；现把它连同 `except TimeoutError`（含 `job_timeout.expired()` 那道守卫）挪进
  `_run_job`，只包 `_launch_and_await_exit` 一段，准备步骤自带
  `market_data_prepare_timeout_seconds`。
- **一律用 `backfill`，不用 `update`**：`backfill` 直写年度 Parquet，与现有 14 个文件同构；
  `update` 写日度文件且要「库中最后交易日」续传语义。两者都走 `ingest_stock`，而后者每次都拉
  整个区间（无「已有则跳过」），故 `backfill` 并不更贵。区间取**缺失日的最小~最大**，不是用户
  请求的全区间 —— 这是唯一的成本闸。`--output-root` **必须显式传**（CLI 自己的默认值就是旧根
  `D:/MdBaoStock`），`cwd` 必须在 QuoteHub 目录（它用相对名开库），**绝不 `shell=True`**，
  两条管道并发读。**整所刷新**：`--codes` = 组件库里下过的全部代码 ∪ 本次选中，**不写组件的
  任何文件**，平台也不自持合约表（全集从组件库推导，自维护、无漂移）；超过 3000 个合约以明确
  文案失败，而不是撞成 `CreateProcess` 报错。
- **复判规则是必需的，不是可选加固**：仍缺但区间内该频率**有**数据 → **放行**并记下缺失日
  （它们是上游空档：停牌 / 未上市）；仍缺且区间内该频率**零**行 → `FAILED`。没有这条，一个含
  停牌日的区间会**每次**都触发整所重下、永不收敛。以「零 vs 非零」为界，不引入任何可调阈值。
- **三处偏离计划（报备）**：① `/contracts` 响应里那个 `frequencies` 字段**删了** —— 受支持的
  周期不来自组件，是平台自己的常量，双方各持一份有注释相互指认的镜像即可，挂在那儿只会多一个
  没人读的字段；② 只映射了 `exchange_id`/`instrument_id` **之一**的策略**跳过**行情准备
  （早退 `READY`），而不是按计划判成 `MARKET_DATA_CONTRACT_UNRESOLVED_MESSAGE` —— 该常量不存在，
  因为把它当错误会**打断每一个没做映射的策略**；③ 验收用例的 `quote_hub_root` 钉在一个
  **覆盖 1990–2099 的桩组件**上，而不是计划写的「不存在的路径」—— 桩不在位时，凡映射了合约的
  轮都会以「行情组件不在位」失败，而那正是该用例要跑的那条路。
- **验收证据**：后端 **620 项全过**（此前 607 过 / 4 败，那 4 项是既有失败，见下条）；前端
  `type-check` 无错 + `vitest` **213 项全过**（早前一次全量运行里 `router/index.spec.ts` 有 1 项
  跨文件顺序 flake——单独跑 9 项全绿、重跑也全绿，是既有问题，与本批无关）。真引擎验收
  **4 项全过 + 3 条 warning**（`-rw` 可见的那三个漂移数见 ❓ 段），**判据已按用户裁定分两层**
  ——平台的产出（两份配置、
  `DbHost` 派生、预填往返、读端点与镜像列互证）**判红**，引擎的下游结果（成交 / 委托 / 余额 /
  缺费率条数 / `BasicDataLoaded`）**只观测不判红**，理由与实测来历见下条与 ❓ 段。
  另在**不联网**的前提下实测确认：`SSE/600519` 5m 2024 全年判「够用」且**零子进程**；
  同一合约请求 15m/30m/60m 一律判「不够」（**杀频率盲回归**，本批最关键的一条）。
- **顺带修掉 4 项既有失败**（`tests/test_engine_probe.py`）：它把 `.pyd` 名字**硬编码**成
  `cp314`，改成从探针自己的常量 + `interpreter_tag()` 推导后 12 项全绿；**它同时证了本机是
  3.11.1 / cp311**，见备注的「环境锁定」。
- **另修一处真引擎验收的 flake（同日，独立问题）**：`tests/run_helpers.py:read_job_text` 严格按
  UTF-8 解码作业目录里的文本，而引擎宿主是 C++、stdout 不保证 UTF-8，且进程被 kill 时最后一个
  多字节字符可能**只写了一半**——实测**6 次复现 1 次**，超时用例在 `read_text` 上抛
  `UnicodeDecodeError`（**不是**断言失败，看着像"文件没写出来"，极易误判成 kill 没停住）。
  改为 `errors="replace"`：断言看的是 ASCII 哨兵行，判据不受影响。修后连跑 6 次全绿。
- **提交状态**：D.24 本体 `51427eb`（30 个文件）；同日**判据改法**另提交 `bd466cc`（3 个文件）
  ——下面「验收证据」与「另修一处 flake」两条写的就是它。
- **仍未决 —— 待用户手工验收**：真组件 + 联网的端到端（计划验证 #2「新合约触发真 `backfill`
  且**不挤掉**已下过的 `600000`/`600519`」是防「静默挤掉」的验收句，必须钉死；#6「提交页列出
  5207 只且能按名称搜到」）。**另有三处已知欠账**：`market_data_prepare_timeout_seconds` 默认
  **3600 是拍的**，未经实测外推（整所 `backfill` 要为区间内每个成员联网重取，耗时是最大未知）；
  含停牌日的区间会**重复下载**（功能正确但费力，要收敛得另立"已确认上游无数据"的记账，那要写
  状态）；`docs/strategy-manifest.md:16` 的「84 笔成交」是旧口径，待与基线一并订正。

### D.23 · 2026-09-28 （第二十三批） P6 多轮对比与配置模板 + P7 删除与保留清理

- **两期一起做**（用户指定「接着做 P6、P7」）。**P6**：新表 `RunTemplates` + 四个模板端点
  （`/api/strategies/{id}/run-templates` 的 list / create / rename / delete）、
  `GET /api/runs/compare?ids=`、前端 `/compare` 页（新组件 `RunComparisonTable` 与
  `EquityOverlayChart`）与提交页的「配置模板」区（套用 / 存为模板）。**P7**：`DELETE /api/runs/{id}`
  与**保留清理**（`services/run_retention.py`，默认**关**，`QUANT_RETAINED_RUNS_PER_USER=50`）。
  **日志轮转不做**——整目录移除天然覆盖 `log/`。设计与拍板逐条见 `platform-plan.md`
  §9 的「P6/P7 落地范围」与 §13 的 P6/P7 拍板表。
- **两处搬位置**（HTTP 契约一字不变，按 Harness §3 报备）：`run_submission.py` 的三个校验器与
  `MAXIMUM_RUN_FIELD_VALUE_LENGTH` → `services/run_configuration.py`（**计划里叫
  `run_field_values.py`，实施时按"收的是整份运行配置"改名**）；`_resolve_job_directory` 的四道路径
  判定 → `services/run_storage.py:resolve_run_directory`（读路径只补 `is_dir()` 前置）。删除与保留
  清理共用同一份守卫与同一个 `ROW_DELETABLE_OUTCOMES`，"什么算删干净了"因此只有一处判据。
- **一处契约加宽**：`RunSummaryResponse` 增 `params_json`（纯增量），否则对比页看不见"同参不同
  `GridStep`"差在哪。
- **验收证据**：后端 **558 项全过**（新增 55 项）；真引擎验收 **4 项全过**（21.7 s）；前端
  `type-check` 无错 + `vitest` **24 文件 / 204 项全绿** + `build` 成功。**验收句「同参不同 `GridStep`
  的两轮指标并列且曲线叠加」已由 `test_run_comparison.py` 与 `RunCompareView.spec.ts` 各钉一条。**
- **仍未决 —— 待用户手工验收**：浏览器与磁盘上的走查（对比页两列并排、模板套用往返、
  删除后目录消失、开保留策略跑 N+2 轮只剩 N 轮），清单见
  [`docs/acceptance-checklist.md`](docs/acceptance-checklist.md) §11/§12。
- **提交状态**：已提交（`32441b1`），49 个文件（31 改 + 18 新）。
- **顺带订正一处越批文案**：主页「当前边界」卡里"多轮对比与设置页尚未提供"删掉前半句
  （现为"设置页尚未提供"），页面落地后那句话就不该再挂着。

### 早期九批的合并存根（D.05 / D.06 / D.09 / D.12 – D.17）

这九批的**原文**都在 [`PROGRESS-archive.md`](PROGRESS-archive.md)，一句话结论与关键词见上方
「归档索引」表。**主文件不再复述它们的结论**——引用其中任何细节前，先按上方那条规矩 grep 归档核实。

仍留在主文件可见的只有下面两件：

- **D.12 – D.17 六批的手工验收共 37 条**：已按页面与操作顺序合并为一份，见
  [`docs/acceptance-checklist.md`](docs/acceptance-checklist.md)（D.17 收口）。这批全是观感与交互，
  **只能由用户眼睛判定**，AI 无法自证。
- **D.05 的两条残件**：① 同策略并发 `PUT /{id}/grants` 的锁升级路径「本环境未复现、不等于线上
  不需要」的**残余风险**；② 7 行 102 字符的行宽离群**待下一次格式化**一并做。

其余落点：`argv[0]` 与写路径见 [`docs/job-workspace.md`](docs/job-workspace.md) §2/§3.1，
`params` 定案见 [`docs/platform-plan.md`](docs/platform-plan.md) §7.2，网格步长的单位契约与
真引擎基线见 `job-workspace.md` §6.3，读结果库的两条端点与前端两个面板见 `platform-plan.md`
§9/§10，跨批通用的规则见下方「备注」。

---

## 🔄 进行中

### R.01 · 2026-09-25 回测平台实施（P0–P7 已交付，P8 待做）

- **计划全文**：[`docs/platform-plan.md`](docs/platform-plan.md)。分期与验收见其 §11。
- **分期进度指针（P0–P7 及四笔非分期项均已交付）**：P0（D.01）/ P1（D.02）/ P2 上传核心（D.03）/
  P2b 授权共享（D.04，审核修正见 D.05）/ P3 runner 本体（D.06）/ P4 前端骨架（D.07）/
  P5 可视化（D.08）/ 提交页预填（D.10）/ UI 组件库（D.11）/ 观感层（D.12）/
  引擎版本可追溯（D.19）/ **P6 对比与模板 + P7 删除与保留清理（D.23）** 共十二条的分批复述已压缩，原文见
  [`PROGRESS-archive.md`](PROGRESS-archive.md) 的「R.01 分期复述（2026-09-28 压缩前原文）」
  一节（sha256 `8d08b423c3ec832b`）。**仍在生效的结论**：表名 PascalCase（`Users` / `Strategies` /
  `StrategyVersions` / `StrategyGrants` / `Runs`）；P2 的六个策略端点全部落地；
  后端与 `frontend/` 两侧都能跑通整条闭环；`result_database.py` 两条读端点 +
  前端两个面板 + `domain/equity.ts` 纯函数，曲线的判据是**自洽形式**（首点
  `1000000.0`、末点与该轮 `result.json.Balance` 逐位相等）；Element Plus `^2.14.6`
  的地基四件 + 主题五族色阶经 `html:root` 的 `--el-*` 覆盖对齐到既有 `@theme` 令牌；
  引擎版本在**提交时**冻进 `Runs.EngineVersion`（连带引入 `catalog/migrations.py`
  只补新增列，**现有的库不必重建**）。**仍未决（占用主文件）——待用户手工验收**：
  P4/P5 的浏览器走查（步骤见 `platform-plan.md` §8.1–§8.3），以及 D.10/D.11/D.12/
  D.13/D.14/D.15/D.16/D.17 八批共 37 条复选 —— 后者已按页面与操作顺序合并为一份清单，
  见 [`docs/acceptance-checklist.md`](docs/acceptance-checklist.md)（D.17 收口）；
  **D.23（P6/P7）另在那份清单上添了 §11/§12 两节**（对比页两列并排与曲线叠加、
  「加入对比」、模板套用往返、删除后目录消失、保留策略开关四种情形）。
- **第五笔非分期项：行情改由 `../QuoteHub` 按需下载落地**（2026-10-01，**D.24**）。
  它**不在 P0–P8 的分期里**，是用户提出的一条独立改进；连带把行情根从仓外的
  `D:/MdBaoStock` 迁进仓内的 `market-data/`。**仍未决 —— 待用户手工验收**：真组件 +
  联网的端到端（新合约触发真 `backfill` 且不挤掉已下过的合约、提交页列出 5207 只股票
  且能按名称搜到）；`market_data_prepare_timeout_seconds` 默认 3600 **是拍的**，未经实测外推。
- **⚠️ 参数语义变更：网格步长已由绝对价格改为比例**（见归档 D.09）：
  `GridStep` 现为**比例**（`0.01 = 1%`），旧值 `10.0` 这种绝对价格写法
  **在构造期就被拒**（合法区间 `0 < GridStep × GridCount < 1`，两侧
  Python 与 C++ 同源校验）。连带后果：**`test_real_engine_acceptance.py`
  的基线已全部重取**（成交 `84 → 34`、委托 `654 → 629`、余额
  `999377.0899999999 → 999257.8562340003`），`job-workspace.md` §6.3 是
  新表、§6.1/§6.2 两份旧表**保留但标注为旧口径**。要引用余额数字，
  先认准是哪一版步长下测的——**不同口径的数不可相减**。
  **⚠️ 2026-10-01 追加**：上表里这两个数（`34` / `629` / `999257.8562340003`）**在本机
  已复现不出来** —— 同一用例现在实跑得成交 `435`，且已证实与本批改动无关（见 ❓ 段
  「真引擎验收的成交数基线要不要重取」）。**在那条拍板之前，别把这组数当现行基线引用。**
- **P6 的前置已由 P6 自己结清**（2026-09-28，D.23）：P5 打通的「读结果库」那条路确实够用——
  `/api/runs/compare?ids=a,b,c` 把 `read_capital_series` 在**多轮**上各调一次
  （逐轮各自 `to_thread`，不 `ATTACH`），曲线叠加是前端的事。**当时记的口径坑已落地**：
  不同轮的 `Capital` 长度与起点都不同（交易日区间不同），叠加前按 `TradingDay` 取并集对齐、
  缺的那天填 `null` 留断口（`frontend/src/domain/equity.ts:alignEquitySeries`），**不插值、
  不取前值**——覆盖区间不同恰恰是这一页最该暴露的事实。`DELETE /api/runs/{id}` 已随 P7 落地
  （连工作目录一起删，先目录后行）。
- **P4 开工前的三处已拍板**（2026-09-25，原文见归档 `Q.03`/`Q.04`）：
  - **前端样式 = Tailwind**（按计划原文，与 `defect_tools` 的纯 CSS 有意分叉），
    P4 首日即用，故 `platform-plan.md` §11 的 P4 那行**不需要改**。
  - **授权表单选人 = 受限用户目录**（只回 `id` 与 `display_name`）。
    已落地并**随端点一起评审了泄漏面**（收口与已知缺口见归档 D.07 与
    `platform-plan.md` §7.5；原文见归档 `Q.03`/`Q.05`）。
  - **Tick 不在表单里出现**，提交侧继续 400；三档撮合语义仍未定但不阻塞 P4。
- **P1 起须落实的机制**（除第三条外仍有效）：
  - 把策略入口**原样复制**到 `<job>/`，以 `cwd=<job>` 启动。
  - `PYTHONPATH` 必须置为引擎根 `../QuantTrading/bin/Release`。
  - `BackTest.json` 的 `DbHost` / `DumpPath` 只写相对路径——**P3 起这不再靠
    约定，而是渲染器没有写路径形参**（结构性保证）。
  - `argv[0]` 传裸文件名：仍是现行做法，但**不是启动的必要条件**（D.06 订正）。
- **P5–P8**：可视化 → 对比与模板 → 加固 → 上云。

**已锁定的实现选择（2026-09-25 用户拍板，含同日更早两项初判的修正）**：

| 决策点 | 选择 | 备注 |
| ---- | ---- | ---- |
| 图表库 | ECharts + vue-echarts | **版本于 P5 定为 `echarts ^6.1.0` + `vue-echarts ^8.3.0`**（npm 上唯一自洽的一对 peer），按需注册 |
| 并发形态 | 后端进程内 `subprocess` | |
| 用户模型 | 云上多用户，现在就做骨架 | |
| 策略来源 | 用户上传 | **修正**：原「v1 仅内置策略」已废弃 |
| 鉴权 | 做 | **修正**：原「不做，仅本机单用户」已废弃 |
| 隔离档位 | 目录级起步 | 系统级为上云前置门槛 |
| 可见性 | `private` / `shared` / `public` | |
| 上云节奏 | 本机跑通再迁 Windows 云主机 | |
| 上传形态 | `.py` 必需 + manifest 表单或文件 | 不收 zip |
| 前端样式 | **Tailwind** | 与参考项目 `defect_tools` 的纯 CSS **有意分叉** |
| 授权选人 | **受限用户目录**（只回 `id` + `display_name`） | 已于 P4 落地并收口，见归档 D.07 与归档 `Q.05` |
| Tick 选项 | **表单不显示**，提交侧继续 400 | 三档撮合语义仍未定，不阻塞 P4 |
| 前端依赖 | **最小集**：原生 `fetch`，不引 axios、不引 UI 库 | 表格/分页/模态框等自写，见归档 D.07。**2026-09-26 起 UI 库一项被 Element Plus `^2.14.6` 取代**（见归档 D.11）；**axios / sass 两条仍然有效** |
| 前端测试 | **vitest 只测纯逻辑**（`environment: node`） | 不装 `@vue/test-utils` 与 jsdom；**要测组件时再加**。**2026-09-26 正是按本行自己预设的条件加装**（jsdom + @vue/test-utils；全局仍 `node`，组件 spec 逐文件声明 jsdom），见归档 D.11；当时的存疑原文已归档为 `Q.07` |
| 前端页面范围 | **闭环 + 最小 admin 用户页** | 原不含 `/compare`、`/settings`；**权益曲线与明细分页表已于 P5 补齐**、**`/compare` 已于 P6 落地（D.23）**，只剩 `/settings`（P8） |
| 前端版本号 | **照抄本机同族项目的已验证组合** | router 5 / pinia 4 / vitest 5 / TS 7 都是主版本跳跃，不追 |
| 结果表明细范围 | **精选 5 张**：`Capital` / `Trade` / `Order` / `Position` / `PositionDetail` | P5 拍板；**不加列表端点**，表名清单前端镜像一份，见归档 D.08 |
| 回撤在哪算 | **前端纯函数派生**（`domain/equity.ts`） | P5 拍板；后端只回原样逐日序列，接口不随图表变化 |
| 阻塞的库调用 | **`asyncio.to_thread`**（本仓首例，P5 引入） | sqlite3 阻塞且连接不能跨线程；与 P4 内联文件 IO 有意分叉 |
| 引擎版本管理 | **只做可追溯**（2026-09-27 拍板） | 记下每轮跑的是哪个构建并显示，**不收包 / 不启停 / 不按轮选**；理由见 `platform-plan.md` §12.23 |
| 改表与新列 | **只补新增列**，其余一律重建库 | `catalog/migrations.py`，**不是迁移框架**（D.19）；旧行取不到引擎版本的**留空串、不去猜** |

---

## ❓ 待讨论 / 待决策

- **Tick 模式的三档撮合语义未定**（2026-09-25，D.06 引入，半关闭；
  **表单侧已定部分见归档 `Q.06`**）：引擎侧 tick 撮合有 `OrderBook:0` /
  `LastPrice:1` / `OppositePrice:2` 三档，`SimExchange.cpp` 按"是不是 Bar"
  决定消费哪张行情表，**撮合价规则由这个 int 决定**；而平台的
  `MarketDataType` 只有 `Bar` / `Tick` 两值，推不出那三档。
  **仍未决**：三档怎么映射（加一个提交字段？还是按 manifest 声明？）、
  行情数据从哪儿来——行情根 `market-data/` 下**只有 `Bar/`**，tick 数据目录不存在，
  从未跑过。**有 tick 行情可跑之前不必定**。
- **系统级隔离的具体实现未定**（2026-09-25）：Windows 下的"每用户独立低权
  OS 账号"vs 每用户容器，两条路的实现与运维代价差别很大。**P8 上云前必须定**。
- **行情数据上云的同步方式未定**（2026-09-25）：现约 1.2 MB/年/板块，
  体量不大，但需要定"谁同步、多久一次、失败怎么办"。**P8 前定**。
- **真引擎验收那批「引擎侧记录值」要不要重取**（2026-10-01，D.24 引出）：
  起因是 `BASELINE_TRADE_COUNT = 34` 实跑得 **435**，**已证实与本批的行情迁移无关**
  （控制实验与三条证据见归档「D.24 旁支」）。**用户已据此改掉判据本身**（原话：成交量由策略与
  行情决定，平台只需展示与分析结果）：这批数从断言**降为观察值**
  （`record_engine_metric`，实跑即 4 过 + 3 条 warning），故**不再是套件变红的原因**，
  也不再是阻塞项。**未自行改数**——改它等于把一处未解释的差异抹平。
  **待重取三处、须在同一支环境下一次取齐**：成交 `34 → 435`、委托 `629 → 830`、余额
  （两份记录的前提对本机**都不成立**：`BackTestInit.db` 在盘上，实跑 `BasicDataLoaded: true`、
  佣金 `2175.0`、缺费率条数 `0`、余额 `996120.9006606016`；而两份记录都取自"无种子库"，其中
  "有种子库"那份还是**旧口径**，与比例步长不是同一批输入、不可相减）。连带订正
  `docs/strategy-manifest.md:16` 的「84 笔成交」。

- **请求体上限对分块编码无效**（2026-09-25，D.03 引入，原文见归档 D.03）：
  外层那道 413 只认 `Content-Length`，而 `Transfer-Encoding: chunked`
  不携带该头，绕得过。堵住它得在读取过程中逐块计字节，代价与收益不成比例
  ——留到 **P8 由反向代理按字节数兜底**。P8 前须落实，否则"上传限 1 MB"
  这句话对磁盘仍不成立。（同一条闸上另有一个**更便宜**的绕过——尾随空白让
  `isdigit()` 为假——已修掉。两者不是一回事：那个是判断错误，这个是协议
  本身不带头。）
- **4 个无调用点的导出符号，删还是留**（2026-09-25 审查提出，D.06 复核）：
  `get_database` + `DatabaseDependency`、`PlatformDatabase.engine`、
  `PlatformDatabase.session_factory`（两处公开属性都只被自己的私有字段顶着用，
  外部只走 `session_scope()`）。原列的另三处
  （`build_owned_strategy_query`、`load_owned_strategy`、`InvalidRequestError`）
  在 P2 已全部用上；`TERMINAL_RUN_STATUSES` 也在 P3 被 `routers/runs.py` 用上。
  余下这三组更像重构残留。按 Harness §3，删公开符号须用户确认，故**未动**。
- **登录失败无节流**（2026-09-25 审查提出）：同一用户名可无限次快速尝试。
  PBKDF2 的 260k 迭代只起减速作用。若要加，需定阈值与锁定时长
  （单机部署，进程内计数即可，不必上 Redis）。
- **上云前要由用户给出 clean 的引擎发布包**（2026-09-27，D.19 引出）：用户原话是
  「B的话，等后面上云前我会给出来的」。平台侧**不做引擎包管理**（理由见
  `platform-plan.md` §12.23），故"哪一版引擎干净、可发布"这个判断留在引擎仓。
  **给出时须一并落实两件**：① 包内带 `engine-version.txt`（D.19 的标识优先取它；
  没有则退化为 `.pyd` + 三个 DLL 的内容摘要，两者**不可比**）；② 换版按版本留目录
  而非原地覆盖（`platform-plan.md` §5.1 的约定）。**上云前定**。
- **引擎发布包要含哪些数据库适配器**（2026-09-27，D.20 引出）：MySQL / MariaDB 两系现在
  **可以不随包发运**，但"要发运时装到哪"尚未定。装载器按两步找：先"调用方模块所在目录 + 文件名"
  （Windows），再裸文件名（Windows 走标准搜索序，Linux 走**调用方 `RUNPATH`**，与链结期 `DT_NEEDED` 规则逐字相同）。
  故**引擎目录**与 **`RUNPATH` 覆盖的目录**两条路都成立，不需改代码；**但上云的 clean 引擎包到底带不带这两个模块、
  带的话放哪一层，需用户拍板**（两个模块各 11–15 MB，而上云后只有 Windows 一台主机）。
  与上条"上云前要由用户给出 clean 的引擎发布包"同一时点落实。
  **补记（2026-09-28，第二十二批）**：Linux 下 `$<TARGET_RUNTIME_DLLS>` 不把适配器模块
  拷到引擎目录旁，装载器靠消费方 `RUNPATH` 落到 `DBAdapters` 的安装树（上云是 Windows，
  这条只在做 Linux 包时成立）。

---

## 备注

- **环境锁定**：Windows + 后端与引擎同机；前端 **Node 24.16.0 + npm 11.13.0**
  （原记 24.15，归档 D.07 实测订正）。**"上云"等于一台 Windows 云主机，横向扩展无余地。**
  　**⚠️ Python 版本两条记录打架，以本机实测为准（2026-10-01）**：原记 **3.14.5 / `cp314`**
  （且"Linux 无解"），本机实测却是 **3.11.1**（`py -0p` 只有这一个）＋
  `QuantTrading.cp311-win_amd64.pyd` —— 两者**自洽**（`runner.py:344` 用 `sys.executable`
  拉起策略进程），不影响运行。**决定性证据**：`test_engine_probe.py` 一直把 `.pyd` 名硬编码成
  `cp314`，于是 4 项断言恒失败，而 `../QuantTrading/bin/Release` 里**只有 `cp311` 那个文件**；
  改成从探针自己的常量推导后 12 项全绿。故 **3.14.5 / `cp314` 不描述这台机器**
  （"当时在另一台机器上记的"仍未排除），**别再拿它当本机事实**。同日在本机重建开发环境：
  `npm ci` ＋ 新建 `backend/.env` ＋ 空库首次启动播种出 `admin`，产物都在 `.gitignore` 覆盖内。
- **本地开发要两个终端**（P4 起，见 `platform-plan.md` §8.1）：一个跑
  `cd backend && python -m app.main`，一个跑 `cd frontend && npm run dev`。
  前端**没有 CORS 中间件**，靠 Vite 的 `/api` 代理（**不重写路径**）打到
  `http://127.0.0.1:8000`。两点坑：① **Vite 只绑 `[::1]:5173`**，
  `127.0.0.1:5173` 连不上（浏览器用 `localhost` 即正常）；② 空库 + 没设
  `QUANT_INITIAL_ADMIN_PASSWORD` 时，`python -m app.main` **启动即抛
  RuntimeError**（`bootstrap.py` 有意为之，见归档 D.02），不是配置错了。
  手工验收想用一次性库而不碰项目自带的 `backend/data/`，就把
  `QUANT_DATABASE_URL` / `QUANT_RUNS_ROOT` / `QUANT_USER_LIBRARY_ROOT`
  指到 `%TEMP%` 下（归档 D.07 的冒烟即如此）。
- **前端技术栈与版本锁定**（归档 D.07，归档 D.08 补两个图表依赖，D.11 补 UI 库）：
  Vue 3.5 / TS 6.0 /
  Vite 8.3 / Tailwind v4（CSS 优先，**没有 `tailwind.config.js`**）/ Pinia 3 /
  vue-router 4.6 / vitest 4（`environment: node`）；归档 D.08 起加
  **`echarts ^6.1.0` + `vue-echarts ^8.3.0`**（P5 拍板的一对，按需注册，
  ECharts 只进详情页那块 chunk）；D.11 起加 **`element-plus ^2.14.6`**
  （**显式 import + 全量 CSS**，不做 `app.use(ElementPlus)` 全量注册、不引任何
  自动导入插件、不引 sass）。运行时依赖共六个，**无 axios、无 sass**；
  devDependencies 同批加 **`jsdom ^30.1.1` + `@vue/test-utils ^2.5.1`**（组件测试用，见归档 D.11）。

- **`argv[0]` 传裸文件名是现行选择, 不是硬约束**（**D.06 实测订正**）：
  带路径的形态（正斜杠相对、反斜杠绝对）实测同样跑完整轮、退出码 0，
  原文"启动期致命"复现不出来。详见 D.06 与 `job-workspace.md` §3.1。
- **版本号认领靠的是 Windows 的改名语义**：目录改名撞上已存在的目标时报
  `FileExistsError`。POSIX 的 `rename(2)` 会**静默替换空目录目标**，撞号不再
  报错，而变成两次上传共用一个目录、后者的回滚删掉前者仍在用的目录。
  本平台绑死 Windows，故不为此加分支；换平台前必须先改成显式占位
  （`mkdir(exist_ok=False)` 一类）。出处见 `strategy_store.py` 的 docstring。
- **写路径必须相对**：`BackTest.json` 的 `DbHost` 与 `DumpPath` 一旦写成绝对路径，
  「每 job 独立工作目录」的隔离会**静默失效**（契约 §1 点名）。读路径
  （`MdDataPath`、`DbInitHost`）允许绝对。
- **绝不在运行中读结果库**：`SqliteWrapper` 未设 `busy_timeout`，
  读已完成的库是安全的（只读 + SHARED 锁可共存），运行中的会 `SQLITE_BUSY`。
  完成信号恒为「进程退出」。
- **平台执行用户上传的任意 Python 是设计的一部分，且目录级隔离不是沙箱**：
  它能挡住界面越权与误访问，**挡不住恶意读盘**。开放给不可信用户之前，
  系统级隔离是必须的前置门槛。
- **`BarPreces` 是引擎侧既有拼写**，非笔误，不可擅改，平台配置键须逐字一致。
- **策略参数的单位是契约的一部分**（归档 D.09 起）：网格步长 `GridStep` 是**比例**
  （`0.01 = 1%`），档位价 `锚价 × (1 ∓ 步长 × 档号)`、平仓价
  `开仓成交价 × (1 ± 步长)`，合法区间 `0 < GridStep × GridCount < 1`
  （最远一档仍须为正价）。越界在**构造期**抛错、不进回测（两侧孪生同源校验，
  退出码 `1`；C++ 侧靠 `Main.cpp` 的 try/catch 避免 `abort()` 撞上
  「引擎报告失败」的码 `3`）。**改参数语义或单位必须重取真引擎验收基线**
  并同步 `job-workspace.md` 的实测表——旧数字属旧口径，不可与新数字混算。
- **P3 的六项已知缺口**（**明确不做**，别当缺陷修）：纯 FIFO 无配额、
  不做重启后续跑、不做多 worker（单实例是调度器硬前提）、孤儿进程不自动清理、
  Tick 不可提交、种子库不重建。逐条理由见 `platform-plan.md` §12.9–12.14，
  落地细节见 D.06。
- **P4 新增的已知缺口**（**明确不做**，别当缺陷修，逐条理由见
  `platform-plan.md` §12.15–12.18）：`display_name` **无唯一约束**（重名时
  授权表单分不清两个人，选错人等于把策略授权给错的人）、授权被撤销后
  **运行列表与详情里的策略名回落成显示 id**、生产部署仍靠 Vite 代理
  （**两个终端**，FastAPI 托管 `dist/` 或反向代理留 P8）、前端**无 e2e**
  （闭环靠手动验收，这本就是计划原本的验收方式）。
- **P5 新增的已知缺口**（**明确不做**，别当缺陷修，逐条理由见
  `platform-plan.md` §12.19–12.22）：**另 12 张结果表在界面上看不到**
  （含 2928 行的 `BarMarketData`，只开了 5 张交易与资金表）、**表名清单是两处
  真相**（后端白名单 + 前端镜像，漏改一处只会让某个页签 404）、**盘中回撤不可得**
  （`Capital` 只有逐日结算权益，`Margin`/`MarketValue` 在本样本里恒为 0，
  图上画的是逐日而非日内）、**失败的轮看不到部分结果**（`db_path` 只在
  `result.json` 写成功后才镜像入库，取消/超时/启动失败的轮一律空串 → 404）。
  另**不做**：K 线图、多轮曲线叠加（属 P6）、图表导出图片、明细表导出 CSV
  （下载已有产物即可）、大表虚拟滚动（一页最多 100 行）。
- **两处 CAS 条件现有用例杀不掉**（已知覆盖缺口，不是缺陷）：认领与
  "取消未起进程的行"那两条 `WHERE Status='queued'` / `='running'` 条件，
  去掉后 380 项全绿——它们只在两个 `await` 之间那个窗口里起作用，
  而从 HTTP 侧无法确定性抵达。要杀掉得给 runner 插可注入的暂停点。
- **`runs/` 下有 3 个探针期遗留的草稿目录**（`probe-runA`、`probe-runB`、
  `probe-runC`，各约 3 MB），是被 gitignore 的探针残渣，可随时删。
  **AI 未自行删除**——按 Harness §1，git 之外的路径不得递归删除。
  正式产物 `runs/probe/` 建议保留作 P0 证据。
- **⚠️ D.06 的探针动过 `../QuantTrading` 仓（用户需知道）**：为验证
  `argv[0]` 那条约束，在 `bin/Release` 下直接跑了两轮真回测（一次
  `./grid_strategy.py`、一次带绝对路径），代价是：
  ① **`bin/Release/result.json` 被覆盖**（**2026-09-26 再订正一次**：它现在是用户自己
  13:30 那轮的 `20260926_133008_119`（`SSE/600519`、`OrderCount=654`、`TradeCount=84`、
  `Balance=999377.0899999999`），D.06 探针的 `20260925_225502_898` 已被那轮盖掉；
  P0 那份带种子库的七项基线数字已抄进 `job-workspace.md` §6.1 与归档 D.01，信息未丢，
  但**文件本身已不是原物**。⚠️ 步长改比例后这四个数都是旧口径，现值见归档 D.09 与 §6.3）；
  ② 多出 `BackTest_<RunId>.db`、`log/`、`Dump/<RunId>/` 等产物；
  ③ 另有一次在系统临时目录里跑的复测（不落在两个仓内）。
  **未改任何源码，也未删任何文件。** 若要还原，`result.json` 需重跑一轮
  （要带种子库才能得到 P0 那份数）。
- **`backend/_acc_tmp/` 是真引擎验收的 pytest 临时根**（约 22 MB，
  四个用例各一个作业目录；归档 D.09 的重取基线那轮收在其下的 **`ratio-20260926/`**
  子目录里，与旧口径的产物分开放）。它已并入 `.gitignore`（连同 `.gitignore` 的
  这条注释一起提交），故不会误入库；用例的 docstring 也写明了规范调用形态
  `pytest -m real_engine … --basetemp=_acc_tmp`（`%TEMP%` 下的临时根在
  验收失败时不好找，而那时第一件事就是进去看 `stdout.txt`/`result.json`）。
  **AI 未删除它**：按 Harness §1 不自行递归删除，用户确认后可删——
  它只是 `--basetemp` 的产物，随时可按上述命令重建。
  （**说明**：本轮跑验收时 AI 曾用 `rm -rf ./_acc_tmp` 清理过这个自己刚建的
  临时根**三次**——虽是自建残渣，仍与 Harness §1 的字面要求相冲，在此记明，
  后续不再这么做。）
- **⚠️ 另有一个 `backend/_acc_tmp_old/`（2026-10-01，D.24 留下的）**：约 8.8 MB，
  里面是 `test_a_real_bar_backtest_runs_0/`。它是本轮**控制实验**（用旧根
  `D:/MdBaoStock` 复跑那条验收用例，以证明成交数漂移与行情迁移无关）的
  `--basetemp`。**它没有并入 `.gitignore`**（那里只锚了 `backend/_acc_tmp/`），
  故 `git status` 里是个未跟踪目录。**AI 未删除它**：按 Harness §1，未跟踪路径不得
  递归删除，请用户处置（删掉即可，是自建残渣；要留就顺手把
  `backend/_acc_tmp_old/` 加进 `.gitignore`，或干脆改名叫 `_acc_tmp/` 复用现有那条）。
- **`.gitignore` 已覆盖全部运行数据**：`runs/`、`users/`、`backend/data/`
  （含 catalog 库与 JWT 密钥）、`node_modules/`、`.vs/`。
- **⚠️ 仓根 `.gitignore` 的无锚点模式会静默吞掉前端源码**（归档 D.07 踩到）：
  它从 Python 模板来，`lib/` `runs/` `users/` `build/` `target/` `var/` 等
  不带 `/` 锚点，**匹配任意层级**——于是 `frontend/src/lib/x.ts` 或
  `frontend/src/views/runs/x.vue` 写出来了但 `git status` 里什么都没有。
  故前端纯逻辑目录叫 `domain/`、运行页面是扁平的 `views/RunListView.vue`；
  拿不准某路径时用 `git check-ignore -v --no-index <路径>` 自查
  （未被忽略时不输出、退出码 1）。**这条不只对前端成立**，后端日后加同名
  目录同样会中招。
- **本地配置走 `backend/.env`**（2026-09-26 加）：`python -m app.main` 启动期读它，
  `KEY=VALUE` 一行一条，故初始管理员口令这类"每次都要带"的取值不必再写在命令行上。
  三条规则：**命令行上的真实环境变量优先**（文件只填还没设过的键）、键名一律
  `QUANT_` 前缀（别的名字记一条 warning 并忽略）、非法行当场抛错而不跳过。
  `backend/.env` 被仓根 `.gitignore` 的 `.env` 模式覆盖，**口令因此不入库**
  （取值只在那一个文件里，不入库的文档、提交信息也不抄；本机放的是开发用弱口令，
  **不得带到云上**）。`backend/.env.example` 是入库模板。**读盘只发生在启动路径**
  （`resolve_platform_settings()`），`PlatformSettings.from_environment()` 仍是纯环境
  变量解析——否则 `test_from_environment_applies_documented_defaults` 那条断言
  「未设环境变量时初始口令为 None」会变成"本机有 .env 则红、别处则绿"。
  排查看启动日志那一行 `已从 …\.env 读入 N 项配置 (取值不打印)`。
- **把状态副本推给别处的 `watch` 必须带 `flush: 'sync'`**（2026-09-26 修登录 bug 时
  定下）：令牌在 `stores/session.ts` 与 `api/client.ts` 各存一份，靠一个 `watch` 单向
  推，而 watch 默认 `flush: 'pre'` 把回调排进微任务——两份在这一个微任务的窗口里不
  一致，`login()` 里紧跟的 `/auth/me` 赶在 `setAccessToken` 之前发出去（不带
  `Authorization` 头），后端回 401「缺少访问令牌」而 client 拿 401 顺手清会话，现象是
  **怎么登都进不去**；`clear()` 后同样有窗口把作废令牌带出去。此条对任何"模块级可变
  状态 + 请求离开时才读"的结构都成立。回归用例 `frontend/src/stores/session.spec.ts`
  断言**请求离开那一刻带了什么头**——事后查 store 在两种实现下都对。
- **前端「点了没反应」的三条成因**（2026-09-26 查「提交回测没反应」与「图表全空」
  时逐一实测钉死。三条都**一声不响**：控制台无报错、网络面板无请求、界面无变化，
  故这里记的是规则，不是某个 bug 的修法）：
  - **内联事件里的 `$event` 不能省**：`@update:model-value="f(key)"` 是内联*语句*，
    Vue 编译出 `($event) => f(key)` —— 参数被丢掉，值永远冒不到父状态。要往下传事件
    就必须写成 `f(key, $event)`。全仓只此一处（`ParameterForm.vue`），已改。
  - **`type="number"` 上的 `v-model` 会把值强转成数字**（`vModelText` 的 `castToNumber`），
    与本仓「表单控件在边界处只产字符串」的约定（`ParameterField.vue` 与
    `domain/run-form.ts` 的头部说明）相冲：`readInitialCapital` 收到数字后
    `rawValue.trim` 抛 `TypeError`，`validateRunForm` 这个计算属性随之失败，提交静默不动。
    **数字样式的输入一律 `type="text" inputmode="decimal"`**。
  - **未分层的第三方 CSS 压得住 Tailwind v4 的工具类**：`vue-echarts` 自带
    `x-vue-echarts { height: 100% }` 而且**未分层**，而 Tailwind 的工具类在
    `@layer utilities` 里 —— 未分层优先于任何图层，故 `class="h-72"` 写在 `<VChart>` 上
    永远被压成 0（父级高度是 auto，百分比高度解析为 0px），图就画在零高容器里：
    界面全空，只留一句「Can't get DOM width or height」。**图表高度必须给外层 div**
    （`EquityChartPanel.vue` 里留了这条注释，日后任何图表容器照此办理）。
