"""\u72ec\u7acb\u5e94\u7528\u542f\u52a8\uff0c\u9694\u79bb\u5b9e\u4f8b\u3001\u914d\u7f6e\u4e0e\u9632\u706b\u5899\u5bbf\u4e3b\u3002"""
from .identity import DISPLAY_NAME
import sys
from modules import resources_rc
from PySide6.QtWidgets import QApplication
from modules.config_manager import BASE_DIR
from modules.common_widgets.input_appearance import install_input_appearance
from modules.common_widgets.tooltip_policy import install_tooltip_policy
from modules.common_widgets.path_drop import install_path_drop_support
from modules.desktop_shell.single_instance import SingleInstanceController
from modules.interaction_guards import install_interaction_guards
from .runtime import RouterRuntime
from .window import RouterWindow


def run(version):
    """\u542f\u52a8\u72ec\u7acb\u5e94\u7528\uff1b\u70df\u6d4b\u4e0d\u8fde\u8bbe\u5907\u3001\u4e0d\u4fee\u6539\u9632\u706b\u5899\u3002"""
    smoke = "--smoke-test" in sys.argv
    if getattr(sys, "frozen", False) and not smoke:
        from modules.platform_services.app_update_install import recover_interrupted_update
        if recover_interrupted_update(BASE_DIR):
            return 0
    if not smoke:
        from modules.error_log_hooks import install_error_logging
        hooks = install_error_logging(version)
        if hooks is not None:
            hooks.install_qt()
    from modules.platform_services.application_identity import set_app_id
    set_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName("RouterManager")
    app.setApplicationVersion(version)
    app.setApplicationDisplayName(DISPLAY_NAME)
    app.setOrganizationName("RouterManager")
    app.setQuitOnLastWindowClosed(False)
    install_interaction_guards(app)
    install_path_drop_support(app)
    install_input_appearance(app)
    install_tooltip_policy(app)
    from .window_geometry import startup_screen
    from modules.desktop_shell.initial_ui_scale import initial_ui_scale
    from modules.config_manager import ConfigManager
    screen = startup_screen()
    selected_scale = initial_ui_scale(ConfigManager(), screen, persist=not smoke)
    runtime = RouterRuntime(version, app)
    runtime.config.set("ui", "scale", str(selected_scale))
    window = RouterWindow(runtime, version, initial_screen=screen)
    app.setWindowIcon(window.windowIcon())
    if smoke:
        import time
        from .settings import RouterSettings
        window.show()
        dialog = RouterSettings(window)
        for index in range(6):
            window.packet_capture_page.openwrt_tab.tabs.setCurrentIndex(index)
            app.processEvents()
        dialog.reject()
        dialog.deleteLater()
        runtime.exit_application()
        deadline = time.monotonic() + 15
        while not runtime._ready_to_close and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        return 0 if runtime._ready_to_close else 1
    instance = SingleInstanceController(lambda: (window.showNormal(), window.raise_(), window.activateWindow()), app)
    if not instance.acquire():
        runtime.exit_application()
        return app.exec() if not runtime._ready_to_close else 0
    app.aboutToQuit.connect(instance.close)
    from modules.platform_services.firewall_session import close_firewall_session
    app.aboutToQuit.connect(close_firewall_session)
    from modules.desktop_shell.startup_firewall_presenter import StartupFirewallPresenter
    firewall = StartupFirewallPresenter(runtime._startup_firewall_controller, runtime)
    window.show()
    firewall.show_for(window)
    return app.exec()
