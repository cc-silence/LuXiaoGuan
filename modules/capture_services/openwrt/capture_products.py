"""Local capture product policy: retain originals and generate only real CAP."""
from pathlib import Path
from .cap_container import convert_to_cap, validate_cap


def raw_capture_suffix(source):
    with Path(source).open("rb") as handle:
        magic = handle.read(4)
    return ".pcapng" if magic == b"\x0a\x0d\x0d\x0a" else ".cap"


def cap_product(source, *, cancel_event=None, enabled=True):
    if not enabled:
        return None
    from .result_converter import unique_result_path
    source = Path(source)
    if source.suffix.lower() == ".cap" and raw_capture_suffix(source) == ".cap":
        return validate_cap(source, cancel_event=cancel_event)
    destination = unique_result_path(source.parent, source.stem, ".cap")
    return convert_to_cap(source, destination, cancel_event=cancel_event)


def cap_product_note(source, *, cancel_event=None, enabled=True):
    """Failure is a separate product warning; the original stays available."""
    if not enabled:
        return None, "未开启同时输出 CAP 格式，原始包已保留。"
    try:
        result = cap_product(source, cancel_event=cancel_event, enabled=enabled)
        return result, "CAP \u5df2\u751f\u6210\uff0c\u539f\u59cb\u5305\u5df2\u4fdd\u7559\u3002"
    except Exception as exc:
        return None, "CAP \u672a\u751f\u6210\uff0c\u539f\u59cb\u5305\u5df2\u4fdd\u7559\uff1a" + str(exc)
