"""Separate connection and dependency export scopes without router operations."""
import json

LOGIN_CATEGORY = 'router-login'
DEPENDENCY_CATEGORY = 'router-dependencies'
SCOPED_CATEGORIES = {LOGIN_CATEGORY, DEPENDENCY_CATEGORY}


def export_context(raw, category):
    """Keep device identity and only the operation evidence belonging to this panel."""
    context=json.loads(raw)
    for key in ('radio_selection','signals','pmkid_tool'):
        context.pop(key,None)
    operations=context.get('operations',{})
    selected={'schema_version':1}
    if category==LOGIN_CATEGORY:
        context.pop('dependencies',None)
        for key in ('login_attempts','network_policy'):
            if key in operations:selected[key]=operations[key]
    elif category==DEPENDENCY_CATEGORY:
        selected['workflows']=[row for row in operations.get('workflows',[])
                               if row.get('operation')=='dependency_request']
    else:
        raise ValueError('Unsupported scoped router log category')
    context['operations']=selected
    return json.dumps(context,ensure_ascii=False,indent=2)
