"""用户管理端点.

除 `GET /directory` 外全部端点限管理员: 用户清单本身即租户列表, 开放给普通用户等于告诉
所有人平台上有谁. `/directory` 是一处有意的放宽, 边界与泄漏面见
`app/routers/users.py::list_user_directory_handler`.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.auth.dependencies import ADMIN_REQUIRED_DETAIL
from app.catalog.database import PlatformDatabase
from app.catalog.enums import UserStatus, UserType
from app.catalog.schemas import (
    MessageResponse,
    PageResponse,
    UserDirectoryEntryResponse,
    UserResponse,
)
from app.catalog.visibility import USER_NOT_FOUND_MESSAGE
from app.main import REDACTED_FIELD_VALUE
from app.routers.users import (
    BLANK_DISPLAY_NAME_MESSAGE,
    LAST_ACTIVE_ADMIN_MESSAGE,
    USERNAME_TAKEN_MESSAGE,
    USER_STATUS_UPDATED_MESSAGE,
)

from .conftest import TEST_ADMIN_PASSWORD, TEST_ADMIN_USERNAME
from .helpers import (
    LOGIN_PATH,
    bearer_headers,
    bulk_create_user_records,
    login,
)


USERS_PATH = "/api/users"
DIRECTORY_PATH = f"{USERS_PATH}/directory"
CURRENT_USER_PATH = "/api/auth/me"
CREATED_USERNAME = "created-by-admin"
CREATED_PASSWORD = "created-member-password"
MEMBER_USERNAME = "member-admin-surface"
INVALID_USERNAME = "has space"
DISPLAY_NAME = "被授权的同事"
OTHER_USERNAME = "zhou-san"
OTHER_DISPLAY_NAME = "周三四"


@pytest_asyncio.fixture
async def admin_token(client: AsyncClient) -> str:
    return await login(client, TEST_ADMIN_USERNAME, TEST_ADMIN_PASSWORD)


@pytest_asyncio.fixture
async def member_token(client: AsyncClient, admin_token: str) -> str:
    """经管理员接口建一个普通账号并登录, 用于验证非管理员的拒绝路径."""

    await _create_user(client, admin_token, MEMBER_USERNAME)

    return await login(client, MEMBER_USERNAME, CREATED_PASSWORD)


async def _create_user(
    client: AsyncClient,
    admin_token: str,
    username: str,
    password: str = CREATED_PASSWORD,
    user_type: UserType = UserType.USER,
    display_name: str | None = None,
):
    """建号.

    `display_name` 缺省取用户名: 建号时它必填, 而绝大多数用例只在意用户名, 逐个写明
    显示名会把"这条用例在建号"这件事淹掉. 要验显示名本身时才显式传.
    """

    return await client.post(
        USERS_PATH,
        json={
            "username": username,
            "password": password,
            "display_name": username if display_name is None else display_name,
            "user_type": user_type.value,
        },
        headers=bearer_headers(admin_token),
    )


async def _read_directory(
    client: AsyncClient, token: str, **parameters: object
) -> PageResponse[UserDirectoryEntryResponse]:
    """请求受限用户目录并解出分页信封."""

    response = await client.get(
        DIRECTORY_PATH, params=parameters, headers=bearer_headers(token)
    )

    assert response.status_code == 200, response.text

    return PageResponse[UserDirectoryEntryResponse].model_validate(response.json())


async def test_admin_creates_a_user(client: AsyncClient, admin_token: str) -> None:
    response = await _create_user(client, admin_token, CREATED_USERNAME)

    assert response.status_code == 201

    created = UserResponse.model_validate(response.json())

    assert created.username == CREATED_USERNAME
    assert created.user_type is UserType.USER
    assert created.status is UserStatus.ACTIVE


async def test_created_user_never_reveals_a_password_hash(
    client: AsyncClient, admin_token: str
) -> None:
    response = await _create_user(client, admin_token, CREATED_USERNAME)

    assert "password" not in response.text.lower()


async def test_created_user_can_sign_in(client: AsyncClient, admin_token: str) -> None:
    await _create_user(client, admin_token, CREATED_USERNAME)

    token = await login(client, CREATED_USERNAME, CREATED_PASSWORD)

    assert token


async def test_admin_can_create_another_admin(
    client: AsyncClient, admin_token: str
) -> None:
    response = await _create_user(
        client, admin_token, CREATED_USERNAME, user_type=UserType.ADMIN
    )

    assert response.status_code == 201
    assert UserResponse.model_validate(response.json()).user_type is UserType.ADMIN


async def test_duplicate_username_is_rejected(
    client: AsyncClient, admin_token: str
) -> None:
    await _create_user(client, admin_token, CREATED_USERNAME)

    response = await _create_user(client, admin_token, CREATED_USERNAME)

    assert response.status_code == 409
    assert response.json()["detail"] == USERNAME_TAKEN_MESSAGE


async def test_username_taken_by_the_seeded_admin_is_rejected(
    client: AsyncClient, admin_token: str
) -> None:
    response = await _create_user(client, admin_token, TEST_ADMIN_USERNAME)

    assert response.status_code == 409
    assert response.json()["detail"] == USERNAME_TAKEN_MESSAGE


async def test_admin_lists_users_including_the_seeded_one(
    client: AsyncClient, admin_token: str
) -> None:
    await _create_user(client, admin_token, CREATED_USERNAME)

    response = await client.get(USERS_PATH, headers=bearer_headers(admin_token))

    assert response.status_code == 200

    page = PageResponse[UserResponse].model_validate(response.json())

    assert page.total == 2
    assert {record.username for record in page.records} == {
        TEST_ADMIN_USERNAME,
        CREATED_USERNAME,
    }


async def _set_user_status(
    client: AsyncClient, admin_token: str, user_id: str, target_status: UserStatus
):
    return await client.patch(
        f"{USERS_PATH}/{user_id}/status",
        json={"status": target_status.value},
        headers=bearer_headers(admin_token),
    )


async def test_admin_disabling_a_user_through_the_api_takes_effect_immediately(
    client: AsyncClient, admin_token: str
) -> None:
    created = UserResponse.model_validate(
        (await _create_user(client, admin_token, CREATED_USERNAME)).json()
    )
    member_token = await login(client, CREATED_USERNAME, CREATED_PASSWORD)

    response = await _set_user_status(
        client, admin_token, created.id, UserStatus.DISABLED
    )

    assert response.status_code == 200
    assert MessageResponse.model_validate(response.json()).message == (
        USER_STATUS_UPDATED_MESSAGE
    )

    revoked = await client.get("/api/auth/me", headers=bearer_headers(member_token))

    assert revoked.status_code == 401


async def test_admin_reenabling_a_user_restores_access(
    client: AsyncClient, admin_token: str
) -> None:
    created = UserResponse.model_validate(
        (await _create_user(client, admin_token, CREATED_USERNAME)).json()
    )

    await _set_user_status(client, admin_token, created.id, UserStatus.DISABLED)
    reenabled = await _set_user_status(
        client, admin_token, created.id, UserStatus.ACTIVE
    )

    assert reenabled.status_code == 200
    assert await login(client, CREATED_USERNAME, CREATED_PASSWORD)


async def test_status_update_on_an_unknown_user_is_not_found(
    client: AsyncClient, admin_token: str
) -> None:
    response = await client.patch(
        f"{USERS_PATH}/no-such-user-identifier/status",
        json={"status": UserStatus.DISABLED.value},
        headers=bearer_headers(admin_token),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == USER_NOT_FOUND_MESSAGE


async def test_member_cannot_create_a_user(
    client: AsyncClient, member_token: str
) -> None:
    response = await client.post(
        USERS_PATH,
        json={"username": CREATED_USERNAME, "password": CREATED_PASSWORD},
        headers=bearer_headers(member_token),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == ADMIN_REQUIRED_DETAIL


async def test_member_cannot_create_an_admin_account(
    client: AsyncClient, member_token: str
) -> None:
    response = await client.post(
        USERS_PATH,
        json={
            "username": CREATED_USERNAME,
            "password": CREATED_PASSWORD,
            "user_type": UserType.ADMIN.value,
        },
        headers=bearer_headers(member_token),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == ADMIN_REQUIRED_DETAIL


async def test_member_cannot_list_users(client: AsyncClient, member_token: str) -> None:
    response = await client.get(USERS_PATH, headers=bearer_headers(member_token))

    assert response.status_code == 403
    assert response.json()["detail"] == ADMIN_REQUIRED_DETAIL


async def test_member_cannot_update_any_user_status(
    client: AsyncClient, member_token: str
) -> None:
    response = await client.patch(
        f"{USERS_PATH}/any-identifier/status",
        json={"status": UserStatus.DISABLED.value},
        headers=bearer_headers(member_token),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == ADMIN_REQUIRED_DETAIL


@pytest.mark.parametrize(
    "endpoint_call",
    [
        ("get", USERS_PATH, None),
        ("post", USERS_PATH, {"username": CREATED_USERNAME, "password": CREATED_PASSWORD}),
        ("patch", f"{USERS_PATH}/any-identifier/status", {"status": "disabled"}),
    ],
)
async def test_user_management_endpoints_reject_anonymous_callers(
    client: AsyncClient, endpoint_call: tuple[str, str, dict[str, str] | None]
) -> None:
    method, path, body = endpoint_call
    body_arguments = {} if body is None else {"json": body}

    response = await getattr(client, method)(path, **body_arguments)

    assert response.status_code == 401


@pytest.mark.parametrize(
    "username",
    ["ab", "", "has space", "has/slash", "has@sign", "a" * 65],
)
async def test_invalid_username_is_rejected(
    client: AsyncClient, admin_token: str, username: str
) -> None:
    response = await _create_user(client, admin_token, username)

    assert response.status_code == 422


@pytest.mark.parametrize("password", ["", "short", "a" * 257])
async def test_out_of_range_password_is_rejected(
    client: AsyncClient, admin_token: str, password: str
) -> None:
    response = await _create_user(client, admin_token, CREATED_USERNAME, password=password)

    assert response.status_code == 422


@pytest.mark.parametrize(
    "parameters",
    [{"limit": 0}, {"limit": 101}, {"offset": -1}],
)
async def test_invalid_pagination_is_rejected(
    client: AsyncClient, admin_token: str, parameters: dict[str, int]
) -> None:
    response = await client.get(
        USERS_PATH, params=parameters, headers=bearer_headers(admin_token)
    )

    assert response.status_code == 422


async def _read_signed_in_user(client: AsyncClient, token: str) -> UserResponse:
    response = await client.get(CURRENT_USER_PATH, headers=bearer_headers(token))

    assert response.status_code == 200

    return UserResponse.model_validate(response.json())


async def test_the_last_active_admin_cannot_be_disabled(
    client: AsyncClient, admin_token: str
) -> None:
    """停用后播种逻辑按"库中是否存在管理员"判断而不看状态, 重启也不会重建, 平台将无法挽回."""

    signed_in_admin = await _read_signed_in_user(client, admin_token)

    response = await _set_user_status(
        client, admin_token, signed_in_admin.id, UserStatus.DISABLED
    )

    assert response.status_code == 409
    assert response.json()["detail"] == LAST_ACTIVE_ADMIN_MESSAGE


async def test_an_admin_can_be_disabled_while_another_remains_active(
    client: AsyncClient, admin_token: str
) -> None:
    second_admin = UserResponse.model_validate(
        (
            await _create_user(
                client, admin_token, CREATED_USERNAME, user_type=UserType.ADMIN
            )
        ).json()
    )

    response = await _set_user_status(
        client, admin_token, second_admin.id, UserStatus.DISABLED
    )

    assert response.status_code == 200


async def test_lockout_stays_blocked_once_the_other_admin_is_disabled(
    client: AsyncClient, admin_token: str
) -> None:
    second_admin = UserResponse.model_validate(
        (
            await _create_user(
                client, admin_token, CREATED_USERNAME, user_type=UserType.ADMIN
            )
        ).json()
    )
    await _set_user_status(client, admin_token, second_admin.id, UserStatus.DISABLED)
    signed_in_admin = await _read_signed_in_user(client, admin_token)

    response = await _set_user_status(
        client, admin_token, signed_in_admin.id, UserStatus.DISABLED
    )

    assert response.status_code == 409
    assert response.json()["detail"] == LAST_ACTIVE_ADMIN_MESSAGE


async def test_disabling_an_already_disabled_admin_is_not_blocked(
    client: AsyncClient, admin_token: str
) -> None:
    """守门看的是"启用中"的转移: 对已停用者重复停用不该被误拒."""

    second_admin = UserResponse.model_validate(
        (
            await _create_user(
                client, admin_token, CREATED_USERNAME, user_type=UserType.ADMIN
            )
        ).json()
    )

    first_attempt = await _set_user_status(
        client, admin_token, second_admin.id, UserStatus.DISABLED
    )
    repeated_attempt = await _set_user_status(
        client, admin_token, second_admin.id, UserStatus.DISABLED
    )

    assert first_attempt.status_code == 200
    assert repeated_attempt.status_code == 200


async def test_a_disabled_admin_can_still_be_reenabled(
    client: AsyncClient, admin_token: str
) -> None:
    second_admin = UserResponse.model_validate(
        (
            await _create_user(
                client, admin_token, CREATED_USERNAME, user_type=UserType.ADMIN
            )
        ).json()
    )
    await _set_user_status(client, admin_token, second_admin.id, UserStatus.DISABLED)

    response = await _set_user_status(
        client, admin_token, second_admin.id, UserStatus.ACTIVE
    )

    assert response.status_code == 200
    assert (await _read_signed_in_user(client, admin_token)).status is UserStatus.ACTIVE


async def test_validation_failure_never_echoes_the_password(client: AsyncClient) -> None:
    """默认的 422 回执会把出错输入整个抄回响应体, 而这类响应常被接入层日志落盘."""

    oversized_password = "p" * 300

    response = await client.post(
        LOGIN_PATH,
        json={"username": TEST_ADMIN_USERNAME, "password": oversized_password},
    )

    assert response.status_code == 422
    assert oversized_password not in response.text
    assert REDACTED_FIELD_VALUE in response.text


async def test_validation_failure_still_names_the_offending_field(
    client: AsyncClient, admin_token: str
) -> None:
    """脱敏只针对敏感字段, 其余字段的值照旧回显, 否则报错就失去了可读性."""

    response = await _create_user(client, admin_token, INVALID_USERNAME)

    assert response.status_code == 422
    assert "username" in response.text
    assert INVALID_USERNAME in response.text


async def test_a_missing_display_name_is_rejected(
    client: AsyncClient, admin_token: str
) -> None:
    """显示名必填: 它是受限目录里唯一能认人的字段, 留空的账号在授权表单里选不到."""

    response = await client.post(
        USERS_PATH,
        json={"username": CREATED_USERNAME, "password": CREATED_PASSWORD},
        headers=bearer_headers(admin_token),
    )

    assert response.status_code == 422
    assert "display_name" in response.text


@pytest.mark.parametrize("display_name", ["", " ", "　", "\t\n"])
async def test_a_blank_display_name_is_rejected(
    client: AsyncClient, admin_token: str, display_name: str
) -> None:
    """空串由 `min_length` 挡; 纯空白由处理器挡 (全角空格与制表符同样算空白).

    两种都必须在建号这一步挡住: 只由空白组成的显示名在目录里是**看得见却认不出是谁**的一行.
    """

    response = await _create_user(
        client, admin_token, CREATED_USERNAME, display_name=display_name
    )

    assert response.status_code in (400, 422)

    if response.status_code == 400:
        assert response.json()["detail"] == BLANK_DISPLAY_NAME_MESSAGE


async def test_a_display_name_is_stripped_before_it_is_stored(
    client: AsyncClient, admin_token: str
) -> None:
    """两侧空白先去掉再存: 否则 "张三" 与 "张三 " 在选人下拉里是两行看不出差别的人."""

    created = UserResponse.model_validate(
        (
            await _create_user(
                client, admin_token, CREATED_USERNAME, display_name=f"  {DISPLAY_NAME}  "
            )
        ).json()
    )

    assert created.display_name == DISPLAY_NAME


async def test_a_created_account_shows_up_in_the_directory(
    client: AsyncClient, admin_token: str
) -> None:
    created = UserResponse.model_validate(
        (await _create_user(client, admin_token, CREATED_USERNAME)).json()
    )

    page = await _read_directory(client, admin_token)

    assert [entry.id for entry in page.records] == [created.id]


async def test_a_member_can_list_the_user_directory(
    client: AsyncClient, member_token: str, admin_token: str
) -> None:
    """目录是**有意**对普通用户开放的——授权端点按 id 指定被授权人, 没有目录就没有操作入口.

    与 `test_member_cannot_list_users` 的 403 并存: 两条边界不同, 都要在.
    """

    page = await _read_directory(client, member_token)

    assert page.total == 1
    assert page.records[0].display_name == TEST_ADMIN_USERNAME


async def test_the_directory_never_reveals_a_username(
    client: AsyncClient, admin_token: str, member_token: str
) -> None:
    """用户名绝不出现: 登录接口为未知用户名跑一遍假校验防的就是用户名枚举.

    断言的场景必须让**显示名与用户名不同**, 否则无从分辨回的是哪一个字段. 注意播种的管理员
    显示名恰好等于用户名 (bootstrap 就那样写), 故与本条断言不相干——被查的是 `zhou-san`.
    """

    await _create_user(
        client, admin_token, OTHER_USERNAME, display_name=OTHER_DISPLAY_NAME
    )

    response = await client.get(DIRECTORY_PATH, headers=bearer_headers(member_token))

    assert response.status_code == 200
    assert OTHER_DISPLAY_NAME in response.text
    assert OTHER_USERNAME not in response.text


async def test_the_directory_never_reveals_user_type_or_status(
    client: AsyncClient, admin_token: str, member_token: str
) -> None:
    """谁是管理员、谁被停用了, 都不该由目录透露."""

    response = await client.get(DIRECTORY_PATH, headers=bearer_headers(member_token))
    payload = response.json()

    assert payload["records"]
    assert all(
        set(record) == {"id", "display_name"} for record in payload["records"]
    )


async def test_the_directory_excludes_the_caller(
    client: AsyncClient, member_token: str
) -> None:
    """自己不出现: 授权给自己会被 `PUT /grants` 以 400 挡住, 列出来等于造一条死路."""

    signed_in = await client.get(
        CURRENT_USER_PATH, headers=bearer_headers(member_token)
    )
    caller_id = signed_in.json()["id"]

    page = await _read_directory(client, member_token)

    assert caller_id not in [entry.id for entry in page.records]


async def test_the_directory_skips_disabled_accounts(
    client: AsyncClient, admin_token: str
) -> None:
    """停用账号登录不了, 授权给它只会得到一条必然无效的选项."""

    created = UserResponse.model_validate(
        (await _create_user(client, admin_token, CREATED_USERNAME)).json()
    )
    await _set_user_status(client, admin_token, created.id, UserStatus.DISABLED)

    page = await _read_directory(client, admin_token)

    assert created.id not in [entry.id for entry in page.records]


async def test_the_directory_skips_accounts_without_a_display_name(
    client: AsyncClient, admin_token: str, database: PlatformDatabase
) -> None:
    """存量行: 显示名必填是这一批才收紧的, 库里可能已有显示名为空的账号.

    这类账号若被列出来, 下拉里是一行**空白**——用户看得见却认不出, 选中后授权给谁全靠猜.
    """

    legacy_user_ids = await bulk_create_user_records(database, 2, "legacy-nameless")

    page = await _read_directory(client, admin_token)

    assert set(legacy_user_ids).isdisjoint(entry.id for entry in page.records)


async def test_the_directory_filters_by_display_name(
    client: AsyncClient, admin_token: str
) -> None:
    await _create_user(
        client, admin_token, OTHER_USERNAME, display_name=OTHER_DISPLAY_NAME
    )
    await _create_user(client, admin_token, CREATED_USERNAME, display_name=DISPLAY_NAME)

    page = await _read_directory(client, admin_token, query=OTHER_DISPLAY_NAME)

    assert [entry.display_name for entry in page.records] == [OTHER_DISPLAY_NAME]


async def test_the_directory_query_escapes_like_wildcards(
    client: AsyncClient, admin_token: str
) -> None:
    """`%` 与 `_` 必须当字面量.

    若转义交给手写的字符串拼接, 一个 `%` 就会匹配全体账号——那正是"注入"这一类缺陷在
    只读接口上的形态: 不写坏数据, 但把该藏的人全捞出来.
    """

    await _create_user(client, admin_token, OTHER_USERNAME, display_name=OTHER_DISPLAY_NAME)

    percent_page = await _read_directory(client, admin_token, query="%")
    underscore_page = await _read_directory(client, admin_token, query="_")

    assert percent_page.total == 0
    assert underscore_page.total == 0


async def test_the_directory_pages_without_repeating_a_record(
    client: AsyncClient, admin_token: str
) -> None:
    """并列次序键: 显示名可以重名, 只按显示名排序时 SQLite 对并列行的顺序未定义.

    若次序键少了 `Id`, 翻页会让同一条记录出现两次、另一条一次都不出现——而单页数据下
    这类偏差看不出来.
    """

    for index in range(3):
        await _create_user(
            client, admin_token, f"tied-{index}", display_name=DISPLAY_NAME
        )

    first_page = await _read_directory(client, admin_token, limit=2, offset=0)
    second_page = await _read_directory(client, admin_token, limit=2, offset=2)

    first_ids = [entry.id for entry in first_page.records]
    second_ids = [entry.id for entry in second_page.records]

    assert len(first_ids) == 2
    assert len(second_ids) == 1
    assert set(first_ids).isdisjoint(second_ids)
    assert first_page.total == 3


async def test_directory_pagination_bounds_are_enforced(
    client: AsyncClient, member_token: str
) -> None:
    for parameters in ({"limit": 0}, {"limit": 101}, {"offset": -1}):
        response = await client.get(
            DIRECTORY_PATH, params=parameters, headers=bearer_headers(member_token)
        )

        assert response.status_code == 422


async def test_the_directory_rejects_an_anonymous_caller(client: AsyncClient) -> None:
    response = await client.get(DIRECTORY_PATH)

    assert response.status_code == 401
