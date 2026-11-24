"""AstrBot 4.29.1 / qq-botpy 1.2.1 single-attempt group transport.

The running framework owns authentication and its session. This module does not load
configuration, print request headers, call SDK retry helpers, or fall back to active
messages. Tests inject an entirely synthetic HTTP object; no live session is started.
"""
import asyncio
import inspect
import json
import math
from urllib.parse import quote

from .official_core import OfficialError, identifier


MAX_REPLY_BYTES = 512 * 1024
METHOD_PATHS = {"post_group_message": "messages", "post_group_base64file": "files"}
API_ORIGIN = "https://api.bot.qq.com"


class OfficialApiError(OfficialError):
    """Only stable categories escape; never expose the remote body or trace."""
    def __init__(self,category,message,*,rejected=False):
        super().__init__(message)
        self.category,self.rejected=category,rejected


def checked_response(status,result):
    if not isinstance(result,dict):
        raise OfficialApiError("unknown","QQ 官方回执无效，结果待确认，本次不重发。")
    codes=[result[name] for name in ("err_code","code") if name in result]
    if any(type(code) is not int for code in codes):
        raise OfficialApiError("unknown","QQ 官方错误码格式无效，结果待确认。")
    code=next((code for code in codes if code),0)
    if status in (401,403) or code in (11241,11243,11251,11253,11254,11261,11262,11264,11265,11274,11275,11282):
        raise OfficialApiError("unauthorized","QQ 官方接口未获授权，请核对应用资格、群授权和连接。",rejected=True)
    if status==429 or code in (20028,304045,304049,304050,1100100,1100308):
        raise OfficialApiError("limited","QQ 官方接口暂时限流，本次不自动重发。",rejected=True)
    if code in (304023,304024) or status in (201,202):
        raise OfficialApiError("pending","QQ 官方消息已进入异步审核，结果待确认，本次不重发。")
    if code in (304026,304027,40034005):
        raise OfficialApiError("expired","QQ 官方被动回复窗口已结束，请发送新消息。",rejected=True)
    if code:
        raise OfficialApiError("rejected","QQ 官方接口拒绝本次请求，请核对参数或应用能力。",rejected=True)
    if status not in (200,201,202):
        raise OfficialApiError("unknown","QQ 官方接口未确认结果，本次不自动重发。")
    return result


class BotpyGroupTransport:
    def __init__(self, http, *, image_schema_verified=False):
        self.http = http
        self.supports_images = image_schema_verified

    async def call(self, method, *, group_openid, revalidate=None, **payload):
        http_method = "POST"
        if method == "delete_group_message":
            # 撤回自己的群消息：DELETE /v2/groups/{group_openid}/messages/{message_id}
            # 官方消息 id 形如 ROBOT1.0_xxx，通常长于身份字段的 64 字符上限，
            # 因此这里单独校验，而不是复用 identifier()。
            message_id = payload.pop("message_id", None)
            if (not isinstance(message_id, str) or not 1 <= len(message_id) <= 256
                    or message_id != message_id.strip() or message_id in (".", "..")
                    or any(ord(c) < 33 or ord(c) == 127 for c in message_id)):
                raise OfficialError("官方群消息标识无效。")
            suffix = "messages/" + quote(message_id, safe="")
            http_method = "DELETE"
        else:
            suffix = METHOD_PATHS.get(method)
            if suffix is None:
                raise OfficialError("不支持的官方群发送动作。")
        if suffix == "files":
            if not self.supports_images:
                raise OfficialError("当前事件类的本地图片协议尚未核验，请使用分页文字。")
            # Verified against AstrBot v4.29.1 upload_group_and_c2c_image;
            # this is our internal alias, not a nonexistent BotPy API method.
            identifier(group_openid)  # Group identity belongs to the path.
        group = quote(identifier(group_openid), safe="").replace(".", "%2E")
        try:
            # Framework authentication may refresh its own token. No message request
            # is issued until authorization is rechecked after this await.
            await self.http.check_session()
            if revalidate is not None:
                revalidate()
            # Current QQ OpenAPI has one origin. Testing eligibility is owned
            # by the platform/app, never guessed by rewriting a sandbox domain.
            url = f"{API_ORIGIN}/v2/groups/{group}/{suffix}"
            session = self.http._session
            headers = self.http._headers
            if (session is None or not callable(getattr(session, "request", None))
                    or not isinstance(headers, dict) or not headers.get("Authorization")):
                raise OfficialError("官方适配器尚未完成连接认证。")
            timeout = self.http.timeout
            if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                    or not math.isfinite(timeout) or timeout <= 0):
                raise OfficialError("官方适配器请求预算无效。")
            import aiohttp
            # One request is intentionally performed exactly once at this layer. Never
            # use BotHttp.request: its connection-reset branch can transparently replay.
            async with session.request(
                method=http_method, url=url, headers=headers,
                json=payload if http_method == "POST" else None,
                allow_redirects=False, timeout=aiohttp.ClientTimeout(total=min(timeout, 20)),
            ) as response:
                chunks, size = [], 0
                async for chunk in response.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > MAX_REPLY_BYTES:
                        raise OfficialError("QQ 官方回执超过限制，发送结果待确认。")
                    chunks.append(chunk)
                raw = b"".join(chunks)
                # 撤回接口成功时可以是空响应体；不要把它当成无法解析的回执。
                if http_method == "DELETE" and response.status in (200, 204) and not raw.strip():
                    return {}
                try:result=json.loads(raw)
                except (ValueError,UnicodeError):
                    checked_response(response.status,{})
                    raise OfficialApiError("unknown","QQ 官方返回了无法解析的回执，结果待确认。") from None
                return checked_response(response.status,result)
        except OfficialError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            # No exception text: SDK or connector exceptions may include private
            # headers, endpoint identities, or a raw response.
            raise OfficialError("QQ 官方发送结果待确认；请用新消息查询，本次不自动重发。") from None

    async def member_page(self,group_openid,*,cursor="",member_openid=None,revalidate=None):
        """Documented GET only; no member moderation or automatic retries."""
        from urllib.parse import urlencode
        group=quote(identifier(group_openid),safe="").replace(".","%2E")
        path=f"/v2/groups/{group}/members"
        if member_openid is not None:
            path+="/"+quote(identifier(member_openid),safe="").replace(".","%2E")
        elif cursor:
            if not isinstance(cursor,str) or len(cursor)>1024 or any(ord(c)<33 or ord(c)==127 for c in cursor):
                raise OfficialError("成员分页游标无效。")
            path+="?"+urlencode({"cursor":cursor})
        try:
            await self.http.check_session()
            if revalidate is not None: revalidate()
            session,headers=self.http._session,self.http._headers
            if session is None or not isinstance(headers,dict) or not headers.get("Authorization"):
                raise OfficialError("官方群连接尚未认证。")
            import aiohttp
            async with session.request(method="GET",url=API_ORIGIN+path,headers=headers,allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=8,connect=2)) as response:
                chunks,size=[],0
                async for chunk in response.content.iter_chunked(65536):
                    size+=len(chunk)
                    if size>MAX_REPLY_BYTES: raise OfficialError("成员响应超过限制，停止本次扫描。")
                    chunks.append(chunk)
                try:result=json.loads(b"".join(chunks))
                except (ValueError,UnicodeError):
                    checked_response(response.status,{})
                    raise OfficialApiError("temporary","官方群成员回执无法解析，停止本次扫描。") from None
                data=checked_response(response.status,result)
            if revalidate is not None: revalidate()
            return data
        except OfficialError: raise
        except asyncio.CancelledError: raise
        except Exception:
            raise OfficialApiError("temporary","官方群成员接口暂不可用，本次不自动重试。") from None


def transport_for(event):
    """Use only the verified BotPy transport family; unknown adapters fail closed."""
    bot = getattr(event, "bot", None)
    api = getattr(bot, "api", None)
    http = getattr(api, "_http", None)
    if (http is None or type(http).__module__ != "botpy.http"
            or type(http).__name__ != "BotHttp"
            or not inspect.iscoroutinefunction(getattr(http, "check_session", None))):
        return None
    event_family = any(cls.__module__ == "astrbot.core.platform.sources.qqofficial.qqofficial_message_event"
        and cls.__name__ == "QQOfficialMessageEvent" for cls in type(event).__mro__)
    image_method = getattr(event, "upload_group_and_c2c_image", None)
    image_schema_verified = False
    if event_family and inspect.iscoroutinefunction(image_method):
        try:
            inspect.signature(image_method).bind(image_base64="SYNTHETIC", file_type=1, group_openid="SYNTHETIC")
            image_schema_verified = True
        except (TypeError, ValueError):
            pass
    return BotpyGroupTransport(http, image_schema_verified=image_schema_verified)
