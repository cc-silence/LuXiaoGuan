"""Safe option flags for an initial security apply; no configuration values."""
ITEM_KEYS = ('time','timezone','language','ssh','web','ping','igmp','http','rescue','dhcp_log',
             'mount','swap','upgrade','replace_ap','identity','lan','homepage','logs')
FLAGS = frozenset({'initial_security','initial_new_ap','initial_files'}
                  | {'initial_selected_' + key for key in ITEM_KEYS}
                  | {'initial_config_' + key for key in ('wireless','network','firewall','dhcp','system','dropbear','uhttpd','luci','fstab')})


def record_initial_selection(journal, review, *, status="started", result=None):
    if journal is None or review.get('kind') != 'initial':
        return
    selected = review.get('options', {}).get('items', ITEM_KEYS)
    selected = set(selected) if isinstance(selected, (tuple,list)) else set()
    configs = {row.get('config') for row in review.get('operations', []) if isinstance(row,dict)}
    flags = {'initial_security':True,'initial_new_ap':bool(review.get('new_ap')),
             'initial_files':bool(review.get('files'))}
    flags.update(('initial_selected_' + key, key in selected) for key in ITEM_KEYS)
    flags.update(('initial_config_' + key, key in configs) for key in ('wireless','network','firewall','dhcp','system','dropbear','uhttpd','luci','fstab'))
    if isinstance(result,dict):
        flags.update((key,result[key]) for key in ('saved','applied','verified','reboot_requested') if type(result.get(key)) is bool)
    journal.record('workflow', operation='policy_apply', status=status, stage='configure', **flags)
