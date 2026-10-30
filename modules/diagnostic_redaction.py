"""Typed diagnostic literals shared by persistence and export redaction."""
from datetime import datetime
import re

_TIMESTAMP = r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})'
_LITERALS = re.compile(
    r'(?<![A-Za-z0-9])(?:' + _TIMESTAMP +
    r'|hcxmon[0-9]{1,3}_[0-9]{8}_[0-9]{6}\.pcapng)(?![A-Za-z0-9])')

def valid_timestamp(value):
    if not isinstance(value,str) or not re.fullmatch(_TIMESTAMP,value):
        return False
    try:
        datetime.fromisoformat(value.replace('Z','+00:00'))
        return True
    except ValueError:
        return False

def mask_diagnostic_literals(text, masker):
    """Protect validated generated literals from partial identity collisions only."""
    if getattr(masker,'overflow',False):
        return masker.text(text)
    pattern = getattr(masker,'pattern',None)
    parts, previous = [], 0
    for match in _LITERALS.finditer(text):
        literal = match[0]
        if literal.startswith('hcxmon'):
            try:
                datetime.strptime(literal.split('_',1)[1][:-7],'%Y%m%d_%H%M%S')
            except ValueError:
                continue
        elif not valid_timestamp(literal):
            continue
        # A known identity equal to the whole literal must still be hidden.
        if pattern is not None and pattern.fullmatch(literal):
            continue
        parts.extend((masker.text(text[previous:match.start()]),literal))
        previous=match.end()
    parts.append(masker.text(text[previous:]))
    return ''.join(parts)
