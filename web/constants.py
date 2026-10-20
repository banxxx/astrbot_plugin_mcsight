"""视图「代码常量」页的数据源。

这些值不读配置文件（插件读配置的唯一入口是 config/whitelist_config.py，调用点就是
_conf_schema.json 里的那些键），全部写死在源码里，改它要动代码并发版，所以本页只列不填。
数值在每次请求时从模块实时取，避免清单与源码脱节。
"""
from __future__ import annotations

from typing import Any

from ..commands import bind as _bind
from ..commands import bind_check as _bind_check
from ..commands import common as _common
from ..commands import console as _console
from ..commands import management as _mgmt
from ..commands import nickname_check as _nick
from ..config import server_config as _sc
from ..features.bindcheck_image import image_generator as _bci
from ..features.player_status import checker as _checker
from ..features.player_status import controller as _ctrl
from ..features.player_status import image_generator as _psi
from ..utils import bind_guard as _bg
from ..utils import bind_route as _br
from ..utils import debounce as _deb
from ..utils import mod_http as _mh
from ..utils import stats_sampler as _ss
from ..utils import stats_snapshot as _snap

# (标题, 常量名, 值, 单位/说明, 源码位置)
ROWS: list[tuple[str, str, Any, str, str]] = [
    ("请求总超时", "TOTAL_TIMEOUT", _mh.TOTAL_TIMEOUT, "秒", "utils/mod_http.py"),
    ("建连超时", "CONNECT_TIMEOUT", _mh.CONNECT_TIMEOUT, "秒", "utils/mod_http.py"),
    ("连接池上限", "CONNECTOR_LIMIT", _mh.CONNECTOR_LIMIT, "个", "utils/mod_http.py"),
    ("熔断失败次数", "BREAKER_FAIL_THRESHOLD", _mh.BREAKER_FAIL_THRESHOLD, "次", "utils/mod_http.py"),
    ("熔断冷却", "BREAKER_COOLDOWN", _mh.BREAKER_COOLDOWN, "秒", "utils/mod_http.py"),
    ("SLP 探测超时", "PING_TIMEOUT", _checker.PING_TIMEOUT, "秒", "features/player_status/checker.py"),
    ("防抖·重", "WINDOW_HEAVY", _deb.WINDOW_HEAVY, "秒", "utils/debounce.py"),
    ("防抖·中", "WINDOW_MEDIUM", _deb.WINDOW_MEDIUM, "秒", "utils/debounce.py"),
    ("防抖·轻", "WINDOW_LIGHT", _deb.WINDOW_LIGHT, "秒", "utils/debounce.py"),
    ("绑定抢码窗口", "WINDOW", _bg.WINDOW, "秒", "utils/bind_guard.py"),
    ("绑定抢码次数", "MAX_INVALID", _bg.MAX_INVALID, "次", "utils/bind_guard.py"),
    ("绑定抢码封禁", "PENALTY", _bg.PENALTY, "秒", "utils/bind_guard.py"),
    ("绑定码路由 TTL", "TTL", _br.TTL, "秒", "utils/bind_route.py"),
    ("同步单轮超时", "SYNC_ROUND_TIMEOUT", _bind.SYNC_ROUND_TIMEOUT, "秒", "commands/bind.py"),
    ("同步上限", "SYNC_BATCH_TIMEOUT_MAX", _bind.SYNC_BATCH_TIMEOUT_MAX, "秒", "commands/bind.py"),
    ("命令长度上限", "MAX_COMMAND_CHARS", _console.MAX_COMMAND_CHARS, "字符", "commands/console.py"),
    ("回显字节上限", "MAX_OUTPUT_CHARS", _console.MAX_OUTPUT_CHARS, "字节", "commands/console.py"),
    ("回显超时", "REQUEST_TIMEOUT", _console.REQUEST_TIMEOUT, "秒", "commands/console.py"),
    ("Token 长度下限", "MIN_TOKEN_CHARS", _mgmt.MIN_TOKEN_CHARS, "字符", "commands/management.py"),
    ("Token 长度上限", "MAX_TOKEN_CHARS", _mgmt.MAX_TOKEN_CHARS, "字符", "commands/management.py"),
    ("每群服务器上限", "MAX_SERVERS_PER_GROUP", _sc.MAX_SERVERS_PER_GROUP, "台", "config/server_config.py"),
    ("采样单轮预算", "ROUND_BUDGET", _ss.ROUND_BUDGET, "秒", "utils/stats_sampler.py"),
    ("采样目标上限", "MAX_TARGETS", _ss.MAX_TARGETS, "人", "utils/stats_sampler.py"),
    ("快照保留", "RETENTION_DAYS", _snap.RETENTION_DAYS, "天", "utils/stats_snapshot.py"),
    ("名单图每页人数", "PER_PAGE", _bci.PER_PAGE, "人", "features/bindcheck_image/image_generator.py"),
    ("翻页发送间隔", "PAGE_SEND_GAP", _bind_check.PAGE_SEND_GAP, "秒", "commands/bind_check.py"),
    ("状态图列数", "COLS", _psi.COLS, "列", "features/player_status/image_generator.py"),
    ("状态图页数上限", "MAX_PAGES", _psi.MAX_PAGES, "页", "features/player_status/image_generator.py"),
    ("昵称检测列表上限", "MAX_LIST_COUNT", _nick.MAX_LIST_COUNT, "条", "commands/nickname_check.py"),
    ("统计图列出条目", "_MAX_LISTED", _ctrl._MAX_LISTED, "条", "features/player_status/controller.py"),
    ("DB 语句超时", "DB_SQL_TIMEOUT", _common.DB_SQL_TIMEOUT, "秒", "commands/common.py"),
    ("DB 保活间隔", "DB_KEEPALIVE_INTERVAL", _common.DB_KEEPALIVE_INTERVAL, "秒", "commands/common.py"),
    ("DB 退避序列", "DB_KEEPALIVE_BACKOFF", _common.DB_KEEPALIVE_BACKOFF, "秒", "commands/common.py"),
]
