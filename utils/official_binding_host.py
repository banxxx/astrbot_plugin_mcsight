"""Lazy official-center assembly. Imports never load configuration or credentials.

Only an accepted, enabled group event can open a session. Production settings
are an explicit per-environment owner file; no old pool, defaults, environment
discovery, credential echo or connection attempt is used during construction.
"""
from copy import deepcopy
from dataclasses import dataclass, field
import time

from .official_binding_policy import OfficialBindingPolicy
from .official_binding_service import BindingScope, OfficialBindingService
from .official_bindings import BindingError
from .official_center import CheckedOfficialBindings, OfficialMySQLFactory
from .official_core import OfficialError, permission, read_json, uuid_value
from .official_transport import ModTarget, V2ModClient, V2TokenPeer
from .official_local import LocalRepository, LocalBindingService


@dataclass(repr=False)
class CenterSettings:
    connection: dict | None = field(repr=False)
    unchanged: object = field(repr=False)
    storage_mode: str = "hybrid"
    server_modes: dict = field(default_factory=dict)


class UnselectedAuthority:
    def active_snapshot(self,*args,**kwargs):
        raise BindingError("SERVER_REQUIRED","local 模式请先选择原服务器。")


def center_repository(settings, revalidate):
    def active():
        revalidate()
        return True
    return CheckedOfficialBindings(OfficialMySQLFactory(lambda: dict(settings), active))


def token_peer(target, token_source, revalidate):
    return V2TokenPeer(V2ModClient(target), token_source, revalidate=revalidate)


class OfficialBindingHost:
    def __init__(self, groups, selection, *, settings_source=None, center_factory=center_repository,
                 peer_factory=token_peer):
        self.groups, self.selection = groups, selection
        self.settings_source = settings_source or self._settings
        self.center_factory, self.peer_factory = center_factory, peer_factory

    def _settings(self, environment):
        path = self.groups.root / uuid_value(environment) / "center_settings.json"
        try:
            before = path.stat()
        except FileNotFoundError:
            raise BindingError("NOT_SUPPORTED", "官方绑定尚未配置独立中心，当前不能开放。") from None
        except OSError:
            raise BindingError("STORAGE_ERROR", "官方中心配置暂不可用。") from None
        try:
            data = read_json(path, {}, limit=16384)
            if (not isinstance(data, dict) or not {"schema_version", "environment_id","enabled","storage_mode","connection"} <= set(data)
                    or set(data)-{"schema_version","environment_id","enabled","storage_mode","connection","server_modes"}
                    or type(data["schema_version"]) is not int or data["schema_version"] != 1
                    or data["environment_id"] != environment or type(data["enabled"]) is not bool):
                raise BindingError("SCHEMA_MISMATCH", "官方中心配置结构或环境不匹配。")
            if not data["enabled"] or data["storage_mode"] not in ("hybrid","remote","local"):
                raise BindingError("NOT_SUPPORTED", "官方中心未启用，或所选存储模式尚未接通。")
            connection = data["connection"]
            modes=data.get("server_modes",{})
            if not isinstance(modes,dict) or len(modes)>2000:
                raise BindingError("SCHEMA_MISMATCH","服务器权威模式映射无效。")
            for server,mode in modes.items():
                uuid_value(server)
                if mode not in ("hybrid","remote","local"):
                    raise BindingError("SCHEMA_MISMATCH","不支持的服务器权威模式。")
            needs_center=data["storage_mode"]!="local" or any(mode!="local" for mode in modes.values())
            if ((needs_center or connection is not None) and (not isinstance(connection,dict)
                    or set(connection)!={"host","port","user","password","database","ssl_ca"})):
                raise BindingError("SCHEMA_MISMATCH", "官方中心连接字段不完整。")
            fingerprint = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            def unchanged():
                try:
                    latest = path.stat()
                    return fingerprint == (latest.st_dev, latest.st_ino, latest.st_size,
                                            latest.st_mtime_ns, latest.st_ctime_ns)
                except OSError:
                    return False
            if not unchanged():
                raise BindingError("CONFLICT", "官方中心配置已变化，请使用新消息。")
            return CenterSettings(dict(connection) if connection is not None else None,unchanged,data["storage_mode"],dict(modes))
        except BindingError:
            raise
        except OfficialError:
            raise BindingError("STORAGE_ERROR", "官方中心配置无效；不会读取旧配置兜底。") from None

    def open(self, event_scope):
        def active():
            current = self.selection()
            if (not isinstance(current, dict) or current.get("platform") != "qq_official"
                    or (current.get("environment_id"), current.get("instance_id")) != event_scope.key[:2]):
                raise BindingError("NOT_SUPPORTED", "当前官方实例已失效，请使用新消息。")

        active()
        with self.groups.lock:
            state = self.groups.list_state(event_scope.environment_id)
            group = next((g for g in state["groups"] if g["group_openid"] == event_scope.group_id), None)
            if group is None or not group["enabled"] or permission(event_scope, group, state["environment_administrators"]) == 0:
                raise BindingError("UNAUTHORIZED", "当前官方群或身份未获授权。")
            group = deepcopy(group)
            administrators = tuple(sorted(state["environment_administrators"]))
        settings = self.settings_source(event_scope.environment_id)
        if not isinstance(settings, CenterSettings) or not callable(settings.unchanged):
            raise BindingError("STORAGE_ERROR", "官方中心依赖无效。")

        deadline = time.monotonic() + 120
        def revalidate():
            active()
            if time.monotonic() >= deadline:
                raise BindingError("NOT_SUPPORTED", "当前事件授权已过期，请使用新消息。")
            if not settings.unchanged():
                raise BindingError("CONFLICT", "官方中心配置已变化，请使用新消息。")
            with self.groups.lock:
                state = self.groups.list_state(event_scope.environment_id)
                latest = next((g for g in state["groups"] if g["group_openid"] == event_scope.group_id), None)
                if (latest is None or not latest["enabled"] or latest["revision"] != group["revision"]
                        or tuple(sorted(state["environment_administrators"])) != administrators
                        or permission(event_scope, latest, state["environment_administrators"]) == 0):
                    raise BindingError("CONFLICT", "官方群配置或权限已变化，请使用新消息。")

        revalidate()
        center = self.center_factory(settings.connection, revalidate) if settings.connection is not None else UnselectedAuthority()
        policy = OfficialBindingPolicy(self.groups, self.selection, event_scope, center)
        return BindingSession(self,event_scope,group,center,policy,revalidate,settings=settings,central=center)

    def recovery(self, *, guard=None, server_id=None, group_id=None):
        """Background authority only for an already durable terminal result."""
        if guard is not None and not callable(guard):
            raise TypeError("Explicit trusted management guard required")
        current = self.selection()
        if not isinstance(current, dict) or current.get("platform") != "qq_official":
            raise BindingError("NOT_SUPPORTED", "当前没有生效的官方环境。")
        expected = dict(current)
        environment = uuid_value(expected.get("environment_id"))
        if guard is not None and guard() is False:
            raise BindingError("UNAUTHORIZED", "当前管理请求未获授权。")
        settings = self.settings_source(environment)
        if not isinstance(settings, CenterSettings) or not callable(settings.unchanged):
            raise BindingError("STORAGE_ERROR", "官方中心依赖无效。")
        if settings.connection is None and server_id is None:
            raise BindingError("NOT_SUPPORTED","当前各服使用 local 权威；请在原服务器查询/处理操作，后台不代提交。")
        def revalidate():
            if self.selection() != expected or not settings.unchanged():
                raise BindingError("NOT_SUPPORTED", "官方恢复来源已停用或变化。")
            if guard is not None and guard() is False:
                raise BindingError("UNAUTHORIZED", "当前管理请求已失效。")
        revalidate()
        with self.groups.lock:
            state = self.groups.list_state(environment)
            groups = {group["group_openid"]: group for group in state["groups"]}
        center = self.center_factory(settings.connection,revalidate) if settings.connection is not None else UnselectedAuthority()
        if server_id is not None:
            uuid_value(server_id)
            group=groups.get(group_id)
            server=next((s for s in group["servers"] if s["server_id"]==server_id and s["api_port"] is not None),None) if group else None
            if server is None or not group["enabled"]:
                raise BindingError("SCOPE_MISMATCH","后台原操作服务器不在当前启用群内。")
            mode=settings.server_modes.get(server_id,settings.storage_mode)
            if mode=="local":
                scope=BindingScope(environment,group_id,server_id)
                peer=self.peer_factory(ModTarget(scope,server["host"],server["api_port"]),lambda:self.groups.runtime_token(environment),revalidate)
                if isinstance(peer,V2TokenPeer): peer.storage_mode=mode
                peer.handshake(); center=LocalRepository(peer)
        return RecoveryContext(self,environment,groups,center,revalidate,settings=settings)


@dataclass(repr=False)
class BindingSession:
    host: object = field(repr=False)
    event: object = field(repr=False)
    group: dict = field(repr=False)
    center: object = field(repr=False)
    policy: object = field(repr=False)
    revalidate: object = field(repr=False)
    peers: dict = field(default_factory=dict, repr=False)
    settings: object = field(default=None,repr=False)
    central: object = field(default=None,repr=False)
    current_server: object = field(default=None,repr=False)

    def mode(self,server):
        return self.settings.server_modes.get(server["server_id"],self.settings.storage_mode) if self.settings is not None else "hybrid"

    def use(self,server):
        self.revalidate()
        self.current_server=server
        self.center=LocalRepository(self.peer(server)) if self.mode(server)=="local" else self.central
        self.policy=OfficialBindingPolicy(self.host.groups,self.host.selection,self.event,self.center)
        return self

    def scope(self, server):
        return BindingScope(self.event.environment_id, self.event.group_id, server["server_id"])

    def peer(self, server):
        self.revalidate()
        key = server["server_id"]
        if key not in self.peers:
            peer = self.host.peer_factory(ModTarget(self.scope(server), server["host"], server["api_port"]),
                lambda: self.host.groups.runtime_token(self.event.environment_id), self.revalidate)
            if isinstance(peer,V2TokenPeer): peer.storage_mode=self.mode(server)
            capabilities = peer.handshake()
            if capabilities.get("token_reservation_query") is not True:
                raise BindingError("NOT_SUPPORTED", "对端尚未具备完整预占恢复能力，绑定保持关闭。")
            self.peers[key] = peer
        return self.peers[key]

    def service(self, server, *, peer=True):
        if self.mode(server)=="local":
            if not isinstance(self.center,LocalRepository): self.use(server)
            return LocalBindingService(self.center,self.policy)
        return OfficialBindingService(self.center, self.peer(server) if peer else None, policy_guard=self.policy.guard)


@dataclass(repr=False)
class RecoveryContext:
    host: object = field(repr=False)
    environment_id: str
    groups: dict = field(repr=False)
    center: object = field(repr=False)
    revalidate: object = field(repr=False)
    peers: dict = field(default_factory=dict, repr=False)
    failures: dict = field(default_factory=dict, repr=False)
    settings: object = field(default=None,repr=False)

    def group_guard(self, group_id):
        self.revalidate()
        with self.host.groups.lock:
            group = self.groups.get(group_id)
            latest = self.host.groups.group(self.environment_id, group_id=group_id)
            if (group is None or latest is None or not latest["enabled"]
                    or latest["revision"] != group["revision"]):
                raise BindingError("NOT_SUPPORTED", "原操作群配置当前不可用于补偿。")

    def peer(self, group_id, server_id, budget):
        self.group_guard(group_id)
        key = group_id, server_id
        if key in self.failures:
            raise BindingError(self.failures[key], "本轮已确认该目标不可用，等待退避后再检查。")
        if key not in self.peers:
            servers = self.groups[group_id]["servers"]
            server = next((s for s in servers if s["server_id"] == server_id and s["api_port"] is not None), None)
            if server is None:
                raise BindingError("SCOPE_MISMATCH", "原操作目标当前不在模组配置内。")
            scope = BindingScope(self.environment_id, group_id, server_id)
            guard = lambda: self.group_guard(group_id)
            budget.consume(1)
            try:
                peer = self.host.peer_factory(ModTarget(scope, server["host"], server["api_port"]),
                    lambda: self.host.groups.runtime_token(self.environment_id), guard)
                if self.settings is not None:
                    mode=self.settings.server_modes.get(server_id,self.settings.storage_mode)
                    if mode=="local": raise BindingError("SCOPE_MISMATCH","local 权威不接收中心副作用。")
                    if isinstance(peer,V2TokenPeer): peer.storage_mode=mode
                capabilities = peer.handshake()
                if capabilities.get("token_reservation_query") is not True:
                    raise BindingError("NOT_SUPPORTED", "原目标的恢复能力尚未就绪。")
                self.peers[key] = peer
            except BindingError as error:
                self.failures[key] = error.code
                raise
            except Exception:
                self.failures[key] = "STORAGE_ERROR"
                raise BindingError("STORAGE_ERROR", "原目标能力检查未完成。") from None
        return self.peers[key]
