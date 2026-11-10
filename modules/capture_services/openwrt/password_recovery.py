"""Device-bound, owned recovery for interrupted password verification."""
import time
from .command_builder import quote_argument as q
from .password_environment import restore_disabled, RELOAD_CHANGED_CONFIG
from .password_restore import PasswordRestoreCheck, _read
from .password_recovery_store import validate
from .signal_recovery import SIGNAL_READY_SECONDS
from .password_pending_changes import snapshot_guards, PendingConfigurationConflict


def _owned_or_absent(package,section,session,action=':'):
    key=package+'.'+section
    pending='routermanager_pw_'+session
    expected='wifi-iface' if package=='wireless' else 'interface'
    # Creation has its full owner identity even before its option marker is saved.
    owner=('rm_recovery_marker=$(uci get '+q(key+'.routermanager_session')+' 2>&1); '
           'rm_recovery_marker_status=$?; '
           'if [ "$rm_recovery_marker_status" -eq 0 ]; then '
           '[ "$rm_recovery_marker" = '+q(session)+' ]; '
           'else [ "$rm_recovery_marker_status" -eq 1 ] && '
           '[ "$rm_recovery_marker" = '+q('uci: Entry not found')+' ] && '
           '[ "$rm_recovery_type" = '+q(pending)+' ]; fi')
    return ('rm_recovery_type=$(uci get '+q(key)+' 2>&1); rm_recovery_status=$?; '
            'if [ "$rm_recovery_status" -eq 0 ]; then '
            '( [ "$rm_recovery_type" = '+q(expected)+' ] || '
            '[ "$rm_recovery_type" = '+q(pending)+' ] ) && ( '+owner+' ) && '+action+'; '
            'elif [ "$rm_recovery_status" -eq 1 ] && [ "$rm_recovery_type" = '+q('uci: Entry not found')+' ]; '
            'then :; else false; fi')


def _original_or_disabled(name, original):
    key=q('wireless.'+name+'.disabled')
    # Only explicit NOTFOUND proves that the original unset option is unchanged.
    return ('rm_recovery_value=$(uci get '+key+' 2>&1); rm_recovery_status=$?; '
            'if [ "$rm_recovery_status" -eq 0 ]; then '
            '[ "$rm_recovery_value" = 1 ] || [ "$rm_recovery_value" = '+q(original or '')+' ]; '
            'elif [ "$rm_recovery_status" -eq 1 ] && '
            '[ "$rm_recovery_value" = '+q('uci: Entry not found')+' ] && '
            + ('true' if original is None else 'false')+'; then :; else false; fi')


class PasswordRecoveryJournal:
    def __init__(self,store,session,fingerprint,data=None):
        self.store,self.session,self.fingerprint=store,str(session),str(fingerprint)
        self.data=data

    @property
    def sta(self):return 'wifinet_pw'+self.session[:8]
    @property
    def net(self):return 'pwnet_'+self.session[:8]

    def capture(self,disabled,devices,baseline):
        if self.sta in devices:raise ValueError('Temporary section already belongs to existing configuration')
        self.data=dict(schema=1,session=self.session,fingerprint=self.fingerprint,
                       disabled=dict(disabled),devices=dict(devices),baseline=baseline,
                       modified=[],radios=[])
        self.store.save(self.data)

    def arm(self,modified,radios):
        if self.data is None:raise RuntimeError('Recovery record was not prepared')
        candidate=dict(self.data,modified=sorted(modified),radios=sorted(radios))
        self.store.save(candidate)
        self.data=candidate

    def setup_guards(self):
        return ['uci -q show wireless >/dev/null','uci -q show network >/dev/null',
                _owned_or_absent('wireless',self.sta,self.session),
                _owned_or_absent('network',self.net,self.session)]

    def guarded_setup(self,commands):
        """Give each new section an owner at its first persistent UCI write."""
        declarations={
            'uci set wireless.'+self.sta+'=wifi-iface':('wireless',self.sta),
            'uci set network.'+self.net+'=interface':('network',self.net),
        }
        if any(commands.count(command)!=1 for command in declarations):
            raise ValueError('Missing or duplicate temporary section creation')
        guarded=self.setup_guards()
        for command in commands:
            if command in declarations:
                package,section=declarations[command]
                guarded.extend([
                    'uci set '+q(package+'.'+section+'=routermanager_pw_'+self.session),
                    'uci set '+q(package+'.'+section+'.routermanager_session='+self.session),
                ])
            guarded.append(command)
        return guarded

    def discard(self):
        if self.data is not None:self.store.remove(self.data)
        self.data=None

    def recover(self,runner,fingerprint,cancel=None,*,deadline=None):
        if self.data is None:return
        validate(self.data)
        if fingerprint!=self.fingerprint:raise RuntimeError('Recovery device fingerprint mismatch')
        if not self.data['radios'] and not self.data['modified']:
            self.discard()
            return
        deadline=deadline if deadline is not None else time.monotonic()+120
        def remaining():
            if cancel is not None and cancel.is_set():raise InterruptedError('Recovery cancelled')
            budget=deadline-time.monotonic()
            if budget<=0:raise TimeoutError('Recovery deadline exhausted')
            return budget
        remaining()
        commands=snapshot_guards(runner,self.sta,self.net,self.data['modified'],
                                 cancel,timeout=min(10,remaining()))+self.setup_guards()
        for name in self.data['modified']:
            device=self.data['devices'][name]
            commands.append('[ "$(uci -q get '+q('wireless.'+name+'.device')+')" = '+q(device)+' ]')
            commands.append(_original_or_disabled(name,self.data['disabled'][name]))
        commands.extend([
            _owned_or_absent('wireless',self.sta,self.session,'uci delete '+q('wireless.'+self.sta)),
            _owned_or_absent('network',self.net,self.session,'uci delete '+q('network.'+self.net)),
        ])
        commands.extend(restore_disabled(name,self.data['disabled'][name]) for name in self.data['modified'])
        commands.extend(['uci commit wireless','uci commit network',RELOAD_CHANGED_CONFIG,'printf PASSWORD_RECOVERY_APPLIED'])
        command=' && '.join('( '+line+' )' for line in commands)
        result=_read(runner,'password_recovery_apply',command,cancel,timeout=min(60,remaining()))
        if result.strip()!='PASSWORD_RECOVERY_APPLIED':raise RuntimeError('Recovery apply was not confirmed')
        check=PasswordRestoreCheck(runner,self.data['baseline'])
        check.affected_radios=set(self.data['radios'])
        error=check.verify(self.sta,self.net,self.data['disabled'],self.data['devices'],
                           self.data['modified'],wait_seconds=min(SIGNAL_READY_SECONDS,remaining()),
                           cancel=cancel,deadline=deadline)
        if error:raise RuntimeError(error)
        remaining()
        self.discard()


def recover_pending(store,runner,fingerprint,cancel=None,*,progress_callback=None):
    from .password_recovery_admission import read_records
    records=read_records(store,fingerprint)
    deadline=time.monotonic()+120
    if records and progress_callback is not None:
        try:
            progress_callback("\u6b63\u5728\u6062\u590d\u4e0a\u6b21\u5bc6\u7801\u9a8c\u8bc1\u7684\u4e34\u65f6\u914d\u7f6e\uff0c\u5e76\u6838\u5b9e\u539f\u6709\u8fde\u63a5\u72b6\u6001\u2026")
        except Exception:
            # Optional UI delivery must not prevent device restoration.
            from modules.capture_services.capture_logging import LOGGER
            LOGGER.warning('Password recovery progress notification unavailable')
    for data in records:
        journal=PasswordRecoveryJournal(store,data['session'],fingerprint,data)
        try:
            journal.recover(runner,fingerprint,cancel,deadline=deadline)
        except PendingConfigurationConflict:
            raise
        except InterruptedError as error:
            raise InterruptedError("\u5df2\u53d6\u6d88\u81ea\u52a8\u6062\u590d\uff0c\u6062\u590d\u8bb0\u5f55\u5df2\u4fdd\u7559\uff1b\u4e0b\u6b21\u767b\u5f55\u540c\u4e00\u8def\u7531\u5668\u540e\u91cd\u8bd5\u3002") from error
        except Exception as error:
            raise RuntimeError("\u4e0a\u6b21\u5bc6\u7801\u9a8c\u8bc1\u7684\u914d\u7f6e\u6062\u590d\u5c1a\u672a\u786e\u8ba4\uff0c\u6062\u590d\u8bb0\u5f55\u5df2\u4fdd\u7559\uff1b\u8bf7\u4fdd\u6301\u539f\u8def\u7531\u5668\u5728\u7ebf\u540e\u91cd\u65b0\u767b\u5f55\u3002") from error
    return len(records)
