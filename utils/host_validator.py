"""服务器 host 的合法性校验（SSRF 防护）。

/mc add、/mc edit host、/mc batchadd 允许把任意 host 写进群配置，机器人随后会向
http://{host}:{port} 发起请求，而 mod_http 的错误文案能区分「连不上/超时/返回了
无法解析的响应」——恶意群管理员可借此对机器人所在内网做端口扫描与存活探测。

规则：
- 语法：允许合法域名或 IPv4 字面量，末尾可带「:游戏端口」（这是插件里唯一的游戏端口写法）；
  IPv6 字面量不支持；拒绝空白、/ \\ ? # @ % 等一切特殊字符；
- 一律拒绝（除非 allow_private=True）：回环地址、链路本地地址（含云厂商
  元数据 169.254.169.254）、未指定地址、保留地址，以及 RFC1918/ULA 内网段、
  localhost/.local/.internal/.home.arpa 等内网主机名；
- allow_private=True 仅供插件超级管理员使用：机器人与 MC 服务器同内网自部署
  是常见场景，交给受信任的超管处理，普通群管理员不得借机器人探测内网。
"""
import ipaddress
import re
from typing import Optional

# 单标签或带点的域名（允许下划线：LAN 内主机名常见写法；允许尾点 FQDN）
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?"
    r"(?:\.[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?)*\.?$"
)

# 内网语义的主机名后缀（不含裸 localhost，单独判断）
_PRIVATE_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa")

# stdlib is_private 在部分 Python 版本不覆盖、但语义上属于内网/非公网的段
_EXTRA_PRIVATE_NETS = (
    ipaddress.ip_network("100.64.0.0/10"),   # CGNAT（云环境常见内部段）
    ipaddress.ip_network("198.18.0.0/15"),   # 基准测试保留段
)


def validate_host(host: str, allow_private: bool = False) -> Optional[str]:
    """校验服务器 host。

    返回 None 表示合法；否则返回面向用户的拒绝原因（可直接拼接进回复）。
    """
    if host is None:
        return "host 不能为空"
    host = host.strip()
    if not host:
        return "host 不能为空"
    if len(host) > 253:
        return "host 过长"
    if any(ch.isspace() for ch in host) or any(ch in host for ch in "/\\?#@%"):
        return "host 含有非法字符"

    # host 可以带游戏端口（如「域名:25565」）：mcstatus 回落与裸 TCP 测延迟都从这一个字段取端口，
    # 模组 API 端口另有 api_port。剥掉端口后缀，地址部分照常按下面的规则校验。
    bare = host
    addr, sep, port_txt = host.partition(":")
    if sep:
        if ":" in port_txt:
            # 多处冒号＝IPv6 字面量（含 [::1]:25565 这种带方括号的写法）
            return "暂不支持 IPv6 地址（请使用 IPv4 或域名）"
        if not port_txt.isdigit():
            return "冒号后面要写 1-65535 的游戏端口"
        if not 1 <= int(port_txt) <= 65535:
            return "游戏端口必须在 1-65535 之间"
        bare = addr

    # IPv4 字面量判断
    try:
        ip = ipaddress.ip_address(bare)
    except ValueError:
        ip = None

    if ip is not None:
        if ip.is_loopback:
            reason = "回环地址"
        elif ip.is_multicast:
            reason = "组播地址"
        elif ip.is_link_local:
            reason = "链路本地地址（含云元数据地址）"
        elif ip.is_unspecified or ip.is_reserved:
            reason = "未指定/保留地址"
        elif ip.is_private or any(ip in net for net in _EXTRA_PRIVATE_NETS):
            # is_private 涵盖 10/8、172.16/12、192.168/16、0.0.0.0/8、ULA 等
            reason = "内网地址"
        else:
            return None
        if not allow_private:
            return f"{host} 是{reason}，普通成员不允许添加内网/回环地址"
        return None

    # 域名/主机名
    lowered = bare.lower()
    is_private_name = lowered == "localhost" or lowered.endswith(_PRIVATE_SUFFIXES)
    if is_private_name and not allow_private:
        return f"{host} 指向本机或内网，普通成员不允许添加"
    if not _DOMAIN_RE.match(bare):
        return "不是合法的域名或 IPv4 地址"
    return None
