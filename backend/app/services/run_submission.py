"""运行提交: 校验 → 渲染两份配置 → 落一行 `queued`.

**权限判定就是可见性判定**, 一行不多: 用户拍板的是"授权即可跑, 不区分 read/run", 而用户能跑
的集合 (归属 ∪ public ∪ 共享且被授权) 与 `build_visible_strategy_query` 的可见集合**逐字相同**
——因为 private 下授权表本就不生效. 故这里调用 `load_visible_strategy`, 不新增越权分支也不新增
查询收口; 跨租户访问照旧一律 404, 不泄漏 id 是否存在.

**平台以策略上传的那份配置为底稿渲染**: 上传带的就是策略自己要读的 JSON, 它既是该版本的参数
模板, 也是渲染的起点 (见 `services/run_configuration.build_strategy_configuration`). 于是策略
读到的键集恒等于"模板的键集 + 平台那三个", 模板里平台控不了的键 (对象、数组、`null`) 也原样
透传, 不会有哪个键因为"平台不认识"而消失.

**提交只写库, 不碰文件系统**: 作业目录由调度侧构造, 因为一次提交要落盘四五个文件, 把这段 IO
塞进请求路径既拖长响应, 又把"磁盘满"变成"提交失败"——而用户拿着一个 201 却找不到工作目录,
比拿一个 500 更难归因.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..catalog.enums import MarketDataType, RunStatus
from ..catalog.models import RunModel, StrategyVersionModel, UserModel
from ..catalog.schemas import RunSubmitRequest
from ..catalog.visibility import load_visible_strategy
from ..config import PlatformSettings
from ..errors import InvalidRequestError
from ..ids import generate_identifier
from ..reference_data.rate_expansion import (
    RunContract,
    describe_missing_rates,
)
from ..reference_data.seed_database import expand_seed_rows, read_seed_rows
from ..scheduler.engine_config import (
    render_engine_config,
    serialize_configuration,
)
from ..strategy_configuration import parse_configuration_template
from .commission_group import ensure_commission_group_exists
from .engine_probe import read_engine_version
from .run_configuration import (
    EXCHANGE_ID_FIELD_NAME,
    INSTRUMENT_ID_FIELD_NAME,
    build_strategy_configuration,
    resolve_run_field_values,
    validate_commission_group_id,
    validate_initial_capital,
    validate_match_mode,
    validate_trading_day_range,
)


logger = logging.getLogger(__name__)

VERSION_NOT_FOUND_MESSAGE = "指定的策略版本不存在"
NO_VERSION_MESSAGE = "该策略还没有可运行的版本"
CONFIGURATION_UNREADABLE_MESSAGE = (
    "该策略版本的配置缺失或无法解析, 请重新上传该版本"
)
MARKET_DATA_MISSING_MESSAGE = "行情数据根目录不存在, 请先配置"
SESSION_FILE_MISSING_MESSAGE = "会话表文件不存在, 请先配置"
MISSING_RATE_MESSAGE_TEMPLATE = (
    "{commission_group_id} 号组下没有 {contract_rates} 的费率, 这一轮的费用会全按 0 算. "
    "请到「基础数据」页给这个合约 (或它所属的品种 / 交易所) 补一条费率再提交"
)


async def submit_run(
    session: AsyncSession,
    settings: PlatformSettings,
    current_user: UserModel,
    request_body: RunSubmitRequest,
) -> RunModel:
    """校验并落一行 `queued`; 调用方负责在这之后提交 `scheduler.wake()`.

    唤醒**必须**排在本次提交落库之后. 反过来的话调度器可能扫不到尚未提交的行, 一次提交就要等
    下一次唤醒——没有看门狗时就是**永不处理**; 有了看门狗也只是晚 5 秒, 但那 5 秒是白等的.
    """

    strategy = await load_visible_strategy(
        session, current_user, request_body.strategy_id
    )

    _ensure_engine_inputs_available(settings)

    version = await resolve_strategy_version(
        session, strategy.id, request_body.strategy_version_id
    )
    configuration_template = parse_version_configuration_template(version)

    match_mode = validate_match_mode(request_body.match_mode)

    run_field_values = resolve_run_field_values(request_body)

    commission_group_id = validate_commission_group_id(request_body.commission_group_id)
    # 这一问**不能**并进下面那次费率校验: 交易对留空时那条整个跳过 (那一轮不指名合约, 引擎也不会
    # 拿空串去查费率), 于是"组不存在"在留空的那些轮里就没人问了 —— 而组号照样会被冻进这一轮的引擎
    # 配置, 表现为引擎按一个空组计费.
    await ensure_commission_group_exists(session, commission_group_id)

    await _ensure_run_rates_available(session, commission_group_id, run_field_values)

    start_trading_day, end_trading_day = validate_trading_day_range(
        request_body.start_trading_day, request_body.end_trading_day
    )

    run_id = generate_identifier()

    engine_configuration_text = render_engine_config(
        run_id=run_id,
        match_mode=match_mode,
        start_trading_day=start_trading_day,
        end_trading_day=end_trading_day,
        initial_capital=validate_initial_capital(request_body.initial_capital),
        commission_group_id=commission_group_id,
        market_data_path=settings.market_data_root,
    )

    strategy_configuration_text = serialize_configuration(
        build_strategy_configuration(
            configuration_template, run_field_values, dict(request_body.params)
        )
    )

    run = RunModel(
        id=run_id,
        user_id=current_user.id,
        strategy_id=strategy.id,
        strategy_version_id=version.id,
        status=RunStatus.QUEUED.value,
        params_json=strategy_configuration_text,
        backtest_config_json=engine_configuration_text,
        # 与两份配置文本同一形态: 提交时冻结. 这一轮"将用哪个引擎跑"在落行那一刻就定了, 与
        # `StrategyVersionId` 一起凑齐"逐字复现"的两个前提. 已知限制: 入队后、起进程前若引擎
        # 被换掉, 记下的版本会与实际不符——而"作业在跑时换 .pyd"本就不受支持 (Windows 上已加载
        # 的扩展模块处于锁定状态, 覆盖会失败), 故这条窗口在实践中关着.
        engine_version=read_engine_version(settings.engine_root),
        # 工作目录名就是主键, 二者是同一件事: 引擎按 RunId 派生结果库名, 平台按它找结果文件,
        # 库里再存一份相对路径只是让"这一轮落在哪"不必靠约定去推.
        workspace_path=run_id,
    )
    session.add(run)

    await session.commit()

    return run


async def _ensure_run_rates_available(
    session: AsyncSession,
    commission_group_id: int,
    run_field_values: Mapping[str, str],
) -> None:
    """该轮那个合约的费率必须能在三级 (合约 / 品种 / 交易所) 之内命中.

    **这是把"静默按 0 计费"变成"提交被拦"的那一步**: 引擎查不到费率时不报错, 它照跑完, 只把缺口
    写进 `result.json` 的 `CommissionMissingCount` —— 用户手上那个回测的手续费是零, 而报告里没有
    一处说明为什么. 在这里拦下, 文案点名缺的是哪个合约的哪个方向, 以及该去哪儿补.

    查的是**这一轮选中的那个组**, 不是某个常量: 同一个合约在 2 号组有费率、在 1 号组没有, 是正常
    局面, 而"该用哪个组"只由这一轮的取解决定.

    用**同一份**读库与展开逻辑 (调度侧写种子库时用的是它们): 两边各写一遍的话, 迟早出现"提交
    过了、跑起来却缺费率"——那正是这条校验存在的理由.

    合约或交易所留空时整个跳过: 那一轮不指名合约, 引擎也不会拿空串去查费率 (见 `RunContract`).
    """

    exchange_id = run_field_values[EXCHANGE_ID_FIELD_NAME]
    instrument_id = run_field_values[INSTRUMENT_ID_FIELD_NAME]

    if not exchange_id or not instrument_id:
        return

    expansion = expand_seed_rows(
        await read_seed_rows(session, commission_group_id),
        commission_group_id,
        (RunContract(exchange_id=exchange_id, instrument_id=instrument_id),),
    )

    if expansion.missing_rates:
        raise InvalidRequestError(
            MISSING_RATE_MESSAGE_TEMPLATE.format(
                commission_group_id=commission_group_id,
                contract_rates=describe_missing_rates(expansion.missing_rates),
            )
        )


def _ensure_engine_inputs_available(settings: PlatformSettings) -> None:
    """引擎的两个只读输入必须先在位.

    拦在提交侧而不是留给调度侧: 这两项缺失是**确定性**失败, 收下它只会让用户等一轮再收到一个
    与他的输入无关的 `failed`. 文案点名缺的是哪一项但不带路径——路径是服务端的目录布局.
    """

    if not settings.market_data_root.is_dir():
        raise InvalidRequestError(MARKET_DATA_MISSING_MESSAGE)

    if not settings.session_file_path.is_file():
        raise InvalidRequestError(SESSION_FILE_MISSING_MESSAGE)


async def resolve_strategy_version(
    session: AsyncSession, strategy_id: str, requested_version_id: str | None
) -> StrategyVersionModel:
    """取要跑的版本: 指定了就用指定的 (必须属于该策略), 没指定就用最新的.

    公开给存模板路径: 模板不绑版本, 但它存的参数要按**最新版本**的配置模板校验, 故那条路
    也要"最新那一版是哪一版"这个判断. 两处各写一遍的话, 判据迟早不一致——比如一边算上了某个
    被撤回的版本.
    """

    if requested_version_id is not None:
        version = await session.scalar(
            select(StrategyVersionModel)
            .where(StrategyVersionModel.id == requested_version_id)
            .where(StrategyVersionModel.strategy_id == strategy_id)
        )
    else:
        version = await session.scalar(
            select(StrategyVersionModel)
            .where(StrategyVersionModel.strategy_id == strategy_id)
            .order_by(StrategyVersionModel.version_no.desc())
            .limit(1)
        )

    if version is None:
        raise InvalidRequestError(
            VERSION_NOT_FOUND_MESSAGE if requested_version_id else NO_VERSION_MESSAGE
        )

    return version


def parse_version_configuration_template(
    version: StrategyVersionModel,
) -> dict[str, object]:
    """取回该版本落库时的配置模板.

    两种解不动的情形**共用一条路径**, 但它们的原因不同, 一个 400 得同时罩住:

    - 那一列是 `None`: 这个版本是"源码 + manifest"那个旧形态留下来的 (见 `catalog/models.py`
      的 `StrategyVersionModel`)。用户已拍板老版本一刀切作废, 但不是"让它静默地按新规则去跑"
      ——旧 manifest 的键 (`entry_filename`、`params` 之类) 会原样变成一张荒唐的参数表单, 而
      策略收到的配置里根本没有它要的键. 明说"重新上传", 好过让它跑出一轮看不懂的失败.
    - 文本解不出对象: 上传时已校验过, 走到这说明库里的快照被谁改坏了.

    两种都译成 400 而不是放它变成 500: 对调用方来说可行的动作一样 (换一个版本或重新上传),
    而 500 会把它归因成"平台坏了, 稍后重试".
    """

    if version.configuration_json is None:
        logger.warning("策略版本是旧形态, 无配置模板: %s", version.id)
        raise InvalidRequestError(CONFIGURATION_UNREADABLE_MESSAGE)

    try:
        return parse_configuration_template(version.configuration_json)
    except ValueError as error:
        logger.error("策略版本的配置快照无法解析: %s: %s", version.id, error)
        raise InvalidRequestError(CONFIGURATION_UNREADABLE_MESSAGE) from error

