"""Task-local, bounded apply evidence. Never store settings, addresses or secrets."""
import re
import time
from .operation_diagnostics import OperationDiagnostics
from ..management_diagnostic_schema import CODES


class ApplyEvidence:
    def __init__(self,journal=None):
        self.journal=journal if isinstance(journal,OperationDiagnostics) else None
        self.epoch=self.journal.epoch if self.journal else None
        self.started=time.monotonic();self.trace_id=None;self.signature=None
        self.counts=dict(connect_attempts=0,verify_attempts=0,restore_attempts=0)
        self.last_stage='';self.last_reason='';self.error_type='';self.native_error=None;self.restored=False;self.primary=None;self.current_stage='';self.runtime_reason=''

    def attach(self,channel):
        from .command_runner import OpenWrtCommandRunner
        runner=getattr(channel,'runner',None)
        if self.journal is not None and isinstance(runner,OpenWrtCommandRunner):
            runner.operation_diagnostics=self.journal

    def emit(self,stage,status='progress',reason='',error=None,**fields):
        if stage!='finish':self.current_stage=stage
        if stage=='rollback' and self.primary is None:
            self.primary=(self.last_stage,self.last_reason,self.error_type,self.native_error)
        if error is not None:
            self.error_type=type(error).__name__
            code=getattr(error,'code','')
            match=re.fullmatch(r'rpc_uci_(?:apply|confirm|rollback|commit)_([0-9]{1,3})',str(code))
            self.native_error=int(match[1]) if match else None
            reason=code if code in CODES else 'native_rpc_error' if match else 'exception'
        if status=='retry':self.last_stage=stage;self.last_reason=reason
        signature=(stage,status,reason,self.error_type,self.native_error)
        if signature==self.signature:return
        self.signature=signature
        if self.journal:
            values=dict(operation='configuration_apply',stage=stage,status=status,reason=reason,
                        elapsed_seconds=time.monotonic()-self.started,**self.counts,**fields)
            if self.trace_id is not None:values['trace_id']=self.trace_id
            if error is not None:values['exception_type']=self.error_type
            if self.native_error is not None:values['native_error']=self.native_error
            identity=self.journal.record('configuration',expected_epoch=self.epoch,**values)
            if self.trace_id is None:self.trace_id=identity

    def mismatch(self,reason):
        if reason:
            self.runtime_reason=reason
            self.emit('verify','retry',reason)

    def finish(self,status,error=None):
        if error is not None and not self.last_reason:
            self.emit(self.current_stage or 'stage','retry',error=error)
        if self.primary is not None:
            self.last_stage,self.last_reason,self.error_type,self.native_error=self.primary
        if error is not None and not self.error_type:self.error_type=type(error).__name__
        self.emit('finish',status,last_stage=self.last_stage,last_reason=self.last_reason,
                  exception_type=self.error_type,restored=self.restored)
