"""User-visible exit warnings, distinct from internal cleanup/update blockers."""
from modules.capture_services.openwrt.models import CaptureTaskType

_CAPTURE_TYPES = {
    CaptureTaskType.PMKID_GLOBAL.value: "PMKID \u6293\u5305",
    CaptureTaskType.PMKID_FILTER.value: "\u5b9a\u5411 PMKID \u6293\u5305",
    CaptureTaskType.HANDSHAKE.value: "\u5e38\u89c4\u6293\u5305",
    CaptureTaskType.AUTO_HANDSHAKE.value: "\u81ea\u52a8\u6293\u5305",
    CaptureTaskType.WPS.value: "WPS \u4efb\u52a1",
    CaptureTaskType.PASSWORD_VERIFY.value: "\u5728\u7ebf\u5bc6\u7801\u9a8c\u8bc1",
}
_TRANSFERS = {"upload", "upload_directory", "download", "download_directory"}


def _transfers(item):
    if item is None:
        return False
    action, args = item[1], item[2]
    if action in _TRANSFERS:
        return True
    return action == "file_batch" and any(job.get("operation") in _TRANSFERS for job in args.get("jobs", ()))


def exit_tasks(service):
    """Read owned state only; never inspect sockets, poll a router or equate login with work."""
    labels = []
    controller = service.task_controller
    if controller.busy:
        _state, kind, _session = controller.snapshot()
        kind = getattr(kind, "value", kind)
        if kind in _CAPTURE_TYPES:
            labels.append(_CAPTURE_TYPES[kind])
    if service.router_terminal.active:
        labels.append("\u7ec8\u7aef\u4f1a\u8bdd\u8fd0\u884c\u4e2d")
    management = service.router_management
    if _transfers(management.current):
        labels.append("\u6587\u4ef6\u4e0a\u4f20\u6216\u4e0b\u8f7d\u4e2d")
    if any(_transfers(item) for item in management.queue):
        labels.append("\u6709\u7b49\u5f85\u4e0a\u4f20\u6216\u4e0b\u8f7d\u7684\u6587\u4ef6")
    for name in ("common_bundle_download", "capture_bundle_download"):
        bundle = getattr(service, name, None)
        if bundle is not None and bundle.active and bundle.phase == "downloading":
            labels.append("\u5e38\u7528\u4f9d\u8d56\u79bb\u7ebf\u5305\u4e0b\u8f7d\u4e2d" if name == "common_bundle_download" else "\u6293\u5305\u4f9d\u8d56\u79bb\u7ebf\u5305\u4e0b\u8f7d\u4e2d")
    return tuple(labels)
