"""Scoped local authority client. No center database or legacy binding fallback.

Only the selected mod owns local rows. The plugin validates each service reply
and applies the same current-event policy before a bind/unbind/resume request.
"""
from copy import deepcopy
from uuid import UUID, uuid5

from .official_bindings import BindingError, OfficialBindings, GAME_RE, TERMINAL, _revision
from .official_binding_service import OfficialBindingService
from .official_core import uuid_value, identifier

NAMESPACE=UUID("19f72d93-52ca-4e50-9829-39295fc04968")


class LocalRepository:
    def __init__(self,peer):
        self.peer=peer
        self.scope=peer.client.target.scope

    def _scope(self,environment,group):
        if (environment,group)!=(self.scope.environment_id,self.scope.group_id):
            raise BindingError("SCOPE_MISMATCH","本服权威范围不匹配。")

    def request(self,endpoint,payload):
        return self.peer._request(self.scope,endpoint,payload)

    def operation(self,data,expected=None):
        row=data.get("operation")
        if not isinstance(row,dict):
            raise BindingError("SCHEMA_MISMATCH","本服未返回完整持久操作。")
        OfficialBindings._validate_operation(row)
        self._scope(row["environment_id"],row["group_id"])
        if (expected is not None and row["operation_id"]!=expected
                or row["intent"]["target_server_ids"]!=[self.scope.server_id]
                or row["kind"]=="bind" and row["original_server_id"]!=self.scope.server_id
                or data.get("state")!=row["state"]):
            raise BindingError("CONFLICT","本服操作回执的范围或状态冲突。")
        return OfficialBindings._public_operation(row)

    def get_operation(self,environment,operation_id,*,group_id):
        self._scope(environment,group_id); uuid_value(operation_id)
        try:
            return self.operation(self.request("operations/"+operation_id,{}),operation_id)
        except BindingError as error:
            if error.code=="SCOPE_MISMATCH": return None
            raise

    def find_request(self,environment,group,request_key):
        return self.get_operation(environment,str(uuid5(NAMESPACE,request_key)),group_id=group)

    def resolve_operation(self,environment,group,prefix,*,requester=None,administrator=False):
        if len(prefix)!=36:
            raise BindingError("INVALID_INPUT","local 模式请填写完整操作编号并指定原服务器。")
        row=self.get_operation(environment,prefix,group_id=group)
        if row is None or not (administrator or row["actor_kind"]=="qq_member" and row["actor_id"]==requester or row["target_user_id"]==requester):
            raise BindingError("SCOPE_MISMATCH","未找到当前身份可查看的本服操作。")
        return row

    @staticmethod
    def _row(row):
        if (not isinstance(row,dict) or not GAME_RE.fullmatch(row.get("game_id",""))
                or row.get("game_key")!=row["game_id"].lower() or row.get("state") not in ("active","revoked")):
            raise BindingError("SCHEMA_MISMATCH","本服账号记录无效。")
        _revision(row.get("revision")); identifier(row.get("user_id"))
        return dict(row,qq=row["user_id"])

    def check(self,environment,group,*,game_id=None,user_id=None,limit=100,cursor=None):
        self._scope(environment,group)
        payload={"game_id":game_id} if game_id is not None else {"user_id":user_id,"limit":limit}
        if cursor is not None: payload["cursor"]=cursor
        data=self.request("check",payload)
        if game_id is not None:
            row=data.get("binding")
            rows=[] if row is None or not data.get("bound") else [self._row(row)]
            if rows and rows[0]["game_key"]!=game_id.lower():
                raise BindingError("CONFLICT","查询回执账号冲突。")
            return rows,None
        rows=data.get("bindings")
        if not isinstance(rows,list) or len(rows)>limit:
            raise BindingError("SCHEMA_MISMATCH","本服分页回执无效。")
        rows=[self._row(row) for row in rows]
        if any(row["qq"]!=user_id or row["state"]!="active" for row in rows):
            raise BindingError("CONFLICT","本服分页回执归属冲突。")
        return rows,data.get("next_cursor")

    def active_snapshot(self,environment,group,user,*,max_rows=4096):
        rows,cursor=[],None
        seen=set()
        while True:
            page,cursor=self.check(environment,group,user_id=user,limit=100,cursor=cursor)
            rows.extend(page)
            if len(rows)>max_rows or any(row["game_key"] in seen for row in page):
                raise BindingError("LIMIT_EXCEEDED","本服账号快照超限或重复。")
            seen.update(row["game_key"] for row in page)
            if cursor is None: return rows
            if not isinstance(cursor,str) or len(cursor)>64:
                raise BindingError("SCHEMA_MISMATCH","本服分页游标无效。")

    def operation_summaries(self,environment,group,*,pending=True,limit=201):
        self._scope(environment,group)
        data=self.request("operations",dict(filter="pending" if pending else "all",limit=limit))
        rows=data.get("operations")
        if not isinstance(rows,list) or len(rows)>limit:
            raise BindingError("SCHEMA_MISMATCH","本服操作列表回执无效。")
        required={"operation_id","kind","state","result_code","created_at","updated_at","pending"}
        for row in rows:
            if (not isinstance(row,dict) or set(row)!=required or row["kind"] not in ("bind","unbind_game","unbind_user")
                    or row["state"] not in TERMINAL|{"PREPARED","RESERVED"} or type(row["pending"]) is not bool):
                raise BindingError("SCHEMA_MISMATCH","本服操作摘要结构无效。")
            uuid_value(row["operation_id"])
        return rows

    def active_user_ids(self,environment,group):
        self._scope(environment,group)
        users,seen,cursors=set(),set(),set()
        payload=dict(view="active_roster",limit=100)
        for _ in range(100):
            data=self.request("check",payload)
            rows=data.get("bindings")
            if not isinstance(rows,list) or len(rows)>100:
                raise BindingError("SCHEMA_MISMATCH","本服检测快照格式无效。")
            for item in rows:
                row=self._row(item)
                if row["state"]!="active" or row["game_key"] in seen:
                    raise BindingError("CONFLICT","本服检测快照重复或状态不一致。")
                seen.add(row["game_key"]);users.add(row["user_id"])
            cursor=data.get("next_cursor")
            if cursor is None:return frozenset(users)
            if not isinstance(cursor,str) or len(cursor)>64 or cursor in cursors:
                raise BindingError("CONFLICT","本服检测分页游标无效。")
            cursors.add(cursor);payload["cursor"]=cursor
        raise BindingError("LIMIT_EXCEEDED","本服检测快照超限，未发布不完整结果。")

    def abort(self,*,environment_id,group_id,operation_id):
        self._scope(environment_id,group_id)
        return self.operation(self.request("unbind",dict(operation_id=operation_id,management_action="cancel")),operation_id)

    def retry_terminal(self,operation_id):
        return self.operation(self.request("unbind",dict(operation_id=operation_id,management_action="retry")),operation_id)


class LocalBindingService:
    def __init__(self,store,policy):
        self.store,self.policy=store,policy
        self.scope=store.scope

    def _result(self,row):
        return OfficialBindingService._result(row["result_code"] or row["state"],row)

    def _wire(self,row,*,current=False):
        kind=row["kind"]; bind=kind=="bind"
        wire={"operation_id":row["operation_id"],"request_key":row["request_key"],
            "actor":{"kind":row["actor_kind"],"id":row["actor_id"]},
            "authorization":deepcopy(row["intent"]["authorization"]),
            "target_server_ids":[self.scope.server_id]}
        if bind:
            wire.update(user_id=row["target_user_id"],game_id=row["intent"]["game_id"],
                token_generation=row["token_generation"],policy_revision=row["policy_revision"],nickname_basis=row.get("nickname_basis") or None)
        else:
            wire.update(kind=kind,target_user_id=row["target_user_id"],targets=deepcopy(row["intent"]["targets"]))
        return wire

    def _visible(self,scope,operation_id,phase):
        if scope!=self.scope: raise BindingError("SCOPE_MISMATCH","本服范围不匹配。")
        with self.policy.guard(scope,{"policy_phase":"access"}):
            row=self.store.get_operation(scope.environment_id,operation_id,group_id=scope.group_id)
            if row is None: raise BindingError("SCOPE_MISMATCH","本服原操作不存在。")
            with self.policy.guard(scope,dict(row,policy_phase=phase)): return row

    def bind(self,scope,request):
        request=OfficialBindingService._request(scope,request)
        existing=self.store.find_request(scope.environment_id,scope.group_id,request["request_key"])
        if existing is not None: return self.recover(scope,existing["operation_id"],token_code=request["token_code"])
        match=self.store.peer.validate(scope,request["token_code"],scope.server_id)
        context=dict(request,kind="bind",game_id=match["game_id"],target_server_ids=[scope.server_id])
        with self.policy.guard(scope,dict(context,policy_phase="prepare")) as lease:
            if lease.revision!=request["policy_revision"]: raise BindingError("CONFLICT","群策略已变化。")
            context["nickname_basis"]=lease.nickname_basis
            with self.policy.guard(scope,dict(context,policy_phase="commit")):
                payload={"operation_id":request["operation_id"],"request_key":request["request_key"],
                    "user_id":request["user_id"],"game_id":match["game_id"],"token_generation":match["token_generation"],
                    "token_code":request["token_code"],"policy_revision":lease.revision,"nickname_basis":lease.nickname_basis,
                    "actor":{"kind":request["actor_kind"],"id":request["actor_id"]},"authorization":request["authorization"]}
                return self._result(self.store.operation(self.store.request("bind",payload),request["operation_id"]))

    def unbind(self,scope,request):
        with self.policy.guard(scope,dict(request,policy_phase="submit")) as lease:
            if lease.revision!=request["policy_revision"]: raise BindingError("CONFLICT","群策略已变化。")
            wire={key:deepcopy(request[key]) for key in ("operation_id","request_key","kind","target_user_id","targets","authorization")}
            wire["actor"]={"kind":request["actor_kind"],"id":request["actor_id"]}
            return self._result(self.store.operation(self.store.request("unbind",wire),request["operation_id"]))

    def inspect(self,scope,operation_id):
        return self._result(self._visible(scope,operation_id,"inspect"))

    def recover(self,scope,operation_id,token_code=None):
        row=self._visible(scope,operation_id,"recover")
        if row["state"] in TERMINAL: return self._result(row)
        with self.policy.guard(scope,dict(row,policy_phase="commit")) as lease:
            if lease.revision!=row["policy_revision"]: raise BindingError("CONFLICT","原操作策略已变化，请显式中止后重新取码。")
            wire=self._wire(row)
            if row["kind"]=="bind":
                if token_code is not None and not row["followup"].get("token_reservation"):
                    wire["token_code"]=token_code
                else: wire["resume"]=True
            return self._result(self.store.operation(self.store.request("bind" if row["kind"]=="bind" else "unbind",wire),operation_id))

    def cancel(self,scope,operation_id):
        row=self._visible(scope,operation_id,"cancel")
        if row["state"]=="COMMITTED": raise BindingError("CONFLICT","已提交操作不能撤销。")
        with self.policy.authorized(scope) as (group,level,_):
            wire=self._wire(row); wire["cancel"]=True
            wire["actor"]={"kind":"qq_member","id":self.policy.event_scope.user_id}
            wire["authorization"]={"scope":"group" if level>=3 else "self","policy_revision":str(group["revision"])}
            return self._result(self.store.operation(self.store.request("bind" if row["kind"]=="bind" else "unbind",wire),operation_id))
