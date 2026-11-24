"""Official chat ingress. Execute on an I/O worker with synthetic dependencies in tests.

No command bodies, raw codes, nickname text or credentials are persisted here.
Every mutation uses the same center state machine and event authorization.
"""
from collections import OrderedDict
from contextlib import contextmanager
import hashlib
import json
import re
from threading import RLock
import time
from uuid import UUID, uuid5

from .official_bindings import BindingError, GAME_RE, MAX_EFFECTS
from .official_core import OfficialError, identifier
from .official_protocol import MESSAGES


COMMANDS = frozenset({"bind", "unbind", "check", "operation"})
NAMESPACE = UUID("19f72d93-52ca-4e50-9829-39295fc04968")
USAGE = {
    "bind": "用法：/mc bind <六位码> [-s 服务器名]；取码账号须满足当前群昵称规则。",
    "unbind": "用法：/mc unbind [游戏名|all|@成员] [-s 服务器名]；不带参数解绑自己名下全部账号，管理员可用 user <官方用户标识>。",
    "check": "用法：/mc check [游戏名|user 官方标识|@成员] [-s 服务器名]；他人完整关联需要管理员权限。",
    "operation": "用法：/mc operation <操作编号或至少8位前缀> [retry [原六位码]|cancel] [-s 服务器名]。",
}


def request_identity(event, action):
    """The stable event origin and normalized action, never the six-digit code."""
    origin = [event.environment_id, event.instance_id, "group", event.group_id,
              event.message_id, event.user_id, action]
    key = hashlib.sha256(json.dumps(origin, ensure_ascii=True, separators=(",", ":")).encode("ascii")).hexdigest()
    return key, str(uuid5(NAMESPACE, key))


def mentioned_user(event,words):
    """Only event-backed mentions are targets; display names are never IDs."""
    mentions=getattr(event,"mentions",()) if event is not None else ()
    if len(words)==2 and words[0].lower()=="user":
        return identifier(words[1])
    if not words and len(mentions)==1: return mentions[0]
    if len(words)==1:
        match=re.fullmatch(r"<@!?([^>]+)>",words[0])
        if match and match[1] in mentions: return match[1]
    return None


def parse(command, arguments,event=None):
    if command not in COMMANDS or not isinstance(arguments, str) or len(arguments) > 512:
        raise BindingError("INVALID_INPUT", "官方绑定参数无效。")
    words, server, positionals = arguments.split(), None, []
    i = 0
    while i < len(words):
        value = words[i]
        if value == "-s":
            if server is not None or i + 1 >= len(words) or words[i + 1].startswith("-"):
                raise BindingError("INVALID_INPUT", USAGE[command])
            server = words[i + 1]
            i += 2
            continue
        if value.startswith("-"):
            raise BindingError("INVALID_INPUT", USAGE[command])
        positionals.append(value)
        i += 1
    if command == "bind" and (len(positionals) != 1 or not re.fullmatch(r"[0-9]{6}", positionals[0])):
        raise BindingError("INVALID_INPUT", USAGE[command])
    if command in ("check","unbind"):
        target=mentioned_user(event,positionals)
        if target is not None: positionals=["user",target]
    if command=="unbind" and not positionals: positionals=["all"]
    user_check=len(positionals)==2 and positionals[0].lower()=="user"
    if user_check: identifier(positionals[1])
    game_check=len(positionals)==2 and positionals[0].upper()=="ID" and GAME_RE.fullmatch(positionals[1])
    if command == "check" and not user_check and not game_check and (len(positionals) > 1 or positionals and not GAME_RE.fullmatch(positionals[0])):
        raise BindingError("INVALID_INPUT", USAGE[command])
    if command == "unbind":
        valid = (len(positionals) == 1 and (positionals[0] == "all" or GAME_RE.fullmatch(positionals[0])))
        valid = valid or (len(positionals) == 2 and positionals[0] in ("user", "ID"))
        if not valid:
            raise BindingError("INVALID_INPUT", USAGE[command])
        if len(positionals) == 2:
            if positionals[0] == "user":
                identifier(positionals[1])
            elif not GAME_RE.fullmatch(positionals[1]):
                raise BindingError("INVALID_INPUT", USAGE[command])
    if command == "operation" and (len(positionals) not in (1, 2, 3)
            or not re.fullmatch(r"[0-9a-f-]{8,36}", positionals[0])
            or len(positionals) >= 2 and positionals[1] not in ("retry", "cancel")
            or len(positionals) == 3 and (positionals[1]!="retry" or not re.fullmatch(r"[0-9]{6}",positionals[2]))):
        raise BindingError("INVALID_INPUT", USAGE[command])
    return positionals, server


class CommandBudget:
    """Bounded global/group/member attempt and concurrency budget, with no codes."""
    def __init__(self, *, clock=time.monotonic):
        self.clock, self.lock, self.entries = clock, RLock(), OrderedDict()
        self.inflight, self.owners = 0, set()

    @contextmanager
    def claim(self, event, *, binding=False):
        owner = event.key
        with self.lock:
            now = self.clock()
            self.entries = OrderedDict((k, v) for k, v in self.entries.items() if now - v[0] < 60)
            if self.inflight >= 4 or owner in self.owners:
                raise BindingError("LIMIT_EXCEEDED", "绑定服务正在处理请求，请稍后查询原操作。")
            if binding:
                keys = [("all",), ("group", event.environment_id, event.group_id), ("member",) + owner]
                if len(set(keys) - set(self.entries)) + len(self.entries) > 4096:
                    raise BindingError("LIMIT_EXCEEDED", "绑定请求预算已满，请稍后重试。")
                for key, maximum in zip(keys, (120, 60, 6)):
                    if self.entries.get(key, (now, 0))[1] >= maximum:
                        raise BindingError("LIMIT_EXCEEDED", "绑定验证请求过于频繁，请稍后重试。")
                for key in keys:
                    start, count = self.entries.get(key, (now, 0))
                    self.entries[key] = start, count + 1
            self.inflight += 1
            self.owners.add(owner)
        try:
            yield
        finally:
            with self.lock:
                self.inflight -= 1
                self.owners.discard(owner)


class OfficialBindingCommands:
    def __init__(self, host, *, budget=None, clock=time.monotonic):
        self.host, self.budget, self.clock = host, budget or CommandBudget(), clock

    def owned_games(self,event,arguments):
        from .official_queries import arguments as query_arguments
        _,selected=query_arguments(arguments)
        session=self.host.open(event)
        server=self._select(session,selected); session.peer(server)
        if hasattr(session,"use"): session.use(server)
        with session.policy.authorized(session.scope(server)):
            rows=session.center.active_snapshot(event.environment_id,event.group_id,event.user_id,max_rows=500)
        return frozenset(row["game_key"] for row in rows)

    def execute(self, event, command, arguments):
        try:
            words, selected = parse(command, arguments,event)
            with self.budget.claim(event, binding=command == "bind"):
                session = self.host.open(event)
                if command == "bind":
                    server = self._locate(session, selected, words[0])
                    if hasattr(session,"use"): session.use(server)
                    request = self._request(session, "bind")
                    request.update(user_id=event.user_id, token_code=words[0])
                    result = session.service(server).bind(session.scope(server), request)
                    return "官方绑定结果", self._result(result)
                if command == "operation":
                    return "官方操作状态", self._operation(session, words, selected)
                if selected=="all":
                    if command=="unbind" and words!=["all"]:
                        raise BindingError("INVALID_INPUT","跨服解绑请使用 /mc unbind all -s all；每服独立报告结果。")
                    servers=self._servers(session)
                    if len(servers)>4 or not servers:
                        raise BindingError("SERVER_REQUIRED","跨服汇总至多4服，请分别指定服务器。")
                    lines=[]; center_seen=False
                    for server in servers:
                        mode=session.mode(server) if hasattr(session,"mode") else "hybrid"
                        if mode!="local" and center_seen: continue
                        center_seen |= mode!="local"
                        lines.append("服务器："+server["name"]+"（"+mode+"）")
                        try:
                            session.peer(server)
                            if hasattr(session,"use"): session.use(server)
                            lines.extend(self._check(session,server,words) if command=="check" else self._unbind(session,server,words))
                        except BindingError as error:
                            lines.append(str(error)+"；此服未确认，其它服的确定结果保留。")
                        except Exception:
                            lines.append("此服结果待确认；使用原操作编号查询，其它服结果保留。")
                    return "逐服官方绑定结果",lines
                server = self._select(session, selected)
                session.peer(server)  # Explicit capabilities gate even for center-only checks.
                if hasattr(session,"use"): session.use(server)
                if command == "check":
                    return "官方绑定查询", self._check(session, server, words)
                return "官方解绑结果", self._unbind(session, server, words)
        except BindingError as error:
            return "官方绑定服务", [str(error), "能力未就绪或结果未知时不会使用旧绑定流程。"]
        except OfficialError:
            return "官方绑定服务", [MESSAGES["INVALID_INPUT"]]
        except Exception:
            # Driver/transport exceptions may contain private connection material.
            return "官方绑定服务", [MESSAGES["RESULT_UNKNOWN"],
                "如已发起变更，请使用已有操作编号查询；不要重复提交。"]

    @staticmethod
    def _servers(session):
        level = 0
        servers = [s for s in session.group["servers"] if s["api_port"] is not None]
        if servers:
            with session.policy.authorized(session.scope(servers[0])) as (_, level, _):
                pass
        return [s for s in servers if not s["hidden"] or level >= 3]

    def _select(self, session, name):
        servers = self._servers(session)
        if name is not None:
            matches = [s for s in servers if s["name"] == name]
            if len(matches) != 1:
                raise BindingError("SCOPE_MISMATCH", "未找到当前身份可访问的模组服务器。")
            return matches[0]
        if len(servers) != 1:
            raise BindingError("SERVER_REQUIRED", "请使用 -s <服务器名> 明确选择服务器。")
        return servers[0]

    def _locate(self, session, name, code):
        if name is not None:
            server = self._select(session, name)
            session.peer(server)
            return server
        servers = self._servers(session)
        if not servers or len(servers) > 4:
            raise BindingError("SERVER_REQUIRED", "自动定位至多探测4服，请使用 -s 明确选服。")
        if len(servers) == 1:
            session.peer(servers[0])
            return servers[0]
        found, uncertain, deadline = [], False, self.clock() + 40
        for server in servers:
            if self.clock() >= deadline:
                uncertain = True
                break
            try:
                peer = session.peer(server)
                result = peer.validate(session.scope(server), code, server["server_id"])
                if result.get("state") == "FOUND":
                    found.append(server)
                elif result.get("state") != "TOKEN_INVALID":
                    uncertain = True
            except BindingError as error:
                if error.code != "TOKEN_INVALID":
                    uncertain = True
        if uncertain or len(found) > 1:
            raise BindingError("SERVER_REQUIRED", "部分服务器结果不明或存在多服命中，请使用 -s 明确选服。")
        if not found:
            raise BindingError("TOKEN_INVALID", MESSAGES["TOKEN_INVALID"])
        return found[0]

    @staticmethod
    def _request(session, action, *, authorization="self"):
        revision = str(session.group["revision"])
        current=getattr(session,"current_server",None)
        local=current is not None and session.mode(current)=="local"
        key,operation_id=request_identity(session.event,action+(":"+current["server_id"] if local else ""))
        targets=([current["server_id"]] if local else sorted(s["server_id"] for s in session.group["servers"]
            if s["api_port"] is not None and (not hasattr(session,"mode") or session.mode(s)!="local")))
        return dict(operation_id=operation_id, request_key=key, actor_kind="qq_member",
            actor_id=session.event.user_id, policy_revision=revision,
            authorization={"scope": authorization, "policy_revision": revision},
            target_server_ids=targets)

    @staticmethod
    def _check(session, server, words):
        user=session.event.user_id
        with session.policy.authorized(session.scope(server)) as (_,level,_):
            if len(words)==2 and words[0]=="user":
                user=identifier(words[1])
                if user!=session.event.user_id and level<3:
                    raise BindingError("UNAUTHORIZED","只有当前群管理员可以查看他人的完整账号关联。")
            elif words:
                game=words[-1]
                rows,_=session.center.check(session.event.environment_id,session.event.group_id,game_id=game,limit=1)
                if not rows: return [game+"：当前权威范围内没有有效绑定。"]
                row=rows[0]
                lines=[row["game_id"]+"：已绑定 · 版本 "+str(row["revision"])]
                if level>=3: lines.append("官方归属："+row["qq"])
                elif row["qq"]==user: lines.append("此账号属于本人。")
                return lines
            rows = session.center.active_snapshot(session.event.environment_id, session.event.group_id,user)
        if not rows:
            return ["未找到目标用户在当前权威范围的有效绑定。"]
        return [f"有效账号共 {len(rows)} 个："] + [f"{row['game_id']} · 版本 {row['revision']}" for row in rows]

    def active_user_ids(self,event,arguments):
        words,selected=parse("check",arguments)
        if words:raise BindingError("INVALID_INPUT","用法：/mc bindcheck [-s 服务器名]，检测所选权威的群成员绑定。")
        session=self.host.open(event); server=self._select(session,selected);session.peer(server)
        if hasattr(session,"use"):session.use(server)
        with session.policy.authorized(session.scope(server)) as (_,level,_):
            if level<3:raise BindingError("UNAUTHORIZED","全群绑定检测需要当前群管理员权限。")
            if hasattr(session.center,"active_user_ids"):
                users=session.center.active_user_ids(event.environment_id,event.group_id)
            else:
                rows=session.center.mirror_snapshot(event.environment_id,event.group_id)
                users=frozenset(row["user_id"] for row in rows if row["state"]=="active")
        session.revalidate()
        return server["name"],users

    def _unbind(self, session, server, words):
        scope, event = session.scope(server), session.event
        action = "unbind_user" if words[0] in ("all", "user") else "unbind_game"
        with session.policy.authorized(scope) as (_, level, _):
            key,_=request_identity(event,action+(":"+server["server_id"] if hasattr(session,"mode") and session.mode(server)=="local" else ""))
            previous = session.center.find_request(event.environment_id, event.group_id, key)
            if previous is not None:
                game = words[-1].lower() if action == "unbind_game" else None
                if (previous["kind"] != action or previous["actor_kind"] != "qq_member"
                        or previous["actor_id"] != event.user_id
                        or game is not None and previous["intent"]["targets"][0]["game_key"] != game
                        or words[0] == "user" and previous["target_user_id"] != words[1]
                        or words[0] == "all" and previous["target_user_id"] != event.user_id):
                    raise BindingError("CONFLICT", "原事件已用于不同解绑目标，请查询原操作。")
                # Reuse the ORIGINAL target set, never a new live user snapshot.
                result = session.service(server, peer=False).recover(scope, previous["operation_id"])
                return self._result(result)
            user = event.user_id
            if words[0] == "user":
                if level < 3:
                    raise BindingError("UNAUTHORIZED", MESSAGES["UNAUTHORIZED"])
                user = words[1]
            if action == "unbind_game":
                game = words[-1]
                rows, _ = session.center.check(event.environment_id, event.group_id, game_id=game, limit=1)
                if rows and rows[0]["qq"] != user:
                    if level < 3:
                        raise BindingError("UNAUTHORIZED", "只能解绑本人的账号。")
                    user = rows[0]["qq"]
            else:
                rows = session.center.active_snapshot(event.environment_id, event.group_id, user, max_rows=MAX_EFFECTS)
            if not rows:
                raise BindingError("SCOPE_MISMATCH", "没有可解绑的有效账号。")
            request = self._request(session, action, authorization="self" if user == event.user_id else "group")
            request.update(kind=action, target_user_id=user,
                targets=[{"game_key": row["game_key"], "expected_revision": str(row["revision"])} for row in rows])
            result = session.service(server, peer=False).unbind(scope, request)
            return self._result(result)

    def _operation(self, session, words, selected):
        servers = self._servers(session)
        if not servers:
            raise BindingError("SCOPE_MISMATCH", "当前群没有可访问的模组服务器。")
        if hasattr(session,"use"):
            if selected is not None:
                session.use(self._select(session,selected))
            elif all(session.mode(server)=="local" for server in servers):
                session.use(self._select(session,None))
        with session.policy.authorized(session.scope(servers[0])) as (_, level, _):
            operation = session.center.resolve_operation(session.event.environment_id, session.event.group_id,
                words[0], requester=session.event.user_id, administrator=level >= 3)
        target = operation["original_server_id"] or next(iter(operation["intent"]["target_server_ids"]), None)
        matches = [server for server in servers if server["server_id"] == target]
        server = self._select(session, selected) if selected is not None else (matches[0] if matches else None)
        if server is None:
            raise BindingError("SCOPE_MISMATCH", "原操作服务器当前不可访问，请联系管理员。")
        if hasattr(session,"use"): session.use(server)
        service = session.service(server, peer=len(words) >= 2 and operation["kind"] == "bind")
        method = {"retry": service.recover, "cancel": service.cancel}.get(words[1] if len(words) >= 2 else "", service.inspect)
        if len(words)==3:
            return self._result(service.recover(session.scope(server),operation["operation_id"],token_code=words[2]))
        return self._result(method(session.scope(server), operation["operation_id"]))

    @staticmethod
    def _result(result):
        code = result.get("code", "STORAGE_ERROR")
        operation = result.get("operation")
        lines = [MESSAGES.get(code, MESSAGES["STORAGE_ERROR"])]
        if result.get("operation_id"):
            lines.append(f"操作编号：{result['operation_id']}")
            lines.append(f"查询：/mc operation {result['operation_id']}")
        if not operation:
            return lines
        lines.append(f"权威状态：{operation['state']}")
        token = operation["followup"].get("token_finalize")
        if token:
            lines.append(f"验证码确认：{token['state']}")
        for server, delivery in sorted(operation["followup"].get("delivery", {}).items()):
            lines.append(f"目标 {server}：镜像 {delivery['mirror']}；白名单 {delivery['whitelist']}；在线限制 {delivery['online']}")
        if result.get("followup_code"):
            lines.append("后续确认：" + MESSAGES.get(result["followup_code"], MESSAGES["STORAGE_ERROR"]))
        if operation["state"] not in ("COMMITTED", "REJECTED", "ABORTED"):
            lines.append("结果尚未确定，请查询或重查原操作；不要据此重复取码提交。")
        return lines
