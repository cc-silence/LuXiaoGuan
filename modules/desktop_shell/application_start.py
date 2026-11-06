"""Own the desktop instance before migrating data or importing configuration."""
import sys


def run_desktop(version):
    from modules.platform_services.user_data_migration import prepare_user_directories
    if "--smoke-test" in sys.argv:
        prepare_user_directories()
        from router_app.bootstrap import run
        return run(version)
    from modules.platform_services.application_identity import set_app_id
    set_app_id()
    from PySide6.QtWidgets import QApplication
    from .single_instance import SingleInstanceController
    app = QApplication.instance() or QApplication(sys.argv)
    instance = SingleInstanceController(lambda: None, app)
    if not instance.acquire():
        return 0
    try:
        prepare_user_directories()
        from router_app.bootstrap import run
        return run(version, instance=instance)
    except BaseException:
        instance.close()
        raise
