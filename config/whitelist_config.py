import json
import os
import re
from typing import List
from astrbot.api.star import Context
from astrbot.api import logger

# 注册名（metadata.yaml 的 name）。注意不能拿它拼配置文件名：
# AstrBot 的配置文件按**插件目录名**命名，本仓库目录是 astrbot_plugin_mcwatcher。
PLUGIN_NAME = "astrbot_plugin_mcsight"
PLUGIN_DIR_NAME = os.path.basename(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class WhitelistManager:
    """
    插件配置管理器（只读，数据源：AstrBot 插件配置）

    所有配置项均由 WebUI 插件配置界面管理，
    本类优先从 data/config/<插件目录名>_config.json 读取用户配置值，
    文件不可用时退回内核注入的配置对象 / context API。
    """
    _instance = None
    _config_cache = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._context = None
            cls._instance._config_obj = None
            cls._instance._config_cache = None
        return cls._instance

    def set_context(self, context: Context):
        """注入 AstrBot 插件上下文，由 main.py 在初始化时调用"""
        self._context = context
        self._config_cache = None  # 清除缓存，强制重新读取

    def set_config(self, config):
        """注入内核传给插件实例的配置对象（AstrBotConfig）

        文件读不到时用它兜底；也保证面板改了配置、热重载后立刻能看到新值。
        """
        self._config_obj = config
        self._config_cache = None

    def clear_cache(self):
        """丢弃配置缓存，让面板刚写入的配置项不用重载插件就能读到新值"""
        self._config_cache = None

    def _get_config(self):
        """
        获取插件配置（带缓存）
        优先从 data/config/<插件目录名>_config.json 读取；
        文件不可用时用内核注入的配置对象兜底，最后才走 context API。
        """
        if self._config_cache is not None:
            return self._config_cache

        # 1. 优先从专用配置文件读取：AstrBot 保存配置时同步落盘，这里读到的就是事实源
        config = self._load_config_from_plugin_file()
        if config:
            self._config_cache = config
            return config

        # 2. 文件不在预期位置（插件目录被改名、只读安装等）：用内核注入的对象
        if self._config_obj is not None:
            try:
                injected = dict(self._config_obj)
            except Exception as e:
                logger.debug(f"读取内核注入的配置对象失败: {e}")
            else:
                if injected:
                    self._config_cache = injected
                    return injected

        # 3. 最后再尝试 context API
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
        从 data/config/<插件目录名>_config.json 读取配置
        使用 utf-8-sig 编码自动处理 BOM 头

        AstrBot 按目录名拼配置文件名，旧版本/手动安装也可能用注册名，
        两个候选都试一下，别因为目录改名就把整份配置读成空。
        """
        data_root = self._get_data_root()
        if not data_root:
            return {}

        candidates = []
        for name in (PLUGIN_DIR_NAME, PLUGIN_NAME):
            if name and name not in candidates:
                candidates.append(name)

        config_path = ''
        for name in candidates:
            path = os.path.join(data_root, 'config', f'{name}_config.json')
            if os.path.exists(path):
                config_path = path
                break
        if not config_path:
            logger.debug(f"专用配置文件不存在（试过 {', '.join(candidates)}）")
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
        1. 环境变量 ASTRBOT_DATA / ASTRBOT_HOME
        2. 内核自己给出的 data 目录（插件配置文件就是它按这里存的）
        3. 从插件位置反推：<data>/plugins/<插件目录> ⇒ <data>
        4. 用户目录下的 .astrbot / Windows AppData
        5. 当前工作目录下的 data
        """
        def looks_like_data_root(path: str) -> bool:
            return bool(path) and os.path.isdir(path) and (
                os.path.isdir(os.path.join(path, 'config'))
                or os.path.exists(os.path.join(path, 'cmd_config.json'))
            )

        # 1. 环境变量
        for env_var in ['ASTRBOT_DATA', 'ASTRBOT_HOME']:
            if os.environ.get(env_var):
                path = os.environ[env_var]
                if os.path.exists(path):
                    return path

        # 2. 内核的规范路径：与它保存 <插件目录名>_config.json 的位置完全一致。
        # 老内核没有这个模块时静默跳过，继续走下面的推导。
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path
            kernel_data = get_astrbot_data_path()
        except Exception as e:
            logger.debug(f"读取内核 data 目录失败: {e}")
        else:
            if looks_like_data_root(kernel_data):
                return kernel_data

        # 3. 从插件位置反推：<data>/plugins/<插件目录> ⇒ <data>。
        # 注意要退两层：dirname(插件目录) 是 plugins，再上一层才是 data。
        # 这比猜 ~/.astrbot、cwd 稳得多，多个 AstrBot 并存时也不会读错实例。
        plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        derived = os.path.dirname(os.path.dirname(plugin_dir))
        if looks_like_data_root(derived):
            return derived

        # 4. 用户目录 / 当前目录
        home = os.path.expanduser('~')
        possible_paths = [
            os.path.join(home, '.astrbot'),
            os.path.join(home, 'AppData', 'Roaming', 'AstrBot'),
            os.path.join(os.getcwd(), 'data'),
            derived,
            os.path.join(derived, '..'),
        ]

        for path in possible_paths:
            if looks_like_data_root(path):
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
                # 尝试多种键路径（目录名与注册名都可能出现）
                for key in [PLUGIN_DIR_NAME, PLUGIN_NAME,
                            'plugins.' + PLUGIN_DIR_NAME, 'plugins.' + PLUGIN_NAME,
                            'plugin_config.' + PLUGIN_DIR_NAME, 'plugin_config.' + PLUGIN_NAME]:
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
        """从配置中读取列表类型配置项，统一成去掉空白的字符串列表"""
        config = self._get_config()
        value = config.get(key, [])
        if isinstance(value, dict) and 'default' in value:
            value = value.get('default', [])
        # 手改 JSON 时可能把名单写成 "111,222" 一整串：按逗号/空白拆开，
        # 否则整串会被当成一个用户 ID，谁也对不上
        if isinstance(value, str):
            value = re.split(r'[,，\s]+', value)
        if not isinstance(value, (list, tuple, set)):
            return []
        return [str(v).strip() for v in value if str(v).strip()]

    def has_mod_api(self, server_dict: dict) -> bool:
        """
        判断服务器是否安装了模组：api_port 必须是 1-65535 的整数。

        空串、非数字、越界（手写配置或旧文件里可能出现）一律视为未装模组，
        否则 /执行、/广播 会把命令 Token 发到一个并不存在的「模组 API」端口上。

        :param server_dict: 服务器条目字典
        :return: api_port 可用则返回 True，否则返回 False
        """
        port = server_dict.get("api_port")
        if isinstance(port, bool):
            return False
        try:
            port = int(port)
        except (TypeError, ValueError):
            return False
        return 1 <= port <= 65535

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
        """端口兜底值（旧配置的 default_api_port；界面上已移除，通常就是 25566）"""
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
        是否启用模组由 has_mod_api() 判断；本方法只在确认要调模组后取端口。
        条目里的端口非法/缺失时退回兜底值（旧配置的 default_api_port，默认 25566），
        仅为兼容历史上没有 api_port 字段的条目——has_mod_api() 已把这类条目挡在调用前。
        """
        port = server_dict.get("api_port")
        try:
            port = int(port)
        except (ValueError, TypeError):
            return self.default_api_port
        return port if 1 <= port <= 65535 else self.default_api_port