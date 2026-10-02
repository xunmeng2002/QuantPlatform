"""登录失败节流: 同一用户名或同一来源地址连续失败若干次后, 锁住一段时间.

计数放在**进程内存**里. 单实例是本仓调度器的硬前提 (见 `docs/platform-plan.md` 的并发一节),
故不存在"多 worker 各记一份、阈值被放大 N 倍"的问题, 也就不需要一张计数表——那张表还会在每次
登录失败时与调度器争 SQLite 的写锁.

代价是**重启即清空**: 进程重启 (含崩溃重启与部署) 会把全部计数与锁定一起抹掉. 这是有意取舍,
不是疏漏.
"""

from __future__ import annotations

import ipaddress
import logging
import math
from dataclasses import dataclass
from time import monotonic
from typing import TypeAlias

from ..config import PlatformSettings


logger = logging.getLogger(__name__)

# 对端地址缺失时的占位. 用它而不是空串: 空串会让"地址取不到"的所有请求并进同一个桶, 而它们
# 之间的关系恰恰是最不该假设的.
UNKNOWN_CLIENT_ADDRESS = "unknown"

# 每个维度的键数硬上限. 用户名最长 64 字符、地址规范化后最长 45 字符, 单条计数约 200~300 字节,
# 故持续灌入时的内存上界是两维各约 1 MB. 正常使用下桶里只有"最近一个锁定窗口内失败过的人",
# 远够不到这个数, 清扫根本不会触发.
MAXIMUM_TRACKED_KEYS_PER_DIMENSION = 4096

USERNAME_DIMENSION = "用户名"
CLIENT_ADDRESS_DIMENSION = "来源地址"

IPAddress: TypeAlias = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork: TypeAlias = ipaddress.IPv4Network | ipaddress.IPv6Network


@dataclass
class LoginFailureRecord:
    """一个桶的连续失败计数与锁定时刻.

    三个字段都是 `monotonic` 的读秒数, 不是墙钟时刻: 节流算的是时间间隔, 墙钟被 NTP 步进或
    手工改表会让一把 15 分钟的锁瞬间失效或变成几小时.
    """

    consecutive_failures: int = 0
    last_failure_at: float = 0.0
    locked_until: float = 0.0

    def remaining_lock_seconds(self, now: float) -> int:
        if now >= self.locked_until:
            return 0

        # 向上取整且最小 1: 向下取整会让调用方在解锁前就重试, 而 `Retry-After: 0` 的语义是
        # "立刻重试", 等于把客户端的重试变成忙等.
        return max(1, math.ceil(self.locked_until - now))

    def is_stale(self, now: float, failure_window_seconds: float) -> bool:
        """既不处于锁定期, 又超过一个失效窗口没有再失败——逻辑上等价于"这条计数不存在"."""

        return (
            now >= self.locked_until
            and now - self.last_failure_at >= failure_window_seconds
        )


class LoginAttemptThrottle:
    """进程内的登录失败节流, 两个维度各自计数.

    **本类没有锁, 也不是巧合**: 全部方法都是同步的, 内部一次 `await` 都没有. 事件循环是单线程
    的, 一个同步方法从进到出不会被别的协程切走, 而登录处理里唯一的阻塞点是同步的 PBKDF2
    (`verify_password`), 它同样不让出控制权. 于是"读计数 -> 写计数"天然原子; 加一把
    `asyncio.Lock` 只会给一个本就不会交错的过程添一层调度, 并把 PBKDF2 拖进临界区, 把登录
    串行成一条队列.

    维持这条不变量的方式: 下面两个写方法 (`record_failure` / `record_successful_login`)
    **永远不要改成 `async def`**, 也不要把 `verify_password` 或任何 IO 挪进它们内部. 一旦
    读-改-写跨过了 `await`, 两个并发请求就能同时读到"未锁"再各自写回, 节流只在并发下静默失效
    ——而单线程下的手工测试根本复现不出来. `tests/test_login_throttle.py` 里有一条
    `inspect.iscoroutinefunction` 断言, 就是给这条纪律一个可执行的落点.

    失效窗口取锁定时长: 距上次失败已过一个窗口, 计数就从头算. 这同时给了两个性质——锁定到期后
    用户重新拿满次数, 以及陈旧计数可被淘汰. 副作用是攻击者每过一个窗口能做 `阈值 - 1` 次猜测,
    这是为内存确定性付出的代价.
    """

    def __init__(self, settings: PlatformSettings) -> None:
        self._maximum_failures = settings.login_throttle_maximum_failures
        self._lock_seconds = settings.login_throttle_lock_minutes * 60
        self._trusted_proxy_networks = parse_trusted_proxy_networks(
            settings.trusted_proxy_addresses
        )
        self._failures_by_username: dict[str, LoginFailureRecord] = {}
        self._failures_by_client_address: dict[str, LoginFailureRecord] = {}

    def client_address_for(self, forwarded_for: str | None, peer_host: str | None) -> str:
        """按配置的可信代理白名单解析出用于分桶的客户端地址."""

        return resolve_client_address(
            forwarded_for, peer_host, self._trusted_proxy_networks
        )

    def remaining_lock_seconds(self, username: str, client_address: str) -> int:
        """两个桶中较长的剩余锁定秒数: 要等到两个都解锁才算真的能登."""

        now = monotonic()

        return max(
            remaining_lock_seconds_of(self._failures_by_username.get(username), now),
            remaining_lock_seconds_of(
                self._failures_by_client_address.get(client_address), now
            ),
        )

    def record_failure(self, username: str, client_address: str) -> None:
        """记一次凭据失败. 两个维度各自加一, 互不影响."""

        now = monotonic()
        self._record(self._failures_by_username, USERNAME_DIMENSION, username, now)
        self._record(
            self._failures_by_client_address, CLIENT_ADDRESS_DIMENSION, client_address, now
        )

    def record_successful_login(self, username: str) -> None:
        """登录成功后清空用户名桶.

        **不清来源地址桶**: 清掉的话, 攻击者每隔几次插一次自己的有效登录就能把地址计数清零,
        整个维度当场作废.
        """

        self._failures_by_username.pop(username, None)

    def _record(
        self,
        store: dict[str, LoginFailureRecord],
        dimension: str,
        key: str,
        now: float,
    ) -> None:
        record = store.get(key)

        if record is None:
            self._make_room(store, now)
            record = LoginFailureRecord()
            store[key] = record
        elif now - record.last_failure_at >= self._lock_seconds:
            record.consecutive_failures = 0

        record.consecutive_failures += 1
        record.last_failure_at = now

        if record.consecutive_failures >= self._maximum_failures:
            record.locked_until = now + self._lock_seconds
            # 键用 %r 而不是 %s: 用户名允许换行, 原样写进日志就成了日志注入. 地址是规范化过的
            # 字面量, 但同一个格式化符号对待两处更省心.
            logger.warning(
                "登录失败达阈值, 按%s锁定 %d 秒: %r", dimension, self._lock_seconds, key
            )

    def _make_room(self, store: dict[str, LoginFailureRecord], now: float) -> None:
        """为一条新计数腾出位置. 只在桶已满时才会真的做事."""

        if len(store) < MAXIMUM_TRACKED_KEYS_PER_DIMENSION:
            return

        for stale_key in [
            key
            for key, record in store.items()
            if record.is_stale(now, self._lock_seconds)
        ]:
            del store[stale_key]

        overflow = len(store) - MAXIMUM_TRACKED_KEYS_PER_DIMENSION + 1

        if overflow <= 0:
            return

        # 先淘汰未锁的、最久未失败的; 处于锁定期的排在后面, 只在整张表都锁着时才轮到它. 淘汰一把
        # 正在生效的锁等于给攻击者一条解锁捷径——他要的正是"把别人的计数挤掉".
        for eviction_key in sorted(
            store,
            key=lambda key: (store[key].locked_until > now, store[key].last_failure_at),
        )[:overflow]:
            del store[eviction_key]


def parse_trusted_proxy_networks(addresses: tuple[str, ...]) -> tuple[IPNetwork, ...]:
    """把配置里的地址与网段解析成网络对象, 解析不了即抛.

    装配期解析一次而不是每请求解析: 登录是热路径, 而白名单不会长.

    故意**不静默跳过坏值**: 一个错别字会让白名单形同不存在, 症状是"全站用户挤进同一个桶",
    而真正的原因会藏在一行看起来已经配好的环境变量里.
    """

    return tuple(ipaddress.ip_network(address, strict=False) for address in addresses)


def resolve_client_address(
    forwarded_for: str | None,
    peer_host: str | None,
    trusted_proxy_networks: tuple[IPNetwork, ...],
) -> str:
    """取出用于分桶的客户端地址.

    只有当**对端地址本身可信**时才去看 `X-Forwarded-For`, 且从最右往左跳过可信项: 每一跳代理
    都会把自己收到的对端追加在最右, 所以右侧才是代理写的, 左侧全是请求方可以随意伪造的.

    解析不出来的项**就地停下并回退对端地址, 绝不继续往左取**. 这一条是本函数的要害: 若改成
    "过滤掉非法项之后再从右数", 可信位上的一个畸形项会把索引左移一格, 正好落进攻击者可写的区域.
    """

    peer_address = _parse_address(peer_host)

    if peer_address is None:
        return peer_host or UNKNOWN_CLIENT_ADDRESS

    if not forwarded_for or not _is_trusted(peer_address, trusted_proxy_networks):
        return str(peer_address)

    for raw_entry in reversed(forwarded_for.split(",")):
        candidate = _parse_address(raw_entry)

        if candidate is None:
            break

        if _is_trusted(candidate, trusted_proxy_networks):
            continue

        return str(candidate)

    # 整条链上都是可信地址 (或全都解析不出来): 除了对端自己也取不到别人, 回退到它.
    return str(peer_address)


def _parse_address(raw_text: str | None) -> IPAddress | None:
    """剥掉 `host:port` 与 `[v6]:port` 的壳, 规范化后解析; 解析不了返回 None.

    规范化 (交给 `ip_address`) 是为了让同一个地址的不同写法落进同一个桶——`2001:DB8::1` 与
    `2001:db8::1` 若各占一个桶, 攻击者靠改大小写就能把阈值翻倍.
    """

    if raw_text is None:
        return None

    candidate = raw_text.strip()

    if candidate.startswith("["):
        closing_bracket = candidate.find("]")

        if closing_bracket != -1:
            candidate = candidate[1:closing_bracket]
    elif candidate.count(":") == 1:
        host, _, port = candidate.partition(":")

        if port.isdigit():
            candidate = host

    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        return None


def _is_trusted(address: IPAddress, trusted_proxy_networks: tuple[IPNetwork, ...]) -> bool:
    """地址是否落在白名单内. 版本不同 (v4 对 v6) 时 `in` 直接为假, 不会抛."""

    return any(address in network for network in trusted_proxy_networks)


def remaining_lock_seconds_of(record: LoginFailureRecord | None, now: float) -> int:
    """取一条计数的剩余锁定秒数; 没有这条计数, 或已过锁定期, 均为 0."""

    return 0 if record is None else record.remaining_lock_seconds(now)
