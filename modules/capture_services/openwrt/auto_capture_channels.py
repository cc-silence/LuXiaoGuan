"""Band-aware foreground capture channel policy, independent of radio numbering."""
from dataclasses import dataclass
import re
from .phy_channels import iter_phy_channels

LABELS = {'2g': '2.4G', '5g': '5G', '6g': '6G'}
SUFFIX = {'2g': 'a', '5g': 'b', '6g': 'c'}
DEFAULTS = {'2g': ','.join(str(n)+'a' for n in range(1, 12)),
            '5g': '36b,40b,44b,48b,149b,153b,157b,161b,165b',
            '6g': '5c,21c,37c,53c,69c,85c,101c,117c'}


def radio_band(bands):
    aliases = {'2g': '2g', '2.4g': '2g', '5g': '5g', '6g': '6g'}
    values = {aliases.get(str(value).lower()) for value in bands}
    return next(iter(values)) if len(values) == 1 and None not in values else None


def default_channels(bands):
    return DEFAULTS.get(radio_band(bands), '')


def normalize_channels(text, band, available=None):
    if band not in SUFFIX:
        raise ValueError("\u65e0\u6cd5\u786e\u8ba4\u672c\u6b21\u6293\u5305\u9891\u6bb5\uff0c\u8bf7\u5237\u65b0\u7f51\u5361\u3002")
    if not isinstance(text, str) or len(text) > 4096:
        raise ValueError('Invalid capture channel list')
    if not text.strip(): return ''
    tokens = text.split(',')
    if len(tokens) > 256: raise ValueError('Capture channel limit exceeded')
    result = []
    for token in tokens:
        match = re.fullmatch(r'([0-9]{1,3})([abc]?)', token.strip())
        if match is None: raise ValueError("\u4fe1\u9053\u683c\u5f0f\u65e0\u6548\uff0c\u8bf7\u4f7f\u7528\u9017\u53f7\u5206\u9694\u7684\u4fe1\u9053\u53f7\u3002")
        channel = int(match[1])
        if match[2] and match[2] != SUFFIX[band]:
            raise ValueError("\u4fe1\u9053\u8868\u4e0e\u6240\u9009\u7f51\u5361\u9891\u6bb5\u4e0d\u4e00\u81f4\uff0c\u8bf7\u91cd\u65b0\u9009\u62e9\u3002")
        valid = 1 <= channel <= 14 if band == '2g' else 15 <= channel <= 196 if band == '5g' else 1 <= channel <= 233
        if (available is None and not valid) or (available is not None and channel not in available):
            raise ValueError("\u6240\u9009\u4fe1\u9053\u4e0d\u5728\u8be5\u7269\u7406\u7f51\u5361\u5f53\u524d\u5141\u8bb8\u7684\u8303\u56f4\u5185\u3002")
        value = str(channel) + SUFFIX[band]
        if value not in result: result.append(value)
    return ','.join(result)


@dataclass(frozen=True)
class CaptureChannelPlan:
    band: str
    channels: str
    available: frozenset = frozenset()


def physical_inventory(text):
    inventory = {}
    for frequency, channel, flags in iter_phy_channels(text):
        band = '2g' if 2400 <= frequency <= 2500 else '5g' if 4900 <= frequency < 5925 else '6g' if 5925 <= frequency <= 7125 else None
        if band is not None:
            values = inventory.setdefault(band, set())
            if "disabled" not in flags.lower(): values.add(channel)
    return inventory


def channel_plan(text, bands, requested):
    inventory = physical_inventory(text)
    preferred = radio_band(bands)
    # A single live hardware band is authoritative; never derive it from phy/radio IDs.
    band = next(iter(inventory)) if len(inventory) == 1 else preferred if preferred in inventory else None
    if band is None:
        raise ValueError("\u65e0\u6cd5\u552f\u4e00\u786e\u8ba4\u7269\u7406\u7f51\u5361\u7684\u53ef\u7528\u9891\u6bb5\uff0c\u5df2\u963b\u6b62\u542f\u52a8\uff0c\u8bf7\u5237\u65b0\u3002")
    available = inventory[band]
    if not available:
        raise ValueError("\u6240\u9009\u9891\u6bb5\u6ca1\u6709\u5f53\u524d\u53ef\u7528\u4fe1\u9053\uff0c\u672a\u542f\u52a8\u6293\u5305\u3002")
    if requested.strip():
        channels = normalize_channels(requested, band, available)
    else:
        recommended = [value for value in DEFAULTS[band].split(',') if int(value[:-1]) in available]
        channels = ','.join(recommended or [str(n)+SUFFIX[band] for n in sorted(available)])
    return CaptureChannelPlan(band, channels, frozenset(available))


def prepare_session_channels(session, requested, snapshot=None):
    """Read live capability once, before takeover or monitor creation."""
    from dataclasses import replace
    from .command_builder import quote_argument
    from .command_runner import require_command_completion
    if not re.fullmatch(r'phy[0-9]+', session.phy): raise ValueError('Unverified physical radio')
    result = session.runner.run('capture_channel_capabilities',
        'iw phy ' + quote_argument(session.phy) + ' info 2>/dev/null',
        timeout=6, cancel_event=session._stop_event, output_limit=131072)
    require_command_completion(result)
    if result.exit_code != 0: raise RuntimeError("\u65e0\u6cd5\u8bfb\u53d6\u7269\u7406\u7f51\u5361\u7684\u4fe1\u9053\u80fd\u529b\uff0c\u672a\u542f\u52a8\u6293\u5305\u3002")
    from .radio_identity import uci_radio_bands
    current = uci_radio_bands(snapshot).get(session.radio_name) if snapshot is not None else None
    bands = (current,) if current in LABELS else session.radio_info.bands
    plan = channel_plan(result.stdout, bands, requested)
    session.radio_info = replace(session.radio_info, interface=session.phy, bands=(LABELS[plan.band],))
    session._channel_plan = plan
    return plan.channels
