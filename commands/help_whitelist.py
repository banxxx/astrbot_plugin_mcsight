from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Image as AstrImage
from ..config.whitelist_config import WhitelistManager
from ..utils.permission import check_permission
from ..features.help_image.image_generator import draw_help_image
from ..features.help_image.bind_help_generator import draw_bind_help_image

async def handle_whitelist(event: AstrMessageEvent, parts: list):
    """处理 /mc whitelist"""
    if not await check_permission(event, "whitelist"):
        yield event.plain_result("权限不足：该命令仅限超级管理员使用。")
        return
    wm = WhitelistManager()
    if len(parts) < 2:
        yield event.plain_result(
            "白名单管理已迁移至 WebUI 插件配置面板。\n"
            "可用命令：/mc whitelist list - 查看当前白名单配置"
        )
        return
    action = parts[1].lower()
    if action == "list":
        msg_list = [
            "当前白名单配置：",
            f"超级管理员：{', '.join(wm.super_admins) or '无'}",
            f"白名单管理员：{', '.join(wm.whitelist_admins) or '无'}",
            f"黑名单：{', '.join(wm.blacklist) or '无'}"
        ]
        yield event.plain_result("\n".join(msg_list))
    elif action in ("add", "remove"):
        yield event.plain_result(
            "白名单的添加和移除请前往 AstrBot WebUI 插件配置面板操作。\n"
            "使用 /mc whitelist list 可以查看当前配置。"
        )
    else:
        yield event.plain_result("未知操作，支持：list（查看配置）")

async def handle_help(event: AstrMessageEvent):
    try:
        img = draw_help_image()
        img.save("mc_help_temp.png")
        yield event.chain_result([AstrImage(file="mc_help_temp.png")])
    except Exception as e:
        yield event.plain_result(f"生成帮助图片失败: {e}")

async def handle_bindhelp(event: AstrMessageEvent):
    try:
        img = draw_bind_help_image()
        img.save("bind_help_temp.png")
        yield event.chain_result([AstrImage(file="bind_help_temp.png")])
    except Exception as e:
        yield event.plain_result(f"生成绑定帮助图片失败: {e}")