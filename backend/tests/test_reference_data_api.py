"""基础数据的管理端点.

三张表对整个平台是同一份, 故全部端点限管理员 —— 它们决定每一轮回测怎么算钱. 本模块除了
CRUD 本身, 还盯着两件容易漏掉的事: 各种冲突给出的说法彼此分得开 (重名 / 重复键 / 组不存在 /
组还被引用着), 以及**费率行的作用域代码说得通** —— 空串是交易所级、已登记的短码是品种级、
长码是合约级, 而一个没登记过的短码要被当场拦下 (见 `_ensure_instrument_scope_is_known`).

写成功之后**不再有"顺带导出种子库"这一步**: 种子库按轮生成、落在各轮自己的作业目录里 (见
`reference_data.seed_database`), 管理端写完只提交事务.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.auth.dependencies import ADMIN_REQUIRED_DETAIL
from app.catalog.database import PlatformDatabase
from app.catalog.enums import CommissionDirection, RateDirection
from app.catalog.schemas import (
    MAXIMUM_INSTRUMENT_ID_LENGTH,
    MAXIMUM_PRODUCT_CODE_LENGTH,
    BaseCommissionResponse,
    CommissionGroupResponse,
    MessageResponse,
    PageResponse,
    ProductResponse,
)
from app.routers.reference_data import (
    BASE_COMMISSION_DELETED_MESSAGE,
    BASE_COMMISSION_DUPLICATED_MESSAGE,
    BASE_COMMISSION_NOT_FOUND_MESSAGE,
    COMMISSION_GROUP_ABSENT_MESSAGE,
    COMMISSION_GROUP_DELETED_MESSAGE,
    COMMISSION_GROUP_DUPLICATED_MESSAGE,
    PRODUCT_DELETED_MESSAGE,
    PRODUCT_DUPLICATED_MESSAGE,
    PRODUCT_NOT_FOUND_MESSAGE,
    UNREGISTERED_PRODUCT_CODE_MESSAGE_TEMPLATE,
)

from .conftest import TEST_ADMIN_PASSWORD, TEST_ADMIN_USERNAME
from .helpers import (
    bearer_headers,
    create_signed_in_account,
    fetch_page,
    login,
    record_ids,
)


REFERENCE_DATA_PATH = "/api/reference-data"
PRODUCTS_PATH = f"{REFERENCE_DATA_PATH}/products"
COMMISSION_GROUPS_PATH = f"{REFERENCE_DATA_PATH}/commission-groups"
COMMISSION_GROUP_OPTIONS_PATH = f"{REFERENCE_DATA_PATH}/commission-group-options"
BASE_COMMISSIONS_PATH = f"{REFERENCE_DATA_PATH}/base-commissions"

MEMBER_USERNAME = "reference-data-member"

# 初始化 CSV 播种进来的 A 股两行 (见 `backend/reference_seed/Product.csv`), 加上用例自己建的
# 品种, 列表的期望次序就是它们按 (交易所, 品种代码) 排出来的样子.
SEEDED_PRODUCT_KEYS = [("SSE", "600"), ("SZSE", "000")]

EXCHANGE_LEVEL_INSTRUMENT_ID = ""
REGISTERED_PRODUCT_ID = "600"
REGISTERED_PRODUCT_EXCHANGE_ID = "SSE"
UNREGISTERED_SHORT_PRODUCT_ID = "601"
EXCHANGE_ID_WITHOUT_THAT_PRODUCT = "SZSE"
# 比品种码阈值长一格: 守门据此认定"这是合约码", 一律放行.
SHORTEST_CONTRACT_INSTRUMENT_ID = "6" * (MAXIMUM_PRODUCT_CODE_LENGTH + 1)

OVER_LONG_EXCHANGE_ID = "S" * 9
OVER_LONG_PRODUCT_ID = "6" * 33
OVER_LONG_INSTRUMENT_ID = "6" * (MAXIMUM_INSTRUMENT_ID_LENGTH + 1)
UNKNOWN_COMMISSION_GROUP_ID = 99
NEGATIVE_RATE = -0.0001
DIRECTION_OUTSIDE_THE_ENGINE_VALUES = 2
BOTH_DIRECTIONS = int(RateDirection.BOTH)


@pytest_asyncio.fixture
async def admin_token(client: AsyncClient) -> str:
    return await login(client, TEST_ADMIN_USERNAME, TEST_ADMIN_PASSWORD)


@pytest_asyncio.fixture
async def member_token(
    database: PlatformDatabase, client: AsyncClient
) -> str:
    """一个普通账号的令牌, 用于验证非管理员的拒绝路径."""

    return (await create_signed_in_account(database, client, MEMBER_USERNAME)).token


def _product_payload(
    exchange_id: str = "SSE",
    product_id: str = "600519",
    product_name: str = "贵州茅台",
) -> dict[str, object]:
    """一份品种请求体, 只给自然键.

    其余七列交给 schema 的默认值 —— 它们同时也是"新建时不必逐列填"这条体验的断言对象.
    """

    return {
        "exchange_id": exchange_id,
        "product_id": product_id,
        "product_name": product_name,
    }


def _commission_group_payload(
    commission_group_id: int = 1, commission_group_name: str = "默认组"
) -> dict[str, object]:
    return {
        "commission_group_id": commission_group_id,
        "commission_group_name": commission_group_name,
    }


def _base_commission_payload(
    commission_group_id: int = 1,
    exchange_id: str = "SSE",
    instrument_id: str = "600519",
    direction: int = CommissionDirection.SELL.value,
) -> dict[str, object]:
    return {
        "commission_group_id": commission_group_id,
        "exchange_id": exchange_id,
        "instrument_id": instrument_id,
        "direction": direction,
        "close_stamp_tax_by_money": 0.0005,
        "min_commission": 5.0,
        "max_commission": 100.0,
    }


async def _create_product(
    client: AsyncClient, token: str, **payload: object
) -> ProductResponse:
    response = await client.post(
        PRODUCTS_PATH,
        json=_product_payload(**payload),
        headers=bearer_headers(token),
    )

    assert response.status_code == 201, response.text

    return ProductResponse.model_validate(response.json())


async def _create_commission_group(
    client: AsyncClient, token: str, **payload: object
) -> CommissionGroupResponse:
    response = await client.post(
        COMMISSION_GROUPS_PATH,
        json=_commission_group_payload(**payload),
        headers=bearer_headers(token),
    )

    assert response.status_code == 201, response.text

    return CommissionGroupResponse.model_validate(response.json())


async def _create_base_commission(
    client: AsyncClient, token: str, **payload: object
) -> BaseCommissionResponse:
    response = await client.post(
        BASE_COMMISSIONS_PATH,
        json=_base_commission_payload(**payload),
        headers=bearer_headers(token),
    )

    assert response.status_code == 201, response.text

    return BaseCommissionResponse.model_validate(response.json())


async def test_creating_a_product_returns_it_and_lists_it(
    client: AsyncClient, admin_token: str
) -> None:
    """建一个品种: 回 201 与本行, 且它真的进了列表."""

    created_product = await _create_product(client, admin_token)

    assert created_product.exchange_id == "SSE"
    assert created_product.product_id == "600519"
    assert created_product.product_class == 6
    assert created_product.volume_multiple == 1
    assert created_product.price_tick == 0.01

    page = await fetch_page(client, PRODUCTS_PATH, admin_token, ProductResponse)

    assert page.total == len(SEEDED_PRODUCT_KEYS) + 1


async def test_products_are_listed_by_exchange_then_code(
    client: AsyncClient, admin_token: str
) -> None:
    """列表按 (交易所, 品种代码) 排 —— 复合自然键, 故次序是稳定的、翻页不会跳行."""

    await _create_product(client, admin_token)

    page = await fetch_page(client, PRODUCTS_PATH, admin_token, ProductResponse)

    assert [
        (product.exchange_id, product.product_id) for product in page.records
    ] == [SEEDED_PRODUCT_KEYS[0], ("SSE", "600519"), SEEDED_PRODUCT_KEYS[1]]


async def test_the_same_product_code_cannot_be_entered_twice_for_one_exchange(
    client: AsyncClient, admin_token: str
) -> None:
    """同一交易所下的同一品种代码只允许一行: 建第二次回 409, 报的是"已存在"."""

    await _create_product(client, admin_token)

    response = await client.post(
        PRODUCTS_PATH,
        json=_product_payload(product_name="改个名字也还是同一个品种"),
        headers=bearer_headers(admin_token),
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == PRODUCT_DUPLICATED_MESSAGE


async def test_a_product_update_replaces_the_whole_row(
    client: AsyncClient, admin_token: str
) -> None:
    """改单是整行替换: 请求里没给的列回到 schema 默认值, 不是"只改给的这几列".

    这条行为是刻意的 (与提交、存模板共用的那套"建单改单同一份字段定义"一脉相承), 故值得钉住:
    某天有人把它改成"只改给的列"时, 前端那份"表单里有什么就发什么"的假设会一起失效.
    """

    created_product = await _create_product(client, admin_token)

    response = await client.patch(
        f"{PRODUCTS_PATH}/{created_product.id}",
        json={"exchange_id": "SSE", "product_id": "600519", "product_name": "贵州茅台"},
        headers=bearer_headers(admin_token),
    )

    assert response.status_code == 200, response.text

    updated_product = ProductResponse.model_validate(response.json())

    assert updated_product.product_name == "贵州茅台"
    assert updated_product.max_market_order_volume == 0
    assert updated_product.session_name == ""
    assert updated_product.volume_multiple == 1


async def test_deleting_a_product_twice_reports_it_as_absent_the_second_time(
    client: AsyncClient, admin_token: str
) -> None:
    """删两次: 第一次 200, 第二次 404 —— 第二次点的那个人要知道它已经不在了."""

    created_product = await _create_product(client, admin_token)

    first_response = await client.delete(
        f"{PRODUCTS_PATH}/{created_product.id}", headers=bearer_headers(admin_token)
    )

    assert first_response.status_code == 200, first_response.text
    assert (
        MessageResponse.model_validate(first_response.json()).message
        == PRODUCT_DELETED_MESSAGE
    )

    second_response = await client.delete(
        f"{PRODUCTS_PATH}/{created_product.id}", headers=bearer_headers(admin_token)
    )

    assert second_response.status_code == 404, second_response.text
    assert second_response.json()["detail"] == PRODUCT_NOT_FOUND_MESSAGE


async def test_a_rate_row_needs_its_commission_group_to_exist_first(
    client: AsyncClient, admin_token: str
) -> None:
    """费率行指向一个不存在的组时回 409, 且说的是"组不存在".

    这两句报错分得开是要紧的: 外键兜底那句是"这个组、交易所、合约与方向下已有一条费率", 对着
    一个根本没有的组说出口, 用户会去翻重名, 而该做的是先把组建出来.
    """

    response = await client.post(
        BASE_COMMISSIONS_PATH,
        json=_base_commission_payload(
            commission_group_id=UNKNOWN_COMMISSION_GROUP_ID
        ),
        headers=bearer_headers(admin_token),
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == COMMISSION_GROUP_ABSENT_MESSAGE


async def test_a_rate_row_is_unique_per_group_exchange_instrument_and_direction(
    client: AsyncClient, admin_token: str
) -> None:
    """同一组、交易所、合约与方向只能有一条费率; 换个方向就是另一条.

    方向在键里而不是"选哪一列": 印花税只在卖出侧收, 只写一行的话另一个方向整笔取不到费率.
    故这里必须验的是"方向不同能各存一条", 而不只是"重复被拒".
    """

    await _create_commission_group(client, admin_token)

    await _create_base_commission(
        client, admin_token, direction=CommissionDirection.BUY.value
    )

    repeated_response = await client.post(
        BASE_COMMISSIONS_PATH,
        json=_base_commission_payload(direction=CommissionDirection.BUY.value),
        headers=bearer_headers(admin_token),
    )

    assert repeated_response.status_code == 409, repeated_response.text
    assert (
        repeated_response.json()["detail"] == BASE_COMMISSION_DUPLICATED_MESSAGE
    )

    sell_row = await _create_base_commission(
        client, admin_token, direction=CommissionDirection.SELL.value
    )

    assert sell_row.direction == CommissionDirection.SELL.value


async def test_deleting_a_commission_group_that_is_still_referenced_is_rejected(
    client: AsyncClient, admin_token: str
) -> None:
    """删一个还挂着费率明细的组: 回 409, 且报出被几条引用着; 那些明细一条都不能少.

    不级联删是刻意的: 用户点的是"删这个组", 而级联顺手删掉的是别处录进来的费率数据.
    """

    commission_group = await _create_commission_group(client, admin_token)

    await _create_base_commission(client, admin_token)

    response = await client.delete(
        f"{COMMISSION_GROUPS_PATH}/{commission_group.id}",
        headers=bearer_headers(admin_token),
    )

    assert response.status_code == 409, response.text
    assert "1 条费率明细" in response.json()["detail"]

    rate_page = await fetch_page(
        client, BASE_COMMISSIONS_PATH, admin_token, BaseCommissionResponse
    )

    assert rate_page.total == 1

    group_page = await fetch_page(
        client, COMMISSION_GROUPS_PATH, admin_token, CommissionGroupResponse
    )

    assert record_ids(group_page) == [commission_group.id]


async def test_renumbering_a_referenced_commission_group_is_rejected(
    client: AsyncClient, admin_token: str
) -> None:
    """改一个还挂着费率明细的组的组号: 回 409, 且字面说的是"改组号"而不是"组号已存在".

    费率行存的是**组号**(不是组的主键), 而外键指向组的组号列且未声明 `ON UPDATE`, 于是改号会让
    那些费率行指向一个不存在的组, SQLite 直接把这次更新拒掉. 落下来的 `IntegrityError` 会被译
    成"该组号已存在" —— 而真相与重名无关, 用户按那句话去查重名只会白费功夫.
    """

    commission_group = await _create_commission_group(client, admin_token)

    rate_row = await _create_base_commission(client, admin_token)

    response = await client.patch(
        f"{COMMISSION_GROUPS_PATH}/{commission_group.id}",
        json=_commission_group_payload(commission_group_id=2),
        headers=bearer_headers(admin_token),
    )

    assert response.status_code == 409, response.text
    assert "改组号" in response.json()["detail"]
    assert "1 条费率明细" in response.json()["detail"]

    group_page = await fetch_page(
        client, COMMISSION_GROUPS_PATH, admin_token, CommissionGroupResponse
    )

    assert [
        (row.commission_group_id, row.commission_group_name) for row in group_page.records
    ] == [(1, "默认组")]

    rate_page = await fetch_page(
        client, BASE_COMMISSIONS_PATH, admin_token, BaseCommissionResponse
    )

    assert record_ids(rate_page) == [rate_row.id]


async def test_renumbering_an_unreferenced_commission_group_succeeds(
    client: AsyncClient, admin_token: str
) -> None:
    """没人引用的组改得动号, 新号就地生效.

    改号一个行数都没变, 故它只能看列表里那一行 —— 没有任何按行数比对的判据会对它起作用.
    """

    commission_group = await _create_commission_group(
        client, admin_token, commission_group_id=7, commission_group_name="自有费率"
    )

    response = await client.patch(
        f"{COMMISSION_GROUPS_PATH}/{commission_group.id}",
        json=_commission_group_payload(
            commission_group_id=8, commission_group_name="自有费率"
        ),
        headers=bearer_headers(admin_token),
    )

    assert response.status_code == 200, response.text
    assert (
        CommissionGroupResponse.model_validate(response.json()).commission_group_id == 8
    )

    group_page = await fetch_page(
        client, COMMISSION_GROUPS_PATH, admin_token, CommissionGroupResponse
    )

    assert [
        (row.commission_group_id, row.commission_group_name) for row in group_page.records
    ] == [(8, "自有费率")]


async def test_deleting_an_unreferenced_commission_group_succeeds(
    client: AsyncClient, admin_token: str
) -> None:
    """没人引用的组删得掉."""

    commission_group = await _create_commission_group(client, admin_token)

    response = await client.delete(
        f"{COMMISSION_GROUPS_PATH}/{commission_group.id}",
        headers=bearer_headers(admin_token),
    )

    assert response.status_code == 200, response.text
    assert (
        MessageResponse.model_validate(response.json()).message
        == COMMISSION_GROUP_DELETED_MESSAGE
    )

    group_page = await fetch_page(
        client, COMMISSION_GROUPS_PATH, admin_token, CommissionGroupResponse
    )

    assert group_page.total == 0


async def test_deleting_a_rate_row_succeeds(
    client: AsyncClient, admin_token: str
) -> None:
    """删一条费率明细: 回 200, 列表里也跟着少一条."""

    await _create_commission_group(client, admin_token)

    created_rate_row = await _create_base_commission(client, admin_token)

    response = await client.delete(
        f"{BASE_COMMISSIONS_PATH}/{created_rate_row.id}",
        headers=bearer_headers(admin_token),
    )

    assert response.status_code == 200, response.text
    assert (
        MessageResponse.model_validate(response.json()).message
        == BASE_COMMISSION_DELETED_MESSAGE
    )

    rate_page = await fetch_page(
        client, BASE_COMMISSIONS_PATH, admin_token, BaseCommissionResponse
    )

    assert rate_page.total == 0


async def test_deleting_a_rate_row_that_is_gone_reports_it_as_absent(
    client: AsyncClient, admin_token: str
) -> None:
    """删一条不存在的费率明细回 404, 而不是 200 —— 假装成功会让界面认为数据变了."""

    response = await client.delete(
        f"{BASE_COMMISSIONS_PATH}/no-such-rate-row", headers=bearer_headers(admin_token)
    )

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == BASE_COMMISSION_NOT_FOUND_MESSAGE


@pytest.mark.parametrize(
    "path",
    [PRODUCTS_PATH, COMMISSION_GROUPS_PATH, BASE_COMMISSIONS_PATH],
)
async def test_a_member_cannot_even_read_the_reference_data(
    client: AsyncClient, member_token: str, path: str
) -> None:
    """普通账号读都读不到这三个管理列表 —— 这三张表是整个平台的算钱依据, 不是租户数据.

    `/commission-group-options` **不在这条断言里**: 它是唯一那条只要登录的只读投影 (提交页要选
    组就得看得见有哪些组), 单独由下一条钉着. 把它加进这个参数表会让本条与那条互相打架.
    """

    response = await client.get(path, headers=bearer_headers(member_token))

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == ADMIN_REQUIRED_DETAIL


async def test_a_member_can_read_the_commission_group_options(
    client: AsyncClient, admin_token: str, member_token: str
) -> None:
    """普通账号取得回选组的选项, 且那条投影只有组号与组名.

    普通用户有权提交回测, 而提交时必须选一个手续费组 —— 把"有哪几个组"也锁在管理员后面, 等于让
    用户去问管理员. 故这一条与上面那条**并存**: 同一张表, 管理列表 403, 选项 200.

    只给两个字段: 主键与两个时间戳对界面毫无用处, 而多一个字段日后总会有人把它当作可以依赖的
    id 引用起来 —— 少给, 那种引用无从发生.
    """

    await _create_commission_group(client, admin_token)

    response = await client.get(
        COMMISSION_GROUP_OPTIONS_PATH, headers=bearer_headers(member_token)
    )

    assert response.status_code == 200, response.text

    option_page = PageResponse.model_validate(response.json())

    assert option_page.records == [
        {"commission_group_id": 1, "commission_group_name": "默认组"}
    ]


async def test_a_member_cannot_write_reference_data(
    client: AsyncClient, member_token: str
) -> None:
    """普通账号写自然也不行: 三张表各验一次建单 (改单、删单走的是同一条依赖)."""

    write_requests = [
        client.post(
            PRODUCTS_PATH,
            json=_product_payload(),
            headers=bearer_headers(member_token),
        ),
        client.post(
            COMMISSION_GROUPS_PATH,
            json=_commission_group_payload(),
            headers=bearer_headers(member_token),
        ),
        client.post(
            BASE_COMMISSIONS_PATH,
            json=_base_commission_payload(),
            headers=bearer_headers(member_token),
        ),
    ]

    for response in [await request for request in write_requests]:
        assert response.status_code == 403, response.text
        assert response.json()["detail"] == ADMIN_REQUIRED_DETAIL


async def test_an_over_long_product_code_is_rejected(
    client: AsyncClient, admin_token: str
) -> None:
    """字符串超出引擎 `char[n]` 的宽度时回 422, 不是写进库再在装载时被引擎截断.

    截断的后果不是"这行短了一点": 引擎按定长比较, 截断后的品种代码与成交里那个对不上, 于是
    这个品种静默地取不到乘数.
    """

    over_long_exchange_response = await client.post(
        PRODUCTS_PATH,
        json=_product_payload(exchange_id=OVER_LONG_EXCHANGE_ID),
        headers=bearer_headers(admin_token),
    )

    assert over_long_exchange_response.status_code == 422, over_long_exchange_response.text

    over_long_product_response = await client.post(
        PRODUCTS_PATH,
        json=_product_payload(product_id=OVER_LONG_PRODUCT_ID),
        headers=bearer_headers(admin_token),
    )

    assert over_long_product_response.status_code == 422, over_long_product_response.text


async def test_a_direction_outside_the_engine_values_is_rejected(
    client: AsyncClient, admin_token: str
) -> None:
    """方向只收买 (0) / 卖 (1) / 双向 (-1) 三档, 别的取值回 422.

    引擎拿成交方向去查行, 库里多存一个 2 只会是一条谁也查不到的记录.

    `-1` 曾经也在这条断言里 (那时它是"引擎取值之外"), 现已是有意收下的合法档 —— 双向, 见下一条.
    """

    response = await client.post(
        BASE_COMMISSIONS_PATH,
        json=_base_commission_payload(
            commission_group_id=UNKNOWN_COMMISSION_GROUP_ID,
            direction=DIRECTION_OUTSIDE_THE_ENGINE_VALUES,
        ),
        headers=bearer_headers(admin_token),
    )

    assert response.status_code == 422, response.text


async def test_the_both_directions_wildcard_is_a_rule_that_is_accepted(
    client: AsyncClient, admin_token: str
) -> None:
    """`-1` (双向) 收得下、读得回: 一条规则管买卖两边.

    它是**平台侧**的那一档 —— 写种子库时被摊成买、卖两行, 引擎只看得到 0 与 1
    (`reference_data.rate_expansion`). 故库里多一条 `-1` 与"多一条 2"是两件事: 后者永远查不到,
    前者展开之后两边都查得到.
    """

    await _create_commission_group(client, admin_token)

    created = await _create_base_commission(
        client, admin_token, direction=BOTH_DIRECTIONS
    )

    assert created.direction == BOTH_DIRECTIONS

    rate_page = await fetch_page(
        client, BASE_COMMISSIONS_PATH, admin_token, BaseCommissionResponse
    )

    assert [row.direction for row in rate_page.records] == [BOTH_DIRECTIONS]


async def test_a_negative_rate_is_rejected(
    client: AsyncClient, admin_token: str
) -> None:
    """费率不能为负: 负费率会让"手续费"反过来变成收益, 而那是核不出来的数."""

    await _create_commission_group(client, admin_token)

    response = await client.post(
        BASE_COMMISSIONS_PATH,
        json={
            **_base_commission_payload(),
            "open_by_money": NEGATIVE_RATE,
        },
        headers=bearer_headers(admin_token),
    )

    assert response.status_code == 422, response.text


async def test_an_empty_instrument_code_is_accepted_as_the_exchange_level(
    client: AsyncClient, admin_token: str
) -> None:
    """合约格留空是**交易所级**那一档, 不是漏填.

    空串在引擎那张表里从来查不到 (它拿成交合约去查), 它的唯一用途就是被平台展开时兜底 —— 故
    它在库里是一条合法记录, 而"以为录好了费率、实际一分钱没收"那种情况由提交期与调度期的展开
    检查拦住 (见 `services.run_submission`).
    """

    await _create_commission_group(client, admin_token)

    created_rate_row = await _create_base_commission(
        client, admin_token, instrument_id=EXCHANGE_LEVEL_INSTRUMENT_ID
    )

    assert created_rate_row.instrument_id == EXCHANGE_LEVEL_INSTRUMENT_ID
    assert created_rate_row.exchange_id == REGISTERED_PRODUCT_EXCHANGE_ID


async def test_a_blank_instrument_code_is_trimmed_to_the_exchange_level(
    client: AsyncClient, admin_token: str
) -> None:
    """带空白的"空"也当归成交易所级 —— 从表格里粘贴时很容易带上格式空白."""

    await _create_commission_group(client, admin_token)

    created_rate_row = await _create_base_commission(
        client, admin_token, instrument_id="   "
    )

    assert created_rate_row.instrument_id == EXCHANGE_LEVEL_INSTRUMENT_ID


async def test_an_unregistered_short_product_code_is_rejected(
    client: AsyncClient, admin_token: str
) -> None:
    """短码没在品种表里登记过时回 400, 文案给出两条出路.

    放行的话它会以"合约级"的身份躺在表里, 而展开时它既配不上任何合约、又遮不住任何品种级规则
    —— 回测照跑完, 费用静默按 0 算. 拦在这里, 用户当场知道要去建品种还是把合约码填全.
    """

    await _create_commission_group(client, admin_token)

    response = await client.post(
        BASE_COMMISSIONS_PATH,
        json=_base_commission_payload(
            instrument_id=UNREGISTERED_SHORT_PRODUCT_ID
        ),
        headers=bearer_headers(admin_token),
    )

    assert response.status_code == 400, response.text

    assert response.json()["detail"] == UNREGISTERED_PRODUCT_CODE_MESSAGE_TEMPLATE.format(
        exchange_id=REGISTERED_PRODUCT_EXCHANGE_ID,
        instrument_id=UNREGISTERED_SHORT_PRODUCT_ID,
        maximum_length=MAXIMUM_PRODUCT_CODE_LENGTH,
    )


async def test_a_product_code_registered_on_another_exchange_is_rejected(
    client: AsyncClient, admin_token: str
) -> None:
    """品种码是**按交易所**登记的: `SSE` 下建得出 `600`, 不代表 `SZSE` 下也有它."""

    await _create_commission_group(client, admin_token)

    response = await client.post(
        BASE_COMMISSIONS_PATH,
        json=_base_commission_payload(
            exchange_id=EXCHANGE_ID_WITHOUT_THAT_PRODUCT,
            instrument_id=REGISTERED_PRODUCT_ID,
        ),
        headers=bearer_headers(admin_token),
    )

    assert response.status_code == 400, response.text


async def test_a_registered_product_code_is_accepted_as_the_product_level(
    client: AsyncClient, admin_token: str
) -> None:
    """已登记的品种码 (这里是初始化 CSV 里的 `SSE / 600`) 走品种级, 放行."""

    await _create_commission_group(client, admin_token)

    created_rate_row = await _create_base_commission(
        client, admin_token, instrument_id=REGISTERED_PRODUCT_ID
    )

    assert created_rate_row.instrument_id == REGISTERED_PRODUCT_ID


async def test_a_code_longer_than_the_product_threshold_is_treated_as_a_contract(
    client: AsyncClient, admin_token: str
) -> None:
    """长于阈值的代码一律当合约码放行 —— 合约清单不在平台手里, 无从核实它是否存在.

    平台能核的只有品种表那一份, 故这条守门是**启发式**: 它拦得住"打错的短码", 拦不住"打错的
    长码". 把这条边界钉住, 是为了让阈值改动时有人被迫读这一段.
    """

    await _create_commission_group(client, admin_token)

    created_rate_row = await _create_base_commission(
        client, admin_token, instrument_id=SHORTEST_CONTRACT_INSTRUMENT_ID
    )

    assert created_rate_row.instrument_id == SHORTEST_CONTRACT_INSTRUMENT_ID


async def test_an_over_long_instrument_code_is_rejected(
    client: AsyncClient, admin_token: str
) -> None:
    """字符串超出引擎 `char[n]` 的宽度时回 422, 不是写进库再在装载时被引擎截断.

    截断的后果不是"这行短了一点": 引擎按定长比较, 截断后的合约代码与成交里那个对不上, 于是这行
    费率静默地取不到.
    """

    response = await client.post(
        BASE_COMMISSIONS_PATH,
        json=_base_commission_payload(instrument_id=OVER_LONG_INSTRUMENT_ID),
        headers=bearer_headers(admin_token),
    )

    assert response.status_code == 422, response.text
