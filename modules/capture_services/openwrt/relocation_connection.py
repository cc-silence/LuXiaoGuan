"""Pinned relocation transport; reads and rollback may reconnect, never stage/apply twice."""
import time
from .checked_apply import ApplyChannel
from .apply_evidence import ApplyEvidence


class RelocationConnection:
    def __init__(self,runner,settings,password,fingerprint):
        self.channel=ApplyChannel(settings,password,fingerprint)
        self.host=settings['host'];self.deadline=None
        self.evidence=ApplyEvidence(getattr(runner,'operation_diagnostics',None))
        self.evidence.attach(self.channel)
    def call(self,action,timeout=8,**values):
        stage='connect'
        try:
            remaining=self.deadline-time.monotonic() if self.deadline is not None else timeout+8
            if remaining<=0:raise TimeoutError('Relocation connection deadline expired')
            if not self.channel.ssh.connected:
                self.evidence.counts['connect_attempts']+=1
                self.evidence.emit('connect',kind='relocation')
            self.channel.connect(self.host,min(4,remaining))
            remaining=self.deadline-time.monotonic() if self.deadline is not None else timeout
            if remaining<=0:raise TimeoutError('Relocation command deadline expired')
            stage={'session':'session','move_stage':'apply','move_status':'verify',
                'move_preview':'restore_verify','finish':'confirm' if values.get('confirm') else 'rollback'}.get(action,'stage')
            self.evidence.emit(stage,kind='relocation')
            return self.channel.call(action,timeout=min(timeout,remaining),**values)
        except Exception as exc:
            self.evidence.emit(stage,'retry',error=exc,kind='relocation')
            raise
    def fresh(self):self.channel.ssh.close()
    def close(self):self.channel.close()
