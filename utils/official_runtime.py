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

from .official_core import OfficialError, OfficialStore, PageCache, event_scope, permission, uuid_value
from .official_binding_policy import OfficialBindingPolicy
from .official_binding_service import OfficialBindingService
from .official_binding_host import OfficialBindingHost
from .official_binding_commands import COMMANDS as BINDING_COMMANDS, OfficialBindingCommands
from .official_binding_recovery import OfficialFollowupRecovery
from .official_binding_admin import OfficialBindingAdmin
from .official_bindings import BindingError
from .official_queries import QueryService, StatisticsQuery, render_pages, render_text_pages, render_statistics_pages
from .official_delivery import transport_for,checked_response
from .official_stats import OfficialStats
from .official_writes import OfficialWriteCommands, COMMANDS as WRITE_COMMANDS
from .official_members import OfficialMembers


HELP = ("MCSight · QQ 官方群\n"
        "/mc setup [识别备注]：登记本群和本人，由后台确认\n"
        "/mc status [-s 服务器名]：在线状态\n"
        "/mc tps [-s 服务器名]：TPS\n"
        "/mc stats <游戏名> [-s 服务器名]：公开统计\n"
        "/mc rank [类型] [总|周|月] -s <服务器名> [-n 1–50]\n"
        "/mc page <页码>：查看最近查询的其它页\n"
        "/mc bindhelp：查看官方绑定开放状态\n"
        "/mc bind <六位码> [-s 服务器名]；/mc unbind <游戏名|all> [-s 服务器名]\n"
        "/mc check [游戏名|user 官方标识|@成员] [-s 服务器名|all]\n"
        "/mc operation <操作编号> [retry [原六位码]|cancel] [-s 服务器名]\n"
        "/mc broadcast -s <服务器名|all> <内容>；/mc console -s <服务器名> <命令>\n"
        "/mc add|edit|del|hide|show：管理员维护服务器；/mc command <编号>：查询写操作回执\n"
        "/mc batchadd|batchremove|move|swap：批量和排序；/mc lastonline 开|关\n"
        "/mc bindcheck [-s 服务器名]；/mc checknick；/mc nicknamecheck 开|关|忽略|取消忽略|忽略列表\n"
        "成员检测需要QQ平台内邀权限；只用可靠群昵称，缺失者标为无法核验。\n"
        "事件未提供群昵称时不以 username 代替，也不会因退群或名单缺失自动解绑。\n"
        "查询需后台启用本群；绑定按配置权威和模组握手门禁开放。控制台须本服独立令牌，未知结果不会自动重发。")

BIND_HELP = ("QQ 官方群须重新配置和绑定，不沿用旧 OneBot 身份或记录。"
             "管理员按交付说明配置 hybrid、remote 或各服 local 权威；每次握手核对范围/结构/名单桥后开放绑定。"
             "未决操作由原请求人发送 /mc operation <完整编号> retry [-s 原服务器]；首次尚未预占可追加原六位码。"
             "中止用 cancel；local 查询须完整编号和原服务器，跨服结果逐服确认。"
             "管理员可 /mc check @成员 或 /mc unbind @成员 -s 原服；数字QQ号不当作官方身份。部署后仍须实际 QQ/MC/数据库验收。")


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
        except OfficialError:
            raise
        except Exception:
            raise OfficialError("官方群消息发送结果待确认，请用新消息查询；本次不重发。") from None

    def _sequence(self):
        self.record["sequence"] += 1
        return self.record["sequence"]

    def can_fallback_text(self):
        # An upload has srv_send_msg=false, so even an unknown upload cannot
        # already have posted a visible reply. Never repeat a message POST.
        return self.message_attempts==0 and self.record["calls"]<4 and time.monotonic()<self.record["expires"]

    async def text(self, value):
        return await self._call("post_group_message", content=value[:1800], msg_type=0,
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
    def __init__(self, root, activation, *, queries=None, renderer=render_pages, sender=OfficialSender, recovery_gate=None):
        self.store = OfficialStore(root)
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
        self.sampling = False
        self.last_sample = None
        self.help_image = None
        self.binding_host = OfficialBindingHost(self.store, lambda: None if self.closed else self.selection())
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
            raise OfficialError("未登记该官方应用环境。")
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
        data["has_mod_token"] = self.store.token_is_set(environment)
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
            state = self.store.list_state(scope.environment_id)
            group = next((g for g in state["groups"] if g["group_openid"] == scope.group_id), None)
            environment_admins = state["environment_administrators"]
            page_revision = (group["revision"], tuple(sorted(environment_admins))) if group is not None else None
            if group is not None and permission(scope, group, environment_admins) == 0:
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
            if command == "setup":
                candidate = self.store.register(scope, arguments)
                await sender.text(f"已登记本群和您的身份，回执：{candidate['candidate_id']}\n"
                                  "请由后台管理员核对、配置和授权。登记不会自动启用业务或授予管理权。")
                return
            if command == "bindhelp":
                await sender.text(BIND_HELP)
                return
            if command == "help":
                def help_revalidate():
                    self.environment(scope.environment_id, active=True)
                    latest = self.store.group(scope.environment_id, group_id=scope.group_id)
                    if latest is not None and permission(scope, latest) == 0:
                        raise OfficialError("当前身份已被禁止使用本群插件。")
                if self._supports_images(sender):
                    if self.help_image is None:
                        try:
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
            if group is None or not group["enabled"]:
                await sender.text("本官方群尚未启用。请先发送 /mc setup，由后台管理员完成独立配置。")
                return
            if command in BINDING_COMMANDS:
                await self._binding_command(scope, sender, command, arguments, group, page_revision)
                return
            if command in ("bindcheck","checknick"):
                await self._member_command(scope,sender,command,arguments,group,page_revision)
                return
            if command in WRITE_COMMANDS:
                minimum_permission = 3 if permission(scope, group, environment_admins) >= 3 else 1
                title,lines=await asyncio.to_thread(self.write_commands.execute,scope,command,arguments)
                current=self.store.list_state(scope.environment_id)
                latest=next((g for g in current["groups"] if g["group_openid"]==scope.group_id),None)
                if latest is None:
                    raise OfficialError("当前官方群配置已撤销，请重新查询。")
                result_revision=(latest["revision"],tuple(sorted(current["environment_administrators"])))
                def write_revalidate():
                    self.environment(scope.environment_id,active=True)
                    latest=self.store.list_state(scope.environment_id)
                    current=next((g for g in latest["groups"] if g["group_openid"]==scope.group_id),None)
                    if (current is None or not current["enabled"]
                            or permission(scope,current,latest["environment_administrators"])<minimum_permission
                            or current["revision"]!=result_revision[0]
                            or tuple(sorted(latest["environment_administrators"]))!=result_revision[1]):
                        raise OfficialError("当前身份已不能接收操作结果。")
                write_revalidate()
                if isinstance(sender,OfficialSender): sender.revalidate=write_revalidate
                pages=render_text_pages(title,lines)
                self.pages.put(scope,result_revision,[],pages,minimum_permission=minimum_permission)
                await sender.text(pages[0])
                if len(pages)>1:await sender.text("继续查看：/mc page 2（10分钟内有效）")
                return
            if command not in ("status", "tps", "stats", "rank", "page", "list"):
                await sender.text("该功能尚未在 QQ 官方模式开放。可使用 /mc help 查看当前能力。")
                return
            if command == "list":
                await sender.text("本群服务器：\n" + "\n".join(s["name"] for s in group["servers"] if not s["hidden"]))
                return
            if command == "page":
                try:
                    index = int(arguments.strip())
                except ValueError:
                    raise OfficialError("用法：/mc page <页码>") from None
            else:
                if scope.key in self.busy:
                    raise OfficialError("上一条查询仍在处理中，请稍后重试。")
                if len(self.busy) >= 16:
                    raise OfficialError("当前查询较多，请稍后重试。")
                now = time.monotonic()
                if now - self.last_queries.get(scope.key, -100) < 5:
                    raise OfficialError("查询间隔至少 5 秒，请稍后重试。")
                if len(self.last_queries) >= 4096:
                    self.last_queries.popitem(last=False)
                self.last_queries[scope.key] = now
                self.busy.add(scope.key)
                try:
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
                            if isinstance(document, StatisticsQuery) and self.renderer is render_pages:
                                pages = await asyncio.to_thread(render_statistics_pages, document)
                            else:
                                pages = await asyncio.to_thread(self.renderer, title, lines)
                        except Exception:
                            pages = []
                    self.environment(scope.environment_id, active=True)
                    self.pages.put(scope, page_revision, pages, text_pages)
                    index = 1
                finally:
                    self.busy.discard(scope.key)
            # 请求期间的停用、权限撤销或修订变化不能继续发送旧结果。
            self.environment(scope.environment_id, active=True)
            def revalidate():
                self.environment(scope.environment_id, active=True)
                latest_state = self.store.list_state(scope.environment_id)
                latest = next((g for g in latest_state["groups"] if g["group_openid"] == scope.group_id), None)
                if (latest is None or not latest["enabled"]
                        or permission(scope, latest, latest_state["environment_administrators"]) == 0
                        or latest["revision"] != group["revision"]
                        or tuple(sorted(latest_state["environment_administrators"])) != page_revision[1]):
                    raise OfficialError("查询期间配置或权限已变化，请重新查询。")
                self.pages.authorize(scope, page_revision, permission(scope, latest, latest_state["environment_administrators"]))
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

    async def _binding_command(self, scope, sender, command, arguments, group, page_revision):
        minimum_permission = 3 if permission(scope, group, page_revision[1]) >= 3 else 1
        if scope.key in self.busy or len(self.busy) >= 16:
            raise OfficialError("上一条请求仍在处理中，请稍后查询原操作。")
        self.busy.add(scope.key)
        try:
            title, lines = await asyncio.to_thread(self.binding_commands.execute, scope, command, arguments)
        finally:
            self.busy.discard(scope.key)

        def revalidate():
            self.environment(scope.environment_id, active=True)
            state = self.store.list_state(scope.environment_id)
            latest = next((g for g in state["groups"] if g["group_openid"] == scope.group_id), None)
            if (latest is None or not latest["enabled"] or latest["revision"] != group["revision"]
                    or permission(scope, latest, state["environment_administrators"]) < minimum_permission
                    or tuple(sorted(state["environment_administrators"])) != page_revision[1]):
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
            state=self.store.list_state(scope.environment_id)
            current=next((g for g in state["groups"] if g["group_openid"]==scope.group_id),None)
            if (current is None or not current["enabled"] or current["revision"]!=group["revision"]
                    or tuple(sorted(state["environment_administrators"]))!=page_revision[1]
                    or permission(scope,current,state["environment_administrators"])<3):
                raise OfficialError("当前群管理权限或配置已变化，停止成员扫描。")
        guard()
        if command=="checknick" and arguments.strip():raise OfficialError("格式检测用 /mc checknick；开关和忽略名单用 /mc nicknamecheck。")
        if command=="checknick" and not group["policy"].get("nickname_check_enabled",True):raise OfficialError("本群昵称检测已关闭。")
        if scope.key in self.busy or len(self.busy)>=16:raise OfficialError("当前请求较多，请稍后重试。")
        self.busy.add(scope.key)
        try:
            roster=await self.members.roster(scope,group["revision"],getattr(sender,"transport",None),guard)
            if command=="bindcheck":
                name,active=await asyncio.to_thread(self.binding_commands.active_user_ids,scope,arguments)
                title,lines=self.members.binding_audit(roster,name,active)
            else:title,lines=self.members.nickname_audit(scope,roster,group)
            guard()
            pages=render_text_pages(title,lines)
            self.pages.put(scope,page_revision,[],pages,minimum_permission=3)
            if isinstance(sender,OfficialSender):sender.revalidate=guard
            await sender.text(pages[0])
            if len(pages)>1:await sender.text("继续查看：/mc page 2（10分钟内有效）")
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
        self.pages.clear()
        self.help_image = None
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


def configure(root, activation, *, owner=None):
    global _runtime
    if _runtime is not None:
        _runtime.close()
    gate = _recovery_gate
    if owner is not None:
        gate = getattr(owner, "_mcsight_official_recovery_gate", None)
        if gate is None:
            gate = Lock()
            setattr(owner, "_mcsight_official_recovery_gate", gate)
    _runtime = OfficialRuntime(root, activation, recovery_gate=gate)
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
