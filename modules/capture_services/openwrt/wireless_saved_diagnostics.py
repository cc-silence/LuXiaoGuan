"""Bounded field-only save verification evidence; never retain option values."""
from .wireless_options import IFACE_KEYS, RADIO_KEYS

FIELDS = tuple(sorted({
    'iface.' + key for key in ('exists','description','mode','device','ssid','disabled','key','macaddr','encryption',
        'hidden','isolate','maxassoc','wmm','ieee80211w','dtim_period','network','maclist','bssid',*IFACE_KEYS)
} | {'radio.' + key for key in ('channel','htmode','country','txpower',*RADIO_KEYS)}
  | {'network.' + key for key in ('hostname','proto','ipaddr','netmask','gateway','metric','peerdns','dns')}
  | {'firewall.zone'}))


def saved_check_detail(result, runner, action):
    field = result.get('verification')
    if type(field) is not str or field not in FIELDS:
        return ''
    journal = getattr(runner, 'operation_diagnostics', None)
    types = {key:result[source] for key,source in (('verification_actual_type','actual_type'),('verification_expected_type','expected_type'))
             if result.get(source) in ('nil','string','table','boolean','number')}
    if journal is not None:
        journal.record('workflow', operation='wireless_setup', status='failed',
                       stage='save_only' if action=='save_only_commit' else 'configure',
                       reason='unconfirmed', verification_field=field, **types)
    return field
