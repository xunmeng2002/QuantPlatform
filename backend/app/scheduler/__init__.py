"""scheduler: 把 `queued` 的运行执行起来的那一层.

分工: `engine_config` / `result` / `output` 是**纯函数**——"写路径必须相对"与"退出码消歧"两条
硬约束最便宜的可测点, 不必起进程也不必起应用; `workspace` 管作业目录的构造; `registry` 管在跑
的句柄; `runner` 是**唯一**写运行终态的地方; `recovery` 在启动时结清上一进程留下的残局;
`scheduler` 是常驻循环本身.

引擎 (../QuantTrading 的 C++ 侧) 一行都不改: 平台只负责把它的输入摆好、把它当一次性进程拉起、
按退出码与结果文件判定这一轮的结果.
"""
