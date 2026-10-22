"""远端任务线程退出后的最终后端释放。"""


def finish_backends(service, cancel):
    """后台等待探测线程并关闭连接；仍有任务时拒绝进入最终阶段。"""
    if service._openwrt_workers:
        raise RuntimeError("Capture workers still active; local cache retained")
    probe = service._probe_thread
    if probe is not None:
        probe.join(timeout=30)
        if probe.is_alive():
            raise TimeoutError("Platform probe has not stopped; cache retained")
    service.windows_backend.stop()
    service.openwrt_backend.stop()
