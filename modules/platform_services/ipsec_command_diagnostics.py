"""Bounded command metadata: never include arguments, output or policy identities."""
LIMIT = 12
OPERATIONS = frozenset(('dump', 'exportpolicy', 'add_filterlist', 'add_filter',
    'add_filteraction', 'add_rule', 'delete_rule', 'delete_filterlist', 'delete_filteraction', 'other'))
OUTCOMES = frozenset(('completed', 'failed', 'timed_out', 'capture_failed'))


def operation_name(args):
    if tuple(args) == ('dump',):return 'dump'
    if len(args) >= 2 and tuple(args[:2]) == ('static', 'exportpolicy'):return 'exportpolicy'
    if len(args) >= 3 and args[0] == 'static':
        value=args[1]+'_'+args[2]
        if value in OPERATIONS:return value
    return 'other'


def safe_commands(value):
    if not isinstance(value,list) or len(value)>LIMIT:return []
    result=[]
    for row in value:
        if not isinstance(row,dict):continue
        operation,outcome=row.get('operation'),row.get('outcome')
        if not isinstance(operation,str) or operation not in OPERATIONS:continue
        if not isinstance(outcome,str) or outcome not in OUTCOMES:continue
        clean=dict(operation=operation,outcome=outcome)
        for key,low,high in (('elapsed_ms',0,180000),('output_bytes',0,4*1024*1024),
                             ('returncode',-(2**31),2**32-1)):
            number=row.get(key)
            if type(number) is int and low<=number<=high:clean[key]=number
        result.append(clean)
    return result
