"""reference_data: 回测引擎「基本数据」的保管与导出.

三张表 (`Product` / `CommissionGroup` / `BaseCommission`) 的**数据**住在 catalog 里, 由管理
页面增删改查 (见 `routers/reference_data.py`); 引擎读的那个 `BackTestInit.db` 是**派生物**,
每次保存后从 catalog 重新生成.

分工:

  - `seed_contract.py` —— 引擎侧的表形状 (表名 / 列名 / 列的 SQL 类型 / 主键), 平台唯一一份;
  - `seed_database.py` —— 按契约从 catalog 导出种子库文件, 以及读回它的状态;
  - `initial_rows.py` —— 随平台发布的三份初始化 CSV, 某张表为空时播种一次.

放在 app 下与 catalog / services 平级, 而不是塞进 catalog: catalog 是平台自己的库 (ORM 与
会话), 这里是"读 catalog、往外写另一个文件", 与 `services/result_database.py` 同类 ——
本层不依赖 FastAPI, 只抛领域异常.
"""
