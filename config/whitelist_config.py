from typing import List
from astrbot.api.star import Context

class WhitelistManager:
    """
    插件配置管理器（只读，数据源：AstrBot 插件配置）
    
    所有配置项均由 WebUI 插件配置界面管理，
    本类仅提供读取接口，不提供任何修改方法。
    """
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._context = None
        return cls._instance

    def set_context(self, context: Context):
        """注入 AstrBot 插件上下文，由 main.py 在初始化时调用"""
        self._context = context

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

    # ---------- 显示选项（新增） ----------
    @property
    def show_server_version(self) -> bool:
        """是否显示服务器版本"""
        return self._get_config_value("show_server_version", False)

    @property
    def show_server_latency(self) -> bool:
        """是否显示服务器延迟"""
        return self._get_config_value("show_server_latency", False)

    # ---------- 内部读取方法 ----------
    def _get_config_list(self, key: str) -> List[str]:
        """从 AstrBot 配置中读取列表类型配置项"""
        if not self._context:
            return []
        try:
            config = self._context.get_config()
            return list(config.get(key, []))
        except Exception:
            return []

    def _get_config_value(self, key: str, default):
        """从 AstrBot 配置中读取普通类型配置项（如 bool）"""
        if not self._context:
            return default
        try:
            config = self._context.get_config()
            return config.get(key, default)
        except Exception:
            return default

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