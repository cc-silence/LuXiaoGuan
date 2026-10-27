"""Logical UCI section references survive anonymous-name regeneration."""
import copy


def reference(snapshot,config,section):
    row=snapshot['configs'].get(config,{}).get(section,{})
    if row.get('.anonymous') is not True:return None
    order=snapshot.get('order',{}).get(config,[])
    if section not in order:raise ValueError('Anonymous section order unavailable')
    return dict(index=order.index(section)+1,kind=row['.type'])


def resolve(snapshot,config,section,ref=None):
    if not ref:return section
    order=snapshot.get('order',{}).get(config,[])
    index=ref.get('index',0)
    if not isinstance(index,int) or not 1<=index<=len(order):return None
    name=order[index-1];row=snapshot['configs'].get(config,{}).get(name,{})
    return name if row.get('.anonymous') is True and row.get('.type')==ref.get('kind') else None


def annotate(snapshot,plan):
    for op in plan['operations']:
        ref=reference(snapshot,op['config'],op['section'])
        if ref:op['section_ref']=ref
        for member in op.get('members',[]):
            ref=reference(snapshot,'wireless',member['section'])
            if ref:member['section_ref']=ref
        if op['action']=='order':
            refs=[]
            for name in op['sections']:
                row=snapshot['configs'].get(op['config'],{}).get(name,{})
                if row.get('.anonymous') is True:
                    refs.append(dict(anonymous=True,values=copy.deepcopy(row)))
                else:refs.append(dict(name=name))
            op['order_refs']=refs
    return plan
