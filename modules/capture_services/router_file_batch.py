"""文件批量任务复用单文件执行器，串行执行并返回真实部分结果。"""
from modules.platform_services.local_file_operations import recycle_local_entry
from .router_file_move import move_local,move_remote


def run_file_batch(manager, jobs, cancel, report, connection_valid):
    """遇到取消、连接变化或失败停止；不撤销已完成文件，不跳过错误。"""
    results = []
    error = ""
    cancelled = False
    done = 0
    total = sum(job["size"] for job in jobs)
    for index, job in enumerate(jobs):
        if cancel.is_set():
            cancelled = True
            break
        if not connection_valid():
            error = "连接已变化，剩余文件未处理。"
            break
        operation, args = job["operation"], job["arguments"]
        actual_size = job["size"]
        def progress(value):
            nonlocal total, actual_size
            size = int(value["total"])
            total += size - actual_size
            actual_size = size
            report(dict(completed=done + int(value["completed"]),
                        total=total,
                        index=index + 1, count=len(jobs), name=value.get("name",job["name"]),stage=value.get("stage","")))
        report(dict(completed=done, total=total, index=index+1,
                    count=len(jobs), name=job["name"]))
        try:
            if operation in {"upload", "download"}:
                value = manager.transfer_file(operation=="upload", cancel=cancel,
                                              progress=progress, **args)
            elif operation in {"upload_directory", "download_directory"}:
                from .openwrt.router_directory_transfer import transfer_directory
                value = transfer_directory(manager, operation=="upload_directory", cancel=cancel,
                                           progress=progress, **args)
            elif operation == "local_move":
                value = move_local(cancel=cancel, **args)
            elif operation == "remote_move":
                value = move_remote(manager,cancel=cancel, **args)
            elif operation == "local_delete":
                value = recycle_local_entry(cancel=cancel, **args)
            elif operation == "delete":
                value = manager.mutate("delete", cancel=cancel, progress=progress, **args)
            else:
                raise ValueError("Unsupported batch operation")
        except InterruptedError:
            cancelled = True
            break
        except Exception as exc:
            error = f'{job["name"]}：{exc}'
            break
        results.append(value)
        done += actual_size
        report(dict(completed=done, total=total, index=index+1,
                    count=len(jobs), name=job["name"]))
    return dict(results=results, count=len(jobs), error=error, cancelled=cancelled)
