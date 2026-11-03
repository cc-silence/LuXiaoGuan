"""A bounded capture failure survives subsequent successful attempts."""
import re
from modules.diagnostic_redaction import valid_timestamp


def sanitize_capture_failure(row,masker):
    from modules.log_services import sanitized_text
    if not isinstance(row,dict):raise ValueError("invalid capture failure")
    radio=row.get("radio","")
    result={"radio":radio if isinstance(radio,str) and re.fullmatch(r"radio[0-9]{1,3}",radio) else "other"}
    attempt=row.get("attempt")
    if type(attempt) is int and 0<attempt<2**32:result['attempt']=attempt
    if valid_timestamp(row.get('timestamp')):result['timestamp']=row['timestamp']
    for key in ('first_log','last_log'):
        text=row.get(key,'')
        result[key]=sanitized_text(text[:4096],masker) if isinstance(text,str) else ''
    return result
