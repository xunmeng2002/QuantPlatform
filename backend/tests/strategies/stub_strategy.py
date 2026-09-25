"""桩策略: 用真实进程与真实文件把平台侧的契约钉出来, 不碰 C++ 引擎.

它经**正常上传路径**落进版本目录、被复制进作业目录, 并在 `sys.executable` 下以裸文件名启动.
于是它跑到哪一步, 就证明平台做到了哪一步——桩不是"模拟引擎", 它是**被测进程**.

## 契约两条, 缺一条用例就变成自证

1. **必须回显它读到的配置**: 把 `BackTest.json` 里的 `RunId` / `MatchMode` / 起止交易日 /
   `DbHost` / `DumpPath` 写进 `result.json` 的对应键. 真引擎正是由 `MatchMode` 反推
   `MarketDataType` 的, 桩照做才谈得上等价; 否则"配置注入生效"这类断言只是把平台写下的值
   从盘上再读一遍, 证明不了策略那一侧收到了什么.
2. **必须自报它在跑**: `sys.argv[0]` 写进 `argv0.txt`, `(phase, pid, 时刻)` 追加进 `span.txt`.
   "什么时候在跑"只有被执行的进程自己知道, 测试从外部采样是猜——而采样偏差会让并发用例
   几乎恒真, 杀不掉任何变异. 每一行写完即关闭, 故进程被强杀时 `start` 行仍在.

## 策略配置文件名是**替换**进来的

平台把策略配置写在 manifest 声明的名字下, 而策略只能按硬编码的名字去读它——真策略
`grid_strategy.py` 同样把 `TestStrategyGrid.json` 写死在源码里. 故本文件的
`<<CONFIG_FILENAME>>` 由测试侧的 `build_stub_source` 替换成具体文件名后再上传.

## 行为与旋钮

行为 (`Behavior`) 只决定**结果文件与退出码**; 时序与输出量是另两个正交的旋钮:

| 计划里的名字 | 表达方式 |
| ---- | ---- |
| `success` | `Behavior=success` |
| `exit1` / `exit2` | `Behavior=exit1` / `exit2` |
| `exit3_reported` / `exit3_crash` | `Behavior=exit3_reported` / `exit3_crash` |
| `exit0_no_result` | `Behavior=exit0_no_result` |
| `mirror` | `Behavior=mirror` (全部镜像列各写一个互异哨兵) |
| `gbk_error_msg` | `Behavior=gbk_error_msg` (结果文件按 GBK 落, 不是合法 UTF-8) |
| `wrong_run_id` | `Behavior=wrong_run_id` (报一个不属于本轮的 RunId) |
| `sleep` | `Behavior=success` + `SleepSeconds>0` |
| `flood` | `Behavior=success` + `FloodBytes` / `StderrFloodBytes` |
| `space_name` | 与行为无关: 入口文件名含空格, 用 `success` |

`sleep` 与 `flood` 不另立行为名: 它们与 `success` 的差别只在时长与输出量, 各给一个旋钮就够了,
多两个名字等于同一件事有两种说法.
"""

import json
import os
import sys
import time

STRATEGY_CONFIGURATION_FILENAME = "<<CONFIG_FILENAME>>"

ENGINE_CONFIGURATION_FILENAME = "BackTest.json"
RESULT_FILENAME = "result.json"
ARGV0_FILENAME = "argv0.txt"
SPAN_FILENAME = "span.txt"

EXIT_CODE_SUCCESS = 0
EXIT_CODE_HOST_INIT_FAILED = 1
EXIT_CODE_RESULT_FILE_UNREADABLE = 2
EXIT_CODE_ENGINE_FAILED = 3

BEHAVIOR_SUCCESS = "success"
BEHAVIOR_EXIT_WITHOUT_HOST = "exit1"
BEHAVIOR_UNREADABLE_RESULT = "exit2"
BEHAVIOR_ENGINE_REPORTED_FAILURE = "exit3_reported"
BEHAVIOR_CRASHED_WITHOUT_RESULT = "exit3_crash"
BEHAVIOR_SUCCESS_WITHOUT_RESULT = "exit0_no_result"
BEHAVIOR_FULL_MIRROR = "mirror"
BEHAVIOR_GBK_ERROR_MESSAGE = "gbk_error_msg"
BEHAVIOR_FOREIGN_RUN_ID = "wrong_run_id"

KNOWN_BEHAVIORS = frozenset(
    {
        BEHAVIOR_SUCCESS,
        BEHAVIOR_EXIT_WITHOUT_HOST,
        BEHAVIOR_UNREADABLE_RESULT,
        BEHAVIOR_ENGINE_REPORTED_FAILURE,
        BEHAVIOR_CRASHED_WITHOUT_RESULT,
        BEHAVIOR_SUCCESS_WITHOUT_RESULT,
        BEHAVIOR_FULL_MIRROR,
        BEHAVIOR_GBK_ERROR_MESSAGE,
        BEHAVIOR_FOREIGN_RUN_ID,
    }
)

# 写出结果文件之后各行为该以什么码退出. 逐项写全而不是"默认成功、列几个例外": 新增行为时漏改
# 这里会静默地按成功收场, 而那种用例看起来一直是绿的.
EXIT_CODES_BY_BEHAVIOR = {
    BEHAVIOR_SUCCESS: EXIT_CODE_SUCCESS,
    BEHAVIOR_FOREIGN_RUN_ID: EXIT_CODE_SUCCESS,
    # mirror 报的是失败: 镜像用例要能验到 ErrorMsg 这一列的镜像, 而一行 `succeeded` 带着一句
    # 报错是自相矛盾的产物. 报失败则所有 27 列都落在同一个自洽的状态下.
    BEHAVIOR_FULL_MIRROR: EXIT_CODE_ENGINE_FAILED,
    BEHAVIOR_ENGINE_REPORTED_FAILURE: EXIT_CODE_ENGINE_FAILED,
    BEHAVIOR_GBK_ERROR_MESSAGE: EXIT_CODE_ENGINE_FAILED,
}

# 引擎的 `MatchMode` 取值 → 它报出的行情模式名. 桩只收录平台当前可提交的那一档: 收到别的值
# 说明平台的取值表与桩的假设不一致, 报一个显眼的未知档比默默写成 "Bar" 好归因.
MATCH_MODE_NAMES = {3: "Bar"}

UNKNOWN_MATCH_MODE_NAME = "Unknown"

# 退出码 3 且结果文件可解析, 才算"引擎报告失败"; 不可解析就是宿主崩溃. 桩用不同的 ErrorId
# 把两者分开, 于是"消歧靠文件不靠码"这条在断言里是可验的.
REPORTED_FAILURE_ERROR_ID = 4242
FOREIGN_RUN_ID = "run-id-the-platform-never-issued"

# 全部镜像列各一个互异哨兵. 取值类型必须与列的声明一致: 类型不符会被平台按"引擎没报"处理,
# 那样断言只会在"某列是 NULL"上失败, 看不出是类型写错了.
MIRROR_SENTINELS = {
    "AccountId": "stub-account-0001",
    "Available": 123456.789,
    # 逐位等于 P0 基线: 它钉住的是"金额列是 Float, 不是 Numeric"——定点数会把末位的 6 抹掉.
    "Balance": 998951.4506464996,
    "BarMarketDataCount": 2928,
    "BasicDataLoaded": False,
    "CommissionMissingCount": 84,
    "CommissionZeroRateKeyCount": 3,
    "DepthMarketDataCount": 17,
    "ErrorId": 0,
    "ErrorMsg": "stub-mirror-error-message",
    "HasCapital": True,
    "InstrumentCount": 5,
    "LastTradingDay": "20241231",
    "MdSubscribeCount": 7,
    "OrderCount": 654,
    "SchemaVersion": 1,
    "TotalCommission": 0.0,
    "TotalStampTax": 0.0,
    "TotalTransferFee": 0.0,
    "TradeCount": 84,
    "VolumeMultipleFallbackProductCount": 2,
}

# 留在结果文件里、但**不入库**的长尾数组: 详情页按需去读文件, 而不是把一整个数组塞进行宽.
MISSING_RATE_KEYS = ["SSE.510300", "SSE.510500"]

GBK_ERROR_MESSAGE = "引擎报告: 行情数据缺失"


def record_span(phase):
    """把这一刻追加进 span.txt, 写完即关闭.

    写一行关一次不是浪费: 进程被 `TerminateProcess` 强杀时没有收尾机会, 而"它到底有没有开始
    跑"只能靠已经落盘的那一行回答.
    """

    with open(SPAN_FILENAME, "a", encoding="utf-8") as span_file:
        span_file.write(f"{phase} {os.getpid()} {time.time():.6f}\n")


def read_json(filename, encoding="utf-8"):
    with open(filename, encoding=encoding) as source_file:
        return json.load(source_file)


def read_engine_configuration():
    return read_json(ENGINE_CONFIGURATION_FILENAME)


def read_strategy_configuration():
    return read_json(STRATEGY_CONFIGURATION_FILENAME)


def build_result(engine_configuration, behavior):
    """按引擎配置与行为算出要落盘的 result.json.

    回显的六个键取自引擎配置而不是策略配置: 它们本来就是引擎写进结果文件的东西, 平台据此判断
    `MatchMode` 是不是真的到了引擎那一侧.
    """

    result = {
        "RunId": engine_configuration["RunId"],
        "MatchMode": engine_configuration["MatchMode"],
        "MarketDataType": MATCH_MODE_NAMES.get(
            engine_configuration["MatchMode"], UNKNOWN_MATCH_MODE_NAME
        ),
        "StartTradingDay": engine_configuration["StartTradingDay"],
        "EndTradingDay": engine_configuration["EndTradingDay"],
        "DbPath": engine_configuration["DbHost"],
        "DumpPath": engine_configuration["DumpPath"],
        "MissingRateKeys": list(MISSING_RATE_KEYS),
        "Success": True,
    }

    result.update(MIRROR_SENTINELS)

    if behavior == BEHAVIOR_FULL_MIRROR:
        # 镜像用例要的是"每一列都原样落地", 故保留哨兵表里的 ErrorMsg 不去清它——清掉就等于
        # 少验一个字符串列的镜像.
        result["Success"] = False
        return result

    # 其余行为一律把 ErrorMsg 清空: 一行 `succeeded` 却带着一句报错, 是自相矛盾的产物, 前端
    # 无从判断该显示什么.
    result["ErrorMsg"] = ""

    if behavior == BEHAVIOR_ENGINE_REPORTED_FAILURE:
        result["Success"] = False
        result["ErrorId"] = REPORTED_FAILURE_ERROR_ID
        result["ErrorMsg"] = "桩: 引擎报告本轮回测失败"
    elif behavior == BEHAVIOR_GBK_ERROR_MESSAGE:
        result["Success"] = False
        result["ErrorId"] = REPORTED_FAILURE_ERROR_ID
        result["ErrorMsg"] = GBK_ERROR_MESSAGE
    elif behavior == BEHAVIOR_FOREIGN_RUN_ID:
        result["RunId"] = FOREIGN_RUN_ID

    return result


def write_result(result, encoding="utf-8"):
    with open(RESULT_FILENAME, "w", encoding=encoding) as result_file:
        json.dump(result, result_file, ensure_ascii=False)


def write_flood(flood_bytes, stderr_flood_bytes):
    """按旋钮往两条流里写字节.

    两条流都要能单独灌满: 管道死锁的暴露条件是"一条流写满而另一条不写", 只给一个总量旋钮的话
    两条流会同时被灌, 读泵串行也能跑完, 用例就废了.
    """

    if stderr_flood_bytes:
        sys.stderr.write("e" * stderr_flood_bytes)
        sys.stderr.flush()

    if flood_bytes:
        sys.stdout.write("o" * flood_bytes)
        sys.stdout.flush()


def main():
    strategy_configuration = read_strategy_configuration()
    engine_configuration = read_engine_configuration()

    behavior = strategy_configuration.get("Behavior", BEHAVIOR_SUCCESS)
    sleep_seconds = float(strategy_configuration.get("SleepSeconds", 0))
    exit_delay_seconds = float(strategy_configuration.get("ExitDelaySeconds", 0))

    if behavior not in KNOWN_BEHAVIORS:
        # 拼错的行为名必须立刻可见: 落到"默认成功"上会让那条用例一直绿着, 而它验的东西一件
        # 都没发生.
        raise ValueError(f"未知的桩行为: {behavior}")

    record_span("start")

    # 陈旧结果防护, 与真策略 `grid_strategy.py` 同款: 先删掉上一轮留下的结果文件, 于是"文件
    # 不存在"才等价于"本轮没走完收尾". 平台据此消歧取消与启动失败, 缺了这一步, 一个被取消的
    # 作业会带着上一轮的结果被判成成功.
    try:
        os.remove(RESULT_FILENAME)
    except FileNotFoundError:
        pass

    # argv[0] 是启动期致命的那个值: 引擎日志器按它拼日志路径, 平台则要求它是裸文件名. 只有
    # 被启动的进程自己写下的才算证据.
    with open(ARGV0_FILENAME, "w", encoding="utf-8") as argv0_file:
        argv0_file.write(sys.argv[0])

    write_flood(
        strategy_configuration.get("FloodBytes", 0),
        strategy_configuration.get("StderrFloodBytes", 0),
    )

    if sleep_seconds:
        time.sleep(sleep_seconds)

    if behavior == BEHAVIOR_EXIT_WITHOUT_HOST:
        record_span("end")
        return EXIT_CODE_HOST_INIT_FAILED

    if behavior == BEHAVIOR_SUCCESS_WITHOUT_RESULT:
        record_span("end")
        return EXIT_CODE_SUCCESS

    if behavior == BEHAVIOR_UNREADABLE_RESULT:
        with open(RESULT_FILENAME, "w", encoding="utf-8") as result_file:
            result_file.write("{ 这不是一份能解析的结果")

        record_span("end")
        return EXIT_CODE_RESULT_FILE_UNREADABLE

    if behavior == BEHAVIOR_CRASHED_WITHOUT_RESULT:
        record_span("end")
        return EXIT_CODE_ENGINE_FAILED

    result = build_result(engine_configuration, behavior)

    if behavior == BEHAVIOR_GBK_ERROR_MESSAGE:
        # 中文 Windows 上引擎有 GBK 变体. 平台读结果文件必须容得下这种字节, 否则一个"引擎报告
        # 失败"会被读成"结果文件不可解析", 而结论正好相反.
        write_result(result, encoding="gbk")
    else:
        write_result(result)

    if exit_delay_seconds:
        time.sleep(exit_delay_seconds)

    record_span("end")

    return EXIT_CODES_BY_BEHAVIOR[behavior]


if __name__ == "__main__":
    sys.exit(main())
