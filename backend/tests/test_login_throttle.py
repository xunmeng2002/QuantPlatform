"""登录失败节流.

两个维度各自计数, 而 `ASGITransport` 把所有请求的对端都固定成 `127.0.0.1`, 不额外做点什么就
只有一个来源桶可分. 故钉**用户名**维度的用例一律带 `X-Forwarded-For` 且每次换一个地址 (让来源
桶每桶只记 1 次, 永不触发), 钉**来源地址**维度的用例反过来用固定地址配不同用户名——这样每条
用例只钉一个维度, 断言失败时能直接读出是哪个桶出的问题.

时间一律不真等: 需要"锁过期"的用例把 `app.auth.login_throttle.monotonic` 换成一个可以推的假
时钟. 注意要 patch **本模块里的那个名字** (`from time import monotonic` 把符号绑在了这里),
去 patch `time.monotonic` 不会生效.
"""

from __future__ import annotations

import inspect

import pytest
from httpx import AsyncClient, Response

from app.auth.dependencies import DISABLED_ACCOUNT_DETAIL
from app.auth.login_throttle import (
    MAXIMUM_TRACKED_KEYS_PER_DIMENSION,
    UNKNOWN_CLIENT_ADDRESS,
    LoginAttemptThrottle,
    parse_trusted_proxy_networks,
    resolve_client_address,
)
from app.catalog.enums import UserStatus
from app.config import PlatformSettings
from app.routers.auth import INVALID_CREDENTIALS_DETAIL, LOGIN_THROTTLED_DETAIL

from .conftest import TEST_ADMIN_PASSWORD, TEST_ADMIN_USERNAME
from .helpers import DEFAULT_MEMBER_PASSWORD, LOGIN_PATH, create_user_record
from .run_helpers import running_client, settings_with


TEST_PEER_ADDRESS = "127.0.0.1"

WRONG_PASSWORD = "definitely-not-the-password"

MEMBER_USERNAME = "throttled-member"

LOOPBACK_ONLY = ("127.0.0.1",)

# TEST-NET-3 与 TEST-NET-2: 专供文档与测试用的地址段, 不会是任何真实主机.
CLIENT_ADDRESS_POOL = [f"203.0.113.{index}" for index in range(1, 40)]
OTHER_CLIENT_ADDRESS_POOL = [f"198.51.100.{index}" for index in range(1, 20)]


def _settings_trusting_the_test_peer(settings: PlatformSettings) -> PlatformSettings:
    """让测试可以在对端地址之外伪造 `X-Forwarded-For`.

    `ASGITransport` 把对端固定成 `127.0.0.1`, 只有把它列进可信代理白名单, 那些头才会被采信.
    """

    return settings_with(settings, trusted_proxy_addresses=LOOPBACK_ONLY)


async def _attempt_login(
    client: AsyncClient,
    username: str,
    password: str,
    forwarded_for: str | None = None,
) -> Response:
    headers = {} if forwarded_for is None else {"X-Forwarded-For": forwarded_for}

    return await client.post(
        LOGIN_PATH, json={"username": username, "password": password}, headers=headers
    )


async def _fail_logins(
    client: AsyncClient,
    username: str,
    count: int,
    forwarded_for_pool: list[str] | None = None,
    password: str = WRONG_PASSWORD,
) -> list[Response]:
    responses = []

    for index in range(count):
        forwarded_for = None if forwarded_for_pool is None else forwarded_for_pool[index]
        responses.append(await _attempt_login(client, username, password, forwarded_for))

    return responses


class _FakeMonotonic:
    """可推进的假时钟. 起始值非 0, 避开 `LoginFailureRecord` 那三个 0.0 默认值的边界."""

    def __init__(self, start_seconds: float = 10_000.0) -> None:
        self._seconds = start_seconds

    def __call__(self) -> float:
        return self._seconds

    def advance(self, seconds: float) -> None:
        self._seconds += seconds


async def test_login_locks_after_the_configured_number_of_failures(
    platform_settings: PlatformSettings,
) -> None:
    """第 5 次仍放行, 第 6 次才 429.

    钉的是查锁发生在验口令**之前**: 第 5 次请求进来时计数还是 4, 故它被放行、并把自己记成第 5
    次失败; 锁定从下一次起生效.
    """

    async with running_client(
        _settings_trusting_the_test_peer(platform_settings)
    ) as (_, client):
        responses = await _fail_logins(client, MEMBER_USERNAME, 6, CLIENT_ADDRESS_POOL)

    assert [response.status_code for response in responses] == [401] * 5 + [429]

    for rejected in responses[:5]:
        assert rejected.json()["detail"] == INVALID_CREDENTIALS_DETAIL

    assert responses[5].json()["detail"] == LOGIN_THROTTLED_DETAIL
    assert 1 <= int(responses[5].headers["retry-after"]) <= 15 * 60


async def test_successful_login_restarts_the_username_count(
    platform_settings: PlatformSettings,
) -> None:
    """成功登录把**该用户名**的计数清零, 于是累计的失败次数从头算."""

    async with running_client(
        _settings_trusting_the_test_peer(platform_settings)
    ) as (application, client):
        await create_user_record(
            application.state.database, MEMBER_USERNAME, DEFAULT_MEMBER_PASSWORD
        )

        await _fail_logins(client, MEMBER_USERNAME, 4, CLIENT_ADDRESS_POOL)
        signed_in = await _attempt_login(
            client, MEMBER_USERNAME, DEFAULT_MEMBER_PASSWORD, CLIENT_ADDRESS_POOL[4]
        )
        responses = await _fail_logins(
            client,
            MEMBER_USERNAME,
            6,
            CLIENT_ADDRESS_POOL[len(CLIENT_ADDRESS_POOL) // 2 :],
        )

    assert signed_in.status_code == 200
    assert [response.status_code for response in responses] == [401] * 5 + [429]


async def test_client_address_bucket_locks_independently_of_usernames(
    platform_settings: PlatformSettings,
) -> None:
    """同一来源地址上换着用户名失败, 也能把该地址锁住."""

    recurring_address = OTHER_CLIENT_ADDRESS_POOL[0]

    async with running_client(
        _settings_trusting_the_test_peer(platform_settings)
    ) as (_, client):
        for index in range(5):
            response = await _attempt_login(
                client, f"absent-user-{index}", WRONG_PASSWORD, recurring_address
            )
            assert response.status_code == 401

        blocked = await _attempt_login(
            client, "brand-new-user", WRONG_PASSWORD, recurring_address
        )
        elsewhere = await _attempt_login(
            client, "brand-new-user", WRONG_PASSWORD, OTHER_CLIENT_ADDRESS_POOL[1]
        )

    assert blocked.status_code == 429
    assert blocked.json()["detail"] == LOGIN_THROTTLED_DETAIL
    # 同一个用户名换个地址就放行: 拦住它的是地址桶, 不是用户名桶.
    assert elsewhere.status_code == 401


async def test_username_bucket_locks_independently_of_client_addresses(
    platform_settings: PlatformSettings,
) -> None:
    """同一用户名上换着来源地址失败, 同样能把该用户名锁住."""

    async with running_client(
        _settings_trusting_the_test_peer(platform_settings)
    ) as (_, client):
        responses = await _fail_logins(
            client, TEST_ADMIN_USERNAME, 5, CLIENT_ADDRESS_POOL
        )
        correct_password_elsewhere = await _attempt_login(
            client,
            TEST_ADMIN_USERNAME,
            TEST_ADMIN_PASSWORD,
            CLIENT_ADDRESS_POOL[-1],
        )

    assert [response.status_code for response in responses] == [401] * 5
    assert correct_password_elsewhere.status_code == 429


async def test_locked_username_rejects_even_the_correct_password(
    platform_settings: PlatformSettings,
) -> None:
    """锁定期间拿对口令也拒绝——这是"锁定"的定义, 不是缺陷."""

    async with running_client(
        _settings_trusting_the_test_peer(platform_settings)
    ) as (_, client):
        await _fail_logins(client, TEST_ADMIN_USERNAME, 5, CLIENT_ADDRESS_POOL)
        locked_out = await _attempt_login(
            client, TEST_ADMIN_USERNAME, TEST_ADMIN_PASSWORD, CLIENT_ADDRESS_POOL[-1]
        )

    assert locked_out.status_code == 429
    assert locked_out.json()["detail"] == LOGIN_THROTTLED_DETAIL


async def test_successful_login_does_not_clear_the_client_address_bucket(
    platform_settings: PlatformSettings,
) -> None:
    """插一次自己的有效登录不能把地址计数清零, 否则攻击者随时可以把它抹掉."""

    recurring_address = OTHER_CLIENT_ADDRESS_POOL[2]

    async with running_client(
        _settings_trusting_the_test_peer(platform_settings)
    ) as (application, client):
        await create_user_record(
            application.state.database, MEMBER_USERNAME, DEFAULT_MEMBER_PASSWORD
        )

        for index in range(4):
            response = await _attempt_login(
                client, f"absent-user-{index}", WRONG_PASSWORD, recurring_address
            )
            assert response.status_code == 401

        signed_in = await _attempt_login(
            client, MEMBER_USERNAME, DEFAULT_MEMBER_PASSWORD, recurring_address
        )
        still_allowed = await _attempt_login(
            client, "another-absent-user", WRONG_PASSWORD, recurring_address
        )
        blocked = await _attempt_login(
            client, "yet-another-absent-user", WRONG_PASSWORD, recurring_address
        )

    assert signed_in.status_code == 200
    # 这次失败就是地址桶的第 5 次: 中间那次成功没有把它清零.
    assert still_allowed.status_code == 401
    assert blocked.status_code == 429


async def test_malformed_request_bodies_are_not_counted(client: AsyncClient) -> None:
    """校验失败到不了处理函数, 天然不入桶——否则构造畸形请求就能把别人锁掉."""

    incomplete_bodies = [
        {},
        {"username": TEST_ADMIN_USERNAME},
        {"password": TEST_ADMIN_PASSWORD},
        {"username": "", "password": TEST_ADMIN_PASSWORD},
        {"username": TEST_ADMIN_USERNAME, "password": ""},
        {"username": "x" * 100, "password": TEST_ADMIN_PASSWORD},
    ]

    for request_body in incomplete_bodies:
        response = await client.post(LOGIN_PATH, json=request_body)
        assert response.status_code == 422

    still_counting_from_zero = await _attempt_login(
        client, TEST_ADMIN_USERNAME, WRONG_PASSWORD
    )

    assert still_counting_from_zero.status_code == 401


async def test_disabled_account_attempts_are_not_counted(
    platform_settings: PlatformSettings,
) -> None:
    """停用账号那条 401 只在口令**已经正确**时才走得到, 记成凭据失败语义上就是错的.

    这里刻意不带来源地址头: 若实现把它们记了进去, 来源桶会在第 6 次把请求拦成 429, 这条用例
    当场变红.
    """

    async with running_client(platform_settings) as (application, client):
        await create_user_record(
            application.state.database,
            MEMBER_USERNAME,
            DEFAULT_MEMBER_PASSWORD,
            status=UserStatus.DISABLED,
        )

        responses = await _fail_logins(
            client, MEMBER_USERNAME, 6, password=DEFAULT_MEMBER_PASSWORD
        )

    assert [response.status_code for response in responses] == [401] * 6

    for response in responses:
        assert response.json()["detail"] == DISABLED_ACCOUNT_DETAIL


async def test_unknown_usernames_are_counted_like_existing_ones(
    platform_settings: PlatformSettings,
) -> None:
    """未知用户名也要计数.

    只对存在的账号计数的话, 攻击者对存在的用户名失败 5 次拿到 429、对不存在的永远 401——429
    本身就成了用户名枚举接口, 等于把登录那处"未知用户名也照常验口令"的防线重新打开.
    """

    async with running_client(
        _settings_trusting_the_test_peer(platform_settings)
    ) as (_, client):
        absent_responses = await _fail_logins(
            client, "never-created-account", 6, CLIENT_ADDRESS_POOL
        )
        existing_responses = await _fail_logins(
            client, TEST_ADMIN_USERNAME, 6, OTHER_CLIENT_ADDRESS_POOL
        )

    assert absent_responses[5].status_code == 429
    assert existing_responses[5].status_code == 429
    assert absent_responses[5].json() == existing_responses[5].json()
    assert absent_responses[5].json()["detail"] == LOGIN_THROTTLED_DETAIL


async def test_lock_expires_after_the_configured_window(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """锁定按秒到期, 且剩余秒数向上取整: 差 1 秒时 `Retry-After` 是 1 而不是 0."""

    fake_clock = _FakeMonotonic()
    monkeypatch.setattr("app.auth.login_throttle.monotonic", fake_clock)

    await _fail_logins(client, TEST_ADMIN_USERNAME, 5)

    fake_clock.advance(15 * 60 - 1)
    one_second_left = await _attempt_login(
        client, TEST_ADMIN_USERNAME, TEST_ADMIN_PASSWORD
    )

    fake_clock.advance(1)
    unlocked = await _attempt_login(client, TEST_ADMIN_USERNAME, TEST_ADMIN_PASSWORD)

    assert one_second_left.status_code == 429
    assert one_second_left.headers["retry-after"] == "1"
    assert unlocked.status_code == 200


async def test_throttle_state_lives_on_the_application_not_the_module(
    platform_settings: PlatformSettings,
) -> None:
    """计数挂在应用实例上: 换一个应用就是一份全新的计数.

    这不只是设计偏好——它是既有那一批登录用例不被串味伤害的前提. 若计数做成模块级全局, 上一条
    用例锁掉的账号会把后面所有用例一起拖成 429.
    """

    async with running_client(platform_settings) as (_, first_client):
        await _fail_logins(first_client, TEST_ADMIN_USERNAME, 5)
        locked_in_first_application = await _attempt_login(
            first_client, TEST_ADMIN_USERNAME, TEST_ADMIN_PASSWORD
        )

    async with running_client(platform_settings) as (_, second_client):
        unlocked_in_second_application = await _attempt_login(
            second_client, TEST_ADMIN_USERNAME, TEST_ADMIN_PASSWORD
        )

    assert locked_in_first_application.status_code == 429
    assert unlocked_in_second_application.status_code == 200


def test_tracked_keys_are_capped_and_active_locks_survive_eviction(
    platform_settings: PlatformSettings,
) -> None:
    """持续灌入随机用户名时内存有上界; 淘汰要挑软柿子, 不能把正在生效的锁挤掉."""

    throttle = LoginAttemptThrottle(platform_settings)
    locked_username = "target-of-a-lockout"

    # 五次失败刻意分散在五个地址上: `remaining_lock_seconds` 取两桶的较大值, 若这五次都来自同一个
    # 地址, 那个地址桶也会一起锁上, 后面的断言就被它兜住了——用户名那一条是否被淘汰根本看不出来.
    for index in range(platform_settings.login_throttle_maximum_failures):
        throttle.record_failure(locked_username, CLIENT_ADDRESS_POOL[index])

    for index in range(MAXIMUM_TRACKED_KEYS_PER_DIMENSION + 10):
        throttle.record_failure(f"flood-{index}", OTHER_CLIENT_ADDRESS_POOL[1])

    # 直接读那个字典是为了钉住"键数有硬上限"这件事本身; 其余断言都走公开接口.
    assert len(throttle._failures_by_username) <= MAXIMUM_TRACKED_KEYS_PER_DIMENSION
    # 查询用的地址从没失败过, 故这个正值只可能来自用户名桶——正是要被淘汰保护的那一条.
    assert (
        throttle.remaining_lock_seconds(locked_username, OTHER_CLIENT_ADDRESS_POOL[2]) > 0
    )


@pytest.mark.parametrize(
    "method_name",
    ["remaining_lock_seconds", "record_failure", "record_successful_login"],
)
def test_throttle_methods_stay_synchronous(method_name: str) -> None:
    """这条纪律没有别的落点, 只能钉在这里.

    事件循环是单线程的, 同步方法从进到出不会被别的协程切走, 于是"读计数 -> 写计数"天然原子,
    节流因此不需要锁. 谁把它改成 `async def`, 谁就在读与写之间开了一个让出点: 两个并发请求会
    同时读到"未锁"再各自写回. 那种失效在单线程的手工测试里根本复现不出来.
    """

    assert not inspect.iscoroutinefunction(getattr(LoginAttemptThrottle, method_name))


@pytest.mark.parametrize(
    ("forwarded_for", "peer_host", "trusted_addresses", "expected_address"),
    [
        # 对端可信: 取最右那一项, 它由最近一跳代理写入, 客户端伪造不了.
        ("1.2.3.4", TEST_PEER_ADDRESS, LOOPBACK_ONLY, "1.2.3.4"),
        # 左侧的伪造项被跳过——这正是"从右往左"的全部价值.
        ("9.9.9.9, 1.2.3.4", TEST_PEER_ADDRESS, LOOPBACK_ONLY, "1.2.3.4"),
        # 对端不可信: 整个头都不看, 用对端地址. 后端直连时默认走的就是这条路.
        ("1.2.3.4", "203.0.113.5", LOOPBACK_ONLY, "203.0.113.5"),
        # 白名单为空 (默认): 谁都不信, 只用对端地址.
        ("1.2.3.4", TEST_PEER_ADDRESS, (), TEST_PEER_ADDRESS),
        # 两跳: 从右往左跳过可信项, 第一个不可信的就是客户端.
        ("1.2.3.4, 10.0.0.9", TEST_PEER_ADDRESS, ("127.0.0.1", "10.0.0.9"), "1.2.3.4"),
        # 可信位上解析不出来 -> 回退对端, **绝不左移到攻击者可写的那一项**.
        # 左边那一项必须是**能解析的**伪造值: 若换成也解析不出来的串, "遇到坏项就停"与"跳过坏项
        # 继续往左"会得到同一个结果, 这条用例就废了——它正是用来钉住后一种写法的.
        ("9.9.9.9, not-an-ip", TEST_PEER_ADDRESS, LOOPBACK_ONLY, TEST_PEER_ADDRESS),
        # 整条链都是可信地址 -> 除了对端自己也取不到别人.
        ("127.0.0.1", TEST_PEER_ADDRESS, LOOPBACK_ONLY, TEST_PEER_ADDRESS),
        ("  1.2.3.4  ", TEST_PEER_ADDRESS, LOOPBACK_ONLY, "1.2.3.4"),
        ("1.2.3.4:5678", TEST_PEER_ADDRESS, LOOPBACK_ONLY, "1.2.3.4"),
        ("[2001:db8::1]:443", TEST_PEER_ADDRESS, LOOPBACK_ONLY, "2001:db8::1"),
        # 规范化: 同一个地址的不同写法不能各占一个桶, 否则改个大小写就能把阈值翻倍.
        ("2001:DB8::1", TEST_PEER_ADDRESS, LOOPBACK_ONLY, "2001:db8::1"),
        (None, TEST_PEER_ADDRESS, LOOPBACK_ONLY, TEST_PEER_ADDRESS),
        (None, None, LOOPBACK_ONLY, UNKNOWN_CLIENT_ADDRESS),
    ],
)
def test_resolve_client_address(
    forwarded_for: str | None,
    peer_host: str | None,
    trusted_addresses: tuple[str, ...],
    expected_address: str,
) -> None:
    resolved_address = resolve_client_address(
        forwarded_for, peer_host, parse_trusted_proxy_networks(trusted_addresses)
    )

    assert resolved_address == expected_address
