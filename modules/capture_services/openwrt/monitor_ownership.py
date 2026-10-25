"""Owned monitor commands use the kernel ifalias ABI (Linux >= 2.6.28)."""
from .command_builder import quote_argument as q


def preflight_command(interface):
    path=q("/sys/class/net/"+interface)
    return ('test -d /sys/class/net && test -r /sys/class/net && test -x /sys/class/net || exit 81; '
            + 'if [ -e '+path+' ]; then printf occupied; else printf absent; fi')


def create_command(record,kind):
    path=q("/sys/class/net/"+record.interface)
    return ('test ! -e '+path+' || exit 72; '
            + 'iw phy '+q(record.radio)+' interface add '+q(record.interface)+' type '+q(kind)+' || exit 74; '
            + 'printf %s '+q(record.ownership_token)+' >'+path+'/ifalias || exit 73')


def owner_guard(record):
    path=q("/sys/class/net/"+record.interface)
    return ('test -d /sys/class/net || exit 81; '
            + 'test -e '+path+' || exit 82; '
            + 'test "$(cat '+path+'/phy80211/name 2>/dev/null)" = '+q(record.radio)+' || exit 83; '
            + 'test "$(cat '+path+'/ifalias 2>/dev/null)" = '+q(record.ownership_token)+' || exit 83; ')


def remove_command(record):
    path=q("/sys/class/net/"+record.interface)
    missing='exit 82' if record.creation_pending else 'exit 0'
    return ('test -d /sys/class/net || exit 81; '
            + 'if [ ! -e '+path+' ]; then '+missing+'; fi; '
            + owner_guard(record)+'iw dev '+q(record.interface)+' del')
