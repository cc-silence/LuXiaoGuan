"""Verify exact SSH exemptions from the saved local policy, without netsh dump."""
import base64
import ipaddress
import re
from . import legacy_ipsec_binary as binary
from .legacy_ipsec_store import LOCAL
from .ipsec_verification import IPsecVerificationError

PERMIT = '{8a171dd2-77e3-11d1-8659-a04f00000000}'
STANDARD = '{62f49e10-6c37-11d1-864c-14a300000000}'
OBJECT = re.compile(r'(ipsecFilter|ipsecNegotiationPolicy|ipsecNFA)\{[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}\}', re.I)


def _fail(reason, field=''):
    raise IPsecVerificationError(reason, field=field)


def _value(node, field, kinds):
    value = node.get(field)
    if not isinstance(value, (tuple, list)) or len(value) != 2 or type(value[0]) is not int or value[0] not in kinds:
        _fail('policy_data')
    return value[1]


def _references(node, field):
    value = _value(node, field, {1, 7})
    if node[field][0] == 1:
        if not isinstance(value, str):
            _fail('policy_data')
        value = [value]
    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
        _fail('policy_data')
    return [x.casefold() for x in value]


def _blob(node):
    data = _value(node, 'ipsecdata', {3})
    if not isinstance(data, dict) or set(data) != {'base64'}:
        _fail('policy_data')
    text = data['base64']
    if not isinstance(text, str) or len(text) > 6 * 1024 * 1024:
        _fail('policy_data')
    try:
        return base64.b64decode(text, validate=True)
    except ValueError:
        _fail('policy_data')


def _verify_filter(plan, node):
    expected = ipaddress.IPv4Address(plan.address).packed
    try:
        legacy, modern = binary.filters(_blob(node))
    except ValueError:
        _fail('policy_data')
    if not legacy and not modern:
        _fail('filter_count')
    for row in legacy:
        for field, value in [('mirror', 1), ('source', b'\x00' * 4), ('mask', b'\xff' * 4),
                             ('destination', expected), ('dstmask', b'\xff' * 4),
                             ('protocol', 6), ('srcport', 0), ('dstport', plan.port),
                             ('is_tunnel', 0), ('special', 0), ('options', 0)]:
            if row[field] != value:
                _fail('filter_endpoint', {'destination':'dstaddr','source':'srcaddr',
                    'mask':'srcmask','mirror':'mirrored'}.get(field, field))
    for row in modern:
        if row['mirror'] != 1:
            _fail('filter_endpoint', 'mirrored')
        if row['source'][0] != 8 or row['source'][1] not in (1, 3):
            _fail('filter_endpoint', 'srcaddr')
        kind, version, address, mask = row['destination']
        if version != 1 or address[:4] != expected or kind not in (1, 4):
            _fail('filter_endpoint', 'dstaddr')
        if kind == 4 and mask[:4] != b'\xff' * 4:
            _fail('filter_dstmask', 'dstmask')
        if row['srcport'][0] != 0:
            _fail('filter_endpoint', 'srcport')
        if row['dstport'][:2] != (1, plan.port):
            _fail('filter_endpoint', 'dstport')
        if row['protocol'] != 6:
            _fail('filter_protocol', 'protocol')
        if row['flags'] != 0:
            _fail('policy_data')
    if legacy and modern and legacy[0]['id'] != modern[0]['id']:
        _fail('policy_data')


def verify_snapshot(plan, snapshot):
    if snapshot.active_id.casefold() != plan.policy_id.casefold() or snapshot.name != plan.policy:
        _fail('rule_association', 'policy')
    nodes = snapshot.nodes
    keys = {}
    for name, kind, reason in zip(plan.names, ('ipsecFilter', 'ipsecNegotiationPolicy', 'ipsecNFA'),
                                  ('filterlist_count', 'action_count', 'rule_count')):
        matches = [key for key, node in nodes.items()
                   if isinstance(node.get('ipsecname', (0, None))[1], str)
                   and node['ipsecname'][1].casefold() == name.casefold()]
        if len(matches) != 1:
            _fail(reason)
        key = matches[0]
        if not OBJECT.fullmatch(key) or not key.casefold().startswith(kind.casefold() + '{'):
            _fail('policy_data')
        keys[kind] = key
    filters, action, rule = (nodes[keys[k]] for k in ('ipsecFilter', 'ipsecNegotiationPolicy', 'ipsecNFA'))
    paths = {k: (LOCAL + '\\' + key).casefold() for k, key in keys.items()}
    policy_path = (LOCAL + '\\' + snapshot.active_id).casefold()
    if _references(rule, 'ipsecfilterreference') != [paths['ipsecFilter']]:
        _fail('rule_association', 'filterlist')
    if _references(rule, 'ipsecnegotiationpolicyreference') != [paths['ipsecNegotiationPolicy']]:
        _fail('rule_association', 'filteraction')
    if _references(rule, 'ipsecownersreference') != [policy_path]:
        _fail('rule_association', 'policy')
    policy = nodes.get(snapshot.active_id, {})
    if _references(policy, 'ipsecnfareference').count(paths['ipsecNFA']) != 1:
        _fail('rule_association', 'policy')
    for node in (filters, action):
        if _references(node, 'ipsecownersreference') != [paths['ipsecNFA']]:
            _fail('foreign_reference', 'filterlist')
    for key, node in nodes.items():
        if key == keys['ipsecNFA']:
            continue
        for field, path in [('ipsecfilterreference', paths['ipsecFilter']),
                            ('ipsecnegotiationpolicyreference', paths['ipsecNegotiationPolicy'])]:
            if field in node and path in _references(node, field):
                _fail('foreign_reference', 'filterlist')
        if 'ipsecnfareference' in node and key != snapshot.active_id:
            if paths['ipsecNFA'] in _references(node, 'ipsecnfareference'):
                _fail('foreign_reference', 'policy')
    if str(_value(action, 'ipsecnegotiationpolicyaction', {1})).casefold() != PERMIT:
        _fail('action_permit')
    if str(_value(action, 'ipsecnegotiationpolicytype', {1})).casefold() != STANDARD:
        _fail('action_permit')
    _verify_filter(plan, filters)
    try:
        data = binary.rule(_blob(rule))
    except ValueError:
        _fail('policy_data')
    if data['interface'] != 0xfffffffd or data['interface_name'].strip(b'\x00'):
        _fail('rule_association', 'conntype')
    if data['is_tunnel'] != 0:
        _fail('rule_tunnel', 'tunnel')
    if data['active'] != 1:
        _fail('rule_association', 'activate')
    return True
