"""绑定时的群名片判定规则（按群可配）。

四种匹配方式，全部大小写敏感：
- paren（默认，逐字沿用改动前的行为）：第一对中/英文括号内的内容 strip 后等于游戏ID；
- equals：名片 strip 后等于游戏ID；
- contains：名片含游戏ID，且前后不紧邻字母/数字/下划线（词边界）；
- template：把 {gameId} 换成游戏ID后，名片 strip 后完全等于结果。

规则缺失、值非法或模板非法时一律回落 paren，即回到改动前的行为（失败关闭）。
"""
import re

RULE_PAREN = "paren"
RULE_EQUALS = "equals"
RULE_CONTAINS = "contains"
RULE_TEMPLATE = "template"
RULES = (RULE_PAREN, RULE_EQUALS, RULE_CONTAINS, RULE_TEMPLATE)

DEFAULT_RULE = RULE_PAREN
TEMPLATE_PLACEHOLDER = "{gameId}"
MAX_TEMPLATE_LEN = 64
SAMPLE_GAME_ID = "Steve"

# 与改动前逐字一致：取第一对括号（中英文可混配），非贪婪到最近的右括号
_PAREN_RE = re.compile(r'[（(](.*?)[）)]')
# 游戏ID字符集，与模组 GameIdValidator 一致；contains 用它划词边界
_WORD = "A-Za-z0-9_"


def normalize_rule(rule) -> str:
    """认不出的规则一律按 paren 处理。"""
    r = str(rule or "").strip().lower()
    return r if r in RULES else DEFAULT_RULE


def template_error(template) -> str:
    """模板合法性；空串表示合法。"""
    tpl = str(template or "")
    if not tpl.strip():
        return "自定义模板不能为空"
    if len(tpl) > MAX_TEMPLATE_LEN:
        return f"自定义模板最多 {MAX_TEMPLATE_LEN} 个字符"
    if tpl.count(TEMPLATE_PLACEHOLDER) != 1:
        return "模板必须包含且只包含一个 {gameId}"
    return ""


def validate(rule, template) -> str:
    """保存前校验；空串表示合法，否则是拒绝原因。"""
    r = str(rule or "").strip().lower()
    if r not in RULES:
        return "不支持的匹配方式"
    if r == RULE_TEMPLATE:
        return template_error(template)
    return ""


def effective_rule(rule, template) -> str:
    """实际生效的规则：模板非法时回落到 paren，避免手改配置把绑定全卡死。"""
    r = normalize_rule(rule)
    if r == RULE_TEMPLATE and template_error(template):
        return DEFAULT_RULE
    return r


def evaluate(card, target, rule, template="") -> tuple:
    """判定名片。返回 (是否通过, 结果码, 括号内取到的值)。

    结果码：ok / no_bracket / empty_bracket / mismatch /
    equals_fail / contains_fail / template_fail。
    第三个值只有 paren 有意义，其余为 None。
    """
    card = card or ""
    r = effective_rule(rule, template)

    if r == RULE_PAREN:
        match = _PAREN_RE.search(card)
        if not match:
            return False, "no_bracket", None
        got = match.group(1).strip()
        if not got:
            return False, "empty_bracket", None
        if got != target:
            return False, "mismatch", got
        return True, "ok", got

    if r == RULE_EQUALS:
        if card.strip() == target:
            return True, "ok", None
        return False, "equals_fail", None

    if r == RULE_CONTAINS:
        pat = rf"(?<![{_WORD}]){re.escape(target)}(?![{_WORD}])"
        if re.search(pat, card):
            return True, "ok", None
        return False, "contains_fail", None

    expected = str(template).replace(TEMPLATE_PLACEHOLDER, target)
    if card.strip() == expected:
        return True, "ok", None
    return False, "template_fail", None


def sample(rule, template="", target=SAMPLE_GAME_ID) -> str:
    """按规则生成期望格式的示例；报错文案与面板实时预览共用。"""
    r = effective_rule(rule, template)
    if r == RULE_PAREN:
        return f"玩家（{target}）"
    if r == RULE_EQUALS:
        return target
    if r == RULE_CONTAINS:
        return f"玩家{target}"
    return str(template).replace(TEMPLATE_PLACEHOLDER, target)
