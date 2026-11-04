"""Same-version hcx candidates verified against current source metadata."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
from .signal_manager import SignalError

_handler=ContextVar('hcx_candidate_confirmation',default=None)

@dataclass(frozen=True)
class HcxReview:
    version: str
    architecture: str
    source: str
    original_size: int
    candidate_size: int
    original_sha256: str
    candidate_sha256: str

@contextmanager
def candidate_confirmation(handler):
    token=_handler.set(handler)
    try:yield
    finally:_handler.reset(token)

def confirm_alternate(device,path,pinned,fetch,cancel,source,message=None):
    from .hcx_sources import official_primary_row,checked_architecture,MAX_BODY
    from .package_download_diagnostics import endpoint
    if cancel is not None and cancel.is_set():raise SignalError('cancelled','Installation cancelled')
    try:
        row=official_primary_row(device,fetch,cancel)
    except InterruptedError:raise
    except (ValueError,RuntimeError,OSError) as exc:
        raise SignalError('dependencies','\u65e0\u6cd5\u6838\u5bf9\u5019\u9009\u5305\u7684\u6765\u6e90\u7d22\u5f15\uff0c\u672a\u6267\u884c\u5b89\u88c5\u3002') from exc
    size=path.stat().st_size
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    if (row['name']!='hcxdumptool' or row['version']!=pinned['version']
            or row['architecture']!=checked_architecture(device)
            or row['filename']!=path.name or not 0<size<=MAX_BODY
            or row['size'] is not None and row['size']!=size or row['sha256']!=digest):
        raise SignalError('dependencies','\u5019\u9009\u5305\u4e0e\u6765\u6e90\u7d22\u5f15\u4e0d\u4e00\u81f4\uff0c\u672a\u6267\u884c\u5b89\u88c5\u3002')
    if device.package_manager=='opkg':
        from .package_space import ipk_metadata
        try:fields=ipk_metadata(path)
        except Exception as exc:raise SignalError('dependencies','\u5019\u9009\u5305\u63a7\u5236\u4fe1\u606f\u65e0\u6cd5\u6821\u9a8c\u3002') from exc
        if any(fields.get(key)!=value for key,value in (('Package',row['name']),('Version',row['version']),('Architecture',row['architecture']))):
            raise SignalError('dependencies','\u5019\u9009\u5305\u5185\u7684\u540d\u79f0\u3001\u7248\u672c\u6216\u67b6\u6784\u4e0d\u7b26\u3002')
    review=HcxReview(row['version'],row['architecture'],endpoint(source),pinned['size'],size,pinned['sha256'],digest)
    tell=message or (lambda *_:None)
    tell('hcx candidate verified: version='+review.version+'; architecture='+review.architecture+'; source='+review.source+'; original_bytes='+str(review.original_size)+'; candidate_bytes='+str(size)+'; original_sha256='+review.original_sha256+'; candidate_sha256='+digest,'diagnostic')
    handler=_handler.get()
    if handler is None:
        raise SignalError('candidate_declined','\u68c0\u6d4b\u5230\u540c\u7248\u672c\u53e6\u4e00\u6784\u5efa\uff0c\u9700\u8981\u7528\u6237\u786e\u8ba4\u540e\u624d\u80fd\u5b89\u88c5\u3002')
    if not handler(review):
        tell('hcx candidate decision=declined; sha256='+digest,'diagnostic')
        raise SignalError('candidate_declined','\u5df2\u8df3\u8fc7 hcx \u5019\u9009\u5305\u3002')
    if cancel is not None and cancel.is_set():raise SignalError('cancelled','Installation cancelled')
    if path.stat().st_size!=size or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
        raise SignalError('dependencies','\u786e\u8ba4\u540e\u5019\u9009\u5305\u5df2\u6539\u53d8\uff0c\u672a\u5b89\u88c5\u3002')
    tell('hcx candidate decision=approved; sha256='+digest,'diagnostic')
    return dict(row,size=size)
