"""Login preparation and cancellation finish in the owning worker."""
from .openwrt.models import OpenWrtConnectionResult


def run_login(backend, settings, password, fingerprint, cancel, progress,
              network_prepare=None, network_ready=None):
    def check():
        if cancel is not None and cancel.is_set():
            raise InterruptedError("Login cancelled")
    try:
        check()
        if network_prepare is not None:
            normalized = backend.validate_settings(settings)
            settings.update(normalized)
            network_prepare(normalized["host"], normalized["port"], cancel)
        elif network_ready is not None:
            network_ready(cancel)
        check()
        result = backend.connect(settings, password, expected_fingerprint=fingerprint,
                                 cancel_event=cancel, progress_callback=progress)
        check()
        return result
    except InterruptedError:
        if cancel is None or not cancel.is_set():
            raise
        backend.disconnect()
        return OpenWrtConnectionResult(status="cancelled", detail="\u767b\u5f55\u5df2\u53d6\u6d88\u3002")
