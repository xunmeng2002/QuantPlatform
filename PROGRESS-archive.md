# QuantPlatform 进度归档

本文件是 [`PROGRESS.md`](PROGRESS.md) 的**归档层**：只收已关闭与已了结条目的
**原文**，按 ID 分段落倒序。条目 ID 取自拆分当日的文档顺序，此后永久稳定——
`D.*` 来自原 ✅ 已完成、`Q.*` 来自原 ❓ 待讨论、`R.*` 来自原 🔄 进行中。

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
