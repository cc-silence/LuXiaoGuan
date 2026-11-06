"""Reject unrelated UCI staging and bind checked changes to the mutation request."""
import hashlib
import re
import shlex
from .command_builder import quote_argument as q
from .password_restore import _read

PROBE = 'rm_pw_sum=$(printf %s "" | sha256sum) && [ "${rm_pw_sum%% *}" = e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 ] && uci changes wireless && printf "\\000" && uci changes network'
NAME = re.compile(r'[A-Za-z0-9_]{1,64}')


class PendingConfigurationConflict(RuntimeError):
    def __init__(self):
        super().__init__("\u68c0\u6d4b\u5230\u5176\u4ed6\u672a\u63d0\u4ea4\u7684\u65e0\u7ebf\u6216\u7f51\u7edc\u8bbe\u7f6e\uff0c\u5df2\u6682\u505c\u64cd\u4f5c\u4ee5\u514d\u987a\u5e26\u4fdd\u5b58\uff1b\u8bf7\u5148\u5728\u8def\u7531\u5668\u4e2d\u4fdd\u5b58\u6216\u64a4\u9500\u8fd9\u4e9b\u8bbe\u7f6e\u3002")


def owned_changes(text, package, temporary, modified, *, allow_owned=True):
    """Parse UCI shell quoting, including embedded newlines, without evaluating it."""
    if package not in ('wireless','network') or any(not isinstance(n,str) or not NAME.fullmatch(n) for n in (temporary,*modified)):
        raise ValueError('Invalid staging ownership')
    allowed_flags={'wireless.'+name+'.disabled' for name in modified}
    records=shlex.shlex(text,posix=True)
    records.whitespace='\r\n';records.whitespace_split=True;records.commenters=''
    for index,record in enumerate(records):
        if index>=8192:raise ValueError('Too many pending changes')
        key=record.partition('=')[0]
        key=key.removeprefix('-')
        temporary_key=package+'.'+temporary
        ours=(key==temporary_key or key.startswith(temporary_key+'.'))
        ours=ours or (package=='wireless' and key in allowed_flags)
        if not allow_owned or not ours:raise PendingConfigurationConflict()


def snapshot_guards(runner,sta,net,modified,cancel=None,*,timeout=10,allow_owned=True):
    # Raw values exist only in transient memory, never in metadata, commands or logs.
    parts=_read(runner,'password_recovery_changes',PROBE,cancel,timeout=timeout).split('\0')
    if len(parts)!=2:raise ValueError('Incomplete pending-change evidence')
    guards=[]
    for package,temporary,text in zip(('wireless','network'),(sta,net),parts):
        owned_changes(text,package,temporary,modified,allow_owned=allow_owned)
        digest=hashlib.sha256(text.rstrip('\n').encode('utf-8')).hexdigest()
        guards.append('rm_pw_changes=$(uci changes '+package+'); rm_pw_status=$?; '
                      '[ "$rm_pw_status" -eq 0 ] && '
                      'rm_pw_sum=$(printf %s "$rm_pw_changes" | sha256sum) && '
                      '[ "${rm_pw_sum%% *}" = '+q(digest)+' ]')
    return guards
