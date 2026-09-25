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

| ID | 主题 |
| ---- | ---- |
| （暂无） | 归档层 `PROGRESS-archive.md` 尚未启用 |

---

## ✅ 已完成

### D.03 · 2026-09-25 （第三批） P2 上传核心 + `/api/health` 收窄为仅管理员

- **交付**：`POST /api/strategies`（建策略 + 落首个版本）、
  `POST /api/strategies/{id}/versions`、`GET` 列表与详情、`DELETE`（软删）。
  授权接口 `PUT /{id}/grants` 推迟到 P2b（见 R.01）。
- **落盘布局**：`<user_library_root>/<user_id>/strategies/<strategy_id>/<version_no>/`
  （`entry.py` 原文 + `manifest.json` 快照）。库里的 `StoragePath` 存**相对**
  `user_library_root` 的路径，换机器、换根目录都不必改库。
- **版本判重是 `(SourceHash, ManifestJson)` 这一对，不是源码单列**：
  同一份源码配不同 manifest 是一份**新**版本。只钉源码会逼用户"改参数必须连
  源码一起改"——荒谬。判重放服务层，判错的代价只是多一个目录，不伤完整性。
  故 P1 的 `UqStrategyVersionsStrategyIdSourceHash` **已撤销**，
  `(StrategyId, VersionNo)` 仍是硬约束。
- **磁盘与库同生共死**：先写同级临时目录、再整目录改名。于是失败时唯一的删除
  动作只落在自己刚建的临时目录上，碰不到任何既有版本目录；改名同时充当
  **版本号的原子认领**——两个并发上传算出同一个号时，撞车表现为改名
  `FileExistsError`，译成 409 让调用方重试，而不是 500。
- **提交失败的守卫接的是"这一层"而非某个异常类型**：一次瞬时故障（磁盘满、
  忙等超时）若只回滚 `IntegrityError`，会留下孤儿版本目录**永久占住那个版本号**。
  故改为 `except Exception`，回滚 + 删目录后原样抛出，只把 `IntegrityError`
  另译成 409。
- **软删必须释放策略名**（审查发现的严重项）：原先 `UNIQUE(OwnerUserId, Name)`
  是整表唯一，已删策略的名字一起被占住，而列表与详情都不再显示它——用户看到
  的是"名字没人用，却说我重名"，且平台不提供硬删，没有任何接口能释放。
  改为**部分唯一索引**（`WHERE DeletedAt IS NULL`），语义正是"名字在**在用的**
  策略之间唯一"。**注意**：`sqlite_where` 是方言选项，日后换库必须把同一条件
  带过去，否则会静默退化成整表唯一。
- **⚠️ 本轮改了 `Strategies` 的表结构，本机已有的旧库须删掉重建**：
  `backend/data/catalog.db`（已 gitignore）。`create_all` 只做
  `CREATE TABLE IF NOT EXISTS`，**不会**改动已存在的表，所以在本轮之前启动过
  后端的机器上仍是整表唯一约束——跑同一份代码，"软删后重建同名"照旧 409，
  而 255+ 项测试全绿（每次都建全新的临时库），归因会被引向代码而非那份旧库。
- **manifest 是策略作者与平台的契约**（`app/manifest.py`）：只固定
  `entry_filename` / `config_filename` / `supported_match_modes` 三个键的约束。
  `params` 的类型枚举**未定案**，只钉在任何方案下都成立的两条：每项是 JSON
  对象、`key` 非空且互不重复。参数项 `extra="allow"`（未知键原样保留），
  manifest 顶层 `extra="forbid"`——顶层键名写错会让参数整批静默落空，
  而参数项里的未知键此刻**正是**待定案的载体。
- **文件名约束是启动期致命的**（不是洁癖）：引擎日志器
  （`Utility::ParseProcessName`）要求 `argv[0]` 无路径分隔符，否则 `fopen`
  失败、进程启动期即终止。校验另拦三类：Windows 非法字符 `<>:"|?*`、
  保留设备名（`con` / `com1`…）、引擎自身读写的文件名
  （`backtest.json` / `sessions.json` / `result.json`）。
  **`:` 单独点名**——`a:b.py` 在 Windows 上**不报错**，它被当成 NTFS 备用
  数据流写进文件 `a`，目录里从此只看得见 `a`；平台会安静地收下一个永远跑不
  起来的策略。这条**初版漏掉了**，是审查实测出来的。
- **报错一律不带字段取值**：值会随响应体进接入层日志，而定问题靠字段名与原因。
  初版校验器把取值拼进了消息，审查指出后逐个剥掉。
- **上传体积两道闸，各管一段**：
  - 内层（处理函数）按上限**分块**读，管**内存**——一次读完等于把"客户端说多大
    就占多少内存"交给调用方。
  - 外层（ASGI 中间件）按 `Content-Length` 在**路由之前**回 413，管**磁盘**——
    内层执行时 Starlette 早已把整个 multipart 体解析完，超出的部分已经落进
    磁盘临时文件，于是"上传限 1 MB"对磁盘根本不成立。**这一层初版没有**。
- **外层那道闸自己也被审查逮到一个绕过**（本批第二轮审查，严重项）：
  初版拿 `str.isdigit()` 当守卫，而成帧层为判长度会按 OWS **裁掉首尾空白**、
  ASGI scope 里放的却是**未裁剪的原文**——`Content-Length: 1179649␠` 一个
  尾随空格就让 `isdigit()` 为假、闸整条跳过，请求被完整解析、超限部分落进磁盘。
  改为直接交给 `int()`（它自己吃 OWS），解析不出来回 400 而非抛异常：
  注册的异常处理器在用户中间件栈**内侧**，从这道中间件抛出去的异常绕开它，
  客户端会拿到 Starlette 的纯文本 500，固定文案与日志都不生效。
  **绕过的成本比记录在案的分块编码遗留项低得多**，故已修并补了回归用例。
- **验收**：`platform-plan.md` §11 的 P2 验收项中，"上传核心"部分**通过**，
  **261 项测试全绿**（新增 85 项在 `tests/test_strategy_upload.py`）。
  "上传后能跑通"须等 P3 runner，**尚未验收**。
- **验收非空的证法**（变异检查，改完即原样恢复，六次均转红）：
  ① 摘掉 Windows 非法字符检查 → 10 条转红；② 报错回显 `item["input"]` →
  7 条转红；③ 唯一索引退回整表唯一约束 → 1 条转红；
  ④ 摘掉请求体 413 中间件 → 1 条转红；⑤ 中间件退回 `isdigit()` 守卫 →
  5 条转红；⑥ 把体积闸余量改小到装不下合法上限 → 1 条转红。
  **⑤ 与 ⑥ 是第二轮审查逼出来的**：前四次全在"改大/摘掉"那一侧，恰好漏掉了
  "闸被绕过"与"闸误伤合法请求"这两个方向。
- **一处自我纠正**：本批一度认定"不回显字段值"的旧测试是**空转的**（理由：
  标记落在 pydantic 自带的报错路径上，而那条路径不带值），并据此改写。**这个
  判断是错的**——实测 pydantic 交出的报错项**带 `input`**，未知顶层键、参数
  key 不合模式、合法字符检查失败三条都带，故旧测试在那次变异下同样会转红。
  改写本身仍保留（把取值撒到我们自己的校验分支与 pydantic 分支两类上，覆盖面
  更宽），但**理由已更正**。教训：pydantic 的 `msg` 不含值 ≠ `errors()` 不含值，
  判"测试是否空转"时必须看完整的报错项结构，不能只看消息文本。
- **D.02 里挂的「偏离计划 §9，待回写」到此结清**：`/api/health` 由免认证 →
  需认证（P1）→ **仅管理员**（本批）。理由是响应携带引擎根与运行根的绝对路径、
  缺失 DLL 名与解释器版本——这是本机内部布局的清单。普通用户并非"少一道授权"，
  而是**他所能做的动作里没有一项需要这份清单**，给到他的只有泄漏面。
  `platform-plan.md` §6 / §7 / §9 的过时处已一并回写。

---

### D.02 · 2026-09-25 （第二批） P1 后端骨架与多用户隔离

- **交付**：五张表、登录与 JWT、**查询统一收口加 `user_id`**。
- **列名与属性名分离**：库列一律 PascalCase，Python 属性 snake_case，
  由 `mapped_column` 的列名参数映射（`database-style.md` §8）；
  约束名由 `MetaData` 命名约定统一生成，不逐个手写。
- **多租户过滤只有一处**：`app/catalog/visibility.py`。路由层不得自行拼
  `where`——条件散落时漏一处即越权，且无处审计。跨租户一律 **404**，不返回 403。
- **验收**（`platform-plan.md` §11 P1）：**通过**，175 项测试全绿（约 60 秒）。
- **验收非空的证法**（两次变异检查，改完即原样恢复）：
  ① 摘掉策略可见性条件 → 10 条转红；② 摘掉运行归属条件 → 4 条转红
  （`test_user_isolation.py`）。空断言会全绿，故这步不能省。
- **修掉一个会把平台锁死的缺陷**：`bootstrap.ensure_initial_admin` 统计管理员时
  **不区分状态**，所以最后一个启用中的管理员一旦被停用，重启也不会重建，
  平台就此失去全部管理入口且无接口可恢复。现于 `PATCH /users/{id}/status`
  加守门：管理员由启用改停用前，须还有其他启用中的管理员，否则 409。
  判据是"至少留一个启用中的管理员"而非"禁止自停用"——后者会连带禁掉
  一个正当动作（有同僚在场的管理员卸任）。
- **422 回执不再回显口令**：默认的校验失败回执会把出错输入整个抄回响应体，
  而这类响应常被接入层日志落盘。现只对 `password` 一类字段抹值（`***`），
  其余字段的值照旧回显，以免报错失去可读性。
- **金额列用 `Float` 而非 `Numeric`**：引擎报 float64
  （`Balance=998951.45064649964`），存 `Numeric(18,6)` 会被舍入成
  `...450646`，**直接破坏 P0 逐位一致的验收判据**。
- **`session_scope()` 不隐式提交**：依赖拆解在响应生成之后运行，
  在那里提交失败已无法转成正常错误响应；显式提交还让"哪里写了库"一眼可见。
- **健康检查改为需认证**（**偏离计划 §9**，待回写）：单机部署无匿名消费者，
  而响应携带绝对路径与缺失 DLL 名。
- **修掉的三个缺陷**：① PRAGMA 监听器用 `with cursor` 开游标——aiosqlite
  的适配游标不支持上下文管理协议，**应用启动即崩**，改为显式 `close()`；
  ② `DISABLED_ACCOUNT_DETAIL` 在两个模块各定义一份，收敛为一处；
  ③ `StrategyGrants` 有两条外键都指向 `Users`，自动命名把二者取成同一个
  `FkStrategyGrantsUsers`，按角色显式区分。
- **代码审查（`code-reviewer`）后的收敛**：分页骨架与页长常量在三个列表端点
  各写一份 → 收进 `catalog/pagination.py`；可见性三个取件函数结构逐行相同 →
  收进 `_load_one_or_raise`；测试侧的造数样板与列表取件同样收口。
  审查另指出 `/api/health` 与 `config.py` 无任何测试，已补齐
  （含兜底 500 只回固定文案的脱敏契约）。

---

### D.01 · 2026-09-25 （第一批） P0 地基探针：硬钉子 ② 已解

- **目标**：验证「每 job 独立工作目录 + Python 策略宿主」能否跑通——
  这是 `QuantTrading` 上平台剩余四件里标注的**硬钉子**。
- **方法**：在 `runs/probe/` 按 [`job-workspace.md`](docs/job-workspace.md) 契约
  构造目录，启动 `QuantTrading` 仓内**未做任何修改**的 `grid_strategy.py`。
- **结果**：**通过**。退出码 `0`；`RunId='probe'` 证明配置注入生效；
  下列 7 项与引擎自有机工作目录（`bin/Release`）下的基准轮**逐位一致**：
  `BarMarketDataCount=2928`、`OrderCount=654`、`TradeCount=84`、
  `Balance=998951.4506464996`、`TotalCommission=420.0`、
  `TotalStampTax=4.297395`、`TotalTransferFee=1.3419585`。
  产物齐备：`Dump/probe/` 17 个 CSV、`BackTest_probe.db`、
  `log/grid_strategy.20260925-154847.log`。**单轮总体积 3.0 MB。**
- **P0 意外收获（比原计划多出的一条硬约束）**：**引擎日志器要求
  `argv[0]` 是无路径分隔符的裸文件名**。Spark 的
  `Utility::ParseProcessName`（`Spark/src/Core/Utility/Utility.cpp:13`）
  在 Windows 分支只以 `strrchr(..., '\\')` 找分隔符、再按首个 `.` 截断扩展名，
  所得串用作 `log/<name>.<时间戳>.log`。故传**正斜杠**路径（含相对路径）
  会拼出含 `:` 或 `/` 的非法路径，`fopen` 失败后**启动期直接终止进程（退出码 1）**；
  传反斜杠绝对路径只是侥幸可用。**唯一稳的形态是裸文件名。**
  详见 [`job-workspace.md`](docs/job-workspace.md) §3.1。
- **修正了计划中的两处错误**（均已回写 `platform-plan.md`）：
  ① 计划原方案「脚本留在原地、只改 CWD」**被实测否决**——正是上述
  `argv[0]` 约束所致；正确做法是把策略入口**原样复制**到 job 目录根部、
  以裸文件名启动，且 `PYTHONPATH` 由「兜底」升格为**必需项**。
  ② 单轮体积实测 3.0 MB，原估 2 MB 偏低。
- **回测结果的可复现性**：隔离到独立工作目录**不改变任何指标**，
  与引擎原目录下逐位相同。

---

## 🔄 进行中

### R.01 · 2026-09-25 回测平台实施（P0 已交付，P1–P8 待做）

- **计划全文**：[`docs/platform-plan.md`](docs/platform-plan.md)。分期与验收见其 §11。
- **P0 ✅ 已完成**（见上 D.01）。
- **P1 ✅ 已完成**（见上 D.02）。实际表名为 PascalCase：
  `Users` / `Strategies` / `StrategyVersions` / `StrategyGrants` / `Runs`。
- **P2 上传核心 ✅ 已完成**（见上 D.03）。
- **P2b 策略授权共享**（下一步）：`PUT /api/strategies/{id}/grants`。
  与上传分批的理由：授权会牵出"被授权人能对该策略做什么"的一整串判定，
  与上传落盘是两件事，混在一批里改，出问题时分不清是哪半边。
  `StrategyGrants` 表 P1 已建，P2 未暴露写入路径。
- **P2 剩余待验收项**："上传后能跑通"须等 P3 runner，**尚未验收**。
- **P1 起须落实的机制**（P0 已定，勿再改动）：
  - 把策略入口**原样复制**到 `<job>/`，以 `cwd=<job>`、**裸文件名** `argv[0]` 启动。
  - `PYTHONPATH` 必须置为引擎根 `../QuantTrading/bin/Release`。
  - `BackTest.json` 的 `DbHost` / `DumpPath` 只写相对路径。
- **P3 runner 本体**：队列 / subprocess / 结果回收 / 启动恢复。验收含
  **强杀后端再启，跑动中的 run 被标 `interrupted`**。
- **P4–P8**：前端骨架 → 可视化 → 对比与模板 → 加固 → 上云。

**已锁定的实现选择（2026-09-25 用户拍板，含同日更早两项初判的修正）**：

| 决策点 | 选择 | 备注 |
| ---- | ---- | ---- |
| 图表库 | ECharts + vue-echarts | |
| 并发形态 | 后端进程内 `subprocess` | |
| 用户模型 | 云上多用户，现在就做骨架 | |
| 策略来源 | 用户上传 | **修正**：原「v1 仅内置策略」已废弃 |
| 鉴权 | 做 | **修正**：原「不做，仅本机单用户」已废弃 |
| 隔离档位 | 目录级起步 | 系统级为上云前置门槛 |
| 可见性 | `private` / `shared` / `public` | |
| 上云节奏 | 本机跑通再迁 Windows 云主机 | |
| 上传形态 | `.py` 必需 + manifest 表单或文件 | 不收 zip |

---

## ❓ 待讨论 / 待决策

- **策略 manifest 里 `params` 项的 schema 细节未定**（2026-09-25）：
  `entry_filename` / `config_filename` / `supported_match_modes` / `params`
  四个顶层键已定，但 `params` 每一项的类型枚举、校验规则、分组与联动
  （如"选了 Bar 才显示周期"）只按 `TestStrategyGrid.json` 的 8 个键拟了初稿，
  未与用户确认。**P2 上传核心已绕开这项开工**：参数项用 `extra="allow"` 原样
  保留未知键，定案后补类型化字段即可，已上传的 manifest 不必改写。
  但仍须在**参数越界校验落地前**定——那既是 `platform-plan.md` §11 的 P2 验收
  项，也是 P4 上传表单动态生成字段的依据。最迟 **P3 渲染策略配置前**定。
- **系统级隔离的具体实现未定**（2026-09-25）：Windows 下的"每用户独立低权
  OS 账号"vs 每用户容器，两条路的实现与运维代价差别很大。**P8 上云前必须定**。
- **行情数据上云的同步方式未定**（2026-09-25）：现约 1.2 MB/年/板块，
  体量不大，但需要定"谁同步、多久一次、失败怎么办"。**P8 前定**。
- **配置模板的存储位置未定**（2026-09-25）：P6 的「配置模板保存复用」既可进
  catalog（加一张表），也可落策略目录下。倾向前者（要按用户维度筛选），
  待 P6 前定。
- **P4 前端样式方案：Tailwind 还是纯 CSS**（2026-09-25）：`platform-plan.md`
  §11 的 P4 写的是「Vue 3 + TS + Vite + **Tailwind** + Pinia」，而参考项目
  `defect_tools` **没有 Tailwind**（纯 CSS + `variables.css`）。这是**技术选型
  变更**，不只是文档记错，故未擅改，P4 开工前须用户拍板。
- **请求体上限对分块编码无效**（2026-09-25，D.03 引入）：外层那道 413 只认
  `Content-Length`，而 `Transfer-Encoding: chunked` 不携带该头，绕得过。
  堵住它得在读取过程中逐块计字节，代价与收益不成比例——留到 **P8 由反向代理
  按字节数兜底**。P8 前须落实，否则"上传限 1 MB"这句话对磁盘仍不成立。
  （同一条闸上另有一个**更便宜**的绕过——尾随空白让 `isdigit()` 为假——已在本批
  修掉，见 D.03。两者不是一回事：那个是判断错误，这个是协议本身不带头。）
- **5 个无调用点的导出符号，删还是留**（2026-09-25 审查提出）：
  `get_database` + `DatabaseDependency`、`PlatformDatabase.engine` /
  `session_factory`、`TERMINAL_RUN_STATUSES`。原列的另三处
  （`build_owned_strategy_query`、`load_owned_strategy`、`InvalidRequestError`）
  在 P2 已全部用上，不再是问题。余下这三组更像重构残留。
  按 Harness §3，删公开符号须用户确认，故**未动**。
- **登录失败无节流**（2026-09-25 审查提出）：同一用户名可无限次快速尝试。
  PBKDF2 的 260k 迭代只起减速作用。若要加，需定阈值与锁定时长
  （单机部署，进程内计数即可，不必上 Redis）。

---

## 备注

- **环境锁定**：Windows + Python 3.11（`.pyd` 是 `cp311-win_amd64`，**Linux 无解**）
  + 后端与引擎同机；前端 Node 24.15。**"上云"等于一台 Windows 云主机，
  横向扩展无余地。**
- **`argv[0]` 必须是无分隔符的裸文件名**——这条是**启动期致命**的约束，
  不是日志美观问题。详见 D.01 与 `job-workspace.md` §3.1。
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
- **`runs/` 下有 3 个探针期遗留的草稿目录**（`probe-runA`、`probe-runB`、
  `probe-runC`，各约 3 MB），是被 gitignore 的探针残渣，可随时删。
  **AI 未自行删除**——按 Harness §1，git 之外的路径不得递归删除。
  正式产物 `runs/probe/` 建议保留作 P0 证据。
- **`.gitignore` 已覆盖全部运行数据**：`runs/`、`users/`、`backend/data/`
  （含 catalog 库与 JWT 密钥）、`node_modules/`、`.vs/`。
- **AI 不推送**（按既有约定）：提交由 AI 做，推送由用户执行。
