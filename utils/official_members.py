"""Read-only QQ group audits, gated by current group administrator grants.

The documented APIs are invite-only. No moderation/automatic unbind, guessed
nickname, persistent member list, credentials or raw message storage.
"""
import asyncio
from collections import OrderedDict,deque
from copy import deepcopy
import re
import time

from .official_core import OfficialError,identifier,text
from .official_delivery import OfficialApiError


class OfficialMembers:
    def __init__(self):
        self.pages,self.nicknames=OrderedDict(),OrderedDict()
        self.requests=deque()
        self.lock=asyncio.Lock()
        self.next_request=0

    def observe(self,scope):
        now=time.monotonic()
        self.nicknames=OrderedDict((k,v) for k,v in self.nicknames.items() if now<v[1])
        mentioned=dict(scope.mention_nicknames)
        observations=[(scope.user_id,scope.nickname)]
        observations.extend((user,mentioned.get(user,"")) for user in scope.mentions)
        for user,nickname in observations:
            key=(scope.environment_id,scope.instance_id,scope.group_id,user)
            self.nicknames.pop(key,None)
            if nickname:
                while len(self.nicknames)>=3000:self.nicknames.popitem(last=False)
                self.nicknames[key]=(nickname,now+300)

    async def roster(self,scope,revision,transport,guard):
        guard()
        if transport is None or not callable(getattr(transport,"member_page",None)):
            raise OfficialError("当前框架事件没有已核验的官方成员读取通道。")
        key=(scope.environment_id,scope.instance_id,scope.group_id,revision)
        now=time.monotonic()
        self.pages=OrderedDict((k,v) for k,v in self.pages.items() if now<v["expires"])
        cached=self.pages.get(key)
        if cached is not None:
            guard()
            if "error" in cached:raise OfficialError(cached["error"])
            return deepcopy(cached)
        if self.lock.locked():raise OfficialError("官方群成员扫描正在进行，请稍后重试。")
        async with self.lock:
            members,seen,cursors=[],set(),set()
            cursor,complete="",False
            deadline=time.monotonic()+45
            try:
                for _ in range(34):
                    guard()
                    delay=max(0,self.next_request-time.monotonic())
                    if time.monotonic()+delay>=deadline:break
                    if delay:await asyncio.sleep(delay)
                    guard()
                    now=time.monotonic()
                    while self.requests and now-self.requests[0]>=60:self.requests.popleft()
                    if len(self.requests)>=55:raise OfficialError("本应用成员读取预算暂满，请一分钟后重试。")
                    self.requests.append(now);self.next_request=now+1.05
                    data=await asyncio.wait_for(transport.member_page(scope.group_id,cursor=cursor,revalidate=guard),
                        timeout=min(8,max(0.1,deadline-now)))
                    rows,next_cursor=data.get("members"),data.get("next_cursor")
                    if (not isinstance(rows,list) or len(rows)>30 or not isinstance(next_cursor,str)
                            or len(next_cursor)>1024 or any(ord(c)<33 or ord(c)==127 for c in next_cursor)):
                        raise OfficialError("官方成员回执格式无效，未将其当作完整名单。")
                    for value in rows:
                        if not isinstance(value,dict):raise OfficialError("官方成员条目无效。")
                        member=identifier(value.get("member_openid"))
                        if member in seen:raise OfficialError("官方成员分页出现重复，本次不发布扫描结论。")
                        seen.add(member)
                        if len(members)>=1000:break
                        try:label=text(value.get("username",""),128)
                        except OfficialError:label=""
                        # username is display-only, NEVER evidence of a group card.
                        members.append(dict(user_id=member,display=label or "昵称不可用",bot=value.get("bot") is True))
                    if not next_cursor and len(seen)<=1000:
                        complete=True;break
                    if len(members)>=1000:break
                    if not rows or next_cursor in cursors:raise OfficialError("官方成员分页无法安全继续。")
                    cursors.add(next_cursor);cursor=next_cursor
                guard()
                result=dict(members=members,complete=complete,expires=time.monotonic()+300)
                while len(self.pages)>=8:self.pages.popitem(last=False)
                self.pages[key]=result
                return deepcopy(result)
            except asyncio.CancelledError:raise
            except Exception as error:
                if isinstance(error,OfficialApiError) and error.category=="unauthorized":
                    message="QQ官方群成员接口属于内邀能力，当前应用或群授权未通过；请由用户在开放平台自行确认资格。"
                elif isinstance(error,OfficialError):message=str(error)
                else:message="官方成员扫描暂未完成，本次不输出不完整的判断。"
                while len(self.pages)>=8:self.pages.popitem(last=False)
                self.pages[key]=dict(error=message,expires=time.monotonic()+60)
                raise OfficialError(message) from None

    def nickname_audit(self,scope,roster,group):
        if not group["policy"]["nickname_check_enabled"]:
            raise OfficialError("本群昵称检测已关闭，可用 /mc nicknamecheck 开。")
        unknown,bad,checked,ignored=0,[],0,0
        now=time.monotonic()
        for member in roster["members"]:
            user=member["user_id"]
            if member["bot"] or user in group["nickname_ignore"]:
                ignored+=1;continue
            key=(scope.environment_id,scope.instance_id,scope.group_id,user)
            cached=self.nicknames.get(key)
            if cached is None or now>=cached[1]:
                unknown+=1;continue
            checked+=1
            match=re.search(r"[（(](.*?)[）)]",cached[0])
            if match is None or not match[1].strip():bad.append(member["display"])
        lines=["官方成员扫描结果："+str(len(roster["members"]))+" 人",
            f"括号昵称格式：可核验 {checked}，不符合 {len(bad)}，无法核验 {unknown}，忽略 {ignored}。",
            "群成员接口的username不是群昵称；仅使用近5分钟插件处理的群事件author或@目标mentions中的member_nickname。"]
        lines.extend("不符合："+label for label in bad[:30])
        if len(bad)>30:lines.append("不符合成员仅展示前30个。")
        if not roster["complete"]:lines.append("扫描达到容量/时间上限，仅为部分成员；不代表完整群名单。")
        lines.append("该检测只提示，不修改群成员或游戏绑定；绑定提交仍独立校验实时昵称。")
        return "官方群昵称检测",lines

    @staticmethod
    def unbound_members(roster,active):
        """返回 (未绑定名单[{user_id,name,qq}], 非机器人成员总数)，供名单图与文本共用。"""
        members=[m for m in roster["members"] if not m["bot"]]
        unbound=[{"user_id":m["user_id"],"name":m["display"],"qq":m["user_id"]}
                 for m in members if m["user_id"] not in active]
        return unbound,len(members)

    @staticmethod
    def binding_audit(roster,name,active):
        members=[m for m in roster["members"] if not m["bot"]]
        unbound=[m for m in members if m["user_id"] not in active]
        lines=["权威服务器："+name,f"读取 {len(members)} 位非机器人成员，其中当前权威未查到active绑定 {len(unbound)} 位。"]
        lines.extend("未绑定："+member["display"] for member in unbound[:30])
        if len(unbound)>30:lines.append("名单只展示前30位，请让本人使用查绑定命令核对。")
        if not roster["complete"]:lines.append("扫描达到容量/时间上限，以上仅针对已读取成员。")
        lines.append("未出现在名单不等于退群；本次不触发任何自动解绑。")
        lines.append("成员扫描结果可缓存5分钟，不作为实时群成员资格证明。")
        return "官方群绑定检测",lines

    def close(self):
        self.pages.clear();self.nicknames.clear();self.requests.clear()
