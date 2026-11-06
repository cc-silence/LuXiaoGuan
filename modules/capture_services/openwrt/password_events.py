"""Typed password events; secrets are transient and status metadata is snapshot-safe."""
from dataclasses import dataclass, field


@dataclass
class PasswordFoundNotice:
    ssid: str
    bssid: str
    password_file: str
    password: str = field(repr=False)

    def clear(self):
        self.password = ""


@dataclass(frozen=True)
class PasswordTargetUpdate:
    ssid: str
    bssid: str
    state: str
    note: str = ""


def target_update_for(target, entry):
    if entry is None:return PasswordTargetUpdate(target.ssid,target.bssid,'verifying')
    if entry.found:state='found'
    elif getattr(entry,'outcome',''):state=entry.outcome
    elif getattr(entry,'error',False):state='failed'
    elif not getattr(entry,'final',True):state='round_pending'
    elif getattr(entry,'unconfirmed',0):state='unconfirmed'
    elif not entry.tried:state='history_skipped' if entry.skipped else 'skipped'
    else:state='no_match'
    note = str(getattr(entry,'note','') or '')
    note += '\n\u672c\u6b21\u5df2\u8bd5 '+str(entry.tried)+' \u4e2a\uff0c\u5386\u53f2\u8df3\u8fc7 '+str(entry.skipped)+' \u4e2a\u3002'
    return PasswordTargetUpdate(target.ssid,target.bssid,state,note)

