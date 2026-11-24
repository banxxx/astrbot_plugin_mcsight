"""Official mutation ingress with a bounded persistent no-replay ledger.

Only hashes, action/target IDs, revisions and stable results are persisted.
Command text and credentials are transient and never logged.
"""
from copy import deepcopy
import hashlib
import json
import re
import unicodedata
from threading import RLock
from uuid import UUID, uuid4, uuid5

from .official_core import MAX_SERVERS, OfficialError, atomic_json, read_json, uuid_value
from .host_validator import normalize_host_input, validate_host
from .official_bindings import BindingError
from .official_binding_service import BindingScope
from .official_transport import ModTarget, V2ModClient

COMMANDS=frozenset({"broadcast","console","exec","add","edit","del","hide","show","command",
    "batchadd","batchremove","move","swap","lastonline","nicknamecheck"})
NAMESPACE=UUID("86e371e0-9fc6-45b3-b7bb-c42e74b8f9dc")
_LOCK=RLock()


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=True,sort_keys=True,separators=(",",":")).encode("ascii")).hexdigest()


def numeric(value,label,minimum,maximum):
    try: result=int(value)
    except (TypeError,ValueError): raise OfficialError(label+"必须是整数。") from None
    if not minimum<=result<=maximum:raise OfficialError(label+"超出允许范围。")
    return result


MAX_NAME_WIDTH=20
MAX_REMARK_WIDTH=50


def display_width(value):
    return sum(2 if unicodedata.east_asian_width(c) in ("W","F") else 1 for c in value)


def name_error(value):
    """与 OneBot 的 validate_server_name 同一口径；返回拒绝原因，合法返回空串。"""
    if any(c.isspace() for c in value):
        return "名称不能含空格（指令用空格分隔参数），请用 - 或 _ 连接"
    bad=[c for c in ("[","]","【","】") if c in value]
    if bad:
        return "名称不能包含方括号（{}），/执行 要用 [服务器名] 定界".format(" ".join(bad))
    if display_width(value)>MAX_NAME_WIDTH:
        return "名称最多 {} 字符（约 {} 个汉字），当前 {} 字符".format(
            MAX_NAME_WIDTH,MAX_NAME_WIDTH//2,display_width(value))
    return ""


def remark_error(value):
    if display_width(value)>MAX_REMARK_WIDTH:
        return "备注最多 {} 字符（约 {} 个汉字），当前 {} 字符".format(
            MAX_REMARK_WIDTH,MAX_REMARK_WIDTH//2,display_width(value))
    return ""


def add_tokens(arguments):
    return re.sub(r"\s*:\s*",":"," ".join(str(arguments).split()).replace("：",":")).split()


def host_parts(value,allow_private,default_game_port=None):
    """与 OneBot 相同的 host 规范化与 SSRF 校验；返回 (host,game_port,error)。"""
    value=normalize_host_input(value)
    error=validate_host(value,allow_private=allow_private)
    if error:return None,None,error
    address,separator,port=value.partition(":")
    if separator:return address,numeric(port,"游戏端口",1,65535),None
    return value,default_game_port,None


class OfficialWriteCommands:
    def __init__(self,groups,selection,*,client_factory=V2ModClient):
        self.groups,self.selection,self.client_factory=groups,selection,client_factory

    def _load(self,event):
        path=self.groups.root/event.environment_id/"command_receipts.json"
        state=read_json(path,{"schema_version":1,"receipts":{}},limit=8*1024*1024)
        if (not isinstance(state,dict) or set(state)!={"schema_version","receipts"} or type(state["schema_version"]) is not int
                or state["schema_version"]!=1 or not isinstance(state["receipts"],dict) or len(state["receipts"])>10000):
            raise OfficialError("写操作去重记录缺损，暂停新操作。")
        required={"event_key","principal_hash","group_hash","action","target_server_ids","payload_hash","config_revision","state","targets"}
        for key,row in state["receipts"].items():
            uuid_value(key)
            if (not isinstance(row,dict) or set(row)!=required or row["state"] not in ("PREPARED","DISPATCHED","SUCCEEDED","REJECTED","UNKNOWN")
                    or row["action"] not in COMMANDS-{"command","exec"} or not isinstance(row["targets"],dict)
                    or not isinstance(row["target_server_ids"],list) or row["target_server_ids"]!=sorted(set(row["target_server_ids"]))
                    or set(row["targets"])!=set(row["target_server_ids"])
                    or any(value not in ("PREPARED","DISPATCHED","SUCCEEDED","REJECTED","UNKNOWN") for value in row["targets"].values())
                    or type(row["config_revision"]) is not int or row["config_revision"]<1
                    or any(not isinstance(row[name],str) or len(row[name])!=64 or any(c not in "0123456789abcdef" for c in row[name])
                        for name in ("event_key","principal_hash","group_hash","payload_hash"))):
                raise OfficialError("写操作去重记录无效，暂停新操作。")
            for server in row["target_server_ids"]: uuid_value(server)
        return path,state

    def _guard(self,event,revision=None,*,admin=True):
        selected=self.selection() or {}
        if (selected.get("platform"),selected.get("environment_id"),selected.get("instance_id"))!=("qq_official",event.environment_id,event.instance_id):
            raise OfficialError("原官方实例已失效。")
        group=self.groups.group(event.environment_id,group_id=event.group_id)
        admins=self.groups.environment_administrators(event.environment_id)
        level=self.groups.access_level(event,group,admins) if group else 0
        if group is None or not group["enabled"] or level<(3 if admin else 1):
            raise OfficialError("当前身份无权执行此操作。")
        if revision is not None and group["revision"]!=revision:
            raise OfficialError("操作期间配置发生变化；本次不继续外发，请查询原回执。")
        return group,level

    def execute(self,event,command,arguments):
        if not isinstance(arguments,str) or len(arguments)>1200:raise OfficialError("操作参数过长。")
        command="console" if command=="exec" else command
        with _LOCK:
            with self.groups.lock:
                group,level=self._guard(event,admin=command!="command")
                if command in ("lastonline","nicknamecheck"):
                    words=arguments.split()
                    if not words or len(words)==1 and words[0].lower() in ("status","state","查询"):
                        field="show_last_online" if command=="lastonline" else "nickname_check_enabled"
                        name="上次在线显示" if command=="lastonline" else "全群昵称检测"
                        return name,["当前"+("开启" if group["policy"][field] else "关闭"),"使用 /mc "+command+" 开|关 修改。"]
                    if command=="nicknamecheck" and words in (["忽略列表"],["ignorelist"]):
                        return "本群昵称检测忽略列表",list(group["nickname_ignore"]) or ["空"]
                path,state=self._load(event)
                if command=="command":
                    operation=uuid_value(arguments.strip()); row=state["receipts"].get(operation)
                    if (row is None or row["group_hash"]!=digest([event.environment_id,event.group_id])
                            or row["principal_hash"]!=digest(event.key) and level<3):
                        raise OfficialError("未找到当前身份可查看的写操作回执。")
                    return "官方写操作状态",self._lines(operation,row,group)
                action,servers,payload,changes=self._parse(
                    command,arguments,group,event,allow_private=level>=4)
                if changes is not None:
                    candidate=deepcopy(group); candidate.update(deepcopy(changes))
                    for server in candidate["servers"]:
                        if not server["server_id"]: server["server_id"]=str(uuid4())
                    self.groups._validate_group(candidate)
                event_key=digest([event.environment_id,event.instance_id,event.group_id,event.message_id,event.user_id,action])
                operation=str(uuid5(NAMESPACE,event_key)); targets=sorted(s["server_id"] for s in servers)
                body_hash=digest([action,payload,changes,targets]); previous=state["receipts"].get(operation)
                if previous is not None:
                    if previous["payload_hash"]!=body_hash: raise OfficialError("原事件已用于不同操作；本次不重复执行。")
                    return "官方写操作原回执",self._lines(operation,previous,group)
                if len(state["receipts"])>=10000: raise OfficialError("写操作回执已满，暂停新增；请按交付说明维护。")
                row=dict(event_key=event_key,principal_hash=digest(event.key),group_hash=digest([event.environment_id,event.group_id]),action=action,
                    target_server_ids=targets,payload_hash=body_hash,config_revision=group["revision"],state="PREPARED",targets={key:"PREPARED" for key in targets})
                state["receipts"][operation]=row; atomic_json(path,state,limit=8*1024*1024)
            if changes is not None:
                row["state"]="DISPATCHED"; atomic_json(path,state,limit=8*1024*1024)
                try:
                    with self.groups.lock:
                        self._guard(event,group["revision"])
                        updated=self.groups.update(event.environment_id,group["config_id"],group["revision"],changes)
                    row["state"]="SUCCEEDED"; row["targets"]={key:"SUCCEEDED" for key in targets}
                    group=updated
                except Exception:
                    row["state"]="UNKNOWN"; row["targets"]={key:"UNKNOWN" for key in targets}
                atomic_json(path,state,limit=8*1024*1024)
                return self._config_title(command,arguments,row),self._lines(operation,row,group)
            output_lines=[]
            for server in servers:
                key=server["server_id"]
                try:
                    with self.groups.lock: self._guard(event,group["revision"])
                    token=(self.groups.runtime_console_token(event.environment_id,key) if action=="console"
                           else self.groups.runtime_token(event.environment_id))
                    if not token: raise OfficialError("目标未配置所需独立令牌。")
                    client=self.client_factory(ModTarget(BindingScope(event.environment_id,event.group_id,key),server["host"],server["api_port"]))
                except Exception:
                    row["targets"][key]="REJECTED"; continue
                row["state"]="DISPATCHED"; row["targets"][key]="DISPATCHED"; atomic_json(path,state,limit=8*1024*1024)
                try:
                    reply=client.request("command",dict(operation_id=operation,request_key=event_key,action=action,payload=payload,
                        config_revision=str(group["revision"])),token=token,revalidate=lambda:self._guard(event,group["revision"]))
                    if reply.get("operation_id")!=operation or reply.get("state") not in ("SUCCEEDED","REJECTED","UNKNOWN"):
                        raise OfficialError("写操作结果待确认。")
                    row["targets"][key]=reply["state"]
                    output=reply.get("output",[])
                    if not isinstance(output,list) or len(output)>30 or any(not isinstance(line,str) for line in output):
                        raise OfficialError("控制台输出结构无效。")
                    output_lines.extend(line[:500] for line in output)
                except Exception:
                    row["targets"][key]="UNKNOWN"
                atomic_json(path,state,limit=8*1024*1024)
            statuses=set(row["targets"].values())
            row["state"]="SUCCEEDED" if statuses=={"SUCCEEDED"} else "REJECTED" if statuses=={"REJECTED"} else "UNKNOWN"
            atomic_json(path,state,limit=8*1024*1024)
            bounded_output="\n".join(output_lines)[:1200]
            return "官方写操作结果",self._lines(operation,row,group)+(["本次控制台回显："]+bounded_output.splitlines() if bounded_output else [])

    @staticmethod
    def _parse(command,arguments,group,event=None,*,allow_private=False):
        if not isinstance(arguments,str) or len(arguments)>1200: raise OfficialError("操作参数过长。")
        servers=deepcopy(group["servers"]); words=arguments.split()
        if command in ("lastonline","nicknamecheck"):
            field="show_last_online" if command=="lastonline" else "nickname_check_enabled"
            policy=deepcopy(group["policy"])
            if not words or words==["status"] or words==["查询"]:
                raise OfficialError(("上次在线显示" if command=="lastonline" else "全群昵称检测")+"当前"+("开启" if policy[field] else "关闭")+"；使用 开/关 修改。")
            if len(words)==1 and words[0].lower() in ("开","on","true","1","开启","关","off","false","0","关闭"):
                policy[field]=words[0].lower() in ("开","on","true","1","开启")
                return command,[],"",dict(policy=policy)
            if command=="nicknamecheck":
                if words[0] in ("忽略列表","ignorelist") and len(words)==1:
                    raise OfficialError("本群昵称检测忽略列表："+("、".join(group["nickname_ignore"]) or "空"))
                action=words[0]
                if action in ("忽略","ignore","取消忽略","unignore"):
                    from .official_binding_commands import mentioned_user
                    target=mentioned_user(event,words[1:] if len(words)>1 else [])
                    if target is None: raise OfficialError("请@唯一成员或指定 user <官方标识>。")
                    values=set(group["nickname_ignore"])
                    if action in ("忽略","ignore"): values.add(target)
                    else: values.discard(target)
                    return command,[],"",dict(nickname_ignore=sorted(values))
            raise OfficialError("用法：/mc "+command+" 开|关；nicknamecheck另支持忽略/取消忽略/忽略列表。")
        if command=="batchadd":
            entries="".join(words).replace("，",",").replace("：",":").split(",")
            if not words or len(entries)>MAX_SERVERS:
                raise OfficialError(f"用法：/mc batchadd 名称:主机[:API端口],名称:主机[:API端口]；最多{MAX_SERVERS}项，整批校验。")
            if len(servers)+len(entries)>MAX_SERVERS:
                raise OfficialError(f"添加失败：每个群最多绑定 {MAX_SERVERS} 个服务器。")
            seen=set()
            for entry in entries:
                parts=entry.split(":")
                if len(parts) not in (2,3): raise OfficialError("批量条目须为 名称:主机[:API端口]，游戏端口随后可edit host修改。")
                if not parts[0]: raise OfficialError("添加失败：名称不能为空。")
                width_error=name_error(parts[0])
                if width_error: raise OfficialError("添加失败："+width_error)
                if parts[0] in seen or any(s["name"]==parts[0] for s in servers):
                    raise OfficialError(f"添加失败：名称「{parts[0]}」已存在。")
                seen.add(parts[0])
                host,game_port,host_error=host_parts(parts[1],allow_private,25565)
                if host_error: raise OfficialError("添加失败："+host_error)
                if game_port!=25565: raise OfficialError("批量添加的主机不能带游戏端口；请先用 /mc add 逐个添加。")
                servers.append(dict(server_id="",name=parts[0],host=host,game_port=25565,
                    api_port=numeric(parts[2],"API端口",1,65535) if len(parts)==3 else None,hidden=False,remark=""))
            return command,[],"",dict(servers=servers)
        if command=="batchremove":
            names="".join(words).replace("，",",").split(",")
            if not words or len(names)>20 or len(set(names))!=len(names) or not set(names)<={s["name"] for s in servers}:
                raise OfficialError("用法：/mc batchremove 名1,名2；每个名称必须存在且不重复，整批修改。")
            targets=[s for s in servers if s["name"] in names]
            return command,targets,"",dict(servers=[s for s in servers if s["name"] not in names])
        if command in ("move","swap"):
            if len(words)!=2: raise OfficialError("用法：/mc move <服名> <从0开始位置> 或 /mc swap <服名1> <服名2>。")
            target=next((s for s in servers if s["name"]==words[0]),None)
            if target is None: raise OfficialError("服务器不存在。")
            if command=="move":
                position=numeric(words[1],"目标位置",0,len(servers)-1)
                servers.remove(target); servers.insert(position,target); targets=[target]
            else:
                second=next((s for s in servers if s["name"]==words[1]),None)
                if second is None or second is target: raise OfficialError("请选择两个不同且存在的服务器。")
                left,right=servers.index(target),servers.index(second); servers[left],servers[right]=servers[right],servers[left]; targets=[target,second]
            return command,targets,"",dict(servers=servers)
        if command in ("broadcast","console"):
            # 与 OneBot 一致：/mc say [-s] 或 /mc say 服名 消息；控制台还接受 [服名]。
            selected=None
            payload=arguments.strip()
            if re.match(r"^-s(?:\s|$)",payload):
                fields=payload.split(maxsplit=2)
                if len(fields)!=3: raise OfficialError("用法：/mc "+command+" -s <服务器名> <内容>；广播可显式选 all。")
                selected,payload=fields[1],fields[2].strip()
            else:
                matched=re.match(r"^\s*[\[【]([^\[\]【】]*)[\]】]\s*(.*)$",payload,re.S)
                if matched:
                    selected,payload=matched.group(1).strip(),matched.group(2).strip()
                elif command=="broadcast":
                    first,_,rest=payload.partition(" ")
                    if first and (first=="all" or any(s["name"]==first for s in servers)):
                        selected,payload=first,rest.strip()
                    else:
                        selected,payload="all",payload
                else:
                    raise OfficialError("用法：/mc console -s <服务器名> <内容>（或 /mc console [服务器名] <内容>）。")
            if command=="console" and selected=="all": raise OfficialError("控制台必须明确选择一台服务器。")
            if not selected: raise OfficialError("请指定服务器名。")
            targets=[s for s in servers if s["api_port"] is not None and (s["name"]==selected or selected=="all")]
            limit=200 if command=="console" else 40
            if not targets or not payload or len(payload)>limit or any(ord(c)<32 or ord(c)==127 for c in payload):
                if command=="broadcast" and payload and len(payload)>limit:
                    raise OfficialError(f"广播消息过长（最多 {limit} 个字符），当前长度：{len(payload)}")
                raise OfficialError("目标或内容无效。")
            return command,targets,payload,None
        if command=="add":
            tokens=add_tokens(arguments)
            if len(tokens) not in (2,3): raise OfficialError("用法：/mc add <名称> <主机[:游戏端口]> [API端口]。")
            width_error=name_error(tokens[0])
            if width_error: raise OfficialError("添加失败："+width_error)
            if len(servers)>=MAX_SERVERS:
                raise OfficialError(f"添加失败：每个群最多绑定 {MAX_SERVERS} 个服务器。")
            if any(s["name"]==tokens[0] for s in servers):
                raise OfficialError(f"添加失败：名称「{tokens[0]}」已存在。")
            host,game_port,host_error=host_parts(tokens[1],allow_private,25565)
            if host_error: raise OfficialError("添加失败："+host_error)
            servers.append(dict(server_id="",name=tokens[0],host=host,game_port=game_port,
                api_port=numeric(tokens[2],"API端口",1,65535) if len(tokens)==3 else None,hidden=False,remark=""))
            return command,[],"",dict(servers=servers)
        if command not in ("edit","del","hide","show") or not words: raise OfficialError("服务器管理参数无效。")
        target=next((s for s in servers if s["name"]==words[0]),None)
        if target is None: raise OfficialError("服务器不存在。")
        if command=="del":
            if len(words)!=1: raise OfficialError("用法：/mc del <服务器名>。")
            servers.remove(target)
        elif command in ("hide","show"):
            if len(words)!=1: raise OfficialError("用法：/mc hide|show <服务器名>。")
            target["hidden"]=command=="hide"
        else:
            if len(words)<3 or words[1] not in ("name","host","port","remark"): raise OfficialError("用法：/mc edit <名称> name|host|port|remark <值>。")
            value=" ".join(words[2:])
            if words[1]=="host":
                host,game_port,host_error=host_parts(value,allow_private)
                if host_error: raise OfficialError("修改失败："+host_error)
                target["host"]=host
                if game_port is not None: target["game_port"]=game_port
            elif words[1]=="name":
                error=name_error(value)
                if error: raise OfficialError("修改失败："+error)
                if any(s is not target and s["name"]==value for s in servers):
                    raise OfficialError(f"修改失败：名称「{value}」已存在。")
                target["name"]=value
            elif words[1]=="remark":
                error=remark_error(value)
                if error: raise OfficialError("修改失败："+error)
                target["remark"]=value
            else: target["api_port"]=numeric(value,"API端口",1,65535)
        return command,[target],"",dict(servers=servers)

    @staticmethod
    def _config_title(command,arguments,row):
        """配置修改成功时先给与 OneBot 相同的一句人话；回执细节仍附在后面。"""
        if row["state"]!="SUCCEEDED":return "官方服务器配置结果"
        words=arguments.split(); name=words[0] if words else ""
        if command=="add":
            tokens=add_tokens(arguments)
            lines=[f"✅ 添加成功：{tokens[0] if tokens else name}"]
            if len(tokens)>=2:
                host=tokens[1]
                lines.append(f"主机：{host}")
                if len(tokens)>=3:
                    lines.append(f"模组 API 端口：{tokens[2]}")
                    try: port=int(tokens[2])
                    except ValueError: port=None
                    if port==25565 and ":" not in host:
                        lines.append("提示：25565 已按模组 API 端口处理；"
                                     "若本意是游戏端口，请写成 主机:25565。")
                else:
                    lines.append("模组 API 端口：未配置（/执行、/广播 不可用）")
                if host.endswith(":25565"):
                    lines.append("提示：显式写了默认端口 25565；使用 SRV 的服务器建议只写域名。")
            return "\n".join(lines)
        if command=="edit":
            mode=words[1] if len(words)>1 else ""
            return {"name":"重命名成功。","host":"修改主机地址成功。",
                    "port":"修改模组 API 端口成功。","remark":"修改备注成功。"}.get(mode,"✅ 修改成功。")
        if command=="del":return f"删除成功：{name}"
        if command=="hide":return f"✅ 已隐藏「{name}」，它不会再出现在查询和状态卡片中。"
        if command=="show":return f"✅ 已取消隐藏「{name}」，它会重新参与查询和状态卡片。"
        if command=="batchadd":
            entries="".join(words).replace("，",",").replace("：",":").split(",")
            return f"批量添加：成功 {len([entry for entry in entries if entry])} 个"
        if command=="batchremove":
            names="".join(words).replace("，",",").split(",")
            return f"批量删除：成功 {len([entry for entry in names if entry])} 个"
        if command=="move":return "移动成功。"
        if command=="swap":return "交换成功。"
        if command=="lastonline":
            on=bool(words) and words[0].lower() in ("开","on","true","1","开启")
            return ("✅ 已开启「上次在线」显示，下次查询服务器时会显示最后活动时间。"
                    if on else "✅ 已关闭「上次在线」显示。")
        if command=="nicknamecheck":
            if words and words[0] in ("忽略","ignore","取消忽略","unignore"):
                return "✅ 已更新昵称检测忽略名单。"
            on=bool(words) and words[0].lower() in ("开","on","true","1","开启")
            return "✅ 已开启本群昵称检测。" if on else "✅ 已关闭本群昵称检测。"
        return "官方服务器配置结果"

    @staticmethod
    def _lines(operation,row,group):
        labels={"PREPARED":"未外发，重启后不自动继续","DISPATCHED":"结果未知，不自动重发","UNKNOWN":"结果未知或部分完成，不自动重发","SUCCEEDED":"已完成","REJECTED":"未获执行确认"}
        names={s["server_id"]:s["name"] for s in group["servers"]}
        lines=["操作编号："+operation,labels[row["state"]]]
        lines.extend(names.get(server,"原服务器")+"："+labels[result] for server,result in row["targets"].items())
        lines.append("查询：/mc command "+operation)
        return lines
