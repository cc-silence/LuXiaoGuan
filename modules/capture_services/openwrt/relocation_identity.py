"""Brand-style names for relocation previews; no device I/O or MAC changes."""
import secrets
import string
from .wireless_identity_profiles import PROFILES

_ROUTER_BRANDS = ('CMCC', 'TP-LINK', 'HUAWEI', 'Xiaomi', 'ZTE', 'Tenda')
_PHONE_NAMES = tuple(dict.fromkeys(host for _, host, _ in PROFILES
    if host.startswith(('vivo-', 'OPPO-', 'HUAWEI-', 'HONOR-', 'iQOO-', 'Galaxy-', 'Redmi-', 'Xiaomi-'))))
_SUFFIX_CHARS = string.ascii_uppercase + string.digits


def generate_relocation_hostname(previous=''):
    """Choose a router brand plus four characters, or an existing phone model."""
    previous = previous.casefold()
    if secrets.randbelow(2):
        return secrets.choice(tuple(name for name in _PHONE_NAMES if name.casefold() != previous))
    brand = secrets.choice(_ROUTER_BRANDS)
    suffix = ''.join(secrets.choice(_SUFFIX_CHARS) for _ in range(4))
    name = brand + '_' + suffix
    if name.casefold() == previous:
        name = name[:-1] + secrets.choice(_SUFFIX_CHARS.replace(suffix[-1], ''))
    return name
