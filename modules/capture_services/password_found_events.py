"""Route verified secrets to the GUI without changing task progress or history."""
from .openwrt.password_events import PasswordFoundNotice, PasswordTargetUpdate


def forward_found_notice(service, update):
    from .password_target_status import apply_update
    if isinstance(update, PasswordTargetUpdate):
        if not service._shutting_down:
            apply_update(service, update)
        return True
    if not isinstance(update, PasswordFoundNotice):
        return False
    if service._shutting_down:
        update.clear()
    else:
        apply_update(service, PasswordTargetUpdate(update.ssid, update.bssid, 'found'))
        service.passwordFound.emit(update)
    return True
