"""更新策略核心分发。

本模块只根据策略名称选择执行器已经提供的核心方法，不负责 PRE/POST 管线、备份、日志或
界面确认。这样新增策略时可集中维护名称到处理方法的映射，避免在共享管线中堆叠分支。
"""


class UpdateStrategyDispatcher:
    """把策略名称分发到 StrategyExecutor 的具体核心实现。"""

    _HANDLER_NAMES = {
        "run_installer": "_run_installer",
        "extract_copy": "_extract_copy",
        "multi_target_copy": "_multi_target_copy",
        "custom_script": "_run_custom_script",
    }

    @classmethod
    def run(cls, executor, strategy, config, download_path, name, parent=None, targets=None):
        """执行一个已验证下载包的策略核心。

        参数：executor 是提供核心静态方法的 StrategyExecutor；其余参数保持原执行器调用
        协议。返回值为 ``(success, message)``。副作用：委托的策略可能安装、复制或运行脚本；
        共享管线的备份恢复仍由调用方负责。
        """

        method_name = cls._HANDLER_NAMES.get(strategy)
        if method_name is None:
            return False, f"未知的更新策略：{strategy}"
        handler = getattr(executor, method_name)
        if strategy == "extract_copy":
            return handler(download_path, config, name, parent, targets)
        return handler(download_path, config, name, parent)
