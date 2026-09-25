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
- **P1 后端骨架 + 多用户**（待开工）：五张表（`users` / `strategies` /
  `strategy_versions` / `strategy_grants` / `runs`）、登录与 JWT、
  **查询入口统一收口加 `user_id`**。
  验收：两个账号互相看不到对方的策略与运行；越权访问返回 **404 而非 403**。
- **P1 起须落实的机制**（P0 已定，勿再改动）：
  - 把策略入口**原样复制**到 `<job>/`，以 `cwd=<job>`、**裸文件名** `argv[0]` 启动。
  - `PYTHONPATH` 必须置为引擎根 `../QuantTrading/bin/Release`。
  - `BackTest.json` 的 `DbHost` / `DumpPath` 只写相对路径。
- **P2 策略上传**：上传 `.py` + manifest（表单或文件）→ 校验 → 版本留档 → 落盘。
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
  未与用户确认。等 P2 做上传表单前定。
- **系统级隔离的具体实现未定**（2026-09-25）：Windows 下的"每用户独立低权
  OS 账号"vs 每用户容器，两条路的实现与运维代价差别很大。**P8 上云前必须定**。
- **行情数据上云的同步方式未定**（2026-09-25）：现约 1.2 MB/年/板块，
  体量不大，但需要定"谁同步、多久一次、失败怎么办"。**P8 前定**。
- **配置模板的存储位置未定**（2026-09-25）：P6 的「配置模板保存复用」既可进
  catalog（加一张表），也可落策略目录下。倾向前者（要按用户维度筛选），
  待 P6 前定。

---

## 备注

- **环境锁定**：Windows + Python 3.11（`.pyd` 是 `cp311-win_amd64`，**Linux 无解**）
  + 后端与引擎同机；前端 Node 24.15。**"上云"等于一台 Windows 云主机，
  横向扩展无余地。**
- **`argv[0]` 必须是无分隔符的裸文件名**——这条是**启动期致命**的约束，
  不是日志美观问题。详见 D.01 与 `job-workspace.md` §3.1。
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
- **`.gitignore` 已加**：`runs/`、`backend/data/`、`node_modules/`、`.vs/`
  （2026-09-25）。多用户后还需加 `users/`。
- **AI 不推送**（按既有约定）：提交由 AI 做，推送由用户执行。
