"""OneBot 与 QQ 官方共用的群级策略定义。

两种平台各自保存群配置：
- OneBot：整平台一个 ``onebot/config.sqlite3``，群按 ``(scope_kind, scope_id)``
  区分，``policy`` 以 JSON 列保存；
- QQ 官方：每个环境一个 ``qq_official/<环境>/config.sqlite3``，群对象在 ``groups``
  表中，``policy`` 以 JSON 列保存。

它们的容器不同，但群对象里的 ``policy`` 必须使用同一套字段名、类型和默认值。
以后新增群规则时，只改这里和对应界面，不要再往全局插件配置里加开关。
"""
from copy import deepcopy

from .bind_rule import normalize_rule

# 群策略格式版本；字段增删时递增，并在读取处显式处理旧版本。
POLICY_SCHEMA_VERSION = 1

# OneBot 群文件格式版本；官方 SQLite 使用独立的 DB_SCHEMA_VERSION。
GROUP_FILE_SCHEMA_VERSION = 1

# 所有群规则都放在 policy 下。布尔项由界面按开关渲染；
# bind_nickname_rule / bind_nickname_template 是绑定时群名片校验规则。
POLICY_DEFAULTS = {
    "enable_group_owner": True,
    "enable_group_admin": True,
    "bind_require_nickname": True,
    "bind_nickname_rule": "paren",
    "bind_nickname_template": "",
    "show_server_version": False,
    "show_server_latency": True,
    "show_last_online": False,
    "nickname_check_enabled": True,
    "use_central_db": False,
}

POLICY_BOOL_KEYS = frozenset(
    key for key, value in POLICY_DEFAULTS.items() if type(value) is bool
)
POLICY_KEYS = frozenset(POLICY_DEFAULTS)


def default_policy(overrides=None) -> dict:
    """返回一份完整的群策略；overrides 只接受已知字段的合法值。"""
    policy = deepcopy(POLICY_DEFAULTS)
    if not isinstance(overrides, dict):
        return policy
    for key, value in overrides.items():
        if key not in policy:
            continue
        if key in POLICY_BOOL_KEYS:
            if type(value) is bool:
                policy[key] = value
        elif key == "bind_nickname_rule":
            policy[key] = normalize_rule(value)
        elif key == "bind_nickname_template" and isinstance(value, str) and len(value) <= 64:
            policy[key] = value
    return policy


def normalize_policy(value) -> dict:
    """把磁盘上的 policy 收敛成标准结构；缺失/坏值按默认值处理。

    这是 OneBot 群文件的读取口径。QQ 官方库中的 policy 更严格：字段缺失时
    直接判为损坏，不做静默补全；只有旧版 v1 JSON 迁移时补齐缺失字段。
    """
    return default_policy(value if isinstance(value, dict) else None)
