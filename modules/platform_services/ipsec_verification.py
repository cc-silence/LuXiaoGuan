"""Bounded, non-sensitive IPsec validation evidence suitable for exported logs."""
REASONS = frozenset(('policy_data','output_decode','output_unrecognized','output_parse','filterlist_count','filter_count',
    'filter_protocol','filter_endpoint','filter_srcmask','filter_dstmask','action_count','action_permit',
    'rule_count','rule_tunnel','rule_association','foreign_reference'))
FIELDS = frozenset(('', 'srcaddr','dstaddr','srcport','dstport','mirrored','protocol','srcmask','dstmask',
    'policy','filterlist','filteraction','conntype','activate','tunnel'))


def safe_evidence(value):
    if not isinstance(value,dict) or not isinstance(value.get('reason'),str) or value['reason'] not in REASONS:
        return {}
    result={'reason':value['reason']}
    if isinstance(value.get('field'),str) and value['field'] in FIELDS:result['field']=value['field']
    for key in ('checked','decoded_candidates','rows'):
        number=value.get(key)
        if type(number) is int and 0<=number<=4096:result[key]=number
    return result


class IPsecVerificationError(ValueError):
    def __init__(self,reason,*,field='',checked=0,rows=0,decoded_candidates=0):
        self.verification=safe_evidence(dict(reason=reason,field=field,checked=checked,
            rows=rows,decoded_candidates=decoded_candidates))
        super().__init__('IPsec verification failed: '+reason+(' ('+field+')' if field else ''))


def failure_message(value):
    evidence=safe_evidence(value)
    if not evidence:return ''
    reason=evidence['reason']
    if reason=='policy_data':return '系统保存的 IP 策略数据格式无法校验'
    if reason=='output_decode':return '无法解码系统返回的 IP 策略结果'
    if reason=='output_unrecognized':return '系统未返回可识别的 IP 策略脚本'
    if reason=='output_parse':return '系统返回的 IP 策略格式无法校验'
    if reason.endswith('_count'):return '本次放行对象缺失或重复（'+reason+'）'
    return '本次放行规则与确认的目标不一致（'+reason+(' / '+evidence['field'] if evidence.get('field') else '')+'）'
