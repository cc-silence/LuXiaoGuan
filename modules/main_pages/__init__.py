"""主页面按需导入，不在加载包时提前构造其他业务依赖。"""

__all__ = ["SoftwareDownloadPage", "SoftwareUpdatePage"]


def __getattr__(name):
    """保留旧导入接口，仅在真正请求页面类时导入并缓存。"""
    if name == "SoftwareDownloadPage":
        from .software_download_page import SoftwareDownloadPage
        globals()[name] = SoftwareDownloadPage
        return SoftwareDownloadPage
    if name == "SoftwareUpdatePage":
        from .software_update_page import SoftwareUpdatePage
        globals()[name] = SoftwareUpdatePage
        return SoftwareUpdatePage
    raise AttributeError(name)
