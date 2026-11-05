"""One lightweight platform boundary for update launch and preparation."""
from .runtime import is_linux


def launch_helper(root,stage,*,appearance=None):
    if is_linux():
        from .linux_update_install import launch_helper as launch
        return launch(root,stage,appearance=appearance)
    from .app_update_install import launch_helper as launch
    return launch(root,stage,appearance=appearance)


def channel_config(root):
    from pathlib import Path
    return Path(root)/('resources/app-update-channel.json' if is_linux() else 'config/app_update.json')


def begin_install(root,stage,*,appearance,parent,ready,failed):
    if is_linux():
        from .linux_update_handoff import LinuxUpdateHandoff
        handoff=LinuxUpdateHandoff(root,stage,appearance,parent,ready,failed)
        try:
            handoff.start()
        except BaseException:
            handoff.deleteLater()
            raise
        return handoff
    launch_helper(root,stage,appearance=appearance);ready();return None


def recover_before_gui(root):
    if is_linux():
        from .linux_update_install import recover_at_startup
        return recover_at_startup(root)
    from .app_update_install import recover_interrupted_update
    return recover_interrupted_update(root)
