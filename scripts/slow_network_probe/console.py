"""Double-click entry for a portable Windows diagnostic bundle."""
from datetime import datetime
import json
import os
from pathlib import Path
import signal
import sys
import threading

from .transport import ROOT


def customer_main(automated=False):
    from modules.network_policy import NetworkChannel
    from modules.platform_services.app_update_access import read_code
    from .presentation import ChineseRunner
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("路小管 · 直连网络测试工具", flush=True)
    print("自动测试软件更新、增量文件、常用依赖和抓包依赖，不需要输入命令。", flush=True)
    print("本轮不走任何代理；先测文件首段，能连通后才下载完整文件并校验。", flush=True)
    print("可能需要几分钟；完整包较大时更久。按 Ctrl+C 可取消，请不要直接关窗口。\n", flush=True)
    cancel = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: cancel.set())
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else ROOT
    directory = base / "测试报告"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        sentinel = directory / ".write-test"
        sentinel.write_bytes(b"")
        sentinel.unlink()
    except OSError:
        directory = Path.home() / "LuXiaoGuan_NetworkReports"
        directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output = directory / ("直连测试_" + stamp + ".json")
    headers = {}
    try:
        code = read_code()
        if code:
            headers["X-RouterManager-Test-Code"] = code
    except OSError:
        print("无法读取本机已保存的内测资格，仍会测试公开清单和随包下载地址。", flush=True)
    runner = ChineseRunner(output, cancel, headers=headers, seed_directory=ROOT/"probe-data")
    identity = json.loads((ROOT/"probe-data/identity.json").read_text(encoding="utf-8"))
    runner.report.update(tool_version=identity["tool_version"], app_version=identity["app_version"],
                         selected_route="direct", sample_only=False)
    runner.save()
    status = 0
    try:
        route = NetworkChannel("direct", "Direct")
        runner.app(route, [])
        for kind in ("common", "capture"):
            if not cancel.is_set():
                runner.offline(kind, route, [])
    except Exception as exc:
        runner.report["fatal_error_type"] = type(exc).__name__
        print("\n测试未完整完成，错误已记录到报告中。", flush=True)
        status = 1
    finally:
        runner.report["cancelled"] = cancel.is_set()
        archive = runner.export()
    print("\n已取消。" if cancel.is_set() else "\n本轮测试已结束（不代表所有网络项目都成功）。", flush=True)
    print("把下面这个压缩包发回来即可：\n" + str(archive), flush=True)
    if not automated:
        if os.name == "nt":
            os.startfile(str(directory))
        try:
            input("\n按回车关闭窗口……")
        except EOFError:
            pass
    return 130 if cancel.is_set() else status
