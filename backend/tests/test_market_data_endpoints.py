"""提交页要用的两个只读端点: 合约清单与覆盖预检.

组件不在位时两者都必须回 200 + `available=false`, **不是** 5xx: 提交页据此禁用下拉框并显示
原因, 而 5xx 会让页面把"另一个仓没装"显示成一个需要用户去猜的加载失败.

鉴权按全仓统一口径: 登录用户可读, 匿名不可读. 这两份清单里没有别人的东西, 但也没有理由比别的
端点更宽.
"""

from __future__ import annotations

from pathlib import Path

from httpx import AsyncClient

from app.main import MARKET_DATA_PREFIX
from app.services import quote_hub

from .helpers import SignedInAccount, bearer_headers
from .quote_hub_stub import DEFAULT_CONTRACT_CODE

CONTRACTS_PATH = f"{MARKET_DATA_PREFIX}/contracts"
COVERAGE_PATH = f"{MARKET_DATA_PREFIX}/coverage"

WINDOW_START_DAY = "20240102"
WINDOW_END_DAY = "20240105"
WINDOW_TRADING_DAY_COUNT = 4

#: 桩库只灌了 5 分钟线, 故这个周期下"一行都没有" —— 期望满、覆盖零, 是真实缺口那种不够.
UNCOVERED_FREQUENCY = "15"
UNSUPPORTED_BAR_PERIOD = "1d"

DEFAULT_EXCHANGE_ID = "SSE"
DEFAULT_INSTRUMENT_ID = "600519"


def _coverage_query(bar_period: str = "5m") -> dict[str, str]:
    return {
        "exchange_id": DEFAULT_EXCHANGE_ID,
        "instrument_id": DEFAULT_INSTRUMENT_ID,
        "bar_period": bar_period,
        "start_trading_day": WINDOW_START_DAY,
        "end_trading_day": WINDOW_END_DAY,
    }


async def test_the_contract_list_lists_tradable_contracts(
    client: AsyncClient, run_owner: SignedInAccount
) -> None:
    response = await client.get(CONTRACTS_PATH, headers=bearer_headers(run_owner.token))

    assert response.status_code == 200

    body = response.json()

    assert body["available"] is True
    assert body["reason"] == ""
    assert [contract["code"] for contract in body["contracts"]] == [DEFAULT_CONTRACT_CODE]
    assert body["contracts"][0]["exchange_id"] == "SSE"
    assert body["contracts"][0]["instrument_id"] == "600519"
    assert body["contracts"][0]["display_name"] == "贵州茅台"


async def test_the_contract_list_degrades_when_the_component_database_is_missing(
    client: AsyncClient, run_owner: SignedInAccount, quote_hub_root: Path
) -> None:
    """列表只读库, 不看入口脚本 —— 故缺的是**库**时它才降级."""

    (quote_hub_root / quote_hub.QUOTE_HUB_DATABASE_FILENAME).unlink()

    response = await client.get(CONTRACTS_PATH, headers=bearer_headers(run_owner.token))

    assert response.status_code == 200

    body = response.json()

    assert body["available"] is False
    assert body["reason"]
    assert body["contracts"] == []


async def test_the_contract_list_requires_authentication(client: AsyncClient) -> None:
    response = await client.get(CONTRACTS_PATH)

    assert response.status_code == 401


async def test_coverage_is_sufficient_for_the_covered_window(
    client: AsyncClient, run_owner: SignedInAccount
) -> None:
    response = await client.get(
        COVERAGE_PATH, params=_coverage_query(), headers=bearer_headers(run_owner.token)
    )

    assert response.status_code == 200

    body = response.json()

    assert body["available"] is True
    assert body["sufficient"] is True
    assert body["missing_day_count"] == 0


async def test_coverage_is_insufficient_when_only_another_frequency_is_ingested(
    client: AsyncClient, run_owner: SignedInAccount
) -> None:
    """提交页的预检必须与调度侧的判据**同一条**: 频率不同就是不够, 不能按合约放行.

    这一条是频率盲回归在 HTTP 层的镜像 —— 桩库里这只合约 5 分钟线齐全, 换成别的分钟周期就必须
    报不够, 否则提交页会说"够了"而调度侧会说"不够", 两个界面各说各话.
    """

    response = await client.get(
        COVERAGE_PATH,
        params=_coverage_query(bar_period=f"{UNCOVERED_FREQUENCY}m"),
        headers=bearer_headers(run_owner.token),
    )

    assert response.status_code == 200

    body = response.json()

    assert body["available"] is True
    assert body["sufficient"] is False
    assert body["expected_day_count"] == WINDOW_TRADING_DAY_COUNT
    assert body["missing_day_count"] == WINDOW_TRADING_DAY_COUNT


async def test_coverage_degrades_for_an_unsupported_bar_period(
    client: AsyncClient, run_owner: SignedInAccount
) -> None:
    response = await client.get(
        COVERAGE_PATH,
        params=_coverage_query(bar_period=UNSUPPORTED_BAR_PERIOD),
        headers=bearer_headers(run_owner.token),
    )

    assert response.status_code == 200

    body = response.json()

    assert body["available"] is False
    assert body["reason"]


async def test_coverage_requires_authentication(client: AsyncClient) -> None:
    response = await client.get(COVERAGE_PATH, params=_coverage_query())

    assert response.status_code == 401
