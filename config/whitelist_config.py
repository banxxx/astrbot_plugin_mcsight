import json
import os
from typing import List
from astrbot.api.star import Context
from astrbot.api import logger

# 插件名称常量，用于构建配置文件路径
PLUGIN_NAME = "astrbot_plugin_mcsight"

class WhitelistManager:
    """
    插件配置管理器（只读，数据源：AstrBot 插件配置）
    
    所有配置项均由 WebUI 插件配置界面管理，
    本类优先从 data/config/插件名_config.json 读取用户配置值，
    如果文件不存在则尝试通过 context API 获取。
    """
    _instance = None
    _config_cache = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._context = None
            cls._instance._config_cache = None
        return cls._instance

    def set_context(self, context: Context):
        """注入 AstrBot 插件上下文，由 main.py 在初始化时调用"""
        self._context = context
        self._config_cache = None  # 清除缓存，强制重新读取

    def _get_config(self):
        """
        获取插件配置（带缓存）
        优先从 data/config/插件名_config.json 读取，
        如果文件不存在则尝试通过 context API 获取。
        """
        if self._config_cache is not None:
            return self._config_cache

        # 1. 优先从专用配置文件读取
        config = self._load_config_from_plugin_file()
        if config:
            self._config_cache = config
            return config

        # 2. 如果文件不存在，尝试通过 context API 获取
        if self._context:
            try:
                if hasattr(self._context, 'get_plugin_config'):
                    config = self._context.get_plugin_config()
                    if config is not None and isinstance(config, dict):
                        # 检查是否包含插件配置的实际值（非 schema）
                        if any(k in config for k in ['show_server_version', 'show_server_latency', 'super_admins']):
                            self._config_cache = config
                            return config
            except Exception as e:
                logger.debug(f"get_plugin_config() 失败: {e}")

            try:
                if hasattr(self._context, 'get_config'):
                    config = self._context.get_config()
                    if isinstance(config, dict):
                        # 尝试提取插件配置
                        for key in [PLUGIN_NAME, 'plugins.' + PLUGIN_NAME]:
                            parts = key.split('.')
                            value = config
                            for part in parts:
                                if isinstance(value, dict) and part in value:
                                    value = value[part]
                                else:
                                    value = None
                                    break
                            if value is not None and isinstance(value, dict):
                                self._config_cache = value
                                return value
            except Exception as e:
                logger.debug(f"get_config() 提取插件配置失败: {e}")

        # 3. 最终回退：尝试从旧位置读取（兼容）
        config = self._load_config_from_fallback()
        if config:
            self._config_cache = config
            return config

        self._config_cache = {}
        return {}

    def _load_config_from_plugin_file(self):
        """
        从 data/config/插件名_config.json 读取配置
        使用 utf-8-sig 编码自动处理 BOM 头
        """
        data_root = self._get_data_root()
        if not data_root:
            return {}

        config_path = os.path.join(data_root, 'config', f'{PLUGIN_NAME}_config.json')
        if not os.path.exists(config_path):
            logger.debug(f"专用配置文件不存在")
            return {}

        try:
            # 使用 utf-8-sig 编码，自动跳过 BOM 头
            with open(config_path, 'r', encoding='utf-8-sig') as f:
                config = json.load(f)
            if not isinstance(config, dict):
                logger.warning(f"配置文件 {config_path} 内容不是 JSON 对象")
                return {}
            # 过滤掉 schema 定义（如果误读到了 _conf_schema.json，则返回空）
            if any(isinstance(v, dict) and 'type' in v for v in config.values()):
                logger.warning(f"配置文件 {config_path} 似乎是 schema 定义而非实际值，跳过")
                return {}
            return config
        except json.JSONDecodeError as e:
            logger.error(f"配置文件 JSON 解析失败: {e}")
            return {}
        except Exception as e:
            logger.error(f"读取配置文件失败: {e}")
            return {}

    def _get_data_root(self):
        """
        获取 AstrBot 数据根目录
        可能的路径顺序：
        1. 环境变量 ASTRBOT_DATA
        2. 环境变量 ASTRBOT_HOME
        3. 用户目录下的 .astrbot
        4. Windows 下的 AppData/Roaming/AstrBot
        5. 当前工作目录下的 data
        6. 插件所在目录的上上级（当插件位于 data/plugins/ 时）
        """
        # 1. 环境变量
        for env_var in ['ASTRBOT_DATA', 'ASTRBOT_HOME']:
            if os.environ.get(env_var):
                path = os.environ[env_var]
                if os.path.exists(path):
                    return path

        # 2. 用户目录
        home = os.path.expanduser('~')
        possible_paths = [
            os.path.join(home, '.astrbot'),
            os.path.join(home, 'AppData', 'Roaming', 'AstrBot'),
            os.path.join(os.getcwd(), 'data'),
        ]
        # 3. 插件所在目录的上级（当插件在 data/plugins/ 下时，其父目录为 data）
        plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        possible_paths.append(os.path.dirname(plugin_dir))  # 可能是 data 目录
        possible_paths.append(os.path.join(os.path.dirname(plugin_dir), '..'))  # 可能退到根

        for path in possible_paths:
            if os.path.exists(path) and os.path.isdir(path):
                # 检查是否存在 config 子目录（或 cmd_config.json）
                if os.path.exists(os.path.join(path, 'config')):
                    return path
                if os.path.exists(os.path.join(path, 'cmd_config.json')):
                    return path

        # 如果都找不到，返回 None
        return None

    def _load_config_from_fallback(self):
        """
        从可能存在的旧位置（如 cmd_config.json）读取配置（兼容）
        """
        data_root = self._get_data_root()
        if not data_root:
            return {}

        # 尝试从 cmd_config.json 读取
        fallback_path = os.path.join(data_root, 'cmd_config.json')
        if os.path.exists(fallback_path):
            try:
                with open(fallback_path, 'r', encoding='utf-8-sig') as f:
                    full_config = json.load(f)
                # 尝试多种键路径
                for key in [PLUGIN_NAME, 'plugins.' + PLUGIN_NAME, 'plugin_config.' + PLUGIN_NAME]:
                    parts = key.split('.')
                    value = full_config
                    for part in parts:
                        if isinstance(value, dict) and part in value:
                            value = value[part]
                        else:
                            value = None
                            break
                    if value is not None and isinstance(value, dict):
                        return value
            except Exception:
                pass
        return {}

    def _get_config_value(self, key: str, default):
        """
        从配置中读取普通类型配置项
        """
        config = self._get_config()
        value = config.get(key, default)
        # 如果 value 是字典且包含 'default' 和 'type'，这很可能是 schema 定义而非实际值
        if isinstance(value, dict) and 'default' in value and 'type' in value:
            return value.get('default', default)
        return value

    def _get_config_list(self, key: str) -> List[str]:
        """从配置中读取列表类型配置项"""
        config = self._get_config()
        value = config.get(key, [])
        if not isinstance(value, list):
            if isinstance(value, dict) and 'default' in value:
                default_val = value.get('default')
                if isinstance(default_val, list):
                    return default_val
            return []
        return value

    def has_mod_api(self, server_dict: dict) -> bool:
        """
        判断服务器是否安装了模组。
        通过检查服务器条目中是否存在 api_port 字段来判断。
        没有 api_port 字段则视为未安装模组。

        :param server_dict: 服务器条目字典
        :return: 如果配置了 api_port 则返回 True，否则返回 False
        """
        return server_dict.get("api_port") is not None

    # ---------- 白名单相关属性 ----------
    @property
    def super_admins(self) -> List[str]:
        """超级管理员列表"""
        return self._get_config_list("super_admins")

    @property
    def whitelist_admins(self) -> List[str]:
        """白名单管理员列表（与群管理员同等权限）"""
        return self._get_config_list("whitelist_admins")

    @property
    def blacklist(self) -> List[str]:
        """黑名单列表"""
        return self._get_config_list("blacklist")

    @property
    def enable_group_owner(self) -> bool:
        """是否允许群主使用管理功能"""
        return self._get_config_value("enable_group_owner", True)

    @property
    def enable_group_admin(self) -> bool:
        """是否允许管理员使用管理功能"""
        return self._get_config_value("enable_group_admin", True)

    # ---------- 显示选项 ----------
    @property
    def show_server_version(self) -> bool:
        """是否显示服务器版本"""
        return self._get_config_value("show_server_version", False)

    @property
    def show_server_latency(self) -> bool:
        """是否显示服务器延迟"""
        return self._get_config_value("show_server_latency", False)

    # ---------- 白名单查询方法 ----------
    def is_super_admin(self, user_id: str) -> bool:
        """检查用户是否是超级管理员"""
        return user_id in self.super_admins

    def is_whitelist_admin(self, user_id: str) -> bool:
        """检查用户是否是白名单管理员"""
        return user_id in self.whitelist_admins

    def is_blacklisted(self, user_id: str) -> bool:
        """检查用户是否在黑名单中"""
        return user_id in self.blacklist

    # ---------- 统一 API 端口配置 ----------
    @property
    def default_api_port(self) -> int:
        """全局默认 API 端口"""
        value = self._get_config_value("default_api_port", 25566)
        try:
            return int(value)
        except (ValueError, TypeError):
            return 25566

    @property
    def mod_api_token(self) -> str:
        """模组 API 鉴权 Token"""
        return self._get_config_value("mod_api_token", "")

    @property
    def use_central_db(self) -> bool:
        """是否启用中心数据库（D1）。若为 False，则回退到调用模组 API 的旧模式"""
        return self._get_config_value("use_central_db", False)

    @property
    def db_host(self) -> str:
        """TiDB 数据库主机地址"""
        return self._get_config_value("db_host", "")

    @property
    def db_port(self) -> int:
        """TiDB 数据库端口"""
        return int(self._get_config_value("db_port", 4000))

    @property
    def db_user(self) -> str:
        """数据库用户名"""
        return self._get_config_value("db_user", "")

    @property
    def db_password(self) -> str:
        """数据库密码"""
        return self._get_config_value("db_password", "")

    @property
    def db_name(self) -> str:
        """数据库名称"""
        return self._get_config_value("db_name", "qqbind_db")

    @property
    def db_charset(self) -> str:
        """字符集"""
        return self._get_config_value("db_charset", "utf8mb4")

    # ---------- 获取服务器端口 ----------
    def get_server_port(self, server_dict: dict) -> int:
        """
        获取服务器的实际 API 端口。
        优先使用服务器条目中的 api_port，否则使用全局 default_api_port。
        """
        port = server_dict.get("api_port")
        if port is not None:
            try:
                return int(port)
            except (ValueError, TypeError):
                pass
        return self.default_api_port