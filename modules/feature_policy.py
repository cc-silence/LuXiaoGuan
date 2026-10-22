"""WinBox 功能可用性策略。

主要作用：集中决定当前运行模式允许创建、显示和执行哪些功能，避免把“主机模式”
判断分散到主窗口、设置页和业务弹窗中。

调用方式：
- ``FeaturePolicy.from_config(config)`` 从 settings.ini 配置创建策略。
- 页面、按钮和服务在创建前调用 ``allows(CAPABILITY_NETWORK_DOWNLOAD)``。
- 以后新增模式或能力时，只在本模块增加能力和规则，再为新功能声明依赖即可。

副作用：无。本模块不读写配置，也不创建 Qt 控件。
"""

from __future__ import annotations


CAPABILITY_NETWORK_DOWNLOAD = "network_download"


class FeaturePolicy:
    """当前 WinBox 运行模式的功能可用性判断器。

    参数：host_mode 为 True 时表示主机模式，关闭联网下载能力。
    返回值：实例可通过 ``allows`` 查询某项能力。
    副作用：无。
    """

    def __init__(self, host_mode=False):
        self.host_mode = bool(host_mode)

    @classmethod
    def from_config(cls, config):
        """从已加载的 settings.ini 配置创建策略实例。"""
        value = config.get("ui", "host_mode", fallback="false")
        return cls(str(value).strip().lower() in {"1", "true", "yes", "on"})

    def allows(self, capability):
        """返回指定能力在当前模式下是否允许使用。"""
        if capability == CAPABILITY_NETWORK_DOWNLOAD:
            return not self.host_mode
        return True

    def requires_restart_free_rebuild(self):
        """说明模式切换需要重建受影响页面，但不要求重启程序。"""
        return True
