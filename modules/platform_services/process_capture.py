"""外部工具边界：有界输出、可取消及所属进程清理。"""

import queue
import subprocess
import time
from threading import Event, Thread

from .runtime import subprocess_creationflags


def capture_process(args, cancel, check=None, timeout=120, cwd=None, output_limit=4 * 1024 * 1024,
                    stderr=subprocess.STDOUT):
    """后台捕获有限输出，返回 CompletedProcess；取消或异常必回收所属进程。"""
    if cancel.is_set():
        raise InterruptedError("外部工具操作已取消。")
    process = subprocess.Popen([str(arg) for arg in args], stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=stderr, cwd=cwd,
                               creationflags=subprocess_creationflags())
    stopped = Event()
    box = queue.Queue(maxsize=16)

    def pump():
        try:
            while not stopped.is_set():
                chunk = process.stdout.read1(65536)
                while not stopped.is_set():
                    try:
                        box.put(chunk, timeout=0.1)
                        break
                    except queue.Full:
                        continue
                if not chunk:
                    return
        except (OSError, ValueError):
            stopped.set()

    thread = Thread(target=pump, daemon=True, name="WinBoxProcessOutput")
    output = bytearray()
    deadline = time.monotonic() + timeout
    try:
        thread.start()
        while True:
            if cancel.is_set():
                raise InterruptedError("外部工具操作已取消。")
            if time.monotonic() >= deadline:
                raise TimeoutError("外部工具操作超时，请检查程序、文件或存储设备。")
            if check:
                check()
            try:
                chunk = box.get(timeout=0.1)
            except queue.Empty:
                if stopped.is_set():
                    raise OSError("读取外部工具输出失败。")
                continue
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > output_limit:
                raise ValueError("外部工具输出超过安全上限，拒绝继续。")
        code = process.wait(timeout=5)
        return subprocess.CompletedProcess(args, code, bytes(output))
    finally:
        stopped.set()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if thread.ident is not None:
            thread.join(timeout=2)
        process.stdout.close()
        if thread.is_alive():
            raise TimeoutError("外部工具输出线程未退出。")
