# QuantPlatform 进度

## 项目定位

本仓是 `QuantPlatform`——一个**云上多用户**的量化回测平台：Vue 3 前端 +
FastAPI 后端，包裹 `../QuantTrading` 的 C++ 回测引擎（`BackTest.dll`），
以「一次性进程 + 退出码 + `result.json`」的方式驱动回测，**运行用户上传的
Python 策略**。

定位与形态**承接 `QuantTrading` 的已定案结论**，不自拟：

- 依据：`../QuantTrading/PROGRESS.md` 与归档 `D.48`（2026-09-18 用户拍板）。
- 要点：**调度层就是 web 后端**；后端**必须与引擎同机**；每 job **独立工作目录**；
  RunId **由调度侧配置注入**；catalog **由调度侧写**，引擎零改动；
  **只收 Python 宿主**，平台拉起的是**用户策略程序**；产物落本机磁盘 + SQLite。
- 总体计划全文见 [`docs/platform-plan.md`](docs/platform-plan.md)。
- job 工作目录契约见 [`docs/job-workspace.md`](docs/job-workspace.md)。

---

## 归档索引

已关闭条目移入 [`PROGRESS-archive.md`](PROGRESS-archive.md)，**原文照抄**。
本表只列 ID 与主题；引用主文件未载的结论前，**必须先 grep 归档核实**。

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
| Q.01 | manifest 的 `params` schema 细节未定（**已定案**，见 D.06） |
| Q.02 | `permission_type` 判定语义未定（**已拍板不判定**，见 D.06） |
| Q.03 | 归属人从哪儿得知同事的 `user_id`（**已拍板：受限用户目录**） |
| Q.04 | P4 前端样式 Tailwind 还是纯 CSS（**已拍板 Tailwind**，与 `defect_tools` 有意分叉） |
| Q.05 | 受限用户目录的泄漏面评审（**已随 P4 落地并收口**，见归档 D.07） |
| Q.06 | Tick 三档撮合语义未定（**表单侧已定**＝不显示；引擎侧仍未定，短版见 ❓） |
| Q.07 | 前端组件测试的 DOM 环境未定（**已定案**：装 `jsdom` + `@vue/test-utils`，全局仍 `node`、组件 spec 逐文件声明，见 D.11） |

---

## ✅ 已完成

### D.11 · 2026-09-26 （第十一批） 引入 Element Plus：地基 4 件 + 两个最脏页

- **起因（用户原话）**：「现在的 UI 太原始了，是不是应该考虑引入 UI 组件库了？」——前端 14 个
  自写组件 + 8 个页面，全仓 `.vue` 里**一个 `<style>` 块都没有**、**零动画、零 toast 通道**，
  表格 / 分页 / 模态框 / 提示条 / 表单控件全用 Tailwind 手写。功能齐了（89 项纯函数测试兜底），
  但观感是「能用」而不是「像个平台」。
- **开工前用户拍板七条**：库 = **Element Plus `^2.14.6`**；范围 = **地基 + 两个最脏页**
  （`/runs` 列表、`/runs/new` 提交）；测试工具**一并装**；引入方式 = **显式 import + 全量 CSS**
  （不入 `unplugin-vue-components` / `unplugin-auto-import`——与归档 D.07 有意拒绝隐式全局的取向
  一致）；换肤 = **`--el-*` 变量，不引 sass**；主题 = **五族色阶全对齐**；地基只换**纯展示共用件
  的内部实现**（对外 props/emits 一字不变）。完整表见 `platform-plan.md` §13。
- **与既有记录的显式处理（不静默覆盖）**：① `platform-plan.md` §12.18 早就预判过这一步
  （「若日后要换 Element Plus，那批里只有布局件需要留」），本批照此执行，布局件
  （`AppLayout` / `FilePicker` / `DirectoryPicker` / `ResultTablePanel` / `EquityChartPanel`）**留下**；
  ② 拍板表「前端依赖」「前端测试」两行**保留原文 + 注明取代日期**（见 🔄 区）；
  ③ ❓「前端组件测试的 DOM 环境未定」**本批结清**，原文搬入归档 `Q.07`。
- **地基四件**（对外契约逐字未变）：`StatusBadge` → `el-tag`（5 个 tone → `type` 全覆盖映射；
  **文字色仍由我们自己的嵌套 `<span>` 给**——EP 浅色 tag 用它自己的基色，配出厂调色板对比度只有
  2.0–2.6:1，今天是 5.3–6.8:1）；`EmptyNotice` → `el-empty`（`:image-size="72"`；
  **两行都进 `#description` 插槽**——默认插槽渲染在描述**下方**的 `.el-empty__bottom`，是第三个
  层级，主句与注解会被拆开）；`ErrorBanner` → 保留自有 flex 包裹层、把 `el-alert` 放进去
  （el-alert 只有 `title` / default 两个插槽，**没有 action 位**）；`PaginationBar` → `el-pagination`
  （**显式钉 `layout="prev, next"`**——默认值会多长出跳页框与**第二条**条数文案）。
- **两条跨层契约原样存活**：① `ParameterField` 的选项下标仍是**字符串**（`''` = 未选）、
  取值恒为 `string | boolean`，转换仍只在 `domain/manifest.coerceParameterInput` 一处；
  ② `PaginationBar` 上报的仍是 **offset 不是页码**（`currentPage` 是 1 起、offset 是 0 起，
  `(page - 1) * limit` 这处换算是全批唯一会静默算错的地方，已由新 spec 两个方向钉住）。
  **参数校验的语义与文案仍全在 `domain/`**——本批**不引 `el-form` / `el-form-item`**，
  不用它的 rules（那会造出与 `visibleFieldErrors` 并列的第二处真相），标签/错误/提示的接线仍手写。
- **`/runs` 五处**：四个筛选/排序下拉 + 页大小换 `el-select`、倒序换 `el-checkbox`、
  表格换 `el-table` + `el-table-column`（两列 `align="right"`、操作列省略 `label`、**不加
  `row-key`**——本表不用选中/展开/树形）、主行动换 `el-button` 套在
  `<RouterLink custom v-slot="{ navigate, href }">` 里**保住真锚点**（中键开新标签页是真实用法）。
  **轮询 / `reloadFromFirstPage` / 六个查询参数 / `hasActiveFilter` 一行未动。**
- **`/runs/new`**：**保留原生 `<form @submit.prevent>`**（回车提交与原生语义不动），只换控件——
  策略/版本与六个运行级字段换 `el-input` / `el-select`（**六个字段仍全是 `type="text"`**，
  「交易日」与「初始资金」靠 `inputmode` 给移动端键盘；改成 `type="number"` 会让浏览器接受 `1e5`
  并弹原生校验气泡，与 `domain/` 的判据打架）、提示条换 `el-alert type="info"`、提交换
  `el-button`。`ParameterForm.vue` **未改**（`updateParameter(descriptor.key, $event)` 那行本来就对）。
- **主题换肤走 `html:root` 而不是 `:root`**：我们的覆盖与 EP 自己的 `:root` 同为**无层样式**、
  同权重 (0,1,0)，同权重下靠源序决胜而源序取决于 Vite 打产物的先后（未实测，不该押）。
  `html:root` 特异性 (0,1,1) 压过 EP 的，**与顺序无关**；`main.ts` 里的 import 顺序仍写对，
  当深度防御。**换肤块是本批唯一新增的 `style.css` 内容**，五族 7 档色阶用 `color-mix()` 生成，
  新写的模板里不再出现硬编码色值。
- **两处与计划的偏差（有意为之，非疏漏）**：① §五原写排序与页大小两个下拉照抄「同上」（可清空），
  但**清空会把排序或页大小的模型置空**（EP 默认回 `undefined`，写 `:value-on-clear="''"`
  则送出一个后端不认的空 `sort_by` → 422），故这两个**不 `clearable`**、恒有值；
  ② 策略/版本两个下拉**失去了「取消选择」这条路**（原生那个空 `<option>` 被 placeholder 取代，
  且不加 `clearable`）——计划里已明确同意。
- **一处可访问性净损失，已记在案**：`aria-describedby`（错误文案 `<p :id>` 的关联）在三分支里
  **只有 `el-input` 那一支能保住**——`el-input` 把非 `class`/`style` 的属性透传落到内层原生
  `<input>`；而 `el-select` 只声明了 `id` 与 `ariaLabel`（`aria-describedby` 落到根 `<div>` 上）、
  `el-checkbox` 只声明了 `ariaControls`（落到根 `<label>` 上）。**同一件事曾是自写控件里天然成立的，
  换库后对下拉与复选框不再成立。**
- **两处计划断言被实物推翻（已按实物改）**：① 计划说「`el-alert` 根节点**没有** `role="alert"`」——
  实测 2.14.6 的源码里是**硬编码**的，故包裹层上**不能**再加（嵌套两块 live region 会重复播报），
  已删除并改用「恰好一处 `role="alert"`，且是 el-alert 自己那个」的断言钉住；
  ② **`ErrorBanner` 的重试按钮此前从未渲染过**——`isRetryVisible?: boolean` 传参缺省时 Vue 会落成
  `false` 而不是 `undefined`，于是「没传」与「明确要隐藏」成了同一件事，**每个调用点都静默丢了
  重试按钮**（`git show HEAD` 确认是既有缺陷，非本批引入）。已用
  `withDefaults(…, { isRetryVisible: true })` 修掉并由新 spec 钉住。
- **验证**：`vue-tsc -b` 退出码 0；`vitest` **121 项全绿 / 12 个文件**（既有 89 项不受影响 +
  新增 **32 项**：`ParameterForm` 4（**复活 D.10 那份 parked spec**，含「选项下标必须以字符串上抛」）、
  `StatusBadge` 8、`PaginationBar` 6、`ErrorBanner` 8、`EmptyNotice` 4，逐个在文件顶部写
  `// @vitest-environment jsdom`，全局 `environment: node` 未动）；`build` 成功（452 ms）。
  **体积账（本批的主要代价）**：主 CSS **17,372 B → 382,097 B**（gzip 约 **52.5 kB**），
  ECharts 那块 559.35 kB **未变**（基线 559,451 B），JS 合计 1,023,443 B、assets 合计 1,405,540 B
  （**JS 增量未取到 like-for-like 基线**——为取它要先 stash，而工作区里有 12 改 + 6 新共 18 个文件，
  不值当，故只报绝对值不报差）。产物自检三项：① EP 片段进了产物；
  ② **Tailwind 的层原样进了产物**（`grep -c '@layer'` = **5**，此前只校验过源文件）；
  ③ **换肤块在位**——`--el-color-primary` 恰好 **2** 处（EP 出厂的 `#409eff` 与我们的
  `var(--color-brand)`），且后者带 `html:root` 选择器。另注：`color-mix()` 在**构建期**就被折算了
  （`--el-color-primary-light-5` 落成字面量 `#8ea7ec`，因为 `--color-brand` 有字面值），
  所以**运行期换肤对 light-N 那几档不成立**，改品牌色要重新构建；剩下 64 处 `color-mix` 是
  Tailwind 自己的 lab/oklab。
- **不做 / 留后**：`ConfirmDialog` → `ElMessageBox`（**形状不匹配**：今天是声明式
  `isOpen` prop + `confirm`/`cancel` emit + `isBusy`，MessageBox 是命令式 + Promise，
  换过去要把每个调用点的 `v-if` 控制反转成 `await` 流程，另一种迁移）；`ElMessage` toast
  （全站今天**没有** toast 通道，这是产品决策不是迁移）；页码按钮；`FilePicker` /
  `DirectoryPicker` / `ArtifactList` / `ResultTablePanel` / `EquityChartPanel`（布局件，
  §12.18 已预判）；其余 5 个页面（后续批次）；`app.use(ElementPlus)` 全量注册 / sass /
  响应式 / 深色模式；把散落的 `text-rose-600` 之类改成语义令牌 `text-danger`（全仓重命名，
  与本批边界冲突）。**`LoadingNotice` 不动**——EP 没有等价物（`v-loading` 是遮罩指令），
  它留在原地也正是 `role="status"` 不丢的原因。
- **文档回写**：`platform-plan.md`（§10 两行加「EP 化 2026-09-26」、P4 依赖那段补日期与现状、
  §12.18 加增补小节含实测代价、§13 新增十行拍板表）、`PROGRESS.md`（本条 + 拍板表两行注明取代 +
  归档索引三行 + R.01 加手工验收待办；**D.08 与 D.09 两条整条移入归档**——主文件加本条后
  越过 50 KB，按 §8.1 把 ✅ 区最旧的整条搬走（搬 D.08 后仍差一点，接着搬 D.09，
  最终 48,709 B），搬运由脚本对条目边界完成、原文取自 `git HEAD`、正文一字未改，
  主文件里 5 处 `见 D.08` 与 4 处 `见 D.09` 一并改写成 `见归档 D.xx`）、两处过期注释
  （`vite.config.ts` 的「不装 jsdom」、`ConfirmDialog.vue` 的「因为最小集里没有组件库」）。
- **待用户手工验收**（浏览器里走一遍，本批验收主体）：
  ① `/runs`——四个下拉与倒序能改、**筛选能被 × 清回「全部」**（本批唯一改动操作方式的地方，
  专看这条）、表格九列对齐（两列右对齐）、徽章配色与可读性、翻页、筛选后回第一页、
  「新建回测」**中键能开新标签页**（验证锚点没丢）；
  ② `/runs/new`——**选策略 → 参数与字段被带出且提示条可见 → 点「重置为默认值」→ 回到默认且
  提示条消失 → 再选一次该策略仍带出 → 换个没跑过的策略为纯默认、无提示条**（这是 D.10 欠着的
  手工验收，本批一并走）；参数项的必填/越界提示仍是 domain 那几句中文；回车能提交；
  ③ **换肤是否生效**——EP 按钮/标签应是品牌蓝 `#1d4ed8`，若仍是出厂亮蓝 `#409eff` 说明换肤没吃到；
  ④ 顺带回归四个共用页面（`/strategies`、`/strategies/:id`、`/users`、`/runs/:id`）：
  徽章/空态/错误条/分页条变了样，功能应不变——**空态的居中与灰阶是这批里最可能想调回来的地方**。

---

### D.10 · 2026-09-26 （第十批） 提交页按策略预填「上一次提交的参数」

- **起因（用户原话）**：「现在每次新建回测要重新输入好多参数，能否针对每个策略保存上一次使用
  参数，在新建回测时，自动填充？」——每次回到提交页都要手敲标的、日期区间、初始资金、
  K 线周期与一整组策略参数，是纯粹的重复劳动。
- **用户拍板的范围**：记住的 = **策略参数 + 运行级字段**（标的 / 日期 / 初始资金 / 周期）；
  「上一次」= **最近一次提交**（不论成败、是否还在跑）；另给一个**「重置为默认值」**按钮；
  粒度 **(用户, 策略)**，**绝不跨用户**（共享策略下也不能把别人的参数填给你）。
- **不新建表**：`Runs` 提交时本就写了两份渲染好的配置文本（`ParamsJson` = 策略配置、
  `BacktestConfigJson` = 引擎 `BackTest.json`，见 `run_submission.py`），「上一次用的参数」就是
  **该用户在该策略下最新那一轮**的这两份文本，派生即可——这也天然满足"不论成败都算"：
  排在最前的是最近一次提交，与它成没成功无关。（`Base.metadata.create_all` 加不了列，
  故刻意不碰 schema。）
- **后端**：新模块 `services/run_prefill.py`（纯读取）+ `catalog/schemas.py` 一个响应模型 +
  `routers/strategies.py` 一条 `GET /{strategy_id}/last-submitted-parameters`
  （可见性闸用 `load_visible_strategy`，共享给你的策略也要能用）+ `engine_config.py` 加
  `resolve_market_data_type`（由 `MATCH_MODE_VALUES` **反查**，int 与枚举的对应关系仍只有一处真相）。
  四条要点：① 归属过滤经 `build_owned_run_query`（该模块禁止路由自己拼 `where`），排序照调度器的
  `(submitted_at, id)` **双键兜平局**（Windows 上 `SubmittedAt` 只有毫秒精度，单键不确定）；
  ② 引擎键名反查——`MatchMode → 行情模式`、**`BarPreces` → `bar_period`（引擎侧既有拼写，
  不是笔误）**、`StartTradingDay` / `EndTradingDay` / `InitialCapital` 直取，
  `MatchMode` 与 `InitialCapital` 用 `isinstance` 验类型，**单个坏值只丢自己那一项**；
  ③ `params` 用**该运行自己那个版本**的 manifest 的 `named_run_field_keys()` 做差集剔掉运行级键
  （manifest 已保证参数键不与运行级键撞名，故差集精确），**后端不做范围过滤**——前端为渲染控件
  本就要逐项判可用性，后端再滤一道就是两处真相，还会静默吞键；④ **坏 JSON 记 warning 后回
  「没有记忆」**，绝不因一轮坏数据让提交页报错。**无历史回 200 而非 404**（`run_id=None` + 空
  `params`）：404 会与「策略不存在 / 不可见」混为一谈，而首次使用这个策略走的就是这条路。
- **前端**：`api/types.ts` + `api/strategies.ts` 的取数（URL 挂在 `/api/strategies/...` 下，
  改地址时两边一起 grep 得到）；两处纯函数——`manifest.createInitialParameterInputs` 扩一个
  `rememberedValues` 形参（候选值一律**再用既有的 `coerceParameterInput` 过一遍**，越界 / 类型不符 /
  已不在选项里 / 非有限数一律回落默认值，范围规则因此不必重写）、
  `run-form.buildPrefilledRunFields`（逐字段复用私有的 `readTradingDay` / `readInitialCapital` /
  `readRunFieldValue`，故界面上的运行级规则仍**只有一处**）；`RunSubmitView.vue` 接线 + 提示条
  「已按你上次提交的参数填充（提交时间）」+ 重置按钮。三处值得后人当心：
  ① **原来的 `watch(descriptors, …)` 已删除**，换成版本下拉的 `@change` 处理器——预填要发一次请求、
  必然晚于 `descriptors` 变化，**保留 watcher 就一定会把填好的记忆清掉**；
  ② **运行级字段的回落值是「当前输入」而不是空串**——换版本时参数一定重建（未声明的键会被后端
  400），而运行级字段与版本无关，只补不改，不静默抹掉用户已经敲进去的东西；`prefill` 为 `null`
  时结果恒等于当前输入，与没有这个功能时一模一样；
  ③ **新增竞态保护**（自增 `selectionToken`）：快速连着换两次策略时先发的回包可能后到，
  会把 A 策略的详情与记忆落进 B 策略的表单——这条对**策略详情**本来就是既有隐患，顺手一起收口。
- **验证**：后端 **456 项全过**（新增 `tests/test_run_prefill.py` **16 项**：正向那条走
  through-HTTP 层真提交一轮再读回，其余边界走记录级；覆盖无历史回 200、`BarPreces → bar_period`、
  `MatchMode 3 → Bar`、运行级键**不出现**在 `params` 里、多轮取最新、`submitted_at` 平局按 `id`
  兜平局、**别人更新的轮不采纳**、**两个用户各读各的**、坏 JSON 降级、单个坏值只丢自己那一项、
  不可见 404、匿名 401）；**三次变异检查**（① 去归属过滤 → 2 红；② 去 `id` 兜平局 → 1 红；
  ③ 去运行级键差集 → 2 红）均只红对应用例，三次都已还原为**逐字节相同**（脚本落在仓库外）；
  `test_real_engine_acceptance.py` 的真回测块内补了预填断言，**真引擎验收 4 项全过**；
  前端 `type-check` 无错 + `vitest` **89 项全绿**（`manifest.spec.ts` 补 4 项、`run-form.spec.ts`
  补 5 项）+ `build` 成功。本轮验收产物在 `backend/_acc_tmp/prefill-20260926/`。
- **不做 / 已知缺口**（本轮有意不做，非缺陷）：
  ① **记忆不可删除**——它是从运行历史派生的，不是一份副本，按钮只表示"本轮不套用"；
  要能真删就得新建一张表。
  ② **不做「常用参数模板」**（多套参数命名保存复用）——那是计划里 P6 的「配置模板」，
  本轮的语义只是「上一次」，不提前做。
  ③ **不预填行情模式**：表单本来就没有这个控件（Tick 不可提交，`MATCH_MODE` 是常量），
  端点上仍回 `match_mode` 供日后放开 Tick 时用。
  ④ **manifest 没声明的约束照样能带出**：如跨参数的 `0 < GridStep × GridCount < 1`——
  manifest 表达不了跨参数约束，引擎构造期会拒并在 `stderr.txt` 写明。
  ⑤ **不记「上次选的是哪一版」**：版本仍默认最新（用户拍板的粒度是策略不是版本）；
  按旧版本存的键若不在当前版本里，逐键回落默认。
- **文档回写**：`platform-plan.md`（§9 新增端点行 + 「提交页预填端点」六条、
  §10 页面表 `/runs/new` 那行、§11 新增「2026-09-26 增补」段含验收与不做项）、
  `PROGRESS.md`（本条 + R.01 新增一行；**D.07 整条移入归档**——主文件加本条后越过 50 KB，
  按 §8.1 把最旧的整条搬走，搬运由脚本对条目边界完成、原文取自 `git HEAD`、
  正文一字未改，主文件里 11 处 `见 D.07` 一并改写成 `见归档 D.07`）。
- **待用户手工验收**（浏览器里走一遍）：选策略 → 字段与参数被带出且提示条可见 → 点「重置为默认值」
  → 回到默认且提示条消失 → 再选一次该策略仍带出（证明记忆没被删）→ 换一个**没跑过**的策略
  → 纯默认、没有提示条。

---

### D.09 · 2026-09-26 （第九批） 网格步长由绝对价格改为比例

- **已归档**：原文见 [`PROGRESS-archive.md`](PROGRESS-archive.md) 的 `D.09`
  （加 D.11 后主文件越过 50 KB 上限时移出）。该条里**仍在生效的结论**：
  `GridStep` 是**比例**（`0.01 = 1%`）、档位价与平仓价两条公式、合法区间
  `0 < GridStep × GridCount < 1`、越界在**构造期**拒启（两侧孪生同源校验），
  以及**重取后的真引擎基线**（成交 `34`、委托 `629`、余额 `999257.8562340003`）
  —— 基线表在 `job-workspace.md` §6.3，单位契约另见下方备注。
  **旧口径与新口径的数字不可相减**；引用该条其余细节前，先 grep 归档核实原文。

---

### D.06 · 2026-09-25 （第六批） P3 runner 本体 + `params` schema 定案

- **已归档**：原文见 [`PROGRESS-archive.md`](PROGRESS-archive.md) 的 `D.06`
  （主文件再次逼近 50 KB 上限时移出）。该条里**仍在生效的结论都由别处承接**：
  `argv[0]` 的订正见 `job-workspace.md` §3.1 与下方备注、写路径的结构性保证见
  §2 与备注、`params` 定案见 `platform-plan.md` §7.2、P3 的六项已知缺口与
  **两处 CAS 覆盖缺口**见下方备注、三版基线数字见 `job-workspace.md` §6。
  **引用该条其余细节前，先 grep 归档核实原文。**

---

### D.05 · 2026-09-25 （第五批） P2b 审核修正与测试补强

- **已归档**：原文见 [`PROGRESS-archive.md`](PROGRESS-archive.md) 的 `D.05`（主文件逼近 50 KB 上限时移出）。
  该条里两件**还没了结**的事仍需在主文件可见：一是同策略并发 `PUT /{id}/grants` 的锁升级
  路径「本环境未复现、不等于线上不需要」的**残余风险**；二是 7 行 102 字符的行宽离群
  **待下一次格式化**一并做。

---

### D.01 · 2026-09-25 （第一批） P0 地基探针

- 原文见 [`PROGRESS-archive.md`](PROGRESS-archive.md) 的 `D.01`（✅ 区超出 5 批时移出；
  索引表那一行仍在，其 `argv[0]` 结论**已被 D.06 实测推翻**，引用前先看订正）。

---

## 🔄 进行中

### R.01 · 2026-09-25 回测平台实施（P0–P5 已交付，P6–P8 待做）

- **计划全文**：[`docs/platform-plan.md`](docs/platform-plan.md)。分期与验收见其 §11。
- **P0 ✅ 已完成**（D.01，**已归档**）。
- **P1 ✅ 已完成**（见归档 D.02）。实际表名为 PascalCase：
  `Users` / `Strategies` / `StrategyVersions` / `StrategyGrants` / `Runs`。
- **P2 上传核心 ✅ 已完成**（见归档 D.03），**「上传后能跑通」已由 P3 结清**。
- **P2b 策略授权共享 ✅ 已完成**（见归档 `D.04`，审核修正见上 D.05），
  **「被授权人可跑」已由 P3 结清**。至此 P2 的六个策略端点全部落地。
  `StrategyGrants` 表 P1 已建、P2b 打通写入路径。
- **P3 runner 本体 ✅ 已完成**（见上 D.06）：提交 / 队列 / 回收 / 恢复 / cancel。
  验收全部通过，含**强杀后端再启、跑动中的 run 被标 `interrupted`**。
- **P4 前端骨架 ✅ 已完成**（见归档 D.07）：`frontend/` 从零到闭环，
  含三处后端前置端点（受限用户目录、显示名必填、`manifest_json` 透传、
  产物清单与下载）。**后端与前端两侧的代码至此都能跑通整条闭环**；
  **P4 的验收项（计划 §11 原文的「登录 → 上传策略 → 提交 → 看指标 → 下载」）
  已由归档 D.07 的代理冒烟 13 项证明链路成立，剩下的「真引擎一轮真回测走完界面」
  属人工验收，须由用户在浏览器里走一遍**（步骤见 `platform-plan.md` §8.1）。
- **P5 可视化 ✅ 已完成**（见归档 D.08）：权益曲线与回撤曲线、5 张结果表的明细分页表。
  两条读端点（`/equity`、`/tables/{table}`）落地在
  `services/result_database.py`；前端两个面板 + `domain/equity.ts` 纯函数。
  **「曲线与实测数据点吻合」的判据已改成自洽形式**（首点 `1000000.0`、
  末点与该轮 `result.json.Balance` 逐位相等），理由见归档 D.08 与计划 §11。
  **P5 的浏览器人工验收（打开一个 succeeded 的轮看曲线与 5 个页签）
  与 P4 那条一样，须由用户走一遍**（步骤见 `platform-plan.md` §8.3，
  前置与 §8.2 相同）。
- **提交页预填 ✅ 已完成**（见上 D.10，非分期项，2026-09-26 用户提出）：
  选中策略即按「该用户在该策略下最近一次提交」填出策略参数与运行级字段，
  另给「重置为默认值」按钮。**它的手工验收（选策略 → 参数与字段被带出且提示条可见
  → 点重置回到默认且提示条消失 → 再选一次该策略仍带出 → 换一个没跑过的策略为纯默认、
  无提示条）与 P4/P5 那两条一样，须由用户在浏览器里走一遍**（前置与 §8.2 相同）。
- **UI 组件库 ✅ 已引入**（见上 D.11，2026-09-26 用户提出）：Element Plus `^2.14.6`，
  地基 4 个共用件 + 两个最脏页（`/runs`、`/runs/new`）换库；主题五族色阶经
  `html:root` 的 `--el-*` 覆盖对齐到既有 `@theme` 令牌。**它的手工验收（`/runs` 四个下拉与
  筛选的 × 清空、九列对齐、分页、中键开新标签页；`/runs/new` 的预填整条流程；
  EP 按钮/标签是否呈品牌蓝 `#1d4ed8`；四个共用页面回归）与 P4/P5 那两条一样，
  须由用户在浏览器里走一遍**（清单见 D.11 末，前置与 §8.2 相同）。
  **本批改动操作方式一处**：筛选不再靠选中列表里的「全部」，改成 placeholder + × 清空。
- **⚠️ 参数语义变更：网格步长已由绝对价格改为比例**（见归档 D.09）：
  `GridStep` 现为**比例**（`0.01 = 1%`），旧值 `10.0` 这种绝对价格写法
  **在构造期就被拒**（合法区间 `0 < GridStep × GridCount < 1`，两侧
  Python 与 C++ 同源校验）。连带后果：**`test_real_engine_acceptance.py`
  的基线已全部重取**（成交 `84 → 34`、委托 `654 → 629`、余额
  `999377.0899999999 → 999257.8562340003`），`job-workspace.md` §6.3 是
  新表、§6.1/§6.2 两份旧表**保留但标注为旧口径**。要引用余额数字，
  先认准是哪一版步长下测的——**不同口径的数不可相减**。
- **P6 起的前置**：P5 已把「读结果库」这条路打通（只读连接、白名单、分页、
  `asyncio.to_thread`），P6 的多轮对比不必再碰 SQL——`/api/runs/compare?ids=a,b,c`
  只需把 `read_capital_series` 在**多轮**上各调一次，曲线叠加是前端的事。
  **口径坑**：不同轮的 `Capital` 长度与起点都不同（交易日区间不同），
  叠加前必须先按 `TradingDay` 对齐，不能按数组下标。`DELETE /api/runs/{id}`
  （要连工作目录一起删）留 P7。
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
| 前端依赖 | **最小集**：原生 `fetch`，不引 axios、不引 UI 库 | 表格/分页/模态框等自写，见归档 D.07。**2026-09-26 起 UI 库一项被 Element Plus `^2.14.6` 取代**（见 D.11）；**axios / sass 两条仍然有效** |
| 前端测试 | **vitest 只测纯逻辑**（`environment: node`） | 不装 `@vue/test-utils` 与 jsdom；**要测组件时再加**。**2026-09-26 正是按本行自己预设的条件加装**（jsdom + @vue/test-utils；全局仍 `node`，组件 spec 逐文件声明 jsdom），见 D.11；当时的存疑原文已归档为 `Q.07` |
| 前端页面范围 | **闭环 + 最小 admin 用户页** | 不含 `/compare`、`/settings`；**权益曲线与明细分页表已于 P5 补齐** |
| 前端版本号 | **照抄本机同族项目的已验证组合** | router 5 / pinia 4 / vitest 5 / TS 7 都是主版本跳跃，不追 |
| 结果表明细范围 | **精选 5 张**：`Capital` / `Trade` / `Order` / `Position` / `PositionDetail` | P5 拍板；**不加列表端点**，表名清单前端镜像一份，见归档 D.08 |
| 回撤在哪算 | **前端纯函数派生**（`domain/equity.ts`） | P5 拍板；后端只回原样逐日序列，接口不随图表变化 |
| 阻塞的库调用 | **`asyncio.to_thread`**（本仓首例，P5 引入） | sqlite3 阻塞且连接不能跨线程；与 P4 内联文件 IO 有意分叉 |

---

## ❓ 待讨论 / 待决策

- **Tick 模式的三档撮合语义未定**（2026-09-25，D.06 引入，半关闭；
  **表单侧已定部分见归档 `Q.06`**）：引擎侧 tick 撮合有 `OrderBook:0` /
  `LastPrice:1` / `OppositePrice:2` 三档，`SimExchange.cpp` 按"是不是 Bar"
  决定消费哪张行情表，**撮合价规则由这个 int 决定**；而平台的
  `MarketDataType` 只有 `Bar` / `Tick` 两值，推不出那三档。
  **仍未决**：三档怎么映射（加一个提交字段？还是按 manifest 声明？）、
  行情数据从哪儿来——`D:/MdBaoStock` 下**只有 `Bar/`**，tick 数据目录不存在，
  从未跑过。**有 tick 行情可跑之前不必定**。
- **系统级隔离的具体实现未定**（2026-09-25）：Windows 下的"每用户独立低权
  OS 账号"vs 每用户容器，两条路的实现与运维代价差别很大。**P8 上云前必须定**。
- **行情数据上云的同步方式未定**（2026-09-25）：现约 1.2 MB/年/板块，
  体量不大，但需要定"谁同步、多久一次、失败怎么办"。**P8 前定**。
- **配置模板的存储位置未定**（2026-09-25）：P6 的「配置模板保存复用」既可进
  catalog（加一张表），也可落策略目录下。倾向前者（要按用户维度筛选），
  待 P6 前定。
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

---

## 备注

- **环境锁定**：Windows + Python **3.14.5**（`.pyd` 是 `cp314-win_amd64`，
  **Linux 无解**）+ 后端与引擎同机；前端 **Node 24.16.0 + npm 11.13.0**
  （原记 24.15，归档 D.07 实测订正）。**"上云"等于一台 Windows 云主机，
  横向扩展无余地。**（原记 Python 3.11 / `cp311`，为早期误记，D.06 订正。）
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
  devDependencies 同批加 **`jsdom ^30.1.1` + `@vue/test-utils ^2.5.1`**（组件测试用，见 D.11）。

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
