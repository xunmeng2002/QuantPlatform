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

已关闭条目的**原文**在 [`PROGRESS-archive.md`](PROGRESS-archive.md)，其**检索索引**
（一行一条 ID ＋ 日期 ＋ 主题 ＋ 判据注记）在 [`PROGRESS-index.md`](PROGRESS-index.md)。
本文件不复述它们（2026-10-01 分层：主文件只留这个指针）。

**引用主文件未载的结论前，必须先按 ID 或关键词在索引里定位、再打开归档取原文核实**，
不得凭记忆断言。归档不参与会话开头的通读。

归档里的条目**可删可改、以简洁为准**（2026-09-18 用户修订），但改写必须保住
**ID、原日期、原结论连同「为什么」**——理由一丢，下个会话就会把已关闭的事重新议一遍，
那才是这条真正要防的损失。

---

## ✅ 已完成

### D.26 · 2026-10-01 （第二十六批） 提交页合约下拉改虚拟滚动：治「进页面点策略没反应」

- **症状与诊断**：用户报「进新建回测后，选择策略那里要好久才有响应」。**不是后端**——本机实测
  `/api/market-data/contracts` 返回 **5207 条只用 14.2 ms**（载荷 541 KB），catalog 库里就
  1 个策略 / 1 个版本 / 1 轮运行，QuoteHub 库 39 MB 且 `MinuteBars` 有
  `(Frequency, TradingDay)` 索引。**是前端主线程被冻住**：提交页的合约下拉用
  `ElSelect` + `v-for` 铺那 5207 条，而 Element Plus 的 `el-select` 有个 `persistent` 属性
  **默认为真**（`es/components/select/src/select.mjs`），tooltip 内容的渲染条件因此写成
  `shouldRender = persistent ? true : open`（`es/components/tooltip/src/content.vue…mjs`）
  ——**下拉内容在挂载期就渲染，根本不必被打开**。策略那一格只是用户进页面后第一件去点的事，
  冻的是那一下。
- **实测证据**（一次性 jsdom 探针，量完即删）：挂载时 DOM 里 0 个 `<li>`（合约还没回来）→
  合约灌入后 **5207 个 `<li>`**，而下拉框**一次都没被打开过**；耗时 200 项 196 ms /
  1000 项 811 ms / **5207 项 21–33 s**（jsdom 比真浏览器慢一个量级，真机估几百毫秒到两秒）；
  此后每次无关的父级重渲染还要再付 50–90 ms。
- **改动**：合约下拉换 `ElSelectV2`（虚拟滚动，EP 自带，**不引新依赖**、模板全量 CSS 已含它的样式），
  选项经新增的 `domain/market-data.buildContractOptions` 映射成它要的 `{ value, label }`
  （取值仍是组件主键 `sh.600519`，显示串仍是 `formatContractLabel` 那三段，故本地按代码 / 名称
  子串搜索一个字没变）。另外三个下拉（策略 / 版本 / 周期）选项个数都是个位数，不动。
- **验收证据**：前端 `type-check` 无错；`vitest` **209 项全绿**（204 → 209，新增 5 条）
  ——`domain/market-data.spec.ts` 3 条钉映射（取值是主键、逐条不丢、空清单回空数组），
  新增的 `views/RunSubmitView.spec.ts` 2 条**钉住形状**：挂载真页面 + 5207 条合约，
  打开**合约那一个**下拉框（不是页面上第一个）后，落进 DOM 的选项行数 **16**（判据写成
  `> 0 且 < 64`，不钉死 EP 的缓冲策略）；该 spec 全程 **1.11 s**，与旧形状的 21–33 s 同量级对比
  即为本批的证据。后端**零改动**。
- **提交状态**：见本批的提交（前端两文件 + 两个 spec）。

### D.25 · 2026-10-01 （第二十五批） 摘掉 manifest：策略配置 JSON 即表单模板，并解绑数据源周期

- **本批的触发点是一个类别错误**，用户先发现、再拍板整批：平台把用户在提交页选的 K 线周期
  写进**两处**——引擎 `BackTest.json.BarPreces`（那是**数据源精度**）与策略配置里的周期键
  （那是**策略的订阅目标周期**）。而磁盘行情只有 5m（14 个文件全是 `*_5m.parquet`），于是选
  15m 时引擎按 `Preces = '15m'` 过滤 → **零行** → 装载期 `ErrorMarketDataNotExist` → 整轮废掉。
  **订正**：这不是三份文档此前说的"静默 0 成交"（`platform-plan.md:373-376`、
  `job-workspace.md:72-78`、`strategy-manifest.md:73-77`），是**报错**；但详情报的是通用的
  「引擎报告本轮回测失败」，用户看不出是周期选错了。三处说法已按实订正。
- **六条用户拍板（不再重议）**：① 整个去掉 manifest，上传 = 一个 `.py` + 一个 `.json`；② **上传的
  配置 JSON 即参数模板**，键即参数、一票一个控件、**键集不可增删**；③ **参数校验彻底不要**（无标题 /
  无范围 / 无选项 / 无分组，控件形态由该键**当前取值的 JSON 类型**决定），参数合法性由策略自己守；
  ④ 约定键名固定为 `ExchangeId` / `InstrumentId` / `BarPreces`；⑤ **周期语义解绑**——引擎那份恒为
  平台常量 `5m`，策略那份是用户选的订阅周期（`5m`/`15m`/`30m`/`60m`，5m 的整数倍，由引擎在运行时
  聚合，`BarAggregator` 要求 `targetSeconds % inputSeconds == 0`）；⑥ 老策略版本**一刀切作废**，
  不做兼容回退，且**不删任何运行数据目录**（Harness §1）。
- **落点**：新增 `app/strategy_configuration.py`（两份文件的形状校验，纯函数），删掉
  `app/manifest.py`；上传改 multipart 两个文件部件；渲染改为「模板 + 用户编辑 + **覆写**三个平台键」；
  解码从「查该版本的 manifest」简化成**常量差集**（策略配置减去 `PLATFORM_KEY_NAMES`）——写路径与
  读路径引用同一份常量，两处不可能不一致；`config.py` 拆出 `MARKET_DATA_PRECISION = "5m"` 与
  `SUBSCRIPTION_BAR_PERIODS`；`/coverage` 去掉 `bar_period`（覆盖判据只问"数据在不在"，与订阅周期
  无关）；前端删 `domain/manifest.ts`、新增 `domain/strategy-configuration.ts` 与
  `useConfigurationFileSource.ts`，`ParameterField` / `ParameterForm` / 提交页参数区全部改为遍历模板键。
- **文档**：`docs/strategy-manifest.md` 与 `.example.json` 整份作废，替换为
  [`docs/strategy-configuration.md`](docs/strategy-configuration.md) 与
  `.example.json`（示例就是真引擎验收跑过的那一对）；`platform-plan.md` §7.1–§7.4 重写（§7.5 授权
  共享的编号未动），§6/§8/§9/§12/§13 与决策表的 manifest 措辞逐处跟上；`backend/.env.example` 与
  `job-workspace.md` 同步。
- **三处偏离计划（报备）**：① **存储**：计划写的"无需任何 schema 变更 / 复用 `ManifestJson`"，
  实施时改为**另起一列** `ConfigurationJson`（可空 `Text`），`ManifestJson` 降为**历史列**恒空串，
  "旧形态版本"由 `ConfigurationJson IS NULL` 表达 —— 那是**结构性**判据，比去猜旧 manifest 的键
  可靠；代价是库里多一列空列（`migrations.py` 只加列，保留它不花钱）。② 旧版本快照没有配置模板时，
  提交与预填走 `InvalidRequestError` 让用户重传，而不是回退到旧 manifest 解析。③ 不做
  「上传即拒策略自带的 `ExchangeId` 参数」——模板里写这三个键只会被覆写，写进文档比加一道闸
  更省事（§7.2 已写明）。
- **验收证据**：后端 **605 项全过**（5 deselected）；前端 `type-check` 无错 + `vitest` **204 项
  全绿**（**其中一次全量运行里 `router/index.spec.ts` 的「未登录时的守卫 · 公开的主页放行,
  且不为它去取身份」红了，紧接着重跑即 204/204 全绿**——就是 D.24 记过的那条**跨文件顺序
  flake**，既有问题，与本批无关；本批没碰 router）；真引擎验收 **5 项全过**（29.09 s，含新增的
  `test_a_subscription_period_finer_than_the_dataset_is_aggregated`——**它直接钉住本批修的那个
  类别错误**：断言引擎那份 `BarPreces == "5m"`、策略那份 == 提交的订阅周期，两者**必须不同**）。
  `BAR_MARKET_DATA_COUNT = 2928` 的基线不受订阅周期影响（引擎那张表装的是读进来的 bar）。
- **仍未决 —— 待用户手工验收**：浏览器里走一遍新上传契约（传 `.py` + 配置 `.json` → 提交页参数区
  按模板键渲染 → 选 15m 提交 → 回测跑通且策略收到 15m bar）；旧版本行在详情页应显示
  「该版本落在改形态之前, 没有配置模板」。
- **提交状态**：`8e96685`，64 个文件（2761 增 / 3490 删——删的比加的多，去 manifest 是净减法）。

### D.24 · 2026-10-01 （第二十四批） 行情改由 QuoteHub 按需下载落地

> **全条见归档**（2026-10-01 D.25 写完后主文件逼近 50 KB，整条移出；一句话结论与关键词见
> [`PROGRESS-index.md`](PROGRESS-index.md) 的 D.24 行）。留在主文件的是**仍未决的那半**：

- **仍未决 —— 待用户手工验收**：真组件 + 联网的端到端（计划验证 #2「新合约触发真 `backfill`
  且**不挤掉**已下过的 `600000`/`600519`」是防「静默挤掉」的验收句，必须钉死；#6「提交页列出
  5207 只且能按名称搜到」）。**另有三处已知欠账**：`market_data_prepare_timeout_seconds` 默认
  **3600 是拍的**，未经实测外推（整所 `backfill` 要为区间内每个成员联网重取，耗时是最大未知）；
  含停牌日的区间会**重复下载**（功能正确但费力，要收敛得另立"已确认上游无数据"的记账，那要写
  状态）；~~`docs/strategy-manifest.md:16` 的「84 笔成交」是旧口径，随手订正即可~~ ——
  **D.25 已把那份文件整个删掉，这条欠账随之了结**。
- **一处越批订正**：D.24 当时把「周期与 parquet 后缀必须逐字一致，填错 → 静默 0 成交」当判据，
  **D.25 已订正为「引擎装载期即拒、报错收场」**，并把周期语义整个解绑（见上）。

### D.23 · 2026-09-28 （第二十三批） P6 多轮对比与配置模板 + P7 删除与保留清理

> **全条见归档**（2026-10-01 移出）。一句话：新表 `RunTemplates` + 四个模板端点、
> `GET /api/runs/compare?ids=`、`DELETE /api/runs/{id}` 与保留清理（默认关）、前端 `/compare` 页
> 与提交页「配置模板」区；两处搬位置而 HTTP 契约一字不变。提交 `32441b1`（49 个文件）。

- **仍未决 —— 待用户手工验收**：浏览器与磁盘上的走查（对比页两列并排、模板套用往返、
  删除后目录消失、开保留策略跑 N+2 轮只剩 N 轮），清单见
  [`docs/acceptance-checklist.md`](docs/acceptance-checklist.md) §11/§12。
  **D.25 改形态后这批的走查项要重看**：模板里的参数键集由配置模板固定，换版本后旧模板可能
  带着新版本没有的键（提交侧以 `UNKNOWN_PARAMETER_MESSAGE` 拒，见 `platform-plan.md` §9 的 P6 第 5 条）。

### 早期九批的合并存根（D.05 / D.06 / D.09 / D.12 – D.17）

这九批的**原文**都在 [`PROGRESS-archive.md`](PROGRESS-archive.md)，一句话结论与关键词见
[`PROGRESS-index.md`](PROGRESS-index.md)。**主文件不再复述它们的结论**——引用其中任何细节前，
先按上方那条规矩在索引里定位、再打开归档核实。

仍留在主文件可见的只有下面两件：

- **D.12 – D.17 六批的手工验收共 37 条**：已按页面与操作顺序合并为一份，见
  [`docs/acceptance-checklist.md`](docs/acceptance-checklist.md)（D.17 收口）。这批全是观感与交互，
  **只能由用户眼睛判定**，AI 无法自证。
- **D.05 的两条残件**：① 同策略并发 `PUT /{id}/grants` 的锁升级路径「本环境未复现、不等于线上
  不需要」的**残余风险**；② 7 行 102 字符的行宽离群**待下一次格式化**一并做。

其余落点：`argv[0]` 与写路径见 [`docs/job-workspace.md`](docs/job-workspace.md) §2/§3.1，
**上传契约与参数模板见 `docs/platform-plan.md` §7.1–§7.4（D.25 重写）与方法
[`docs/strategy-configuration.md`](docs/strategy-configuration.md)**，网格步长的单位契约与
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
- **第六笔非分期项：摘掉 manifest（配置 JSON 即模板 + 周期解绑）**（2026-10-01，**D.25**）。
  同样**不在 P0–P8 的分期里**，是被「选 15m 就整轮废掉」这个类别错误牵出来的一整批。**它改的是
  上传契约本身**：此后**上传 = `.py` + 配置 `.json` 两份文件**，参数区由那份 JSON 的键渲染，
  三个运行级键（`ExchangeId` / `InstrumentId` / `BarPreces`）由平台覆写。**旧形态的策略版本一律
  作废**（用户已拍板，不做兼容回退）：详情页显示「该版本落在改形态之前, 没有配置模板」，提交与
  预填会让用户重传。**仍未决 —— 待用户手工验收**：浏览器里走一遍新契约（见 D.25 那条）。
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
| 上传形态 | **`.py` + 配置 `.json` 两份文件**（D.25 改） | 原为「`.py` + manifest」；不收 zip |
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
- **`_handle_validation_error` 遇到 `ValueError` 会二次抛错**（2026-10-01，D.25 实施中发现的
  **既有缺陷**，本批**未修**）：`app/main.py` 的校验错误处理器直接 `json.dumps` 错误的
  `ctx`，而 `ctx` 里可能躺着**异常对象本身**，于是抛 `TypeError: Object of type ValueError is
  not JSON serializable` —— 500，且真实原因被盖掉。可达路径：已认证客户端 POST 一个
  `filename=""` 的 part 即可（前端不会这么发，但接口是公开的）。**为什么本批不顺手修**：本批
  的护栏是"最小改动 + 一个横跨八段的改动面"，动公共错误处理器会把每一类 422 的响应体都牵进来；
  另起一批改，判据也更好写（断言响应体是 JSON、且不含异常对象）。
- **系统级隔离的具体实现未定**（2026-09-25）：Windows 下的"每用户独立低权
  OS 账号"vs 每用户容器，两条路的实现与运维代价差别很大。**P8 上云前必须定**。
- **行情数据上云的同步方式未定**（2026-09-25）：现约 1.2 MB/年/板块，
  体量不大，但需要定"谁同步、多久一次、失败怎么办"。**P8 前定**。

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
- **`el-select` 的 `persistent` 默认为真，选项在挂载期就进 DOM**（D.26 实测）：几千条选项时
  必须换 `el-select-v2`（虚拟滚动），否则整个页面在数据回包那一刻冻住若干秒。判据不在"选项对不对"
  而在**落进 DOM 的行数**（`views/RunSubmitView.spec.ts`）。
- **`BarPreces` 是引擎侧既有拼写**，非笔误，不可擅改，平台配置键须逐字一致。
  它在两份配置里各出现一次而**语义不同**（D.25 解绑）：引擎 `BackTest.json` 那份恒为落盘精度
  `5m`，策略配置那份是用户选的**订阅周期**——别再把一个值写进两处。
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
