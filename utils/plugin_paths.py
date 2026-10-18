"""插件运行期数据的落点：<AstrBot data>/plugin_data/astrbot_plugin_mcsight/

插件目录会被「更新」整包删掉再按仓库内容重装（core/star/updator.py 里是 remove_dir + 解压），
留在自己目录里的数据会一起没，所以活数据一律放这儿。
"""
import os

from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

PLUGIN_NAME = "astrbot_plugin_mcsight"


def data_dir() -> str:
    """插件数据根目录，已确保存在"""
    path = os.path.join(get_astrbot_plugin_data_path(), PLUGIN_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def sub_dir(name: str) -> str:
    """数据根下的子目录，如 group_configs / help_cache / avatar_cache"""
    path = os.path.join(data_dir(), name)
    os.makedirs(path, exist_ok=True)
    return path
