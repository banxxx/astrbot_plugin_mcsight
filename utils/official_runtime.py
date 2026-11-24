"""官方独立路由；绑定命令始终受独立中心配置和 V2 能力握手门禁约束。"""
import asyncio
import base64
from collections import OrderedDict
import hashlib
import inspect
import json
import re
import time
from pathlib import Path
from threading import Lock, RLock

from .official_core import OfficialError, OfficialStore, PageCache, event_scope, uuid_value
from .debounce import gate as debounce_gate, should_notify as debounce_notify, window_of as debounce_window
from .official_binding_policy import OfficialBindingPolicy
from .official_binding_service import OfficialBindingService
from .official_binding_host import OfficialBindingHost
from .official_binding_commands import COMMANDS as BINDING_COMMANDS, OfficialBindingCommands
from .official_binding_recovery import OfficialFollowupRecovery
from .official_binding_admin import OfficialBindingAdmin
from .official_bindings import BindingError
from .official_queries import (QueryDocument, QueryService, StatisticsQuery, render_pages,
                               render_text_pages, render_official_pages, render_statistics_pages_async,
                               render_official_help_image, render_unbound_pages)
from .official_delivery import OfficialApiError, transport_for, checked_response
from .official_stats import OfficialStats
from .official_writes import OfficialWriteCommands, COMMANDS as WRITE_COMMANDS
from .official_members import OfficialMembers


HELP = ("MCSight · QQ 官方群\n"
        "/mc setup <验证码>：在后台“新增群”生成验证码后发送，创建本群配置\n"
        "/mc status [-s 服务器名]：在线状态\n"
        "/mc tps [-s 服务器名]：TPS\n"
        "/mc stats <游戏名> [-s 服务器名]：公开统计\n"
        "/mc rank [类型] [总|周|月] -s <服务器名> [-n 1–50]\n"
        "/mc page <页码>：查看最近查询的其它页\n"
        "/mc guide [1|2]：命令指引按钮菜单（官方群；2 为管理员页）\n"
        "/命令指引、/管理指引：直接打开常用 / 管理指引（后者仅群管理员）\n"
        "/mc sid：显示你自己的官方 member_openid，10 秒后自动撤回\n"
        "/mc bindhelp：查看官方绑定开放状态\n"
        "/mc bind <六位码> [-s 服务器名]；/mc unbind [游戏名|all] [-s 服务器名]\n"
        "/mc check [游戏名|user 官方标识|@成员] [-s 服务器名|all]\n"
        "/mc operation <操作编号> [retry [原六位码]|cancel] [-s 服务器名]\n"
        "/mc broadcast [服务器名|all] <内容>（也可 -s）；/mc console [-s] <服务器名> <命令>\n"
        "/mc add|edit|del|hide|show：管理员维护服务器；/mc command <编号>：查询写操作回执\n"
        "/mc batchadd|batchremove|move|swap：批量和排序；/mc lastonline 开|关\n"
        "/mc token <服务器名> <Token|清除>：设置该服控制台 Token；/mc whitelist list：查看权限名单（脱敏）\n"
        "/mc whitelist add|remove @成员；/mc whitelist super add|remove @成员；/mc blacklist add|remove @成员\n"
        "/mc bindcheck [-s 服务器名]；/mc checknick；/mc nicknamecheck 开|关|忽略|取消忽略|忽略列表\n"
        "成员检测需要QQ平台内邀权限；只用可靠群昵称，缺失者标为无法核验。\n"
        "事件未提供群昵称时不以 username 代替，也不会因退群或名单缺失自动解绑。\n"
        "查询需后台启用本群；绑定按配置权威和模组握手门禁开放。控制台须本服独立令牌，未知结果不会自动重发。")

TOKEN_CLEAR_WORDS = ("清除", "删除", "unset", "clear", "off")
TOKEN_MIN_CHARS, TOKEN_MAX_CHARS = 16, 128

ACCESS_LABELS = {
    "super_admins": "超级管理员",
    "whitelist_admins": "白名单管理员",
    "blacklist": "黑名单",
}
ACCESS_ACTIONS = {
    "add": "add", "添加": "add",
    "remove": "remove", "del": "remove", "delete": "remove", "移除": "remove", "删除": "remove",
}
ACCESS_LIST_HELP = ("名单管理：/mc whitelist list；/mc whitelist add|remove @成员；"
                    "/mc whitelist super add|remove @成员；/mc blacklist add|remove @成员。"
                    "仅超级管理员可用，目标须 @ 唯一一位成员；成员标识按当前群生效。")


def _mask_access_value(value):
    """群内展示权限名单时脱敏；数字 QQ 与短标识完全不显示。"""
    value = str(value)
    if value.isdigit() or len(value) <= 8:
        return "***"
    return value[:3] + "…" + value[-3:]


def _access_value_summary(values, limit=12):
    values = sorted(values)
    if not values:
        return "无"
    shown = [_mask_access_value(item) for item in values[:limit]]
    if len(values) > limit:
        shown.append(f"…等 {len(values)} 项")
    return "、".join(shown)


def _member_label(nickname):
    nickname = (nickname or "").strip()[:32]
    return f"「{nickname}」" if nickname else "该成员"

BIND_HELP = ("QQ 官方群须重新配置和绑定，不沿用旧 OneBot 身份或记录。"
             "管理员按交付说明配置 hybrid、remote 或各服 local 权威；每次握手核对范围/结构/名单桥后开放绑定。"
             "未决操作由原请求人发送 /mc operation <完整编号> retry [-s 原服务器]；首次尚未预占可追加原六位码。"
             "中止用 cancel；local 查询须完整编号和原服务器，跨服结果逐服确认。"
             "管理员可 /mc check @成员 或 /mc unbind @成员 -s 原服；数字QQ号不当作官方身份。部署后仍须实际 QQ/MC/数据库验收。")

GUIDE_BUTTON_TIPS = "请升级QQ客户端后重试"
# 官方群消息只能在发出后 2 分钟内撤回；10 秒足够复制 UID，又不会长期暴露。
SID_RECALL_DELAY_SECONDS = 10
GUIDE_PAGE1_ROWS = (
    (("status", "在线状态", "/mc status"), ("tps", "当前 TPS", "/mc tps")),
    (("stats", "玩家统计", "/mc stats"), ("rank_duration", "时长排行", "/mc rank 时长")),
    (("rank_death", "死亡排行", "/mc rank 死亡"), ("rank_kill", "击杀排行", "/mc rank 击杀生物")),
    (("rank_fish", "钓鱼排行", "/mc rank 钓鱼"), ("check", "账号查询", "/mc check")),
    (("help", "查看帮助", "/mc help"), ("bindhelp", "绑定帮助", "/mc bindhelp")),
)
GUIDE_PAGE2_ROWS = (
    (("add", "添加服务器", "/mc add"), ("edit", "编辑服务器", "/mc edit"),
     ("hide", "隐藏服务器", "/mc hide")),
    (("show", "显示服务器", "/mc show"), ("lastonline_on", "开启上次在线", "/mc lastonline 开"),
     ("lastonline_off", "关闭上次在线", "/mc lastonline 关")),
    (("batchadd", "批量添加", "/mc batchadd"), ("move", "移动排序", "/mc move"),
     ("swap", "交换排序", "/mc swap")),
    (("broadcast", "广播", "/mc broadcast"), ("help", "查看帮助", "/mc help"),
     ("bindhelp", "绑定帮助", "/mc bindhelp")),
)


def _guide_button(button_id, label, data, style=1):
    """QQ 自定义按钮；群聊 type=2 只把 @机器人 + data 填入输入框，仍须用户发送。"""
    if not isinstance(label, str) or not label or len(label) > 10:
        raise OfficialError("命令指引按钮文字无效。")
    if not isinstance(data, str) or not data.startswith("/mc "):
        raise OfficialError("命令指引按钮数据无效。")
    return {
        "id": button_id,
        "render_data": {"label": label, "visited_label": label, "style": style},
        "action": {"type": 2, "permission": {"type": 2}, "data": data,
                   "unsupport_tips": GUIDE_BUTTON_TIPS},
    }


def _guide_rows(page, level):
    if page == 1:
        rows = [list(row) for row in GUIDE_PAGE1_ROWS]
    else:
        if level < 3:
            raise OfficialError("管理指引仅限本群管理员使用。")
        rows = [list(row) for row in GUIDE_PAGE2_ROWS]
    # QQ keyboard.content.rows 的每一项是 {"buttons": [...]}，不是裸按钮数组。
    return [{"buttons": [_guide_button(*button) for button in row]} for row in rows]


def _guide_markdown(page):
    if page == 1:
        return "\n".join([
            "# MCSight · 命令指引",
            "> 点击按钮只会填入命令，发送后执行",
            "",
            "***",
            "",
            "在线状态、TPS、玩家统计、排行榜、账号查询与帮助入口。多服务器时，排行榜需用 -s 指定服务器。",
        ])
    return "\n".join([
        "# MCSight · 管理指引",
        "> 仅群管理员可用，点击按钮填入命令后发送",
        "",
        "***",
        "",
        "服务器维护、批量排序与运行管理。上次在线开关也在这里。",
    ])


def _guide_text(rows):
    lines = ["当前客户端未能加载命令按钮，可直接发送以下命令："]
    for row in rows:
        labels = []
        for button in row.get("buttons", ()):
            label = button.get("render_data", {}).get("label", "")
            data = button.get("action", {}).get("data", "")
            if data:
                labels.append(f"{label} {data}")
        if labels:
            lines.append(" · ".join(labels))
    return "\n".join(lines)[:1800]


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


class _ReplyRecord(dict):
    def __init__(self, ledger, environment, key, values):
        super().__init__(values)
        self._ledger, self._environment, self._key = ledger, environment, key

    def __setitem__(self, name, value):
        super().__setitem__(name, value)
        if name in ("calls", "sequence"):
            self._ledger._update(self._environment, self._key, name, value)


class ReplyLedger:
    """跨热重载/进程重启去重；只保留事件指纹和发送计数，不保留原始身份或正文。"""
    def __init__(self, root=None, clock=time.time, mono=time.monotonic):
        self.root = Path(root) if root is not None else None
        self.clock, self.mono = clock, mono
        self.memory = {}
        self.lock = RLock()

    def _path(self, environment):
        return self.root / environment / "event_receipts.json"

    def _load(self, environment):
        if self.root is None:
            return self.memory.setdefault(environment, {})
        try:
            with self._path(environment).open("rb") as stream:
                raw = stream.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError
            state = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                               parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite")))
            if (not isinstance(state, dict) or set(state) != {"schema_version", "environment_id", "events"}
                    or type(state["schema_version"]) is not int or state["schema_version"] != 1
                    or state["environment_id"] != environment or not isinstance(state["events"], dict)
                    or len(state["events"]) > 4096):
                raise ValueError
            for key, entry in state["events"].items():
                if (not re.fullmatch(r"[0-9a-f]{64}", key) or not isinstance(entry, dict)
                        or set(entry) != {"expires_at", "calls", "sequence"}
                        or type(entry["expires_at"]) not in (int, float)
                        or type(entry["calls"]) is not int or not 0 <= entry["calls"] <= 4
                        or type(entry["sequence"]) is not int or not 0 <= entry["sequence"] <= 4):
                    raise ValueError
            return state["events"]
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            raise OfficialError("官方事件防重记录损坏，已暂停发送；请由管理员处理。") from None

    def _save(self, environment, events):
        if self.root is None:
            self.memory[environment] = events
        else:
            from .official_core import atomic_json
            atomic_json(self._path(environment), {"schema_version": 1, "environment_id": environment,
                                                  "events": events}, limit=2 * 1024 * 1024)

    def _update(self, environment, key, field, value):
        with self.lock:
            events = dict(self._load(environment))
            entry = dict(events.get(key, {}))
            if not entry:
                raise OfficialError("事件防重状态丢失，本次不重复发送。")
            entry[field] = value
            events[key] = entry
            self._save(environment, events)

    def claim(self, scope):
        with self.lock:
            events = dict(self._load(scope.environment_id))
            now = self.clock()
            events = {key: value for key, value in events.items() if value["expires_at"] > now}
            key_data = "\0".join(scope.key + (scope.message_id,)).encode("utf-8")
            key = hashlib.sha256(key_data).hexdigest()
            if key in events or len(events) >= 4096:
                return None
            # 落盘预占在任何回复副作用之前；重启后同一事件保持 consumed，禁止盲重发。
            events[key] = {"expires_at": now + 7 * 86400, "calls": 0, "sequence": 0}
            self._save(scope.environment_id, events)
            return _ReplyRecord(self, scope.environment_id, key,
                                {"expires": self.mono() + 300, "calls": 0, "sequence": 0})

    def health(self, environment):
        try:
            events = self._load(uuid_value(environment))
            retained = sum(value["expires_at"] > self.clock() for value in events.values())
            return {"ready": retained < 4096, "receipts_count": retained, "capacity": 4096,
                    "message": "发送防重记录正常。" if retained < 4096 else "发送防重记录已满，新请求暂停；请安排维护。"}
        except OfficialError as error:
            return {"ready": False, "receipts_count": None, "capacity": 4096, "message": str(error)}


class OfficialSender:
    """直接调用官方 SDK 群接口，无 event.send 拆包或主动回退。

    发送接口缺失则拒绝。SDK 内部重试/限额仍须目标框架部署验收。
    一轮最多 4 个接口调用；本阶段一图(上传+消息)加一条翻页提示。
    """
    def __init__(self, event, scope, record):
        self.scope, self.record = scope, record
        self.revalidate = None
        self.api = None
        self.transport = transport_for(event)
        self.message_attempts=0
        self.last_message_rejected=False
        bot = getattr(event, "bot", None)
        for target in (getattr(bot, "api", None), bot):
            if callable(getattr(target, "post_group_message", None)):
                self.api = target
                break

    def supports_images(self):
        if self.transport is not None:
            return self.transport.supports_images
        return self.api is not None and callable(getattr(self.api, "post_group_base64file", None))

    async def _call(self, method, **values):
        if self.revalidate is not None:
            self.revalidate()
        if self.transport is None and (self.api is None or not callable(getattr(self.api, method, None))):
            raise OfficialError("当前适配器未提供所需官方群发送接口。")
        if self.record["calls"] >= 4:
            raise OfficialError("本次回复已达发送预算，请使用新消息查询。")
        if time.monotonic() >= self.record["expires"]:
            raise OfficialError("本次被动回复窗口已结束，请发送新消息。")
        callback = None
        if self.transport is None:
            callback = getattr(self.api, method)
            try:
                inspect.signature(callback).bind(group_openid=self.scope.group_id, **values)
            except (ValueError, TypeError):
                raise OfficialError("当前官方群发送接口缺少必要参数，请核对适配器版本。") from None
        self.record["calls"] += 1
        if method=="post_group_message": self.message_attempts+=1
        try:
            if self.transport is not None:
                result = self.transport.call(method, group_openid=self.scope.group_id,
                                             revalidate=self.revalidate, **values)
            else:
                result = callback(group_openid=self.scope.group_id, **values)
            if not inspect.isawaitable(result):
                raise OfficialError("官方群发送接口不符合异步协议。")
            result = await asyncio.wait_for(result, timeout=20)
            checked_response(200,result)
            if method == "post_group_message" and not isinstance(result.get("id"), str):
                raise OfficialError("官方群消息缺少回执，结果待确认；本次不重发。")
            return result
        except OfficialApiError as error:
            # An explicit rejection proves this message was not accepted, so a
            # plain-text fallback cannot duplicate a visible reply.
            if method == "post_group_message" and error.rejected:
                self.last_message_rejected = True
            raise
        except OfficialError:
            raise
        except Exception:
            raise OfficialError("官方群消息发送结果待确认，请用新消息查询；本次不重发。") from None

    def _sequence(self):
        self.record["sequence"] += 1
        return self.record["sequence"]

    def can_fallback_text(self):
        # An upload has srv_send_msg=false. A message POST is only safe to
        # replace when the platform explicitly rejected that single attempt.
        no_visible_message = (self.message_attempts==0
                              or (self.message_attempts==1 and self.last_message_rejected))
        return no_visible_message and self.record["calls"]<4 and time.monotonic()<self.record["expires"]

    async def recall(self, message_id):
        """尽力撤回自己刚发的群消息；失败不重试，也不向群里补发错误。"""
        if not isinstance(message_id, str) or not message_id:
            return False
        if self.revalidate is not None:
            try:
                self.revalidate()
            except Exception:
                return False
        if self.record["calls"] >= 4 or time.monotonic() >= self.record["expires"]:
            return False
        self.record["calls"] += 1
        try:
            if self.transport is not None:
                result = self.transport.call("delete_group_message",
                                             group_openid=self.scope.group_id,
                                             revalidate=self.revalidate,
                                             message_id=message_id)
            else:
                callback = getattr(self.api, "delete_group_message", None)
                if not callable(callback):
                    return False
                result = callback(group_openid=self.scope.group_id, message_id=message_id)
            if not inspect.isawaitable(result):
                return False
            result = await asyncio.wait_for(result, timeout=20)
            checked_response(200, result)
            return True
        except Exception:
            return False

    async def text(self, value):
        return await self._call("post_group_message", content=value[:1800], msg_type=0,
                                msg_id=self.scope.message_id, msg_seq=self._sequence())

    async def markdown(self, content, rows):
        """Markdown + 自定义按钮；content 必须为空字段，不能同时发 content 文本。"""
        return await self._call("post_group_message", msg_type=2,
                                markdown={"content": content[:4000]},
                                keyboard={"content": {"rows": rows}},
                                msg_id=self.scope.message_id, msg_seq=self._sequence())

    async def image(self, payload):
        if len(payload) > 4 * 1024 * 1024:
            raise OfficialError("图片超过发送限制。")
        if not self.supports_images():
            raise OfficialError("当前官方适配器只支持 HTTPS 图片链接；为保护玩家数据，本插件不上传个人图床，改发分页文字。")
        encoded = base64.b64encode(payload).decode("ascii")
        if len(encoded) > 4 * 1024 * 1024:
            raise OfficialError("图片编码体积超过上限，请缩小查询范围。")
        uploaded = await self._call("post_group_base64file", file_type=1,
                                    file_data=encoded, file_name="mcsight.png",srv_send_msg=False)
        info = uploaded.get("file_info")
        if not isinstance(info, str) or not info:
            raise OfficialError("官方群图片上传未得到文件引用。")
        return await self._call("post_group_message", msg_type=7, media={"file_info": info},
                                msg_id=self.scope.message_id, msg_seq=self._sequence())


class OfficialRuntime:
    def __init__(self, root, activation, *, queries=None, renderer=render_pages, sender=OfficialSender,
                 recovery_gate=None, mod_token=None, policy_defaults=None, center_defaults=None,
                 access_lists=None):
        # 官方与 OneBot 共用全局 mod_api_token、权限名单；这里只注入取值函数，不在官方目录另存凭据。
        self.store = OfficialStore(root, token_provider=mod_token, policy_defaults=policy_defaults,
                                   access_provider=access_lists)
        self.activation = activation
        self.stats = OfficialStats(self.store.root)
        self.queries = queries if queries is not None else QueryService(history=self.stats)
        self.renderer = renderer
        self.sender = sender
        self.pages = PageCache()
        self.replies = ReplyLedger(self.store.root)
        self.busy = set()
        self.last_queries = OrderedDict()
        self.closed = False
        self.tasks = set()
        # 测试可调小；生产固定 10 秒，仍在 QQ 官方 2 分钟撤回窗口内。
        self.sid_recall_delay = SID_RECALL_DELAY_SECONDS
        self.sampling = False
        self.last_sample = None
        self.help_image = None
        self.bind_help_image = None
        self.binding_host = OfficialBindingHost(self.store, lambda: None if self.closed else self.selection(),
                                                center_defaults=center_defaults)
        self.binding_commands = OfficialBindingCommands(self.binding_host)
        self.write_commands=OfficialWriteCommands(self.store,lambda:None if self.closed else self.selection())
        self.members=OfficialMembers()
        self.recovery = OfficialFollowupRecovery(self.binding_host, gate=recovery_gate)
        self.binding_admin = OfficialBindingAdmin(self.binding_host, self.recovery,
            authorize=lambda environment: self.environment(environment, active=True))
        self.recovery_task = None
        self.last_recovery = None

    def selection(self):
        state = self.activation.status()
        return state.get("selection") if state.get("state") in ("active", "not_ready", "configuring") else None

    def environment(self, environment, *, active=False):
        uuid_value(environment)
        state = self.activation.status()
        if not any(e["environment_id"] == environment and e["platform"] == "qq_official"
                   for e in state.get("environments", [])):
            raise OfficialError("未登记该官方环境。")
        if active:
            selected = self.selection() or {}
            if selected.get("platform") != "qq_official" or selected.get("environment_id") != environment:
                raise OfficialError("只有当前运行的官方环境可以发起 MC 查询。")
        if self.closed:
            raise OfficialError("插件正在卸载，请稍后重试。")
        return environment

    def state(self, environment):
        self.environment(environment)
        data = self.store.list_state(environment)
        remarks = self.store.server_remarks(environment)
        for group in data["groups"]:
            for server in group["servers"]:
                remark = remarks.get(server["server_id"]) or {
                    "remark_src": "manual", "remark_motd": "", "remark_motd_at": 0}
                server["remark_src"] = remark["remark_src"]
                server["remark_motd"] = remark["remark_motd"]
                server["remark_motd_at"] = remark["remark_motd_at"]
                server["remark_display"] = (remark["remark_motd"]
                    if remark["remark_src"] == "motd" and remark["remark_motd"]
                    else server["remark"])
                server["remark_runs"] = (remark.get("remark_motd_runs")
                    if remark["remark_src"] == "motd" and remark.get("remark_motd_runs")
                    else None)
        data["has_mod_token"] = self.store.token_is_set(environment)
        data["console_token_ids"] = self.store.console_token_ids(environment)
        selected = self.selection() or {}
        data["active"] = selected.get("platform") == "qq_official" and selected.get("environment_id") == environment
        data["binding_ready"] = None
        data["binding_readiness"]="per_request_handshake"
        data["binding_recovery"] = self.last_recovery
        data["message_delivery"] = self.replies.health(environment)
        data["statistics_sampling"] = self.last_sample
        # 普通配置页面仅返回审计计数；审计明细由用户本地管理，不额外扩大暴露范围。
        data["authorization_history_count"] = len(data.pop("authorization_history"))
        return data

    def scope(self, event):
        if self.closed or not self.activation.accepts(event):
            return None
        return event_scope(event, self.selection())

    def binding_service_for(self, event, *, center, peer):
        """Construct the center workflow from an accepted event and explicit host dependencies."""
        scope = self.scope(event)
        if scope is None:
            raise BindingError("UNAUTHORIZED", "当前消息不属于生效的官方群。")
        policy = OfficialBindingPolicy(self.store, lambda: None if self.closed else self.selection(), scope, center)
        return OfficialBindingService(center, peer, policy_guard=policy.guard)

    async def handle(self, event):
        task = asyncio.current_task()
        if task is not None:
            self.tasks.add(task)
        try:
            await self._handle(event)
        finally:
            if task is not None:
                self.tasks.discard(task)

    async def _handle(self, event):
        scope = self.scope(event)
        if scope is None:
            return
        record = self.replies.claim(scope)
        if record is None:
            return
        sender = self.sender(event, scope, record)
        try:
            group = self.store.group(scope.environment_id, group_id=scope.group_id)
            environment_admins = self.store.environment_administrators(scope.environment_id)
            page_revision = (group["revision"], tuple(sorted(environment_admins))) if group is not None else None
            if group is not None and self.store.access_level(scope, group, environment_admins) == 0:
                await sender.text("您已被列入本群插件黑名单，无法使用本插件。")
                return
            if group is not None:self.members.observe(scope)
            raw = event.message_str.strip().lstrip("/")
            split = raw.split(maxsplit=1)
            tail = split[1].strip() if len(split) == 2 else "help"
            parsed = tail.split(maxsplit=1)
            command = parsed[0].lower() if parsed else "help"
            command={"say":"broadcast","remove":"del"}.get(command,command)
            arguments = parsed[1] if len(parsed) > 1 else ""
            # 与 OneBot 同一套防抖窗口：按用户 + 命令分档；超级管理员不受限。
            level = self.store.access_level(scope, group, environment_admins) if group is not None else 0
            if level < 4 and command != "setup":
                debounce_cmd = {"broadcast":"say","del":"remove","console":"exec"}.get(command, command)
                allowed, remaining = debounce_gate(scope.key, scope.user_id, debounce_cmd)
                if not allowed:
                    if debounce_notify(scope.key, scope.user_id, debounce_cmd):
                        await sender.text(f"该命令 {debounce_window(debounce_cmd):.0f} 秒内只能"
                                          f"使用一次，请 {remaining} 秒后再试。")
                    return
            if command == "setup":
                code = arguments.split(maxsplit=1)[0] if arguments.strip() else ""
                if not code:
                    await sender.text("请在后台「新增群」中生成验证码，然后在群内发送 /mc setup 验证码。")
                    return
                try:
                    created_group, created = self.store.claim_setup_code(
                        scope.environment_id, code, scope.group_id)
                except OfficialError as error:
                    await sender.text(str(error))
                    return
                name = created_group.get("alias") or created_group.get("display_group") or "新群"
                if created:
                    await sender.text(f"已创建本群配置「{name}」。"
                                      "请后台管理员添加服务器并启用本群；群主和管理员可直接使用管理命令。")
                else:
                    await sender.text(f"本群已配置「{name}」，无需重复登记。")
                return
            if command == "bindhelp":
                # 官方群使用独立绑定帮助图；注入的测试渲染器仍走文本分支。
                if self._supports_images(sender) and self.renderer is render_pages:
                    if self.bind_help_image is None:
                        try:
                            self.bind_help_image = await asyncio.to_thread(
                                render_official_help_image, "bind")
                        except Exception:
                            self.bind_help_image = None
                    if self.bind_help_image is not None:
                        try:
                            await sender.image(self.bind_help_image)
                            return
                        except OfficialError:
                            # 图片消息结果不明时不重发文字，避免重复可见回复。
                            if isinstance(sender, OfficialSender) and not sender.can_fallback_text():
                                raise
                await sender.text(BIND_HELP)
                return
            if command == "help":
                def help_revalidate():
                    self.environment(scope.environment_id, active=True)
                    latest = self.store.group(scope.environment_id, group_id=scope.group_id)
                    if latest is not None and self.store.access_level(scope, latest) == 0:
                        raise OfficialError("当前身份已被禁止使用本群插件。")
                if self._supports_images(sender):
                    if self.help_image is None:
                        try:
                            if self.renderer is render_pages:
                                # 官方群使用独立帮助图，包含 setup / operation 等官方命令。
                                self.help_image = await asyncio.to_thread(
                                    render_official_help_image, "help")
                            else:
                                help_pages = await asyncio.to_thread(self.renderer, "MCSight 官方命令帮助", HELP.splitlines()[1:])
                                if len(help_pages) == 1 and len(help_pages[0]) <= 3 * 1024 * 1024:
                                    self.help_image = help_pages[0]
                        except Exception:
                            pass  # No outgoing request yet; the full help text is available.
                    if self.help_image is not None:
                        help_revalidate()
                        if isinstance(sender, OfficialSender):
                            sender.revalidate = help_revalidate
                        try:
                            await sender.image(self.help_image)
                            return
                        except OfficialError:
                            if record["calls"] and not (isinstance(sender,OfficialSender) and sender.can_fallback_text()):
                                raise
                help_revalidate()
                if isinstance(sender, OfficialSender):
                    sender.revalidate = help_revalidate
                await sender.text(HELP)
                return
            if command in ("guide", "命令指引", "指引", "管理指引"):
                # “管理指引”本身即管理页入口，不允许通过参数退回第 1 页。
                await self._guide_command(scope, sender,
                                          "2" if command == "管理指引" else arguments,
                                          group, page_revision[1])
                return
            if command == "sid":
                # 引导用命令：不要求群已启用或已有超管，回复后自动撤回。
                await self._sid_command(scope, sender)
                return
            if group is None:
                await sender.text("本官方群尚未启用。请在后台「新增群」生成验证码，"
                                  "再在群内发送 /mc setup 验证码。")
                return
            if not group["enabled"]:
                await sender.text("本群功能已关闭，请在后台「群与服务器 → 本群规则」中启用本群功能。")
                return
            if command in BINDING_COMMANDS:
                await self._binding_command(scope, sender, command, arguments, group, page_revision)
                return
            if command in ("bindcheck","checknick"):
                await self._member_command(scope,sender,command,arguments,group,page_revision)
                return
            if command in WRITE_COMMANDS:
                minimum_permission = 3 if self.store.access_level(scope, group, environment_admins) >= 3 else 1
                title,lines=await asyncio.to_thread(self.write_commands.execute,scope,command,arguments)
                latest=self.store.group(scope.environment_id,group_id=scope.group_id)
                if latest is None:
                    raise OfficialError("当前官方群配置已撤销，请重新查询。")
                admins=self.store.environment_administrators(scope.environment_id)
                result_revision=(latest["revision"],tuple(sorted(admins)))
                def write_revalidate():
                    self.environment(scope.environment_id,active=True)
                    current=self.store.group(scope.environment_id,group_id=scope.group_id)
                    admins=self.store.environment_administrators(scope.environment_id)
                    if (current is None or not current["enabled"]
                            or self.store.access_level(scope,current,admins)<minimum_permission
                            or current["revision"]!=result_revision[0]
                            or tuple(sorted(admins))!=result_revision[1]):
                        raise OfficialError("当前身份已不能接收操作结果。")
                write_revalidate()
                if isinstance(sender,OfficialSender): sender.revalidate=write_revalidate
                pages=render_text_pages(title,lines)
                self.pages.put(scope,result_revision,[],pages,minimum_permission=minimum_permission)
                await sender.text(pages[0])
                if len(pages)>1:await sender.text("继续查看：/mc page 2（10分钟内有效）")
                return
            if command in ("whitelist", "blacklist"):
                await self._access_list_command(scope, sender, command, arguments, group, page_revision)
                return
            if command == "token":
                await self._token_command(scope, sender, arguments, group, page_revision)
                return
            if command == "list":
                if not group["servers"]:
                    await sender.text("当前没有任何服务器。")
                    return
                lines = ["已添加的服务器："]
                for entry in group["servers"]:
                    port = (f" (模组端口: {entry['api_port']})" if entry["api_port"] is not None
                            else " (未装模组)")
                    hidden = " （隐藏）" if entry["hidden"] else ""
                    lines.append(f"{entry['name']} -> {entry['host']}{port}{hidden}")
                await sender.text("\n".join(lines))
                return
            if command not in ("status", "tps", "stats", "rank", "page"):
                await sender.text(f"未知子命令: {command}，使用 /mc help 查看帮助。")
                return
            if command == "page":
                try:
                    index = int(arguments.strip())
                except ValueError:
                    raise OfficialError("用法：/mc page <页码>") from None
            else:
                self.environment(scope.environment_id, active=True)
                token = self.store.runtime_token(scope.environment_id) if any(s["api_port"] is not None and not s["hidden"] for s in group["servers"]) else ""
                query_group = dict(group, environment_id=scope.environment_id)
                if command=="rank":
                    try:
                        games=await asyncio.to_thread(self.binding_commands.owned_games,scope,arguments)
                        query_group["self_games"]=games
                    except Exception:
                        query_group["self_games"]=None
                        query_group["self_games_note"]="本人绑定暂不可读取；本页继续展示公开排行。"
                document = await asyncio.wait_for(self.queries.query(command, arguments, query_group, token), timeout=45)
                title, lines = document
                self.environment(scope.environment_id, active=True)
                text_pages = render_text_pages(title, lines)
                pages = []
                if self._supports_images(sender):
                    try:
                        if self.renderer is render_pages and isinstance(document, StatisticsQuery):
                            pages = await render_statistics_pages_async(document)
                        elif self.renderer is render_pages and isinstance(document, QueryDocument):
                            # 与 OneBot 共用同一套状态 / TPS / 排行生成器
                            pages = await render_official_pages(document, group.get("policy"))
                        else:
                            pages = await asyncio.to_thread(self.renderer, title, lines)
                    except Exception:
                        pages = []
                self.environment(scope.environment_id, active=True)
                self.pages.put(scope, page_revision, pages, text_pages)
                index = 1
            # 请求期间的停用、权限撤销或修订变化不能继续发送旧结果。
            self.environment(scope.environment_id, active=True)
            def revalidate():
                self.environment(scope.environment_id, active=True)
                latest = self.store.group(scope.environment_id, group_id=scope.group_id)
                admins = self.store.environment_administrators(scope.environment_id)
                if (latest is None or not latest["enabled"]
                        or self.store.access_level(scope, latest, admins) == 0
                        or latest["revision"] != group["revision"]
                        or tuple(sorted(admins)) != page_revision[1]):
                    raise OfficialError("查询期间配置或权限已变化，请重新查询。")
                self.pages.authorize(scope, page_revision, self.store.access_level(scope, latest, admins))
            revalidate()
            # 媒体上传有等待窗口，消息发布前仍须检查；假发送器无需网络接口。
            image_output = self._supports_images(sender)
            if isinstance(sender, OfficialSender):
                sender.revalidate = revalidate
            if image_output:
                try:
                    payload, count = self.pages.get(scope, page_revision, index)
                except OfficialError:
                    image_output = False
            if image_output:
                try:
                    await sender.image(payload)
                except OfficialError:
                    if record["calls"] and not (isinstance(sender,OfficialSender) and sender.can_fallback_text()):
                        raise
                    image_output = False
            if not image_output:
                payload, count = self.pages.get_text(scope, page_revision, index)
                await sender.text(payload)
            if count > 1:
                following = index + 1 if index < count else 1
                if image_output:
                    await sender.text(f"第 {index}/{count} 页。继续查看：/mc page {following}（10 分钟内有效）")
                else:
                    await sender.text(f"继续查看：/mc page {following}（10 分钟内有效）")
        except OfficialError as error:
            # 已发出请求但结果不明时，禁止通过异常兜底重复发消息。
            if (record["calls"]==0 or isinstance(sender,OfficialSender) and sender.can_fallback_text()) and not self.closed:
                if isinstance(sender, OfficialSender):
                    sender.revalidate = None
                try:
                    await sender.text(str(error))
                except OfficialError:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception:
            if (record["calls"]==0 or isinstance(sender,OfficialSender) and sender.can_fallback_text()) and not self.closed:
                try:
                    await sender.text("官方群查询暂不可用，请稍后重试。")
                except OfficialError:
                    pass

    def _access_mutex(self):
        lock = getattr(self, "_access_mutex_obj", None)
        if lock is None:
            lock = asyncio.Lock()
            self._access_mutex_obj = lock
        return lock

    async def _send_access_lists(self, sender, scope, keys):
        access = self.store.access_lists(scope.environment_id)
        lines = ["当前权限名单（公共 + 本环境补充，已脱敏）："]
        for key in keys:
            lines.append(f"{ACCESS_LABELS[key]}：" + _access_value_summary(access.get(key, ())))
        lines.append("官方成员标识按群精确匹配，换群后需重新 @成员 添加；增删请 @ 成员操作。")
        await sender.text("\n".join(lines))

    async def _guide_command(self, scope, sender, arguments, group, environment_admins):
        """官方群专用 Markdown + 按钮菜单；群聊点击只填入命令，仍需用户发送。"""
        text = arguments.strip()
        try:
            page = int(text or "1")
        except ValueError:
            await sender.text("用法：/命令指引 或 /管理指引；也可发送 /mc guide [1|2]")
            return
        if page not in (1, 2):
            await sender.text("用法：/命令指引（常用）或 /管理指引（管理）；也可发送 /mc guide 1|2")
            return
        level = (self.store.access_level(scope, group, environment_admins)
                 if group is not None else 0)
        if page == 2:
            if group is None:
                await sender.text("本官方群尚未启用。请先完成 /mc setup 登记并由后台启用本群。")
                return
            if not group["enabled"]:
                await sender.text("本群功能已关闭，请在后台「群与服务器 → 本群规则」中启用本群功能。")
                return
            if level < 3:
                await sender.text("权限不足：管理指引仅限本群管理员使用。")
                return
        rows = _guide_rows(page, level)
        markdown = _guide_markdown(page)

        def guide_revalidate():
            self.environment(scope.environment_id, active=True)
            latest = self.store.group(scope.environment_id, group_id=scope.group_id)
            admins = self.store.environment_administrators(scope.environment_id)
            current = (self.store.access_level(scope, latest, admins)
                       if latest is not None else 0)
            if latest is not None and current == 0:
                raise OfficialError("当前身份已被禁止使用本群插件。")
            if page == 2 and (latest is None or not latest["enabled"] or current < 3
                              or latest["revision"] != group["revision"]
                              or tuple(sorted(admins)) != environment_admins):
                raise OfficialError("当前群管理权限或配置已变化，请重新发送 /mc guide 2。")
        guide_revalidate()
        if isinstance(sender, OfficialSender):
            sender.revalidate = guide_revalidate
        try:
            await sender.markdown(markdown, rows)
        except OfficialApiError as error:
            # Only an explicit rejection proves Markdown was not delivered; an
            # unknown/pending result must never post a duplicate text reply.
            if (not error.rejected or not isinstance(sender, OfficialSender)
                    or not sender.can_fallback_text()):
                raise
            await sender.text(_guide_text(rows))

    async def _sid_command(self, scope, sender):
        """只把发送者自己的 member_openid 发给本人，并在短时间后自动撤回。"""
        message = ("你的官方成员标识（member_openid）：\n"
                   f"{scope.user_id}\n"
                   f"{self.sid_recall_delay:g} 秒后自动撤回；仅用于本插件名单配置，请勿转发。")
        result = await sender.text(message)
        message_id = result.get("id") if isinstance(result, dict) else None
        recall = getattr(sender, "recall", None)
        if isinstance(message_id, str) and message_id and callable(recall):
            self._schedule_sid_recall(recall, message_id)

    def _schedule_sid_recall(self, recall, message_id):
        """延迟撤回；失败静默，避免把撤回错误再发进群。"""
        async def run():
            try:
                await asyncio.sleep(max(0, self.sid_recall_delay))
                await recall(message_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
        task = asyncio.create_task(run())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _access_list_command(self, scope, sender, command, arguments, group, page_revision):
        """官方群专属：按 @成员 维护本环境补充名单，不读写 OneBot 公共名单。"""
        if self.store.access_level(scope, group, page_revision[1]) < 4:
            await sender.text("权限不足：该命令仅限超级管理员使用。")
            return
        words = arguments.split()
        first = words[0].lower() if words else ""
        if command == "blacklist":
            if not words:
                await sender.text("用法：/mc blacklist list；/mc blacklist add|remove @成员")
                return
            if first in ("list", "列表"):
                await self._send_access_lists(sender, scope, ("blacklist",))
                return
            key, action_word = "blacklist", first
        else:
            if not words:
                await sender.text(ACCESS_LIST_HELP)
                return
            if first in ("list", "列表"):
                await self._send_access_lists(sender, scope, ("super_admins", "whitelist_admins", "blacklist"))
                return
            if first == "super":
                if len(words) < 2:
                    await sender.text(ACCESS_LIST_HELP)
                    return
                key, action_word = "super_admins", words[1].lower()
            else:
                key, action_word = "whitelist_admins", first
        action = ACCESS_ACTIONS.get(action_word)
        if action is None:
            await sender.text(ACCESS_LIST_HELP)
            return
        if len(scope.mentions) != 1:
            await sender.text("请 @ 唯一一位成员来指定目标；为避免泄露，不接受直接输入官方标识。")
            return
        target = scope.mentions[0]
        if action == "add" and key == "blacklist" and target == scope.user_id:
            await sender.text("不能把自己加入黑名单。")
            return
        if action == "remove" and key == "super_admins" and target == scope.user_id:
            await sender.text("不能把自己从超级管理员名单移除。")
            return
        label = ACCESS_LABELS[key]
        who = _member_label(dict(scope.mention_nicknames).get(target))
        is_environment_admin = target in set(page_revision[1])
        async with self._access_mutex():
            in_official, in_public = self.store.access_membership(scope.environment_id, key, target)
            if action == "add":
                in_blacklist = False
                if key != "blacklist":
                    in_blacklist = any(self.store.access_membership(
                        scope.environment_id, "blacklist", target))
                if in_official:
                    message = f"{who}已在{label}名单中。"
                elif in_public:
                    message = f"{who}已在公共{label}名单中（由 WebUI 管理），无需重复添加。"
                elif in_blacklist:
                    message = f"{who}当前在黑名单中，黑名单优先；请先移除黑名单。"
                elif is_environment_admin and key == "super_admins":
                    message = f"{who}已是本环境管理员，权限已高于{label}名单。"
                else:
                    self.store.update_official_access(scope.environment_id, key, target, True)
                    message = f"✅ 已添加{who}到{label}名单（仅当前官方群生效）。"
            else:
                if in_official:
                    self.store.update_official_access(scope.environment_id, key, target, False)
                    message = f"✅ 已从{label}名单移除{who}。"
                    if in_public:
                        message += f"\n注意：{who}仍在 WebUI 公共名单中，权限不会降低；请在 WebUI 修改。"
                    elif key == "super_admins" and is_environment_admin:
                        message += "\n注意：该成员仍是本环境管理员，权限不会降低。"
                elif in_public:
                    message = f"{who}来自 WebUI 公共名单，官方群命令不能移除；请在 WebUI 修改。"
                else:
                    message = f"{who}不在{label}名单中。"
        await sender.text(message)

    async def _token_command(self, scope, sender, arguments, group, page_revision):
        """与 OneBot /mc token 同一套语法与校验：每服 console token，仅超管可改。"""
        def guard():
            self.environment(scope.environment_id, active=True)
            latest = self.store.group(scope.environment_id, group_id=scope.group_id)
            admins = self.store.environment_administrators(scope.environment_id)
            if (latest is None or not latest["enabled"] or latest["revision"] != group["revision"]
                    or tuple(sorted(admins)) != page_revision[1]
                    or self.store.access_level(scope, latest, admins) < 4):
                raise OfficialError("当前群管理权限或配置已变化，停止修改命令 Token。")
        guard()
        words = arguments.split()
        if len(words) < 2:
            await sender.text("用法:\n"
                              "/mc token <服务器名> <Token>   为该服设置命令 Token\n"
                              "/mc token <服务器名> 清除      关闭该服的 /执行")
            return
        name, value = words[0], words[1]
        target = next((s for s in group["servers"] if s["name"] == name), None)
        if target is None:
            await sender.text(f"未找到名为「{name}」的服务器。")
            return
        clearing = value in TOKEN_CLEAR_WORDS or value.lower() in TOKEN_CLEAR_WORDS
        token = "" if clearing else value
        if not clearing:
            if len(token) < TOKEN_MIN_CHARS:
                await sender.text(f"❌ 设置失败：Token 太短（至少 {TOKEN_MIN_CHARS} 个字符），当前 {len(token)} 个")
                return
            if len(token) > TOKEN_MAX_CHARS:
                await sender.text(f"❌ 设置失败：Token 太长（最多 {TOKEN_MAX_CHARS} 个字符），当前 {len(token)} 个")
                return
            if any(ord(c) < 0x21 or ord(c) > 0x7e for c in token):
                await sender.text("❌ 设置失败：Token 只能由不含空格的可见字符组成")
                return
        guard()
        try:
            self.store.save_console_token(scope.environment_id, group["config_id"],
                                          target["server_id"], group["revision"], token)
        except OfficialError as error:
            await sender.text(f"❌ 设置失败：{error}")
            return
        guard()
        if isinstance(sender, OfficialSender):
            sender.revalidate = guard
        if clearing:
            await sender.text(f"✅ 已清除「{name}」的命令 Token，该服务器的 /执行 已关闭。")
            return
        reply = (f"✅ 已为「{name}」设置命令 Token（{len(token)} 个字符）。\n"
                 f"需与该服务器模组的 consoleToken 完全一致；模组侧改动要重启服务器才生效。")
        if target["api_port"] is None:
            reply += f"\n⚠️ 该服务器还没有配置 API 端口，先用 /mc edit {name} port <端口> 补上。"
        await sender.text(reply)

    async def _binding_command(self, scope, sender, command, arguments, group, page_revision):
        minimum_permission = 3 if self.store.access_level(scope, group, page_revision[1]) >= 3 else 1
        if scope.key in self.busy or len(self.busy) >= 16:
            raise OfficialError("上一条请求仍在处理中，请稍后查询原操作。")
        self.busy.add(scope.key)
        try:
            title, lines = await asyncio.to_thread(self.binding_commands.execute, scope, command, arguments)
        finally:
            self.busy.discard(scope.key)

        def revalidate():
            self.environment(scope.environment_id, active=True)
            latest = self.store.group(scope.environment_id, group_id=scope.group_id)
            admins = self.store.environment_administrators(scope.environment_id)
            if (latest is None or not latest["enabled"] or latest["revision"] != group["revision"]
                    or self.store.access_level(scope, latest, admins) < minimum_permission
                    or tuple(sorted(admins)) != page_revision[1]):
                raise OfficialError("请求期间配置或权限已变化，请用新消息查询原操作。")
        revalidate()
        if isinstance(sender, OfficialSender):
            sender.revalidate = revalidate
        pages = render_text_pages(title, lines)
        self.pages.put(scope, page_revision, [], pages, minimum_permission=minimum_permission)
        payload, count = self.pages.get_text(scope, page_revision, 1)
        await sender.text(payload)
        if count > 1:
            await sender.text("继续查看：/mc page 2（10 分钟内有效）")

    async def _member_command(self,scope,sender,command,arguments,group,page_revision):
        def guard():
            self.environment(scope.environment_id,active=True)
            current=self.store.group(scope.environment_id,group_id=scope.group_id)
            admins=self.store.environment_administrators(scope.environment_id)
            if (current is None or not current["enabled"] or current["revision"]!=group["revision"]
                    or tuple(sorted(admins))!=page_revision[1]
                    or self.store.access_level(scope,current,admins)<3):
                raise OfficialError("当前群管理权限或配置已变化，停止成员扫描。")
        guard()
        if command=="checknick" and arguments.strip():raise OfficialError("格式检测用 /mc checknick；开关和忽略名单用 /mc nicknamecheck。")
        if command=="checknick" and not group["policy"]["nickname_check_enabled"]:raise OfficialError("本群昵称检测已关闭。")
        if scope.key in self.busy or len(self.busy)>=16:raise OfficialError("当前请求较多，请稍后重试。")
        self.busy.add(scope.key)
        try:
            # 与 OneBot 相同的进度提示；扫描可能持续数十秒。
            try:
                await sender.text("🔍 正在检测群成员昵称，成员较多时可能需要几秒，请稍候..."
                                  if command == "checknick" else
                                  "🔍 正在比对群成员与绑定记录，成员较多时可能需要几秒，请稍候...")
            except OfficialError:
                pass
            roster=await self.members.roster(scope,group["revision"],getattr(sender,"transport",None),guard)
            unbound=member_total=None
            if command=="bindcheck":
                name,active=await asyncio.to_thread(self.binding_commands.active_user_ids,scope,arguments)
                title,lines=self.members.binding_audit(roster,name,active)
                unbound,member_total=self.members.unbound_members(roster,active)
            else:title,lines=self.members.nickname_audit(scope,roster,group)
            guard()
            text_pages=render_text_pages(title,lines)
            image_pages=[]
            # 与 OneBot /绑定检测 共用同一份名单图；注入的测试渲染器仍走文本分支。
            if (command=="bindcheck" and unbound is not None and self._supports_images(sender)
                    and self.renderer is render_pages):
                if not unbound:
                    if isinstance(sender,OfficialSender):sender.revalidate=guard
                    await sender.text(f"✅ 检测完成！本群 {member_total} 位成员均已绑定游戏账号。")
                    return
                try:
                    info={"group_total":member_total,"unbound_total":len(unbound),
                          "stamp":time.strftime("检测于 %H:%M:%S")}
                    image_pages=await render_unbound_pages(unbound,info)
                except Exception:
                    image_pages=[]
            self.pages.put(scope,page_revision,image_pages,text_pages,minimum_permission=3)
            if isinstance(sender,OfficialSender):sender.revalidate=guard
            image_output=bool(image_pages)
            if image_output:
                try:
                    payload,count=self.pages.get(scope,page_revision,1)
                    await sender.image(payload)
                except OfficialError:
                    # 上传失败尚未发出消息可安全改用文字；消息 POST 结果不明时不重发。
                    if isinstance(sender,OfficialSender) and not sender.can_fallback_text():
                        raise
                    image_output=False
            if not image_output:
                payload,count=self.pages.get_text(scope,page_revision,1)
                await sender.text(payload)
            if count>1:
                if image_output:
                    await sender.text(f"第 1/{count} 页。继续查看：/mc page 2（10 分钟内有效）")
                else:
                    await sender.text("继续查看：/mc page 2（10分钟内有效）")
        finally:self.busy.discard(scope.key)

    def start_recovery(self):
        if (self.closed or (self.selection() or {}).get("platform") != "qq_official"
                or self.recovery_task is not None and not self.recovery_task.done()):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self.recovery_task = loop.create_task(self._recovery_loop())
        self.tasks.add(self.recovery_task)
        self.recovery_task.add_done_callback(self.tasks.discard)

    async def _recovery_loop(self):
        while not self.closed:
            selected = self.selection() or {}
            if selected.get("platform") == "qq_official":
                self.last_recovery = await asyncio.to_thread(self.recovery.run_once)
            await asyncio.sleep(5)

    async def sample_once(self):
        """Called by the existing single daily sampler, only for the active official source."""
        selected = self.selection() or {}
        if self.closed or self.sampling or selected.get("platform") != "qq_official":
            return
        environment = selected["environment_id"]
        self.environment(environment, active=True)
        self.sampling = True
        task = asyncio.current_task()
        if task is not None:
            self.tasks.add(task)
        counts = {"sampled": 0, "failed": 0, "skipped": 0}
        started = time.monotonic()
        try:
            roster = await asyncio.to_thread(self.stats.roster, environment)
            if not roster:
                self.last_sample = dict(counts)
                return
            token = None
            for group_id, server_id, player in roster:
                self.environment(environment, active=True)
                if time.monotonic() - started >= 480:
                    counts["skipped"] += 1
                    continue
                group = self.store.group(environment, group_id=group_id)
                server = next((s for s in group["servers"] if s["server_id"] == server_id), None) if group else None
                if not group or not group["enabled"] or server is None or server["hidden"] or server["api_port"] is None:
                    counts["skipped"] += 1
                    continue
                try:
                    if token is None:
                        token = self.store.runtime_token(environment)
                    data = await asyncio.wait_for(self.queries.mod(server, token, "/api/stats/" + player), timeout=8)
                    self.environment(environment, active=True)
                    latest = self.store.group(environment, group_id=group_id)
                    if latest is None or latest["revision"] != group["revision"] or not latest["enabled"]:
                        counts["skipped"] += 1
                        continue
                    await asyncio.to_thread(self.stats.record, environment, group_id, server_id, player, data)
                    counts["sampled"] += 1
                except (OfficialError, asyncio.TimeoutError):
                    counts["failed"] += 1
                await asyncio.sleep(0.3)
            self.last_sample = dict(counts)
        except OfficialError:
            self.last_sample = dict(counts, unavailable=True)
        finally:
            self.sampling = False
            if task is not None:
                self.tasks.discard(task)

    def close(self):
        self.closed = True
        self.binding_admin.close()
        self.store.close()
        self.pages.clear()
        self.help_image = None
        self.bind_help_image = None
        self.members.close()
        try:
            current = asyncio.current_task()
        except RuntimeError:
            current = None
        for task in tuple(self.tasks):
            if task is not current:
                task.cancel()

    @staticmethod
    def _supports_images(sender):
        check = getattr(sender, "supports_images", None)
        return bool(check()) if callable(check) else True


_runtime = None
_recovery_gate = Lock()


def configure(root, activation, *, owner=None, mod_token=None, policy_defaults=None, center_defaults=None,
              access_lists=None):
    global _runtime
    if _runtime is not None:
        _runtime.close()
    gate = _recovery_gate
    if owner is not None:
        gate = getattr(owner, "_mcsight_official_recovery_gate", None)
        if gate is None:
            gate = Lock()
            setattr(owner, "_mcsight_official_recovery_gate", gate)
    _runtime = OfficialRuntime(root, activation, recovery_gate=gate, mod_token=mod_token,
                               policy_defaults=policy_defaults, center_defaults=center_defaults,
                               access_lists=access_lists)
    return _runtime


def current():
    if _runtime is None or _runtime.closed:
        raise OfficialError("官方群功能尚未初始化。")
    return _runtime


def start_recovery():
    if _runtime is not None and not _runtime.closed:
        _runtime.start_recovery()


async def handle(event):
    try:
        await current().handle(event)
    except OfficialError:
        return
