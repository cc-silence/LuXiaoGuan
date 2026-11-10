"""\u8def\u7531\u5668\u7ba1\u7406\u72ec\u7acb\u5165\u53e3\uff1b\u5148\u5206\u6d41\u5de5\u4f5c\u8fdb\u7a0b\uff0c\u518d\u542f\u52a8\u754c\u9762\u3002"""
import sys
APP_VERSION = "1.0.60_alpha"


def main():
    """\u542f\u52a8\u72ec\u7acb\u5e94\u7528\uff0c\u8fd4\u56de\u8fdb\u7a0b\u9000\u51fa\u7801\u3002"""
    from multiprocessing import freeze_support
    freeze_support()
    if len(sys.argv)>1 and sys.argv[1]=='--linux-install':
        from modules.platform_services.linux_user_install import main as run_linux_install
        return run_linux_install(sys.argv[2:])
    if len(sys.argv)>1 and sys.argv[1] in {"--linux-update-ui","--linux-update-recover-ui"}:
        from modules.app_update_services.linux_update_ui import main as run_linux_ui
        return run_linux_ui(sys.argv[1:],recover=sys.argv[1]=="--linux-update-recover-ui")
    if len(sys.argv)>1 and sys.argv[1] in {"--linux-update-worker","--linux-update-recover"}:
        from modules.app_update_services.linux_update_worker import main as run_linux_worker
        return run_linux_worker(sys.argv[1:],recover=sys.argv[1]=="--linux-update-recover")
    if "--file-presence-worker" in sys.argv:
        from modules.platform_services.linux_file_presence import run_worker
        return run_worker(sys.argv)
    if "--credential-worker" in sys.argv:
        from modules.platform_services.credential_worker import run_worker
        return run_worker(sys.argv)
    if "--desktop-shortcut-worker" in sys.argv:
        from modules.platform_services.linux_desktop_shortcut import run_worker
        return run_worker(sys.argv)
    if "--firewall-session" in sys.argv:
        from modules.platform_services.firewall_session_worker import run_worker
        return run_worker(sys.argv)
    if "--local-gpo-firewall" in sys.argv:
        from modules.platform_services.local_gpo_firewall import run_request_file
        return run_request_file(sys.argv)
    if "--hidden-command-worker" in sys.argv:
        from modules.platform_services.elevated_background_command import run_worker
        return run_worker(sys.argv)
    # Internal workers must retain the exact process launched for authenticated IPC.
    from modules.platform_services.source_runtime import ensure_source_runtime
    ensure_source_runtime()
    if "--smoke-test-ssh-dependencies" in sys.argv:
        import paramiko, bcrypt, cryptography, nacl, cffi, invoke
        return 0
    if "--smoke-test-terminal" in sys.argv:
        from PySide6.QtWidgets import QApplication
        from modules.terminal_widgets.smoke_test import run_terminal_smoke
        app = QApplication(sys.argv)
        return 0 if run_terminal_smoke(app) else 1
    if getattr(sys,"frozen",False) and "--smoke-test" not in sys.argv:
        from pathlib import Path
        from modules.platform_services.update_dispatch import recover_before_gui
        if recover_before_gui(Path(sys.executable).parent):return 0
    from modules.desktop_shell.application_start import run_desktop
    return run_desktop(APP_VERSION)


if __name__ == "__main__":
    raise SystemExit(main())
