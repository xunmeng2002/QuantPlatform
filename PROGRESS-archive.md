# QuantPlatform 进度归档

本文件是 [`PROGRESS.md`](PROGRESS.md) 的**归档层**：只收已关闭与已了结条目的
**原文**。条目 ID 取自拆分当日的文档顺序，此后永久稳定——
`D.*` 来自原 ✅ 已完成、`Q.*` 来自原 ❓ 待讨论、`R.*` 来自原 🔄 进行中。

**排列**（2026-09-25 收拢）：`D.*` 全部在前，其后 `Q.*`，**各自按编号升序**
（此前是按追加次序，`D.02` 会排到 `Q.04` 之后，grep 时容易以为丢了）；
标题层级统一为 `## `（从主文件搬来的条目原本是 `### `）。

搬运规则只有一条：**只移动、不删改**。归档条目原文照抄，保留原日期与原结论；
订正写在引用处（`PROGRESS.md`）或本条开头的说明里，不覆写原文。

**本文件不参与会话开头的通读**：凡主文件不载的结论，引用前必须先在此 grep
核实，不得凭记忆断言。

---

## D.01 · 2026-09-25 （第一批） P0 地基探针：硬钉子 ② 已解

> 归档于 2026-09-25（D.06 拆分时，✅ 区超出 5 批）。
> **本条里的 `argv[0]` 结论已被 P3 实测推翻**：带路径的 `argv[0]` 实测能跑完整轮、
> 退出码 0。订正见 `PROGRESS.md` 的 D.06、`docs/job-workspace.md` §3.1 与
> `docs/platform-plan.md` §3/§4/§12.6。**下列原文一字未改**，读到时以订正为准。

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

## D.02 · 2026-09-25 （第二批） P1 后端骨架与多用户隔离

> 归档于 2026-09-25（D.07 拆分时，✅ 区超出 5 批）。**正文一字未改**（标题层级按本文件体例由 `###` 归一为 `##`）；
> 引用其中结论（跨租户一律 404、管理员守卫、422 不回显口令、金额列用 Float）
> 前，先看 D.03/D.07 里有没有订正。
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

## D.03 · 2026-09-25 （第三批） P2 上传核心 + `/api/health` 收窄为仅管理员

> 归档于 2026-09-25（D.07 拆分时，✅ 区超出 5 批且主文件越过 50 KB 目标）。
> **正文一字未改**（标题层级按本文件体例由 `###` 归一为 `##`）。
> 其中三条结论另有更近的出处：「版本号认领靠改名语义」见 `PROGRESS.md`
> 备注；「体积闸对分块编码无效」见其 ❓ 区；「软删释放策略名」见
> `catalog/models.py` 的部分唯一索引与 `strategy_store.py` 的注释。
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

> 归档于 2026-09-26（D.08 拆分时，✅ 区超出 5 批）。

## D.04 · 2026-09-25 （第四批） P2b 策略授权共享

- **交付**：`PUT /api/strategies/{id}/grants`——整体替换某策略的授权集合，
  只对归属人开放，非归属人与不存在的策略一律 404。请求体是**替换后的全集**，
  空列表即撤销全部授权。`StrategyGrants` 的写入路径至此打通，
  P2 的六个策略端点全部落地。
- **授权驱动可见性**（**2026-09-25 用户拍板**，本轮开工前专门问过一次）：
  授权非空即把 `private` 转 `shared`，授权清空即转回 `private`，`public` 一概不动。
  **这一条不是顺手加的，是没有它这条接口就不成立**：`StrategyVisibility` 定义
  `private` 下**授权表不生效**，而整条接口清单里**没有第二处**能把可见性改成
  `shared`——建策略时的表单字段是唯一入口，建完就改不了。于是"授权与可见性各自
  为政"会让 `private` 策略上的授权**回 200 而无人获得访问权**：调用方看到的成败
  与事实相反，且他无从自救。这正是本项目一贯要避免的那种"点了没反应"。
- **`public` 为什么不动**：那份授权本就多余（全体登录用户已然可见），
  而 `public` 转 `shared` 会把**没被逐一点名的用户静默挡在外面**——
  收窄既有访问不是这条接口该做的事，真要做也该由调用方明说。
- **原子性**：校验（上限、重复、授权给归属人、被授权人在册）**严格排在任何写之前**，
  删旧、插新、改可见性则在**同一个事务**里提交。两层各管一段：前者保证被拒的请求
  碰都没碰过库，后者保证即使写到一半失败也不会留下半份名单。
- **被授权人按 `user_id` 指定，不按用户名**：按用户名的话，
  "用户名不存在"与"授权成功"就构成一个**用户名存在性探针**——而登录接口已经
  专门为此付过代价（账号不存在时也照跑一遍 260k 次口令校验，
  见 `auth.py` 的 `DUMMY_PASSWORD_HASH`）。已按安全的那个写，代价是归属人
  此刻没有得知同事 `user_id` 的途径，**这条记入 ❓ 待 P4 定**。
- **上限 `MAXIMUM_GRANTS_PER_STRATEGY = 1000` 的理由**：在册校验走一条 `IN`，
  参数个数受 SQLite 的 `SQLITE_MAX_VARIABLE_NUMBER` 约束（3.32 起默认 32766，
  本机 3.39.4 实测 32766 通过、32767 起报 `too many SQL variables`）。
  **上限不只是业务尺寸，它同时是挡住 `IN` 越界那道 500 的闸**：体量闸（约 1.2 MB）
  挡不住——被授权人 id 的 schema 下限是 1 个字符，且名单不许重复，于是 3 个字符的
  互异 id 凑满 32767 条的请求体只有约 85 万字节，过得了闸，随后在参数绑定处抛
  `OperationalError`；该异常在端点里**不被捕获**（只 `except IntegrityError`），
  客户端拿到 500 而非 400。已用探针实测复现（临时把上限摘掉发 32767 条）。
  业务侧的答案则是：规模该由业务定，不该由序列化体积的副产物定。
  **一处两次算错**：初稿写"约 2.8 万个条目，已越过那道坎"，改成"2.36 万"后**仍是错的**
  ——2.36 万按 50 字节/条估算，漏了花括号、引号与逗号，且默认了 32 位主键；按 32 位
  主键实算是约 **2.14 万**，而按 schema 允许的短 id 实算是约 **4.92 万**（远超 32766）。
  教训与 D.03 那次同类：**写进 docstring 的数字要当场算，不能凭印象**；
  补一句：**算的时候要把"什么算最短"的假设一并写出来**，否则改正也只是换一个错数。
- **验收**：`platform-plan.md` §11 的 P2b 行**部分通过**——「被授权人可见」
  与「非授权人一律 404」通过；**「可跑」待 P3**，`run` 权限此刻只是落库的一个
  取值，判定它要等 `POST /api/runs`。**283 项测试全绿**（新增 22 项在
  `tests/test_strategy_grants.py`；这是**交付当时**的数，审核轮补测后见 D.05）。
- **验收非空的证法**（变异检查，八次，改完即原样恢复，均转红）：
  ① 可见性不再随授权变更 → 4 条转红；② 去掉 `public` 保护 → 2 条；
  ③ 不校验被授权人在册 → 3 条；④ 不校验重复被授权人 → 1 条；
  ⑤ 不拒绝授权给归属人 → 1 条；⑥ 整体替换退化为追加 → 3 条（其中一条是
  重复插入撞上主键，译成 409 而非 200）；⑦ 删除动作先于校验且已落库 → 1 条；
  ⑧ 归属校验放宽为"可见即可" → 1 条。
- **一处自我纠正（第 ⑦ 次变异）**：初版把它做成"在校验之后、删除那里插一个
  `commit()`"，结果**八项全绿**。原因不是测试不行，而是**变异瞄错了位置**——
  实现里校验本就严格排在删除之前，那次插入根本没有改变任何被执行路径，
  测的是幻觉。改成"把整段删除挪到校验之前并落库"后才如预期转红 1 条。
  教训：**变异必须落在真的会改变执行顺序的地方**，否则"全绿"既可能是测试无用，
  也可能是变异无效，二者必须分清——这正是 D.03 里那次自我纠正的同一个坑。
- **顺带收敛（DRY）**：`SignedInAccount`、可见策略列表与策略详情的读取辅助
  此前在两个测试模块里各有一份，现收进 `tests/helpers.py` 并由三处共用；
  字段从只留 `user_id` 改为连 ORM 记录一起留，免去每个用例按主键回查一次。
  `test_strategy_upload.py` 与 `test_strategy_visibility.py` 的重复副本已删。
- **没有改动可见性查询本身**：`build_visible_strategy_query` 一行未动，
  授权端点只是让数据落到它已经认得的形态上。故 D.02 的越权变异检查
  （摘掉可见性条件 → 10 条转红）仍然覆盖本轮。
- **同步回写**：`platform-plan.md` §6 修订 ①（授权 PK 随 P2b 落地）、
  新增 §7.5（授权共享语义与可见性耦合）、§9 的 P2 落地范围、
  §11 的 P2b 行、§13 已锁定选择表新增"授权与可见性的衔接"一行。

---

## D.05 · 2026-09-25 （第五批） P2b 审核修正与测试补强

> 归档于 2026-09-26（主文件逼近 50 KB 上限时从 ✅ 区移出）。**下列原文一字未改。**

- **第二轮审核**（本批修正被打回复核）结论：**0 严重 / 2 中 / 5 低**，
  其中**中-1 是本项目第二次同类错误，且方向相反**——见下。
- **中-1（已改，事实错误）**：`_ensure_each_grantee_exists` 的 docstring 里
  "不设上限就只剩体量闸这一个约束"这句话**是假的**。实算（仓内真实常量）：
  体量闸 1,179,648 字节；按 32 位主键的条目最多 **21,447** 条（未越过 32,766），
  但 `StrategyGrantRequest.grantee_user_id` 的 schema 下限是 **1 个字符**，
  且名单不许重复，于是 3 字符互异 id 凑 **32,767** 条的请求体只有约 **85 万**字节，
  **过得了体量闸却越过了 `IN` 的参数上限**。实测该链路：端点只 `except
  IntegrityError`，`OperationalError` 落到兜底处理器 → **500 而非 400**。
  已临时摘掉上限跑探针复现，随后按字节还原。**结论方向不变（上限必须有），
  但"为什么"原本写反了**——照着错的理由推理的人会以为上限只是业务取舍。
- **中-2（已改）**：上限的**取值**没有任何测试钉住——把常量从 1000 改成 10 或
  40000，全部授权用例照样绿，而"单次授权的用户数不得超过 N"是**对外可见的契约**。
  已补一条钉住字面值的用例（`MAXIMUM_GRANTS_PER_STRATEGY == 1000` 且文案含 1000）：
  改这个数应当是有人专门决定过的事。
- **低-1 / 低-2（已改，文案）**：`enums.py` 的 `StrategyVisibility` 说"按授权集合在
  private 与 shared 之间切换"，漏了 `public` 那一格（`_apply_visibility_for_grants`
  首行即 `return`）；"故不存在授权写了却谁也不可见的状态"是绝对表述，而测试套件
  自己就构造了这个状态（`private` + 授权行），已加"**public 除外**""**经该入口**"
  两处限定。`GrantPermission` 原文用现在时描述两件**都还没落地**的事，
  且易被读成互斥映射（实际 `build_visible_strategy_query` 不看 `permission_type`），
  已改为"应对应"的约定语气并点明：给可见性查询补 `permission_type == 'read'`
  不是"让注释成真"，那会挡住只有 run 权限的人。
- **低-3（已改）**：两条旧用例的唯一前置写入没有断言，写入路径若退化成
  "回 200 但不落行"，这两条**依旧全绿**（"other 看不到"在"压根没写过"时同样成立）。
  已给 setup 补 200 断言。
- **低-4（已改）**：`test_user_isolation.py` 里 5 处手抄的详情请求改经
  `get_strategy_response`；其中"不带令牌"那一处**刻意保留**原样，它测的正是无令牌。
- **低-5（未改，记下理由）**：7 行 102 字符的行宽离群（本仓其余代码 ≤92）。
  `.editorconfig` 无行宽约定，非可强制项，改它只换来 7 行 diff 而无行为变化；
  留给下一次格式化一并做。

- **来源**：D.04 交付后按项目规则送 `code-reviewer` 复核，结论
  **0 严重 / 5 中 / 4 轻**。本批逐条处置，改动集中在测试与注释，
  **端点行为一行未改**——这正是要的：D.04 已验收的契约不该在审核轮里漂移。
- **补的用例**（D.04 记的 283 项此后变为 **287** 项；`test_strategy_grants.py`
  由 22 项增至 26 项——其中两条是就地加严，另四条是新增）：
  - **恰好等于上限**的名单要被收下。原上限用例只测"超了被拒"，
    判据写成 `>=` 也照样绿——那会让上限悄悄比声明的少一个，调用方无从知道。
  - `shared` 且无授权，收空名单后应落回 `private`（原先只覆盖了反向）。
  - **非归属人对 `public` 策略改授权仍是 404**。原"非归属人"那条用的是
    `private` 策略，404 也可能只是没通过可见性那一关；换成 `public` 后
    可见性对非归属人是敞开的，此时还拒就只剩归属校验一个原因。
  - 超限与重复名单被拒时，**从非空旧名单起步**再断言旧名单原样保留。
    从空名单起步的话，"旧名单没被改动"与"压根没写过"是同一个观测结果，
    断言落在后者上也照样绿。
- **新增与加严的用例非空的证法**（变异检查四次，改完即按字节还原，均转红）：
  ① 上限判据 `>` 改 `>=` → 恰好等于上限那条转红；② 可见性只转 `shared`、
  不转回 `private` → 2 条转红；③ 归属收口放宽为"可见即可" → 非归属人改
  `public` 策略那条转红；④ 删除动作挪到校验之前并落库 → **3 条**转红，
  其中两条正是本轮加严的那两条。**D.04 的同一次变异只杀得掉 1 条**，
  差别就在"旧名单是不是非空起步"——这正是加严要换来的东西。
- **DRY 收敛**：`STRATEGIES_PATH` 三处重复 → 统一取自 `tests/helpers.py`；
  策略详情的 GET 在四个测试模块里各抄一遍 → 收进 `get_strategy_response` /
  `read_strategy_detail` / `read_strategy_status_code`；`test_strategy_visibility.py`
  的 `owner_and_viewer` 由 4 元组改为 `AccountPair` 数据类——两个同类
  `SignedInAccount` 靠位置区分归属人，记反了整条越权用例就测成相反的意思。
  另删掉三处重构后不再有调用点的导入。**`AccountPair` 留在本模块**，不进
  `helpers`（`GrantCast` 同理，两处 docstring 已写明同一条规则：
  `helpers` 放跨模块基元，单一场景的"角色组"与用例放一起）。
- **一处审查意见不予采纳（理由记下，免得下次重议）**：
  「同策略并发 PUT 会锁升级失败，应回 500 而非 409」。**实测未复现**——
  以 ASGITransport 并发压了两轮（4 并发 × 5 轮，8 并发 × 8 轮且请求体非空，
  共 64 次 PUT），**全部 200**，终态一致（5 条授权、可见性 `shared`）。
  连接池为 `AsyncAdaptedQueuePool`。故未改代码；`GRANTS_CONFLICTED_MESSAGE`
  那条 `IntegrityError` 兜底仍按库内房规保留，它由"删除与重插之间被抢占"
  触发——本环境测不出来，不等于线上不需要。**残余风险记在此处。**
- **一处审查意见属既知行为**：422 响应体会回显被拒字段的值。这是 D.02 就拍板过的
  全局取舍（只对 `password` 一类字段抹值，其余照旧回显以免报错失去可读性），
  非本轮引入，未改。两条业务拒绝（400，未知/重复被授权人）另有专门用例断言
  不回显提交的 `grantee_user_id`（见 D.04）。
- **文档同步**：`enums.py` 的 `GrantPermission` docstring 原写"read 可看策略与
  历史运行"，与 `visibility.py` 的"运行只有提交人本人可见，授权不延伸到他人的
  运行记录"相冲，已改为与实现一致。`platform-plan.md` §7.5 原引"见 §6 的
  `StrategyVisibility` 语义"，而 §6 并未写过该语义，已补 DDL 注释并改指该行。
  另修正 D.04 同一条里"旧版 SQLite 上限 999"的残留（上限 1000 本就越过 999，
  该子句自相矛盾，与已改正的 docstring 保持一致）。

---

## D.06 · 2026-09-25 （第六批） P3 runner 本体 + `params` schema 定案

> 归档于 2026-09-26（主文件再次逼近 50 KB 上限时从 ✅ 区移出）。**下列原文一字未改。**

- **交付**：`POST /api/runs`、`POST /api/runs/{id}/cancel`、
  `app/scheduler/` 八个模块（`engine_config` / `result` / `output` /
  `workspace` / `registry` / `runner` / `recovery` / `scheduler`）、
  启动恢复、`PlatformSettings` 三项新配置、`engine_probe.py` 搬移、
  health 三个存在性字段，以及 manifest 的 `params` schema 定案 +
  新增 `run_field_keys`（见 §7.2）。**P2 的「上传后能跑通」与 P2b 的
  「被授权人可跑」两项挂账至此结清。**
- **开工前拍板五项**（后果见 `platform-plan.md` §13 的第二张表）：
  授权粒度**不区分 `read`/`run`**（跑权限 == 可见性，`GrantPermission`
  原样保留、只改 docstring）；`public` **隐含可跑**；`params` schema
  本轮定案；P3 范围含 **cancel**（只读端点留 P5、`DELETE` 留 P7）；
  **Tick 本轮不开**（提交侧 400，判据是具名常量 `SUBMITTABLE_MATCH_MODES`）。
- **验收**：**380 项测试**（376 项默认全绿 112 秒 + **4 项真引擎验收**
  默认不跑，标 `real_engine`，见 `backend/tests/test_real_engine_acceptance.py`）。
  真引擎四项全通过：① 一轮 Bar 成功且指标与基线同口径（并回读渲染出的
  `BackTest.json` / 策略配置，确认 `run_field_keys` 真的写进了策略侧）；
  ② 两轮并发各写自己的库（区间相交 + 库文件名各嵌自己的 RunId）；
  ③ 超时真把引擎杀掉（1 秒时限 + 十五年区间，静默期后仍是 `timeout`、
  无 `result.json`、stdout 里无收尾标记）；④ 取消运行中的真作业
  （等它真在跑再取消），同样验静默期与产物缺席。
  「重启后端把在跑的轮标 `interrupted`」在桩侧另有 3 条用例
  （`test_run_recovery.py`，经**停止态**的库门面播种，第二次启动时验恢复）。
- **真引擎实测出的四条新事实**（**其中第一条推翻 P0 的记载**）：
  - **`argv[0]` 不必是裸文件名**：`./grid_strategy.py`（正斜杠相对）与
    `C:\...\Temp\<job>\grid_strategy.py`（反斜杠绝对）各跑一轮，**都是
    退出码 0、整轮回测跑完**。故 D.01 记的"正斜杠路径会在启动期终止进程
    （退出码 1）"**在现行构建上复现不出来**。差别的来源无法判别（当时是**由
    日志器的 `strrchr` 行为推出的**推论、还是旧构建确实如此），但**就现行
    `cp314` 构建而言，"裸文件名"不是启动的必要条件**。平台仍固定传它，
    理由是它与其余契约自洽（入口、配置、产物同落 CWD），不是"否则起不来"。
    D.01 原文已入归档，订正写进 `job-workspace.md` §3.1 与
    `platform-plan.md` §3/§4/§12.6。
  - **引擎比预期快得多**：三个月 5m 回测约 **1.7 秒**，2010–2024
    （58176 根 bar）约 **3.8 秒**。故真引擎的超时验收只能靠**压时限**
    （1 秒必杀），不能靠拉长时间范围。
  - **种子库缺失时的余额**：本轮基线 `Balance=999377.0899999999`，
    与 P0 的 `998951.4506464996` 差 **425.6393535003**，恰好等于 P0 那轮
    费用三项之和 `420.0 + 4.297395 + 1.3419585 = 425.6393535`。
    **引擎行为未变，变的只是输入**——两份基线在 `job-workspace.md` §6.1/§6.2
    并存，各自注明前提，**没有用新数字覆盖旧基线**。
  - **`Dump/` 不必预建**：目录里只有四个输入文件时，引擎自己建出 `Dump/`
    与 `Dump/<RunId>/`。
- **变异检查（§7.3）**：① 写路径改绝对 → 转红；② 串行读两路输出 → 转红
  （65 秒，管道死锁）；③ 去掉尾部对齐 → 被纯函数用例杀掉（集成侧的 flood
  用例全是 ASCII，**杀不掉它**——这正是纯函数用例存在的理由）；④ 去掉尾部
  截断 → tracemalloc 实测 9.18 MB vs 1 MB 阈值，转红；⑤ 去掉"只对跑完的
  终态镜像"那道守卫 → 转红；⑥ "退出码 0 即成功" → 转红；⑦ 退出码 3 只看码
  不看文件 → **初版杀不掉**，把 `build_unexpected_exit_message` 提成公开
  函数并断言 `error_msg` 后才转红。
- **两处 CAS 现有用例杀不掉（覆盖缺口，记在这里免得被当"测过了"）**：
  `_claim_next_run` 的 `WHERE Status='queued'` 与 `_interrupt_unstarted_run`
  的同名条件，都只在"读到的状态"与"更新时的状态"之间那个 `await` 宽的窗口里
  才起作用，而该窗口**无法从 HTTP 侧确定性抵达**（取消端点另有 409 守卫挡住
  了另一条路径）。去掉任一条，现有 380 项全绿。代码注释已写明两处条件不能省；
  要真杀掉得给 runner 插一个可注入的暂停点。**这是已知缺口，不是已知缺陷。**
- **产品代码偏差清单**（相对计划原文，均已落地并写进注释）：
  `sys.executable` + 裸文件名的启动形态（Windows `CreateProcess` 不认文件关联，
  实测 `WinError 2`）；`cancel_signal` 用 `Event` 而非标志位；
  `run_submission.py` 越过 200 行；收尾抢输时**不写任何指标列**，
  只做一次不碰 `Status` 的窄 UPDATE 补 `StdoutTail`/`StderrTail`/`ExitCode`；
  `interrupted` / `timeout` **不镜像**指标（`ErrorMsg` 保住自己的取消/超时文案）；
  `ResultMirror` 27 列（计划 DDL 里数成 25，差的两列是 `DbPath`/`DumpPath`）；
  `JobOutputCapture` 的句柄泄漏修掉（Windows 上 `unlink` 成功与否即"关没关"）；
  超时路径也记 `exit_code`；`result.py` 的 GBK 兜底解码；取消路径用
  `populate_existing` 而非 `expire_all`；`_finalize` 里删掉一个无行为的
  `error_msg` 赋值块，其理由折进镜像注释；新增 `build_unexpected_exit_message`。
- **P3 新增的已知缺口**（不做，写进 `platform-plan.md` §12.9–12.14）：
  纯 FIFO 无配额、不做重启后续跑、不做多 worker（单实例是硬前提）、
  孤儿进程不自动清理、Tick 不可提交、种子库不重建。
- **文档回写**：`platform-plan.md`（§2 工具链订正、§3/§4 的 `argv[0]` 订正、
  §5.1/§12.1 的 `cp314`、§6 的 27 列、§7.2 的 `params` 定案与
  `run_field_keys`、§8 的调度器模块表、§9 的 P3 落地范围、§11 的 P2/P2b/P3
  三行、§12 新增缺口、§13 新增拍板表）、`job-workspace.md`（§1 的 `Dump/`、
  §2 的结构性约束与派生行为、§3.1 重写、§6 拆成两轮基线）、
  `enums.py` 的 `GrantPermission` docstring、`strategy_store.py` 与
  `test_health.py` 里的 `cp311` 残留。另启用 `PROGRESS-archive.md`
  （D.01 与 Q.01/Q.02 入档）。

---

## D.07 · 2026-09-25 （第七批） P4 前端骨架 + 三处后端前置端点

> 归档于 2026-09-26（主文件再次逼近 50 KB 上限时从 ✅ 区移出）。**下列原文一字未改。**

- **交付**：`frontend/` 从零到闭环——47 个源文件 / 约 6850 行。技术栈
  **Vue 3.5 + TS 6.0 + Vite 8.3 + Tailwind v4 + Pinia 3 + vue-router 4.6**，
  HTTP 用**原生 `fetch`**（不引 axios），**不引 UI 组件库**（表格、分页、
  模态框、提示条、表单控件全部自写，共 14 个 `components/`）；
  依赖只有 `vue`/`vue-router`/`pinia` 三个。目录：`api/`（`client.ts` 单一出入口
  + `types.ts` 手写契约 + 四个域模块）、`stores/`（session / strategy-catalog /
  user-directory）、`router/`（懒加载 + 登录守卫）、`domain/`（纯逻辑，
  六个模块 + 四份 spec）、`components/`、`composables/`、`views/`（8 个页面）。
- **后端三处前置件**（都是前端做不下去的硬原因，不是顺手加的）：
  `GET /api/users/directory`（受限用户目录，授权表单选人用）、
  `POST /api/users` 的 `display_name` **收紧为必填**（空白 → 400
  「显示名不能为空白」）、`StrategyVersionResponse` 增 `manifest_json`
  纯透传（提交表单按它生成控件）、`GET /api/runs/{id}/files` 与
  `/files/{relpath}`（防穿越 + 一律附件下发）。
- **验收**：后端 **406 项通过 / 4 项 deselect**（真引擎那 4 项默认不跑，
  新增 26 项含目录、显示名、manifest 透传、产物四条线）；前端
  `type-check` 无错、`vitest run` **58 项全绿**（5 个 spec，只测纯逻辑，
  不装 `@vue/test-utils` 与 jsdom）、`vite build` 成功（按路由分包，
  证明懒加载真的生效）。
- **代理链路冒烟 13 项**（真后端 + 真 Vite 代理，全程打
  `http://[::1]:5173/api`；用**一次性临时库**，`QUANT_DATABASE_URL` /
  `QUANT_RUNS_ROOT` / `QUANT_USER_LIBRARY_ROOT` 都指向 `%TEMP%`，
  **项目自带的 `backend/data/` 与 `runs/` 一行未碰**）：首页 200、
  `/api/health` 无令牌 401、未知路径交前端路由、登录换到令牌、
  建号缺 `display_name` 得 **422 且 `detail` 是数组**、显示名全空白得 400、
  建两个号 201、目录只回 `id` + `display_name`、`query` 过滤命中、
  **目录排除了自己**、运行与策略列表回 `{total, offset, limit, records}` 信封。
- **⚠️ Vite 只绑 `[::1]:5173`**（IPv6 回环），`http://127.0.0.1:5173` 连不上——
  浏览器用 `http://localhost:5173/` 即可（Windows 上 `localhost` 先解析到 `::1`），
  但拿 `curl`/脚本探活的人会以为服务没起。**代理目标是显式写死的
  `http://127.0.0.1:8000`**，故它打后端走的是 IPv4，两边不冲突。
- **两处「前端比后端更严」的偏差（本批发现的真问题）**：`ManifestParameter`
  的 `label` / `type` 与 `StrategyManifest.params` 在 TS 里**都写成了必填**，
  而后端 `StrategyParameter` 给这些字段都写了默认值、`params` 是
  `default_factory=list`。后果是**一份完全合法的 manifest 会被前端判为"读不动"，
  参数表单整个不渲染**——这是本项目最忌讳的「界面放行、后端 400」的**反面**
  （界面拦住、后端放行），且用户对着一个不出现的控件无从下手。已按后端默认值
  放宽（省 `type` 即 `string`、`label` 空串回落 `key`、`params` 可省），
  各补一条回归用例。**教训：契约要从后端源码逐字读，不能凭接口形状推。**
- **错误信封的三处契约在客户端逐条对上**（`api/client.ts`）：422 的 `detail`
  是**数组**（只读 `loc`/`msg`，**不读 `input`**，免得把提交的取值渲染到界面上）、
  401 **可能没有 `detail`**（Starlette 层就挡了，回固定文案
  「登录状态已失效, 请重新登录」）、`fetch` 抛异常归一成 `status === 0`
  （「无法连接到服务器, 请确认后端已启动」）。三类各有断言。
- **时间戳必须补 `Z`**：`clock.utc_now()` 回的是**朴素 UTC**（JSON 里没有
  时区标记），`new Date(...)` 会按本地时区解析，UTC+8 下显示**早 8 小时**。
  客户端一律经 `domain/format.ts` 的解析函数补 `Z` 再格式化，并给这条专门
  写了回归用例（用例在 UTC 环境下是盲的，注释已写明这一点）。服务端发的是
  朴素 UTC 这件事本身没改——改它要动全仓所有时间序列化，不属本批。
- **表单里没有行情模式选择**（Tick 拍板不给）：`match_mode` 固定 `Bar`，
  判据是常量 `SUBMITTABLE_MATCH_MODE = 'Bar'`，与后端的
  `SUBMITTABLE_MATCH_MODES` 同名同值。参数控件的派生规则与后端
  `validate_parameter_value` **同一套语义，报错文案逐字照抄**
  （「需为整数」「不得小于 0.1」「必填」…）——界面放行而后端回 400 是本项目
  最忌讳的「点了有反应但没用」。
- **版本号照抄本机同族项目的已验证组合，不追最新**：`vue-router` 最新 5.x、
  `pinia` 4.x、`vitest` 5.x、`typescript` 7.x **全是主版本跳跃**，
  在 P4 顺带做迁移审查是拿验收换未知数。四处都钉在老主版本上，日后单独评估。
  **有意不照抄兄弟项目的**：`axios`（改原生 fetch）、`element-plus`（最小集）、
  `sass`（改 Tailwind）、`unplugin-auto-import`（隐式全局与「名称即意图」相冲）。
- **`defect_tools`（又名 `amies`）不在本机**（全盘搜过 `D:/Gitee`、`D:/Github`、
  `D:/Files`、`C:/Users/...`，含 `variables.css` 与 `tailwind.config*` 的文件名
  搜索；只有两份 PROGRESS 与计划正文提到它、**都不给路径**）。故"与它有意分叉"
  这句话的可核对依据只能是**本机真实存在的同族项目**
  `D:/Gitee/ShopKit/frontend-platform` 与 `D:/Gitee/RMS/frontend-admin`
  （同一作者、同栈）。**注意它们与计划 §8/§10 的描述不同**：样式是
  **SCSS + Element Plus**、没有 `variables.css`；计划的这两节已按实况回写。
- **踩到并写进 `frontend/.gitignore` 的一个坑**：仓根 `.gitignore` 是从 Python
  模板来的，`lib/` `runs/` `users/` `build/` `target/` 等是**无锚点**模式，
  会匹配任意层级——于是 `frontend/src/lib/x.ts` 或
  `frontend/src/views/runs/x.vue` 会被**静默忽略**（文件写出来了，
  `git status` 里什么都没有）。故前端的纯逻辑目录叫 `domain/`、运行页面是扁平的
  `views/RunListView.vue`。`frontend/.gitignore` 里留了自查命令
  `git check-ignore -v --no-index <路径>`。
- **新记的已知缺口**（见 `platform-plan.md` §12.15–12.18）：
  `display_name` **无唯一约束**（重名时授权表单分不清两个人，选错人等于把策略
  授权给错的人；修法是加唯一约束，但那会新增一条 409 路径，超出 P4）；
  授权被撤销后策略从可见列表消失，**运行列表与详情里的策略名回落成显示 id**；
  生产部署仍靠 Vite 代理（**两个终端**），FastAPI 托管 `dist/` 或反向代理留 P8；
  前端**无 e2e**，闭环靠手动验收（这也是计划原本的验收方式）。
- **文档回写**：`platform-plan.md`（§7.5 目录端点的落地与泄漏面收口、§8 目录树
  与本地开发起法、§9 新增目录端点行并订正 P3/P5 的落地范围、§10 页面表按实况改、
  §11 的 P4 行、§12 新增缺口、§13 新增拍板表）、`PROGRESS.md`（本条 +
  **Node 版本由 24.15 订正为 24.16** + 备注），归档（**D.02 与 D.03 整条搬入**
  ——✅ 区超 5 批且主文件越过 50 KB 目标；Q.05 关闭、Q.06 半关闭）。

---

## D.08 · 2026-09-26 （第八批） P5 可视化：权益曲线与回撤 + 5 张结果明细表

> 归档于 2026-09-26（主文件加 D.11 后越过 50 KB 上限，按 Harness §8.1
> 把 ✅ 区最旧的整条移出）。**下列原文一字未改。**


- **交付（后端，不新增 Python 依赖）**：新模块
  `backend/app/services/result_database.py`——白名单 5 张表
  （`Capital` / `Trade` / `Order` / `Position` / `PositionDetail`）、
  只读连接 contextmanager、`read_capital_series` 与 `read_result_table_page`；
  `catalog/schemas.py` 增 `EquityPointResponse` / `RunEquityResponse` /
  `ResultTableResponse`；`routers/runs.py` 增
  `GET /api/runs/{id}/equity` 与 `GET /api/runs/{id}/tables/{table_name}`
  （`offset` / `limit` 走 `Query` 约束），加两个私有前置
  `_require_finished_run`（非终态 → **409**）与
  `_resolve_result_database_path`（`resolve()` 后必须仍在作业目录内）。
- **交付（前端）**：`echarts ^6.1.0` + `vue-echarts ^8.3.0`（已拍板，
  按需 `use([...])` 注册）；`api/types.ts` 加结果库契约与 5 张表的**镜像**清单、
  `api/runs.ts` 加两个请求函数；`domain/equity.ts`（**纯函数**：
  `buildEquitySeries` / `summarizeEquity`）+ 15 项 spec；`format.ts` 加
  `formatPercentRatio`；新组件 `EquityChartPanel.vue`（概要四项 + **两张独立的图**：
  权益曲线与回撤面积图，各带 `dataZoom`）与 `ResultTablePanel.vue`
  （5 个页签按需取数、表头取响应的 `columns`、`PaginationBar`）；
  `RunDetailView.vue` 在终态挂载这两节，非终态显示一句提示（复用既有
  `watch(isTerminal)`，翻成终态即取数，不必再等一次 2 秒轮询）。
- **验收**：后端 **432 项通过 / 4 项 deselect**（新增 26 项，见下）；
  前端 `type-check` 无错、`vitest run` **75 项全绿**（6 个 spec）、
  `vite build` 成功——ECharts 只进详情页那一块 chunk（559 kB / gzip 190 kB，
  按路由懒加载，首屏不付这份钱）。**真引擎验收 4 项全过**：在既有的
  `test_a_real_bar_backtest_runs_through_the_platform` 的 `async with` 块内补了
  `/equity` 与 `/tables/Trade`、`/tables/Order` 三次请求（该测试原有的断言都在
  块外，而 API 调用必须在块内）。
- **代理链路冒烟 12 项**（沿用归档 D.07 的一次性临时库手法，项目自带的
  `backend/data/` 与 `runs/` 一行未碰）：权益 62 点、首点 `1000000.0`、
  末点 `999377.0899999999`、`Trade` 总 84（本页 5 行、含 `TradingDay` 列）、
  `Order` 总 654、`Position` 116、`PositionDetail` 290，以及
  `BarMarketData` **404**（真实存在但未开）、`limit=101` **422**、`offset=-1`
  **422**、`/files` 回归 200。
- **验收判据订正**：计划 §11 原文的「`1000000.0 → 999549.73`」中，`999549.73`
  是 **P0 有种子库那轮的基线，而那批产物已不在盘上**（`runs/` 已被清掉）。
  改为**自洽形式**：首点恒为 `1000000.0`（`Capital` 的种子行 = 初始资金）、
  末点与该轮 `result.json.Balance` **逐位相等**。这条不依赖种子库在不在，
  比钉死一个常数更强，本轮实测即 `999377.0899999999`（无种子库变体）。
- **对着真结果库核实过的引擎事实**（写进断言之前先只读验过，不是推测）：
  `Capital` 22 列、PK `(TradingDay, AccountId)`、**首行是种子行**
  （`Deposit` = 初始资金，`Balance` = `PreAvailable` = `1000000.0`），
  首行 `TradingDay` == `StartTradingDay`、末行 == `LastTradingDay`；
  **`Order` 是 SQLite 保留字**，不加引号直接 `near "Order": syntax error`；
  `?mode=ro` **确实是只读的**（对同一文件写报 `attempt to write a readonly
  database`）；行值只有 `str`/`int`/`float`（无 BLOB、无 NULL）。
- **三处必须写对的地方**：① **`as_uri()` 要求绝对路径**，先 `resolve()`，
  否则 `ValueError: relative paths can't be expressed as file URIs`；
  ② **`db_path` 视为不可信输入**——它是引擎侧写进 `result.json` 再由调度侧镜像
  入库的值，而策略是任意 Python、**它同样可以被伪造**，故与产物路径同等对待；
  ③ **`asyncio.to_thread` 本仓首例**：sqlite3 是阻塞 API 且连接不能跨线程，
  把「开连接 → 查询 → 关连接」整个放进同一个 worker；与 P4 直接内联文件 IO
  的做法**有意分叉**（一条 `COUNT(*)` + 一页 `SELECT` 的耗时随库长大，
  目标是云上多用户，不该占着事件循环）。
- **回撤不在后端算**：端点只回 `Capital` 的原样逐日序列，回撤与收益率由前端
  纯函数派生。理由是它是**派生数据**——后端一旦算了，前端要点另一条曲线就再加
  一个字段，接口会随图表变化；而前端这份是纯逻辑，正好进 vitest 单测。
  `summarizeEquity` 用**单遍峰值跟踪**而不是 `Math.max(...balances)`
  （大序列会撞上实参个数上限），且比较用 `>` 而非 `>=`（等深时记**第一个**谷底）。
- **矩阵式变异检查**（本项目既定手法，改坏即转红、改完原样恢复）：
  去掉表名白名单 → 白名单组转红；表名不加引号 → `Order` 那条转红
  （`sqlite3.OperationalError: near "Order": syntax error`）；
  去掉 `is_relative_to` → 穿越用例转红；去掉 `TERMINAL_RUN_STATUSES` 判定 →
  「运行中」用例转红；去掉 `?mode=ro` → 只读用例转红。**只读这条是断言不是注释**：
  对 contextmanager 出来的连接执行 `CREATE TABLE` 必须抛 `OperationalError`。
- **两处 Harness 红线当场处理**：`EquityChartPanel`（215 行）与
  `ResultTablePanel`（224 行）都越过了「单文件 200 行须先请示」——
  在**不牺牲可读性**的前提下压回 199 / 198 行（单行三属性元素、抽
  `describeTabClass` 辅助、收紧文档注释），未改变任何行为。
- **新记的已知缺口**（见 `platform-plan.md` §12.19–12.22）：
  另 12 张表在界面上看不到（含 2928 行的 `BarMarketData`）；
  **表名清单是两处真相**（后端白名单 + 前端镜像，漏改一处只会让某个页签 404）；
  **盘中回撤不可得**（`Capital` 只有逐日结算权益，`Margin`/`MarketValue`
  在本样本里恒为 0，图上画的是逐日而非日内）；
  **失败的轮看不到部分结果**（`db_path` 只在 `result.json` 写成功后才镜像入库，
  取消/超时/启动失败的轮一律空串 → 404）。**不做**：K 线图、多轮曲线叠加（P6）、
  图表导出图片、明细表导出 CSV、大表虚拟滚动（一页最多 100 行）。
- **文档回写**：`platform-plan.md`（§9 新增「P5 落地范围」六条 + 排序与分页说明、
  §10 页面表 `/runs/:id` 改 ✅ P5 并补 ECharts 落地实况、
  §11 的 P5 行改 ✅ 并**订正 `999549.73` 那条判据**、§12 新增缺口 19–22、
  §13 新增「P5 开工前拍板表」）、`PROGRESS.md`（本条 + R.01 标题与 P5 行）。

---

## D.09 · 2026-09-26 （第九批） 网格步长由绝对价格改为比例

> 归档于 2026-09-26（加 D.11 后主文件越过 50 KB 上限，按 Harness §8.1
> 把 ✅ 区最旧的整条移出）。**下列原文一字未改。**


- **起因（用户实测报的异常）**：`GridStep=10` 在 `SZSE/000001`（锚价 11.94）上每一档都远在
  市价之外，一轮 **610 笔委托、0 笔成交**；同一份参数在 `SSE/600519`（约 1558）上只有
  0.64% 的间距，照常成交。故它是**绝对价差**，在低价标的上结构性失效。用户以为
  "同参数直接跑 QTT 有成交"，实为**换了标的**——那轮的 `MissingRateKeys` 指认它跑的是
  `SSE/600519`（记此以免后人照抄这个比较）。
- **改法（用户拍板：线性比例）**：档位价 `锚价 × (1 ∓ 步长 × 档号)`、平仓价
  `开仓成交价 × (1 ± 步长)`，步长是**比例**（`0.01 = 1%`）。落点三处：① 引擎仓
  `test/PythonStrategyGrid/grid_strategy.py` 及其运行副本 `bin/Release/grid_strategy.py`；
  ② C++ 孪生 `test/TestStrategyGrid/GridStrategy.cpp/.h` 与其单测；③ **平台已上传的那份**
  （源码 + manifest 快照 + 库里那一行，见下）。
- **构造期校验（两侧同源）**：`0 < GridStep × GridCount < 1`（最远一档仍为正价）。Python 抛
  `ValueError`（中文消息落在作业目录的 `stderr.txt`，用户看得见）；C++ 抛 `std::logic_error`，
  且 `Main.cpp` 新增 `try/catch` 把它挡成**退出码 1「宿主启动失败」**，不再走 abort
  （`RunResult.h` 记明 MSVC 下 abort 与「引擎报告失败」同为码 3，只能靠结果文件消歧）。
- **连带项**：三份 `TestStrategyGrid.json` 的 `10.0 → 0.01`；manifest 的默认值 `0.01`、
  标签改「网格步长(比例, 0.01=1%)」、`minimum` 由 `0` 收到 `0.0001`（0 必被拒启，不该在表单里可选）；
  真引擎验收基线重取（下表）；`job-workspace.md` 新增 §6.3 并在 §6.2 加指针、计划 §11 追加
  「口径变更」段；`UnitTests.exe` 与两个 `TestStrategyGrid.exe` 重编（**旧 exe 配新配置会
  静默 0 成交**，故必须跟着重编）。
- **基线重取（同链路、同输入、只换 `GridStep`）**：

  | 指标 | 旧（绝对 10.0） | 新（比例 0.01） |
  | ---- | ---- | ---- |
  | `TradeCount` | 84 | **34** |
  | `OrderCount` | 654 | **629** |
  | `Balance`（无种子库） | 999377.0899999999 | **999257.8562340003** |
  | `BarMarketDataCount` | 2928 | 2928（不变） |
  | `PositionDetail` | 290 | 282 |

  成交变少是**预期**：旧值在 `600519` 上等于 0.64% 间距，新值 1% 更宽。
  **带种子库那份 `998951.4506464996` 是旧口径的**（本机种子库不在盘上，无法重取），
  与新版余额不可相减——「费用三项 = 两份基线的差」这条判据只在旧口径内成立，已写进
  `job-workspace.md` §6.3。
- **验证**：C++ 单测 **112/112、743 断言全过**（新增 1 条"比例越界构造期拒启"用例）；
  做**两次变异**——把两处公式改回绝对形态、把比例上界拿掉——分别转红且只红对应用例，
  两次都已还原并重建；真引擎验收 **4 项全过**；**两个孪生在同一输入下逐位同值**
  （`629 / 34 / 999257.8562340003`，C++ 那轮在 `bin/Debug` 下跑）；比例守卫逐点探过：
  `0.01×5` ✓、`0.1999×5` ✓、`0.2×5` ✗、`10×5` ✗、`0` ✗、`-0.01` ✗、`GridCount=0` ✗。
- **副作用（用户需知道）**：① 平台那份**已上传的版本 1 是就地改写**——源码、`manifest.json`
  快照、库里的 `ManifestJson` 与 `SourceHash` 三处一起动（提交页读的是库里那份，落单就会
  "界面显示旧默认值、实跑新代码"）。旧件已复制留档在
  `%TEMP%/quant-cdp/uploaded-version-1-backup-20260926/`；**策略版本本应不可变**，就地改是
  用户点名的落点，若要改走"上传新版本"则须重走一遍上传接口。② 引擎仓 **8 个文件已改但
  未提交**（AI 不代提交引擎仓，待用户定）。③ `bin/Release/result.json` **未动**；
  `bin/Debug` 下多了一轮真回测产物；本轮验收产物在 `backend/_acc_tmp/ratio-20260926/`。

---

## D.10 · 2026-09-26 （第十批） 提交页按策略预填「上一次提交的参数」

> 归档于 2026-09-26（加 D.12 后主文件越过 50 KB 上限，按 Harness §8.1
> 把 ✅ 区最旧的整条移出）。**下列原文一字未改。**

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

---

## D.11 · 2026-09-26 （第十一批） 引入 Element Plus：地基 4 件 + 两个最脏页

> 归档于 2026-09-26（加 D.13 后主文件越过 50 KB 上限，按 Harness §8.1
> 把 ✅ 区最旧的整条移出）。**下列原文一字未改。**

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
  新增 **32 项**：`ParameterForm` 4（**复活归档 D.10 那份 parked spec**，含「选项下标必须以字符串上抛」）、
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
  提示条消失 → 再选一次该策略仍带出 → 换个没跑过的策略为纯默认、无提示条**（这是归档 D.10 欠着的
  手工验收，本批一并走）；参数项的必填/越界提示仍是 domain 那几句中文；回车能提交；
  ③ **换肤是否生效**——EP 按钮/标签应是品牌蓝 `#1d4ed8`，若仍是出厂亮蓝 `#409eff` 说明换肤没吃到；
  ④ 顺带回归四个共用页面（`/strategies`、`/strategies/:id`、`/users`、`/runs/:id`）：
  徽章/空态/错误条/分页条变了样，功能应不变——**空态的居中与灰阶是这批里最可能想调回来的地方**。

---

---

## D.12 · 2026-09-26 （第十二批） 观感层：统一外壳 / 版面原语 / 动效与焦点 / 两条反馈通道

> 归档于 2026-09-26（加 D.14 后主文件越过 50 KB 上限，按 Harness §8.1
> 把 ✅ 区最旧的整条移出）。**下列原文一字未改。**
>
> 这是**半关闭条目**：正文归档，仍生效的结论与**未走完的手工验收清单**留在主文件里。

- **起因（用户原话）**：「已经加了UI组件库了吗？看起来跟没加也没什么两样啊」——看过 D.11
  （commit `83374ec`）之后的判断。**这个判断成立**，且原因不在"库没生效"（实测转译产物里
  EP 的样式表 361,959 B 在的），而在 D.11 只做了"换组件"这一个动作：`--el-*` 全被指回本仓
  那 9 个颜色令牌，EP 控件的出厂观感因此被抹平成我们自绘的样子；页头 / 卡片 / 区块标题 /
  表壳仍是 Tailwind 手写类（页头类名串 6 处、卡片 14 处 9 种变体、表壳 5 种），换个控件外壳
  也看不出来；且全站 `transition-*` / `focus-visible:` / `ring-*` / `@keyframes` 实测都是
  **0 次**，也没有 toast 通道。
- **用户拍板**（2026-09-26）：① 保持**顶部导航**，不做侧边栏；② 成功与失败**都弹 toast**
  （成功约 2 s 自动消失，失败不消失且可关闭）；③ **换 `ElMessageBox` 并删掉
  `ConfirmDialog.vue`**——D.11 记为「不做」的那条在本批兑现。
- **四处新增件**：`PageHeader`（`title`/`description` + `leading`/`badges`/`actions` 三个
  插槽）、`SurfaceCard`（`title`/`description`/`tag`/`isBodyPadded`，无 `title` 时主体直接
  落在卡片上）、`PaginationToolbar`（每页 `ElSelect` + `PaginationBar`，自己承担"改每页 →
  offset 归零"）、`ContentSkeleton`（`el-skeleton` + 包裹层 `role="status"`），以及
  `composables/use-feedback.ts` 的四个**纯函数**（`showSuccessToast` / `showFailureToast` /
  `describeApiFailure` / `confirmAction`）。四份新 spec 钉住各自的契约。
- **反馈三类分流**（本批的判据，逐站照此判）：**a 表单自己的提交**（登录、提交回测、上传
  策略/版本、建号）——成功 toast，**失败有意就地留在表单里**（错误要与出错的字段同屏，弹到
  右上角等于让用户去别处找）；**b 表单之外的页面级动作**（删策略、取消运行、改账号状态、
  保存授权、下载产物）——成败都 toast；**c 加载类**——`ErrorBanner` + 重试或组件就地提示。
  连带把 4 个视图里"一个 ref 同时承载页面级加载失败与用户动作失败"逐处拆开；
  `UserAdminView` 那两条常驻提示条（全站唯一的成功提示就是那条永不消失的绿色 banner）删除。
  20 处重复的 `error instanceof ApiError ? error.detail : '…'` 收进 `describeApiFailure`，
  7 个因此用不上的 `ApiError` import 随之删掉（Harness §5 的 3 处阈值）。
- **三处有意偏离方案**：① `PaginationBar` 根行的 `justify-between` **保留**——在
  `PaginationToolbar` 里它是可证的空操作（收缩至内容的 flex 子项），删掉只会改变
  `ResultTablePanel` 独立使用时的观感；② 首屏骨架的判据写成 `isLoading = (手上还没有数据)`，
  在 `/strategies`、`/users`、`/strategies/:id` 三处都如此——否则翻页、传完新版本、建完号
  都会整片闪一次骨架屏（骨架屏是**带动画**的，而本批判据是"列表数据刷新不加任何动画"）；
  ③ manifest 编辑框的等宽字体写在 `el-input` **外层**即可：`.el-textarea__inner` 上是
  `font-family: inherit; font-size: inherit`（已在 2.14.6 的产物上核实），不必动用
  `input-style`。
- **行为差一处**：弹窗确认后立刻关闭，写入中的忙碌态改由**触发它的那个按钮**挂 `:loading`
  （三处调用点的触发键都在按钮上），D.11 里那句「处理中…」随之消失；按钮**同时换字**
  （`:loading` 的转圈不进可访问名，读屏用户只能靠"登录中…"这类文案得知提交在飞）。
- **验证**：`vue-tsc -b` 退出码 0（中途修一处：`el-table` 的 `#default="{ row }"` 是
  `DefaultRow`，传给吃 `User` 的函数须显式断言，因为 `Record` 索引签名满足不了必需属性）；
  `vitest` **146 项全绿 / 17 个文件**；`build` 成功（396 ms）。体积：主 CSS 382,097 →
  **383,310 B**，JS 合计 1,023,443 → **1,045,022 B**，ECharts 那块 559.35 → 558.63 kB（不变）。
- **本批不做**：深色模式、响应式/移动端、`text-rose-600 → text-danger` 全仓重命名、
  `ResultTablePanel` 换 `el-table`（列由服务端数据动态生成，`el-table` 无法静态声明）、
  页码按钮与跳页、侧边栏/页脚/主题切换器。
- **文档回写**：`platform-plan.md`（§10 有界面的 8 行加「观感层统一 2026-09-26」、P4 依赖那段
  补第二批现状、§12.18 第二段增补含实测结论与体积账、§13 新增「观感层拍板」表十行）。
  `PROGRESS.md` 本条 + 归档索引一行；**D.10 整条移入归档**（加本条后主文件越过 50 KB，
  按 §8.1 搬 ✅ 区最旧的整条，搬运由脚本对条目边界完成、原文取自 `git HEAD`、正文一字未改，
  移出后 42,953 B、加完本条 **49,058 B**），主文件里 3 处 `见 D.10` 一并改写成 `见归档 D.10`。
- **待用户手工验收**（本批验收主体）：① 外壳——顶栏滚动时吸附、当前页品牌色高亮、宽屏内容到
  1280px、页标题比正文明显大；② 一致性——任挑两页并排看，卡片内边距/圆角/阴影/表头应是同一套
  （`/strategies`、`/users` 的表格应与 `/runs` 一样）；③ 反馈——八处动作成功与失败**都**要弹
  （故意制造失败：填一个已存在的登录名、断网后点下载），成功约 2 s 自己消失、失败点得住 ×；
  ④ 确认弹窗三处——**ESC 能关**（新增能力）、点遮罩能关、回车**不会**误删；
  ⑤ 动效与焦点——**只用键盘 Tab 走一遍全站**，每个可点元素都该看得见焦点环，
  `prefers-reduced-motion` 打开后过渡基本消失；⑥ 骨架屏——四个列表/详情页首屏是灰条，
  **翻页与轮询刷新时不许出现**。**D.11 与 D.10 两笔欠账一并走**（前置与计划 §8.2 相同）。

---

## D.13 · 2026-09-26 （第十三批） 公开主页「薪火量化」+ 界面改名

> 归档于 2026-09-26（写 D.15 后主文件越过 50 KB 上限，按 Harness §8.1
> 把 ✅ 区最旧的整条移出）。**下列原文一字未改。**
>
> 这是**半关闭条目**：正文归档，仍生效的结论与**未走完的手工验收清单**留在主文件里。

- **起因（用户原话）**：「先要做一个主页出来吧，现在似乎只有登录页面。主页需要提供一些针对当前
  量化项目的介绍，另外，我这里量化平台的名称定的是：薪火量化」。**现状确实如此**：`/` 此前是一条
  **裸重定向**到 `/runs`，登录后也落在 `/runs`，于是全站**没有任何介绍面**——访客打开站点看到的是
  一张登录表单。产品名「量化回测平台」是写死的字面量，散在 5 处。
- **用户拍板三条**（2026-09-26）：① **公开主页**——`/` 不需要登录，访客看到介绍与「登录」入口，
  登录后同一页换成工作入口；② **一屏落地页**——主视觉 → 能做什么 → 一次回测的流程 → 技术底座 →
  收尾一个「当前边界」卡（第四节 2026-09-26 改为「引擎与运行环境」，见下面「用户看过主页后的订正」）；③ **改名范围 = 界面文案 + 页面标题**，仓库名 / 包名 / 目录名 / 文档标题
  一律不动（`docs/platform-plan.md` 的 H1 仍是旧的，已在 §1 注明"不动"）。
- **`/` 由 redirect 变 `home`，并把兼职的两件事拆开**：`meta.isPublic` 此前**兼着**「不需要登录」
  与「不挂外壳」两个意思（`AppLayout.vue` 靠它决定画不画顶栏），而主页要求这两件事解耦。新增一个
  **抑制型**字段 `meta.hidesHeader`，只写在 `/login` 与 404 两条路由上；`isPublic` 的原义一行不改。
  取抑制极性而不是 `showsTopBar` 这类正向标志：新加一条公开放行的路由时，漏写的后果是"多出一个
  顶栏"（肉眼可见），而不是静默地少掉导航。`main.ts` 的 401 处理器判据同样是 `isPublic`，**一行
  未动**，与守卫口径天然一致。
- **守卫补一条分支（必须补的一处）**：守卫第一句是 `if (to.meta.isPublic) return true`，于是
  "有令牌但还没取到用户"这个状态在公开页上永远不会被填充。症状是**登录后按 F5 停在 `/`，顶栏画不出
  显示名与徽章，主页还在问你要不要登录**（"半登录"态）。改成：判据取 `hidesHeader !== true`，
  即带顶栏的页面才补一次 `loadCurrentUser()`（`/login` 与 404 不挂外壳，没有消费者，不白付一次
  往返）；**补失败时不跳转**——令牌过期的访客从公开主页被弹到登录页会让"公开"名不副实，只清会话
  并停在原页；受保护页仍回登录页。**不放进视图的 `onMounted`**：那会把"什么时候需要身份"散进页面
  （下一个带顶栏的公开页一定漏），且首帧会先闪一下空名字 + `?` 头像。
- **`session.hasCurrentUser` 是"己方身份已就绪"的唯一出处**：`isAuthenticated` 只看令牌，
  而令牌在、用户没取回来时显示名与角色都画不出来。顶栏用户区与主页的两态入口**都只读它**，于是
  「`?` 头像 + 空名字 + 一个点了只会跳登录页的『退出登录』」这个错误态在结构上不可能出现——而首页
  一公开，它就会出现在第一屏。
- **`views/HomeView.vue`（新建）**：五节，正文窄栏 `max-w-5xl`（与 `RunSubmitView` 自限 `max-w-4xl`
  同一路数）。hero **自绘**不套 `PageHeader`（那是"工作页页头"原语：`h1` 是 `text-xl`、布局是
  「标题 + 右侧动作」，套五个会得到五处空位与五个"页头"语义）；`h1.text-3xl` 是全仓首个 ≥`text-2xl`
  的字号，**全页仍只有一个 `h1`**。能力卡 4 张、流程 5 步、引擎与运行环境 5 条、边界卡 5 条，全部
  `v-for` 遍历模块级 `readonly` 数组（`:key` 取 `title`/`term`，不用下标）。流程那张卡**无标题**：
  卡上挂了 `title` 就会多一层正文包裹 `div`，而 `<ol>` 的子元素只能是 `<li>`。
- **用户看过主页后的订正（2026-09-26，本批追加）**：第四节原为「技术底座」8 条，用户否掉了其中三行
  web 栈（前端/后端框架、引擎接入的进程与调度），原话「这里 web 的技术方案没什么可介绍的」——判断
  成立：这一页的读者是写策略的人，关心策略跑不跑得动、多快、产物是什么。已砍三行、标题改「引擎与运行
  环境」、留 5 条；数组改名 `foundationRows` → `engineEnvironmentRows`（名字跟职责走），「不介绍站点
  自己的实现」写进文件头措辞清单（两条 → 三条）。被砍的 `后端` 行里「跨用户 404」半句能力区已说过，
  删掉不丢信息。CSS 384,550 B 未变，JS 合计 1,053,155 → 1,052,817 B。
- **两处措辞上的自觉选择**：hero 写「**多用户**的量化回测平台」而**不写「云上」**——上云是部署
  节奏（本机跑通再迁 Windows 云主机），不是今天的能力（`PROGRESS.md` 项目定位里的「云上多用户」
  说的是**目标形态**，两者不冲突）；边界卡只说「尚未提供」，**不写 `/compare`、不写 P6/P8、
  不写「即将支持」**——那是边界卡，不是路线图。
- **改名清单（6 处，全部落地）**：`index.html` 的 `<title>`、`AppLayout` 的 Logo 汉字 `量`→`薪`
  与品牌字、品牌区 `RouterLink` 由 `runs` 改指 `home`、`LoginView` 的 `PageHeader title`、主页 hero、
  后端 `main.py` 的 `APPLICATION_DESCRIPTION`。**`APPLICATION_TITLE` 保留 `QuantPlatform`**：
  `description` 是 `/docs` 上唯一出现旧中文名的位置（留着它就是全仓唯一一处"两个中文名并存"），
  而 `title` 是 API 身份标识（`openapi.json` 的 `info.title`），与按拍板不动的仓库名同一层。
  **后端零 API / 零表结构改动**，只动这一句人类可读的字符串；`frontend/dist/` 里的旧标题是构建
  产物（`dist/` 在 `.gitignore` 里），**没有手改**，下次 build 自带新标题。
- **登录页补一条「← 回到主页」**：这是本批唯一的附加小改。登录页不挂顶栏、主页又公开了，没有这条
  链接，从主页翻过来的人只能靠浏览器后退。
- **三份新 spec**（`router/index.spec.ts` / `components/AppLayout.spec.ts` / `views/HomeView.spec.ts`，
  逐个首行 `// @vitest-environment jsdom`）：钉住 `resolve('/')` 是 `home` 且 `isPublic`、
  `resolve('/nope')` 仍是 `not-found`（通配没被静态段吃掉）、未登录访问 `/runs` 带 `redirect=/runs`、
  带令牌停在主页会补一次 `/auth/me`、补身份失败时**仍停在主页**且会话被清、访客态是「登录」按钮而
  **没有** `?` 头像与「退出登录」、管理员才有「用户管理」与角色徽章、主页两态入口互斥。
  **实测 20 文件 / 168 项全绿**（此前 17 / 146）；`npm run build` 通过，
  CSS 383,310 → **384,550 B**，JS 合计 1,045,022 → **1,053,155 B**（新增的 HomeView chunk 6,820 B），
  ECharts 那块 558,685 B 不变。
- **踩到一个极难认的测试陷阱（值得单独记）**：路由 `router` 是**模块级单例**，第一次 `mount` 把它
  装进某个 app 之后，**每一个**导航守卫都在那个 app 的注入上下文里跑（`app.runWithContext`），
  守卫里的 `useSessionStore()` 于是永远取到**第一个** app 的那个 pinia。于是"每个用例一个
  `createPinia()`"的常规写法在这些文件里必然失效：守卫读的是旧 pinia 里的旧 store（令牌早被清掉），
  用例读的是新 store，两边永远对不上。**症状极具迷惑性**：`push` 自身成功、地址也对，只是"令牌明明
  在，守卫就是不认"。处置：这两个挂载型 spec **整文件共用一个 pinia**，每个用例开头显式
  `clear()` 会话（那才是"重新打开一个页面"），理由写进两份 spec 的文件头。**不挂载**的
  `router/index.spec.ts` 不受影响，仍每用例一个新 pinia（它要测的正是"store 在创建那一刻读存储"）。
- **本批不做**：按路由改浏览器标签页标题（现状 `meta` 没有 `title` 字段，加它要动 8 条路由 + 一处
  watch，是一套跨文件新机制；日后正确落点是守卫里统一 `document.title = meta.title`）、页脚、
  深色模式、主题切换器、移动端专版、markdown 渲染与图片插画资产（都要新依赖或外部资产）、
  「主页」导航项（品牌区即入口，导航项一律表达"你在哪个工作面"）、路线图式文案。
- **文档回写**：`docs/platform-plan.md` §1 补产品名一行、§10 的 `/` 行**只追加不改写历史**并紧跟
  一段日期化订正、§13 追加一张拍板表；本文件 项目定位 补产品名一行、归档索引 补 D.11 一行、
  6 处 `见 D.11` 改写成 `见归档 D.11`（纯叙述里的 ID 提及保留）。
- **顺手记下三条"实物与文档不一致"**（只记不改，见 备注）：① Harness §8.1 说归档"按 ID 分段落
  **倒序**"，实物是**升序**（`D.01 → D.11 → Q.01`）；② §8.1 说 ✅ 区保留"最近 3–5 批"，实物在滚完
  D.11 后是「`D.13` + `D.12` 两份整条 + 4 份指向归档的指针条目」，50 KB 上限是更硬的约束；
  ③ 全仓 UI 文案有两处**全角句号**（`RunDetailView.vue`、`StrategyDetailView.vue`，都在
  `ElMessageBox` 的 message 里），主页统一用半角 `.`，不引入第三种习惯。
- **待用户手工验收**（浏览器里走一遍，前置与 §8.2 相同）：① 清 localStorage 后访问 `/` —— 停在
  主页不被弹走、顶栏在、右上角是「登录」而**没有**「退出登录」与 `?` 头像；② 点顶栏「登录」登录后
  落在 `/runs`，顶栏有显示名与（管理员）角色徽章；③ 已登录后手敲 `/` —— hero 是「进入回测运行」+
  「新建回测」，顶栏显示名**不是空的**（这一条就是上面那个"半登录"陷阱的反面）；④ 浏览器标签页
  标题是「薪火量化」（`index.html` 的改动*需重启 dev server*）；⑤ 顶栏品牌区方块是「薪」、旁边
  「薪火量化」、点它回主页；⑥ `/login` 与 `/nope` **都没有顶栏**（与改前一致），登录页标题是
  「薪火量化」，404 的 CTA 仍是「回到回测运行」；⑦ 未登录访问 `/runs`（旧书签）→ 弹登录页带
  `?redirect=/runs`，登录后回 `/runs`；⑧ 未登录在主页点顶栏「回测运行」→ 同上；⑨ 已登录在主页点
  「退出登录」→ 跳登录页，浏览器**后退**回 `/` 时是未登录态（不残留「进入回测运行」）；
  ⑩ 主页通读一遍——没有未实现功能的描述，边界卡五条与现状逐条对得上；⑪ 键盘 Tab 走一遍主页，
  每个可点元素都有品牌色焦点环；⑫ 后端 `/docs` 描述是「薪火量化: 包裹 QuantTrading 引擎…」，
  标题仍是 `QuantPlatform`。**D.12、D.11、D.10 三笔欠账一并走**。

---

## D.14 · 2026-09-26 （第十四批） 修「切换登录状态后空白页, 要按 F5」

> 归档于 2026-09-27（写 D.18 前主文件余量不足一条条目，按 Harness §8.1 把 ✅ 区最旧的整条移出）。**下列原文一字未改。**


- **起因（用户原话）**：「现在怎么经常切换登录状态后页面不显示出来，要按F5才显示？」——症状是
  **切一次页面之后内容区永久空白, 刷新才回来, 控制台一声不吭**。
- **根因**: `App.vue` 的路由出口是 `<Transition name="page" mode="out-in">`, 而 `mode="out-in"`
  要求孩子是**单个元素**。被路由的组件若渲染成**片段**, 过渡的状态机就再也配不上: 离场做完之后
  进场不再触发, 内容区只剩一个注释节点 `<!---->`。**首屏不走过渡, 故首屏正常**——这正是
  「打开就行、切一下就白」的由来。
- **为什么只在 dev 出**: 模板根节点**前面写一行注释**就让它编译成「注释 + 元素」两兄弟; dev 保留
  注释 (`comments: true`) 于是根节点成片段, prod 剥掉注释于是根节点是元素。逐个编译 9 个路由视图
  后只有 `LoginView.vue` 与 `RunSubmitView.vue` 命中 (两处都是「模板第一行是一段说明性注释」)。
  用户的路径恰好必经登录页: 登录与退出登录都要**离开** `/login`。
- **三处修复**: ① `App.vue` 给 `<component :is>` 外面包一层 `<div :key="route.path">`——
  结构性防御, 任何路由组件的孩子一律变成单元素, 不必要求每个页面自己守规矩; ② `LoginView.vue`
  与 ③ `RunSubmitView.vue` 把那段前导注释**移进根元素内部**(根因处清理), 并注明「写在里面是有意
  的」, 免得下次有人顺手挪回外面。
- **假绿陷阱（值得记）**: `@vue/test-utils` **默认把 `<Transition>` 换成 `transition-stub`**, 于是
  断言跑的是「不会过渡的切换」。实测在**修复前**那份代码上, 带默认 stub 的用例**全过**; 改成
  `stubs: { transition: false }` 后第三个断言立刻失败。**凡测过渡, 这一条不能省**。
- **新增 `frontend/src/App.spec.ts`**（2 用例）钉住它。两条同样实测得来的要点: 断言取 `main` 的
  `text()` 而非 `wrapper.html()` (空白时 `html()` 恰好是 `<!---->` 这种**非空**字符串, 断言"非空"
  会假绿); `mode="out-in"` 的离场/进场各占一帧, 只 `await nextTick()` 会在「离场完、进场未开始」
  那一拍上断言, 故用 rAF 跑三轮把过渡显式排空。
- **CSS 账**: spec 里 `transition` 这个字眼被 Tailwind v4 扫进产物 (它扫"所有没被 .gitignore 的
  文件"), 主 CSS 384,550 → **385,126 B** 全是死代码。`style.css` 加一行
  `@source not "./**/*.spec.ts";` 收口, 字节回到 **384,550**。
- **实测**: `npm test` **21 文件 / 170 项**全绿; `vue-tsc -b` 退出码 0; `npm run build` 成功,
  CSS **384,550 B** / JS **1,052,834 B**。
- **大小账与 D.12 回滚**: 写完本条主文件越过 50 KB, 按 §8.1 把 ✅ 区最旧的整条 `D.12` 搬进
  `PROGRESS-archive.md` (脚本搬移, 原文取自 `git HEAD`, 归档侧 sha256 `57f3c286…` 校验通过)。
  D.12 是**半关闭条目**, 故主文件留一截**短版**: 仍生效的结论与未走完的清单都还在, 正文指向归档。
  主文件 **45,198 B**, 归档 **88,736 B**。
- **本批不动**: 路由表与守卫、`style.css` 既有动效规则、依赖清单; `docs/platform-plan.md` 的观感层
  一节没提这套机制 (grep `out-in|Transition` 零命中), 故文档未改。
- **待手工验收**: 见 [`docs/acceptance-checklist.md`](docs/acceptance-checklist.md) §2 / §4
  （登录、退出登录、提交一轮，三步都**不该需要 F5**）。

---

## D.15 · 2026-09-26 （第十五批） 观感层之二：把 Element Plus 的令牌桥补完

> 归档于 2026-09-27（写 D.18 后主文件越过 50 KB 上限，按 Harness §8.1 移出）。**下列原文一字未改。**


- **起因（用户原话）**：「总感觉外观差点意思，你有没有什么建议」。逐文件读下来，主因**不是配色**，
  而是**令牌桥只搭了一半**：`style.css` 的 `html:root` 此前只桥了五个色族 + `--el-border-color(-light)`
  + `--el-bg-color` + 圆角 + 字体；**文字阶 / 填充阶 / 表格 / 阴影**仍是 EP 出厂值。于是同一个卡片里
  同时出现"两套灰 + 两种线深 + 两种表头"——表格单元格是 EP 的中性灰 `#606266`，紧挨着的卡片标题是
  偏蓝的 slate-700 `#334155`；表格内线 `#ebeef5`，卡片描边 `#e2e8f0`；表头是"白底 + `#909399` 小字"，
  而卡片的表头是"有底色、有描边、加粗"。人眼说不出哪一条不对，只觉得"差意思"。
- **改了什么**（除最后两条外全在 `style.css`，逐条都能整体回退）：① 文字阶 5 档与填充阶 5 档逐档
  对齐 slate 刻度（`--el-text-color-regular` = slate-600、`--el-fill-color` = slate-100 等），于是
  "EP 控件里的字"与"手写的字"同值；② `--el-border-color-lighter` 指回 `--color-line`，表格内线由此
  与卡片描边同色（`--el-table-border-color` 出厂就指向它）；③ `--el-border-radius-small` 由 2px 提到
  4px，与 base 的 6px、方块标的 8px 成 4/6/8 一档；④ 四档 `--el-box-shadow*` 由"全向模糊 `0 0 12px`"
  换成向下投的海拔，色相取 slate-900 而不是纯黑；⑤ `SurfaceCard` **去掉 `shadow-sm`**——卡片只靠
  描边（描边 + 极浅阴影同时上会互相削弱），阴影留给浮层；⑥ 字号阶梯：工作页 `h1` 20 → 24px，主页
  小节 `h2` 18 → 20px；⑦ 表单 label 的两种写法（`text-sm text-slate-600` 与`+ medium + slate-700`）
  统一为后者，共 12 处（RunListView 3 / RunSubmitView 8 / ResultTablePanel 1）；`FilePicker.vue`
  那处是原生 file input 而不是 label，未动。
- **踩到的坑（值得记）**：EP 把 `--el-table-*` 声明在 **`.el-table`** 上、`--el-skeleton-*` 声明在
  **`.el-skeleton`** 上，而不是 `:root`。自定义属性**就近取胜**，写在 `html:root` 里的那六条会
  **静默失效**——这六个变量名在 EP 的 `:root` 块里 grep 得到，所以"看起来已经桥过去了"，实际没有
  （页面不报错，只是没生效）。故这六条单独放在 `html:root .el-table`（0,2,1）与 `html:root
  .el-skeleton` 两个块里，压过 EP 的（0,1,0）。实测产物里两处声明都在，我们的在后**且**特异性更高。
- **顺手避开的一处**：骨架屏的流光正好由 `--el-fill-color` 与 `--el-fill-color-darker` 两档出场，
  若让它们跟着新刻度走，流光强度会变成原来的三倍（四个列表页首屏都在动）——故那两条点名取紧邻的
  一格，与出厂强度相当。
- **两处按对比度定的值**：表头字色**点名**取 `regular`（slate-500 落在表头那层 `#f1f5f9` 上是
  4.34:1，差一点没过 AA 的 4.5:1；取 regular 是 6.9:1，而出厂的中性灰 `#909399` 只有 3.0:1）。
- **实测**：`npm test` **21 文件 / 170 项**全绿（含改过的 `SurfaceCard.spec.ts`——原断言 `shadow-sm`，
  现改为断言"有描边且**没有**阴影"）；`vue-tsc -b` 退出码 0；产物 CSS **385,651 B**（上一批 384,550，
  +1,101 全是新加的令牌声明，注释一条都没进产物）/ JS **1,052,969 B**（+135 = 12 处 label 类名变长）。
- **本批不动**：布局、间距、文案、路由、依赖清单、`docs/platform-plan.md`（那一节记的是 D.12 的机制，
  本批是纯令牌补全，不新增机制，故不回写）。
- **待手工验收**: 8 项已按页面重排，见 [`docs/acceptance-checklist.md`](docs/acceptance-checklist.md) §5 / §6 / §8（表格、灰阶、卡片、标题、label、阴影、骨架屏、焦点环）。这批全是观感改动，我无法自证，请以眼睛为准；不满意可整体回退（除 `SurfaceCard` 的 `shadow-sm` 与 12 处 label 外都是纯令牌）。

> 归档于 2026-09-27（写 D.19 后主文件越过 50 KB 上限，按 Harness §8.1 把 ✅ 区最旧的整条
> 移出）。**下列原文一字未改**（唯一改动是标题层级 `### ` → `## `）。D.16 是**已关闭**条目，
> 主文件只留一条指向本处的指针。

## D.16 · 2026-09-26 （第十六批） 主页第一屏的数字带 + 标签页图标

- **起因**: D.15 治的是"杂"（令牌桥只搭了一半），而用户说的感受是"**素 / 太空**"，两者不是一回事 ——
  D.15 交付时当面说明了这点，用户答「接着做吧」。故本批只做两件"对外第一眼"的事。
- **① 主页 hero 加一条实测数字带**（`HomeView.vue`）: 三个数全部是**已有实测值**，与同页「引擎与
  运行环境」一节里的两行**同源** —— `3.8 秒`（2010–2024 全段，58176 根 bar / 678 笔成交）、
  `17 张`（每轮落下的结果表）、`3.0 MB`（单轮产物体积）。此前第一屏只有三行字，没有任何视觉落点；
  数字带把"多快 / 拿到什么"从一段文字变成一眼扫得到的东西，且**不引入任何未实现的能力**（只搬已有
  事实，不改文案面）。分隔线画在整条带上而不逐列加边框：窄屏三列折成一列时，逐列画会折出一堆断头
  线。每条 `dt`/`dd` 外各自套一层 `div` 是合法 HTML5（`dl` 允许用 `div` 分组）。
- **② 标签页图标**（新建 `frontend/public/favicon.svg` + `index.html` 一行 `link rel="icon"`）: 此前
  `public/` 目录都不存在，标签页是浏览器默认图标。图标是**纯几何标记、不排汉字**：16px 下"薪"有
  17 画，缩到那一格只会糊成一团；且 SVG 图标由浏览器在**页面之外**渲染，不走页面的字体栈，没装中文
  字体的机器上会直接落成一个空方框。三根柱子中高两低 —— 既像一根火苗，又像一张柱状图。`#1d4ed8`
  在这里是**写死**的（页面外渲染，读不到 `color-brand` 令牌），是继后端 `/docs` 主题之后全仓第二处
  硬编码色值，换品牌色时要一起手改 —— 这条写进了 SVG 自己的注释里。
- **实测**: `npm test` **21 文件 / 170 项**全绿（新加的是静态标记，**没加测试** —— 为三个数字写断言
  等于把文案抄进测试，与 D.13 定的口径一致）；`vue-tsc -b` 退出码 0；`public/favicon.svg` 928 B，
  构建后原样出现在 `dist/` 根且 `dist/index.html` 里的 `href="/favicon.svg"` 未被改写；产物 CSS
  **386,583 B**（+932 = 新用到的几个工具类）/ JS **1,053,635 B**（+666 = 数字带的数据与模板）。
- **本批不做（都可随时补）**: 顶栏那个 28px 方块标仍是汉字「薪」，**与图标不是同一个标记** —— 要不要
  把它也换成这套几何标记，留给用户定（D.13 已记过"薪"在这个尺寸里笔画偏挤）；没做 `.ico` 兜底（老
  浏览器会去求一个不存在的 `/favicon.ico`，404 无害）；没加 `theme-color`（移动端地址栏配色，一行）。
- **待手工验收**: 见 [`docs/acceptance-checklist.md`](docs/acceptance-checklist.md) §3（数字带）
  与 §2 前两条（标签页图标、顶栏标记）。


> 归档于 2026-09-27（写 D.19 后主文件越过 50 KB 上限，按 Harness §8.1 把 ✅ 区最旧的整条
> 移出）。**下列原文一字未改**（唯一改动是标题层级 `### ` → `## `）。D.17 是**已关闭**条目，
> 主文件只留一条指向本处的指针。

## D.17 · 2026-09-26 （第十七批） 顶栏标记换成与标签页同一份几何标记 + 五批验收清单合并成一份

- **起因（用户原话）**：「这个汉字「薪」改了。D.12 / D.13 / D.14 / D.15 / D.16 这几个要怎么验收」——
  两件事：收掉 D.16 留的口子（顶栏 28px 方块标仍是汉字「薪」）；把散在五批里的验收清单合并成一份。
- **① 顶栏标记**（`AppLayout.vue`）: 方块里的汉字「薪」换成 `<img :src="brandMarkUrl">`，引的**就是**
  `public/favicon.svg` 那一份文件 —— 不在组件里复刻一段 SVG：两处几何数据早晚漂移，而"应用里的标"与
  "标签页上的标"本来就该是同一个。换成几何标记的原因还是 D.13 记过的那条：「薪」17 画，塞进 28px
  方块里笔画会挤成一团。圆角在 SVG 文件里已经画好，故不再叠 `rounded-md`；`alt=""` 是**有意**的
  —— 它纯装饰，紧挨着的「薪火量化」四个字才是这一项的可访问名。
- **踩到的坑（值得记）**: 第一次写成静态 `src="/favicon.svg"`，`npm test` 当场挂掉 **2 个文件**
  （`App.spec.ts` / `AppLayout.spec.ts`，各 0 用例，连一条都跑不到）。根因是 `@vitejs/plugin-vue` 在
  **没有 dev server** 时用 `assetUrlOptions = { includeAbsolute: true }`（`dist/index.mjs:216-219`），
  把静态属性改写成一句 `import '/favicon.svg'`；`public/` 的文件不参与打包，生产构建侥幸能过，
  **Vitest 却解析成 `file:///favicon.svg`，Windows 的 `fileURLToPath` 拒绝这个形式**。
  `npm run dev` 碰不到（base 为 `/` 时 `includeAbsolute` 是 false）—— 浏览器里正常，只有测试环境炸。
  改法是把地址放进 `brandMarkUrl` 常量走**指令绑定**（`transformAssetUrls` 只改静态属性）—— 全仓
  第一个从模板里引 `public/` 的资源，坑写在常量上方。
- **② 验收清单合并**（新建 `docs/acceptance-checklist.md`）: 五批共 **32 项**（并入 D.10 / D.11 的
  欠账后共 **37 条**复选）按**页面与操作顺序**重排，去掉批与批之间的重复（骨架屏、焦点环、卡片一致性
  被三批分别点到过），并入一直挂在
  D.12 名下的 D.10 / D.11 两笔欠账。核对时改正了两处会把人指错地方的旧措辞：建号失败走的是**就地**
  通道（`UserAdminView` 写 `createErrorMessage`）而不是 toast，能稳定造出 toast 失败的是**断网点
  下载**；「已存在的登录名」填的是 `/users` 的建号表单（全仓没有注册页）。
- **实测**: `npm test` **21 文件 / 170 项**全绿（此前那 2 个失败文件恢复正常）、`vue-tsc -b` 退出码 0、
  `npm run build` 成功；产物 CSS **386,583 B**（与上一批逐字节相同 —— 本批没动样式）/ JS
  **1,053,564 B**（上一批 1,053,635，−71 = 汉字与那串类名换成 `h-7 w-7`）；`dist/favicon.svg` 928 B
  仍在 `dist/` 根，`dist/index.html` 的 `href="/favicon.svg"` 未被改写。
- **本批不动**: 路由、守卫、样式表、依赖清单、后端。**没加测试** —— 新加的是静态标记，为它写断言
  等于把标记抄进测试（D.13 定的口径）；而"静态 `src` 会炸测试"这件事**本身就是响的**（改回去两份
  spec 立刻挂），不需要再加一条断言守着。
- **大小账**: 主文件 **49,791 B**（上限 50,000）—— 余量已不足一条条目，故写明「下一批写条目之前先按
  §8.1 滚 `D.14`」。**该提醒已兑现**：`D.18` 写前滚了 `D.14`，写完越限后又滚了 `D.15`（两次的实测数字
  见 D.18 的大小账）。✅ 区现余 3 份整条（`D.18` / `D.17` / `D.16`），正好是 §8.1 说的「最近 3–5 批」，
  主文件 47,402 B；下一批再写条目时滚动候选是 `D.16`。
- **待手工验收**: 五批清单已合并，见 [`docs/acceptance-checklist.md`](docs/acceptance-checklist.md)
  —— 本批新增的两处在 §2 前两条（顶栏标记与标签页图标是同一个）与 §3（数字带）。


> 归档于 2026-09-27（写 D.19 及其 R.01 / ❓ / 拍板表三处更新后主文件越过 50 KB 上限，按
> Harness §8.1 把 ✅ 区最旧的整条移出）。**下列原文一字未改**（唯一改动是标题层级
> `### ` → `## `）。D.18 是**已关闭**条目，主文件只留一条指向本处的指针。

## D.18 · 2026-09-27 （第十八批） 策略 manifest 的作者向说明 + 一份可直接用的示例

- **起因（用户原话）**：「你需要把我上传策略要提供的 json文件给一个demo，看看这个文件放哪里合适」。
  此前 manifest 只有**实现口径**（`platform-plan.md` §7.2 / `app/manifest.py`），没有作者向说明，
  也没有能直接拿去试的样例。
- **交付三件**: ① `docs/strategy-manifest.example.json`（可直接上传的完整 manifest）；②
  `docs/strategy-manifest.md`（字段表 / 两条坑 / 倒推办法 / 文件放哪）；③ `platform-plan.md` §7.2 加了
  一段指向它。
- **示例不是现编的**: 取自真引擎验收（`test_real_engine_acceptance.py`）跑过的那一对 —— 引擎仓未做
  修改的 `bin/Release/grid_strategy.py` + 它自带的 `Configs/TestStrategyGrid.json`。校验走**真实代码
  路径**：`parse_strategy_manifest` 解析这份示例，`_build_strategy_configuration` 渲染出的配置与那份
  真实配置文件**键集合与取值完全相同（8 个键，不多不少）**，只有次序不同（JSON 对象无序，无影响）。
- **文档的两条坑**: ① 平台**不读**策略自带的配置文件，渲染结果 ≡「已映射的运行级字段 ∪ 已声明的
  参数」—— 漏声明一个键，策略以退出码 1 收场，且 `config_filename` 必须与策略里 `open` 的名字一字
  不差；② `bar_period` 不映射会**静默 0 成交**（两处周期不一致，不报错）。
- **放哪的结论**: 演示件留在 `docs/`（文档的一部分，只读）；**每个策略自己的 manifest 与它的 `.py` 放
  一起**，因为平台按**版本**存 manifest，两者要一起改一起传。备选（挪到 `frontend/public/` 即可做上传
  页的「下载示例」链接，代价是进前端产物）**本批未做**，理由写在文档里。
- **顺手订正**: `job-workspace.md` 的「见 `platform-plan.md` §6.1」是**错引用**（`§6.x` 是它自己的
  小节号，platform-plan 没有 §6.1，manifest 在 §7.2）。已改为 §7.2 并附新文档链接。
- **实测**: `npm test` **21 文件 / 170 项**全绿、`vue-tsc -b` 退出码 0（本批没动源码）；示例的解析与
  渲染比对退出码 0，见上条。
- **大小账**: 写本条前 47,360 B。写完一度到 **50,141 B，破了 50 KB 上限**，压两轮仍超 141 B，
  于是按 §8.1 再把 ✅ 区最旧的整条 `D.15` 移入归档（同一套脚本：原文比对 HEAD、正文 sha256
  `06cfbfaaa2dcd371` 两侧一致、只移动不删改；移出 4,056 B，短版 1,434 B）→ **47,402 B**。
  `D.14` 是本条写之前滚的（sha256 `1e995a4d03d5a3aa`），两次都留了短版指向本清单。
  教训记在这里：**一条 4 KB 上下的条目，落在 47 KB 的主文件里就是越界** —— 写之前先按条目预估
  体量决定要滚几条，而不是写完再挤。
- **本批不做**: 不加「下载示例」入口（要动上传页）；示例里**没有** `options` / `boolean` 的完整写法
  —— 字段表里有片段，配进示例则会让真实策略收到用不到的键。


---

## D.19 · 2026-09-27 （第十九批） 引擎版本可追溯：记下每一轮跑的是哪个构建
> 归档于 2026-09-27（写 D.20 后主文件越过 50 KB 上限时移出；标题层级 `### ` → `## `，其余原文一字未改）。
> **下列原文一字未改**。本条里唯一仍未了结的部分（引擎版本一行的手工验收）已改写为短版留在
> `PROGRESS.md` 的 ✅ 区；引用其余细节前请在本文件 grep 核实。

- **起因（用户原话）**：「你现在使用的 QuantTrading 是我的另一个项目的，现在是怎么处理的？现在是
  把那个目录下的库和可执行程序拷贝到本项目下吗？需要考虑一下实际部署的时候应该怎么做。」第一问
  的答案是**否**：引擎**不进本仓、不复制**，`QUANT_ENGINE_ROOT` 按路径引用，`.pyd` 与三个 DLL 靠
  `PYTHONPATH` + `LOAD_WITH_ALTERED_SEARCH_PATH` 就地加载，job 目录里只有平台自己产的四个文件
  （引擎侧唯一被复制的只有 `Sessions.json`）。第二问暴露了一个真缺口：**平台没有"引擎版本"这个
  概念** —— `engine_probe` 只探"能不能跑"，`/api/health` 报的也是"能不能跑"，`Runs` 四十多列里
  没有一列记"跑的是哪一版"。于是升级引擎 = 原地覆盖 `bin/Release`，此后历史轮与新轮在库里**逐列
  相同**；而 `StrategyVersionId` 那条"供逐字复现"的承诺要三样（策略版本 / 参数配置 / 引擎与行情），
  缺了最后一栏就复现不出来。这与 D.06 同类：口径变了而库里没有线索，旧数字再也不能与新数字相减。
- **范围经用户拍板 = 只做可追溯**：不收引擎包、不做启用/停用/删除、不按轮选引擎。理由是引擎包
  70–120 MB 且**平台不知道它的依赖闭包**，收包就得校验完整性，校验不了就不该收；那件事归部署流程
  （clean 引擎包等上云前由用户给出）。拍板的另一半：新列靠**只补新增列的小迁移**落到现有的库上。
- **交付四处**: ① `services/engine_probe.read_engine_version()` 两层口径 —— `<引擎根>/engine-version.txt`
  首个非空行优先，取不到则退化为 `.pyd` + 三个运行时 DLL 的内容摘要 `sha256:<12 位>`，两者都取不到
  为**空串**（"不知道"，不另造哨兵值）；② `RunModel.engine_version`（`String(64)`，默认 `''`）在
  **提交时冻结**，与 `BacktestConfigJson` / `ParamsJson` 同形态；③ 详情接口 + `/api/health` 各暴露
  一处，**列表不带**（与 `hostname` 同形）；④ 前端详情页「引擎版本」一行。
- **两条不可混的口径**（已写进 `platform-plan.md` §5.1 与 `types.ts`）：`sha256:` 前缀是**有意**的，
  它是"读不出人写版本号"时的降级而**不是等价替代** —— 兜底摘要只覆盖那四个文件，引擎包里别的文件
  变了它不会变。故 `engine-version.txt` 由 QuantTrading 侧产出（本仓不生成），上云前随包给。
- **本批唯一的新机制：`catalog/migrations.py`**: `create_all` 对**已存在的表一列都不改**，于是"加一列
  模型"与库里那 **10 行真实运行记录**直接冲突 —— 补不上就只能重建库。新增的 `add_missing_columns()`
  只做一件事（比对 `Base.metadata` 与实际表结构，缺哪个补哪个），三条边界写进 docstring：DDL 从模型
  渲染（**类型不另写一份**，避免 §12.20 那种"两处真相"）；**只补新增列**，改类型/删列/加约束一律
  不做并记 warning 点名"需重建库"（NOT NULL 且默认值渲染不进 DDL 的列，如 `default=utc_now`，也走
  这条 —— 拒绝得静默的话症状会推迟到第一次写入）；幂等。**旧 10 行的 `EngineVersion` 只能是空串**：
  它们由哪一版引擎跑的无从得知，**不去猜**。
- **顺手发现（只记不改）**: `Runs.Hostname` 是**死列** —— 全仓没有任何代码路径写它（恒为 `''`），
  而详情页有一行「执行主机」按它渲染。它当初为"多机部署分辨哪台机跑的"留，而本平台**后端必须与
  引擎同机**、横向无余地，故今天没有语义。删一列属 Harness §3 的"删除既有导出符号"，**须另行确认**。
  已记入 `platform-plan.md` §12.24。
- **偏离计划一处（有意）**: 计划的"要改的文件"表里写了哈希复用 `strategy_store.compute_source_hash`，
  实际**没有**复用 —— 它所在的模块会牵进 SQLAlchemy + `catalog.models` + `config`，而 `engine_probe`
  是有意保持轻依赖的（调度侧要 import 它）；且两处形态不同（多文件累积 vs 单块字节摘要）。
- **实测**: 迁移先在 `catalog.db` 的**副本**上走一遍（补列日志出现、`EngineVersion` 到位、10 行一行
  不少且取值 `''`），确认无误后才落到真库（同一条日志、同样 10 行）；第二遍初始化未再补列。后端
  **480 项全过**（+24：`test_engine_probe.py` 13 / `test_catalog_migrations.py` 6 / `test_health.py` 3 /
  `test_run_submission.py` 1 / `test_run_queries.py` 1）；前端 `type-check` 无错 + **170 项全绿** +
  `build` 成功。**未做**: 不引迁移框架（Alembic 之类）、不写 `engine-version.txt`、不给引擎根加新的
  环境变量（版本文件名是平台侧约定常量）。
- **大小账**: 本条 5.4 KB，而开工时主文件已 48,652 B，故**一边写一边滚**，共移出三条整条
  `D.16` / `D.17` / `D.18`（原文与 `git HEAD` 逐字节一致，只改标题层级 `### ` → `## `；sha256
  `fa2ce99d9fab7a3b` / `29d2994102841fdc` / `496314d2e6616951`），归档索引各补一行、主文件各留
  一条指针。加上本条与 R.01 / ❓ / 拍板表三处更新后为 **49.3 KB 上下**。教训（D.18 已写过一次，
  这次又踩）：**写之前先按条目预估体量决定滚几条** —— 5.4 KB 的条目落进 48.6 KB 的主文件里，
  滚一条不够。**下一批的处境是新的**：✅ 区只剩 `D.19` 一份整条可滚，而 R.01 里已积了六批非分期
  项的复述（预填 / UI 库 / 观感层 / 引擎版本 …），那些是**活的**不能滚。故下次写条目之前，
  先考虑**把 R.01 里已完成的复述压缩成指针**，而不是继续滚 ✅ 区（50 KB 是更硬的约束，这条张力
  D.13 的归档说明里已记过一次）。

## D.20 · 2026-09-27 （第二十批） 引擎的 MySQL / MariaDB 适配器改为按配置运行时装载
> 归档于 2026-09-27（写 D.21 后主文件越过 50 KB 上限时移出；标题层级 `### ` → `## `，其余原文一字未改）。
> **下列原文一字未改**。本条里那条「四个 `.exe` 宿主进程终止」的订正已由 `D.21` 收口（改为写 ERROR 日志 + 交出空适配器）；发布包口径那条仍留在 `PROGRESS.md` 的 ❓ 段。引用其余细节前请在本文件 grep 核实。

- **跨三仓的一批**（DBAdapters / QuantTrading / 本仓），本仓只落一句文档。用户原话：「要么两个平台都能与
  数据库解耦，根据配置引用，要么都全绑定吧。行为要一致。」
- **改了什么**：引擎原先四个数据库适配器**在链接期全部解析**，于是 MySQL（14.8 MB）与
  MariaDB（11.3 MB）两系客户端库即便当前配置只用 SQLite 也照样进地址空间——Windows 侧上一批用
  `/DELAYLOAD` 绕过，**ELF 没有对应物**，两平台行为不一致。本批把机制下沉到 DBAdapters（四个
  wrapper 各加一个 `extern "C"` 工厂 + 一个**静态库**装载器 `DbAdapters::BackendLoaderStatic`），QuantTrading 侧收缩成
  「配置 `DbType` → 模块基名」的映射，两平台走同一套。**DuckDB / SQLite 仍直连不装载**（前者
  `MdReader.cpp` 每轮无条件用，后者是默认后端）。
- **本仓的那一句**：[`docs/job-workspace.md`](docs/job-workspace.md) §3.2 原文的结论（`PYTHONPATH` 单独一条
  即可满足 `.pyd` 同目录依赖）**仍然成立**，补的一句是：MySQL / MariaDB 已不在 `.pyd` 的导入表里，
  **引擎包可以不发运它们**；`SqliteWrapper` / `DuckdbWrapper` 仍是硬依赖。
- **实测（本仓承担的验收）**: `cd backend && python -m pytest -m real_engine` **4 passed** —— 不设 `PYTHONPATH`
  之外的任何环境变量。缺库路径两平台各测一次：模块移走后 `DbType="2"` 得到一条点名 `DbType` 的
  `RuntimeError`；验完后文件与 `DbType` 均已改回。
- **⚠️ 订正：「不是进程静默终止」只在 Python 宿主上成立**（pybind11 转译成 `RuntimeError`）。四个 `.exe` 宿主的
  `CreateDatabaseAdapter` 6 个调用点**无一有 `try`/`catch`**，异常逃出 `main` → `std::terminate`（Windows 退出码 127
  且日志为空；Linux exit 134 但 stderr 有 `terminate called after throwing ... what():`）。**既有的，与本批无关**
  （本批只改"对象怎么造"）；是否补捕获待用户决定，详见 QuantTrading 仓 `PROGRESS.md` 的 ❓ 同名条。
- **两平台一致已实证**：本机 WSL 首次建起 Linux 树（Spark / DBAdapters / QuantTrading 163 目标全绿），
  `readelf -d libBackTest.so` 的 NEEDED 只剩 `libSqliteWrapper.so` + `libDuckdbWrapper.so`（改前含两系共 26.2 MB），
  且 `./TestStrategyGrid` 整轮跑通（退出码 0、`Success:1`），`DbType="2"` 文案与 Windows 同形。
- **⚠️ 一个必须说清的副作用**：不再链接 = `$<TARGET_RUNTIME_DLLS>` 不再把
  `MysqlWrapper.dll` / `MariadbWrapper.dll` 及其客户端链拷进引擎的 `bin/Release`。
  **已存在的文件不会消失**，故现有工作树不受影响；只有**全新克隆后从头构建**才会缺，
  且缺了不影响任何默认路径（`DbType` 默认 `"1"` = SQLite）。发布包要含哪些适配器见 ❓。
- **机制的设计说明在引擎侧**：`DBAdapters/docs/backend-runtime-loading.md`（为什么需要它 / C 入口
  契约与"异常不跨 C ABI" / 两步查找次序 / 平台差异三处 / 已知边界）。初稿把这些写成了代码注释，
  经用户指出后**已按 Harness §4 收敛**为文档 + 代码只留平台与 API 的坑。

> 归档于 2026-09-28（第二十三批开工前压缩：✅ 区已无 50 KB 余量）。
> **下列原文一字未改**；仍在生效的结论与那一处新的失效形态见 `PROGRESS.md` 的 D.21 短版。

## D.21 · 2026-09-27 （第二十一批） 取不到数据库适配器时的收场口径订正：不是进程终止，而是 ERROR + 失败退出

- **跨两仓的一批**（DBAdapters / QuantTrading），本仓只落一句文档订正。承接 D.20 留下的「⚠️ 订正」，
  本批把 [`docs/job-workspace.md`](docs/job-workspace.md) §3.2 的「注意」段改写为实测口径。
- **订正内容**：原文说「四个 `.exe` 宿主**没有捕获点**，缺了是**进程终止**而非报错」——该结论只在
  D.20 当时的实现下成立（`MysqlWrapper` 构造函数 `throw`，异常一路逃出 `main` → `std::terminate`）。
  引擎侧把失败出口改成**写一条 ERROR 日志 + 交出空适配器**之后，由引擎既有的判空通路接管：
  `SimExchange::Init()` 返回失败，宿主以 `ExitCodeHostInitFailed`（实测 `1`）收场。**不是进程终止，
  也不是抛异常**，四个 `.exe` 宿主与 Python 宿主行为一致。
- **为什么这件事对平台有意义**：进程被 `abort` 打死时，**日志器那口后台缓冲连同 stdio 缓冲一起丢**
  （实测 `0xC0000409`、日志 0 字节），runner 拿到的是一个没有诊断的退出码；现在拿到的是退出码 `1`
  + 一条点名 `DbType`、列明全部合法取值与「缺省」的 ERROR，外加引擎自有的 `Create Db Failed.`。
  **runner 侧零改动**——它本来就只看退出码与 `result.json`，而 §4 的退出码表里 `1` = 宿主启动失败
  早已在册，这是那条口径第一次真正被走到。
- **同一批的另一处**：未识别的**非空** `DbType`（如 `"9"`、`"sqllite"`）不再**静默退化成 SQLite**，
  而是走上面同一条失败路径；空串仍按缺省取 SQLite。平台侧写入点恒写 `"1"`，是合法取值，**平台代码
  零改动**。
- **实测（本仓承担的验收）**: `cd backend && python -m pytest -m real_engine` → **`4 passed`**
  （默认后端这条最常见的路没被改坏）。引擎侧另有 `DbType="9"` 探针在四个 `.exe` 宿主上退出码 `1`
  且日志可读。
- **风险（§7）**: 无多线程/锁/内存管理改动；本仓**无代码改动**，只改一句文档。
- **补记（2026-09-28）：`DbType` 由字符串改为 `int`，本仓的写入点与断言随之改。** 本条上面那句
  「平台侧写入点恒写 `"1"`」**已被推翻**：Spark 早已有 `enum class DbTypeType : int32_t
  { DuckDb = 0, SqliteDb = 1, MysqlDb = 2, MariaDb = 3 }`（`Spark/Types.h`，模板生成、明写禁手改），
  用户裁定「以枚举值为准」，QuantTrading 的配置模型随之把 `DbType` 由 `string` 声明成 `int`、
  四份 `Configs/*.json` 的值改裸整数。本仓改动三处：① `app/scheduler/engine_config.py` 新增具名
  常量 `SQLITE_DATABASE_TYPE = 1`（原为裸字面量 `"1"`），注释写明「取值即 Spark `DbTypeType` 的
  枚举值」，`DATABASE_TYPE_FIELD_HINT` 与写入点照旧引用它；② `tests/test_run_scheduler.py` 的断言
  改为 `engine_configuration["DbType"] == SQLITE_DATABASE_TYPE`，不再写裸值；③
  [`docs/job-workspace.md`](docs/job-workspace.md) §3.2 那句「只有 `DbType` 取 `2` / `3` 时才需要」
  随之一致（原文的半角引号写法已不存在）。**本仓行为变更：无**——渲染出的取值恒为合法值。
  **实测**: `cd backend && python -m pytest tests/test_engine_config.py tests/test_run_scheduler.py -q`
  → **29 passed**；`python -m pytest -m real_engine --basetemp=_acc_tmp/dbtype-int -q` → **`4 passed`**，
  作业配置里 `"DbType": 1`、stdout `DbType:1`，指标与 `job-workspace.md` §6.3 基线**逐位相同**
  （成交 `34` / 委托 `629` / 余额 `999257.8562340003`）。**一处新的失效形态（平台须知）**：
  旧字符串写法 `"1"` 现在**装不上**——jsoncpp 的 `asInt()` 遇字符串值抛 `LogicError`，抛出点仍在
  宿主 `try` 之外，实测退出码 `0xC0000409`、**日志 0 字节**、无任何提示，比 `DbType = 9` 那条
  （退 `1` + 可读 ERROR）更难查。凡手工改过 `DbType` 的配置都要写成裸整数。
- **大小账（补记）**: 写本补记前主文件 **49,966 B**，故按本条自己留下的那条教训**先压缩 R.01 的
  十段分期复述**——原文 4,887 B（sha256 `8d08b423c3ec832b`）已整块搬入
  [`PROGRESS-archive.md`](PROGRESS-archive.md) 的「R.01 分期复述（2026-09-28 压缩前原文）」一节
  （用脚本按行搬移、**未手抄**），主文件只留一条指针 + 仍未了结的手工验收项，落到 **46,189 B**
  后才写本条；补记自身约 1.5 KB，收尾 **47.7 KB 上下**。
- **提交状态**：**未提交、未推送**。
- **大小账**: 主文件开工时 49,380 B，故先移出 `D.20` 整条（`sed` 按行搬移、**未手抄**，只改标题层级
  `### ` → `## `）。**下一批已无缓冲**：✅ 区只剩 `D.21` 一份整条可滚，按上一批留下的教训，
  **下次先压缩 R.01 里已完成的复述**。

## Q.01 · 策略 manifest 里 `params` 项的 schema 细节未定（2026-09-25）

> 归档于 2026-09-25（D.06 拆分时）。**已了结**：P3 开工前定案——四类型
> （`integer` / `number` / `string` / `boolean`）+ `options` + 范围 + 分组，
> 且**缺省即必填**（不设 `required`）。定案全文见
> `docs/platform-plan.md` §7.2 与 `app/manifest.py`。

- **策略 manifest 里 `params` 项的 schema 细节未定**（2026-09-25）：
  `entry_filename` / `config_filename` / `supported_match_modes` / `params`
  四个顶层键已定，但 `params` 每一项的类型枚举、校验规则、分组与联动
  （如"选了 Bar 才显示周期"）只按 `TestStrategyGrid.json` 的 8 个键拟了初稿，
  未与用户确认。**P2 上传核心已绕开这项开工**：参数项用 `extra="allow"` 原样
  保留未知键，定案后补类型化字段即可，已上传的 manifest 不必改写。
  但仍须在**参数越界校验落地前**定——那既是 `platform-plan.md` §11 的 P2 验收
  项，也是 P4 上传表单动态生成字段的依据。最迟 **P3 渲染策略配置前**定。

---

## Q.02 · `permission_type` 的判定语义未定（2026-09-25，D.05 引入）

> 归档于 2026-09-25（D.06 拆分时）。**已了结**：用户拍板——**不区分 `read` /
> `run`，授权即可跑；`public` 隐含可跑**。故提交回测的权限判定与可见性判定
> 逐字重合，既无 403 分支也无新的越权分支；`GrantPermission` 字段原样保留，
> 只把 docstring 改成"已拍板不判定"。见 `docs/platform-plan.md` §11/§13 与
> `backend/app/catalog/enums.py`。

- **`permission_type` 的判定语义未定**（2026-09-25，D.05 引入）：`read` / `run`
  两档此刻只是**落库的一个取值，没有任何一处读它**——`build_visible_strategy_query`
  只认可见性，不看授权粒度，故 D.04 记的"可跑待 P3"不是没测，是**还没有判据**。
  P3 的 `POST /api/runs` 开工前要定两件事：① 无 `run` 授权者提交回测，回 403
  还是 404——D.02 起越权一律 404 的理由是"不区分无权与不存在"，但授权粒度是
  **归属之外的另一个维度**，此时"策略存在"对调用方已不是秘密，沿用 404 是否
  还站得住得明说；② `public` 是否**隐含可跑**——若隐含，等于把"公开"从"可看"
  扩到"可跑"，是一次权限放宽，须用户拍板。

---

## Q.03 · 归属人从哪儿得知同事的 `user_id`（2026-09-25，D.04 引入）

> 归档于 2026-09-25（P4 前置决策时）。**已拍板**：开放**受限用户目录**——
> 只回 `id` 与 `display_name`，供授权表单选人。**仍未决的部分留在
> `PROGRESS.md`**（该端点自己的泄漏面评审），未整条关闭。

- **归属人从哪儿得知同事的 `user_id`**（2026-09-25，D.04 引入）：授权端点按
  `user_id` 指定被授权人——按用户名会构成一个**用户名存在性探针**，与登录接口
  专门付代价防的那件事相冲（`auth.py` 的 `DUMMY_PASSWORD_HASH`）。代价是平台
  **没有给普通用户看的用户目录**（`GET /api/users` 是管理员专属，理由正是
  "用户清单本身即租户列表"），于是 P2b 写完了，人却无从在界面上操作它。
  **P4 的共享表单开工前必须定**。候选：① 管理员在用户管理页代建授权；
  ② 开放受限目录（只回 `id` 与 `display_name`）；③ 归属人凭用户名提交。
  ②③ 都得先想清泄漏面。**本轮未擅自加任何用户目录。**

---

## Q.04 · P4 前端样式方案：Tailwind 还是纯 CSS（2026-09-25）

> 归档于 2026-09-25（P4 前置决策时）。**已拍板：Tailwind**（按
> `platform-plan.md` §11 原文）。参考项目 `defect_tools` 用纯 CSS +
> `variables.css`，故本平台与它在此**有意分叉**，不是照抄不一致。

- **P4 前端样式方案：Tailwind 还是纯 CSS**（2026-09-25）：`platform-plan.md`
  §11 的 P4 写的是「Vue 3 + TS + Vite + **Tailwind** + Pinia」，而参考项目
  `defect_tools` **没有 Tailwind**（纯 CSS + `variables.css`）。这是**技术选型
  变更**，不只是文档记错，故未擅改，P4 开工前须用户拍板。

---

## Q.05 · 受限用户目录的泄漏面尚未评审（2026-09-25，D.04 引入）

> 归档于 2026-09-25（D.07 拆分时）。**已了结**：端点随 P4 落地
> （`GET /api/users/directory`），边界就此定案。原文里的三问逐条落为：
> ① 单页还是可搜索 → **可搜索**（`query` 子串），但**不按可见性过滤**，
> 即列出全体启用用户；② `display_name` 是否可空 → **收紧为建号必填**
> （空白回 400「显示名不能为空白」），故"空则回退 `username`"那条泄漏路径
> 不存在；③ 要不要只列有共享关系的用户 → **否**，全量枚举的代价已由
> "只回 `id` 与 `display_name`"这一收口承担。泄漏面表、响应里刻意不出现的
> 字段（`username` / `user_type` / `status`）与已知缺口（显示名无唯一约束）
> 见 `PROGRESS.md` 的 D.07 与 `docs/platform-plan.md` §7.5。
> **下列原文一字未改。**

- **受限用户目录的泄漏面尚未评审**（2026-09-25，D.04 引入，半关闭）：选人问题
  **已拍板**——开放受限目录，只回 `id` 与 `display_name`（原文见归档 `Q.03`）。
  **仍未决的是那条端点自己的边界**：它把"用户清单即租户列表"这条既有理由
  （`GET /api/users` 是管理员专属正基于此）**放宽了一档**，放给全体登录用户。
  开工前要定：回单页还是可搜索、`display_name` 是否可空（现已可空，空则回退
  `username` 就是泄漏面）、要不要按可见性过滤（只列与自己有共享关系的用户）。
  **P4 的共享表单开工前必须定**——表单要靠它填被授权人。

---

## Q.06 · Tick 模式的三档撮合语义未定（2026-09-25，D.06 引入）

> 归档于 2026-09-25（D.07 拆分时，**半关闭**：未决部分仍留在
> `PROGRESS.md` 的 ❓ 区）。这里只收**已了结的那半**——P4 的「新建回测」
> 表单不显示 Tick 选项、提交侧继续一律 400。下列原文是原条目的"已定"
> 子项，一字未改（缩进保留）。定案出处见 D.06 与
> `docs/platform-plan.md` §12.13/§13。

  - **已定（2026-09-25 用户拍板）**：P4 的"新建回测"表单**不显示 Tick 选项**，
    提交侧继续一律 400（判据是常量 `SUBMITTABLE_MATCH_MODES`）。故 P4 表单
    按 Bar 单模式生成，不做模式联动。

---

## Q.07 · 前端组件测试的 DOM 环境未定（2026-09-26，D.10 引入）

> 归档于 2026-09-26（**本批结清**：装了 `jsdom` + `@vue/test-utils`，组件 spec 逐个在
> 文件顶部写 `// @vitest-environment jsdom`，那条 parked spec 已改写入库；见 D.11）。
> **下列原文一字未改** —— 当时的措辞保留，其中「未定」已由 D.11 结清。

- **前端组件测试的 DOM 环境未定**（2026-09-26 修提交按钮时暴露）：
  本仓前端只有纯函数 spec，`vitest` 跑在 `environment: node` 下，而 SFC 一律按 SSR 模式
  编译（只有 `ssrRender`、没有 `render`），故 `createRenderer` 那条零依赖的组件测试
  路子走不通；修提交按钮时写好的 `ParameterForm.spec.ts`（断言「敲进父状态的值
  就是敲进去的那个」）**因此没能入库**，暂存在
  `%TEMP%/quant-cdp/parked-specs-20260926/`。真跑组件测试要加 **`jsdom` +
  `@vue/test-utils`** 两个 devDependencies —— 按 Harness §2 须先经用户同意，故记此待定；
  不加就继续靠真实浏览器的 CDP 探针验收（本轮即如此）。

---

## Q.08 · 配置模板的存储位置未定（2026-09-25，P6 引入）

> 归档于 2026-09-28（**本批结清**：用户拍板放 **catalog 新增一张表**，即 `RunTemplates`，
> 不落策略目录；实施见 D.23 与 `platform-plan.md` §9 的「P6/P7 落地范围」、
> §13 的 P6/P7 拍板表）。**下列原文一字未改** —— 当时的措辞保留，
> 其中「待 P6 前定」已由 D.23 结清（当时"倾向前者"的初判与拍板一致）。

- **配置模板的存储位置未定**（2026-09-25）：P6 的「配置模板保存复用」既可进
  catalog（加一张表），也可落策略目录下。倾向前者（要按用户维度筛选），
  待 P6 前定。

---

## R.01 分期复述（2026-09-28 压缩前原文）

> 2026-09-28 写 D.21 补记时主文件再度逼近 50 KB，按 R.01 自己留下的那条教训"下一批先压缩 R.01
> 里已完成的复述"移出。**下列原文一字未改**（块 sha256 `8d08b423c3ec832b`，共 48 行），原出处是 `PROGRESS.md`
> 的 `R.01`。压缩后的 `R.01` 只留一条指针，并在指针里保留了本块中**仍未了结**的手工验收项。
> 引用其中任何细节前请在本文件 grep 核实原文。

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
- **提交页预填 ✅ 已完成**（见归档 D.10，非分期项，2026-09-26 用户提出）：
  选中策略即按「该用户在该策略下最近一次提交」填出策略参数与运行级字段，
  另给「重置为默认值」按钮。**它的手工验收（选策略 → 参数与字段被带出且提示条可见
  → 点重置回到默认且提示条消失 → 再选一次该策略仍带出 → 换一个没跑过的策略为纯默认、
  无提示条）与 P4/P5 那两条一样，须由用户在浏览器里走一遍**（前置与 §8.2 相同）。
- **UI 组件库 ✅ 已引入**（见归档 D.11，2026-09-26 用户提出）：Element Plus `^2.14.6`，
  地基 4 个共用件 + 两个最脏页（`/runs`、`/runs/new`）换库；主题五族色阶经
  `html:root` 的 `--el-*` 覆盖对齐到既有 `@theme` 令牌。**它的手工验收（`/runs` 四个下拉与
  筛选的 × 清空、九列对齐、分页、中键开新标签页；`/runs/new` 的预填整条流程；
  EP 按钮/标签是否呈品牌蓝 `#1d4ed8`；四个共用页面回归）与 P4/P5 那两条一样，
  须由用户在浏览器里走一遍**（清单见归档 D.11 末，前置与 §8.2 相同）。
  **本批改动操作方式一处**：筛选不再靠选中列表里的「全部」，改成 placeholder + × 清空。
- **观感层 ✅ 已完成**（见上 D.12，第十二批，2026-09-26）：8 个有界面的页面一次性统一到
  `PageHeader` + `SurfaceCard` + `PaginationToolbar` 那一套，6 处手写提示条 → `el-alert`、
  2 处手写表 → `el-table`、404 → `el-result`，并补上全站此前**一件都没有**的动效与焦点层，
  以及成功/失败**两条 toast 通道**（自绘 `ConfirmDialog.vue` 连同 3 处调用点一并删除）。
  **它的手工验收与 D.11、D.10 两笔欠账一并走**：外壳吸附与品牌色高亮、任挑两页并排看是否
  同一套、八处动作成败都弹、三处确认弹窗能 ESC 与点遮罩关且**回车不会误删**、只用键盘 Tab
  走一遍全站看得见焦点环、路由 150 ms 淡入、四个列表/详情页**首屏是骨架而翻页与轮询不闪**。
  清单见 D.12 末，**须由用户在浏览器里走一遍**（前置与计划 §8.2 相同）。
- **引擎版本可追溯 ✅ 已完成**（见上 D.19，非分期项，2026-09-27 用户提出）：每轮在**提交时**把
  引擎标识冻进 `Runs.EngineVersion`，详情页与 `/api/health` 各显一处；范围**只做可追溯**（不收包 /
  不启停 / 不按轮选）。连带引入了 `catalog/migrations.py`（只补新增列），**现有的库不必重建**。
  **手工验收**: 打开一个已完成的轮，详情页「概要」多出「引擎版本」一行。换版约定见
  `platform-plan.md` §5.1（**按版本留目录，不要原地覆盖**）。
