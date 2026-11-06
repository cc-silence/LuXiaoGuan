"""Offline resource compatibility, separate from signatures and package selection."""
import sys
from modules.app_update_services.manifest import version_key


class BundleClientUpdateRequired(ValueError):
    """A signed resource requires a newer application reader."""


class BundleCoverageUnavailable(ValueError):
    """The application needs components absent from the signed resource."""


def require_schema(value, expected=1):
    if type(value) is int and value > expected:
        raise BundleClientUpdateRequired("\u79bb\u7ebf\u4f9d\u8d56\u5305\u7684\u683c\u5f0f\u6bd4\u5f53\u524d\u8f6f\u4ef6\u65b0\uff0c\u8bf7\u5148\u66f4\u65b0\u8f6f\u4ef6\u540e\u518d\u4f7f\u7528\u3002")
    if type(value) is not int or value != expected:
        raise ValueError("\u79bb\u7ebf\u4f9d\u8d56\u5305\u683c\u5f0f\u58f0\u660e\u65e0\u6548\u3002")


def current_app_version():
    value = getattr(sys.modules.get('__main__'), 'APP_VERSION', None)
    if value is None:
        from main import APP_VERSION
        value = APP_VERSION
    version_key(value)
    return value


def require_client_version(metadata, current_version=None):
    if 'min_app_version' not in metadata:
        return
    minimum = metadata['min_app_version']
    try:
        required = version_key(minimum)
    except (ValueError, TypeError):
        raise ValueError("\u79bb\u7ebf\u4f9d\u8d56\u5305\u7684\u6700\u4f4e\u8f6f\u4ef6\u7248\u672c\u58f0\u660e\u65e0\u6548\u3002") from None
    current = current_app_version() if current_version is None else current_version
    if version_key(current) < required:
        raise BundleClientUpdateRequired(
            f"\u6b64\u79bb\u7ebf\u4f9d\u8d56\u5305\u9700\u8981 {minimum} \u6216\u66f4\u65b0\u7248\u8f6f\u4ef6\uff0c\u5f53\u524d\u4e3a {current}\uff1b\u8bf7\u5148\u66f4\u65b0\u8f6f\u4ef6\u3002")
