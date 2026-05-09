from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Image as AstrImage
from .checker import query_all_servers
from .image_generator import draw_multi_server_image
from ...config.server_config import get_all_servers

async def run_player_status(event: AstrMessageEvent):
    servers = get_all_servers()
    if not servers:
        yield event.plain_result("还没有添加任何服务器。")
        return

    # 查询所有服务器
    results = await query_all_servers(servers)

    # 绘制图片
    try:
        img = await draw_multi_server_image(results)
        img.save("mc_status_temp.png")
        yield event.chain_result([AstrImage(file="mc_status_temp.png")])
    except Exception as e:
        yield event.plain_result(f"生成图片失败: {e}")