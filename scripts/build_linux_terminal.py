"""Build the existing terminal bridge on Debian 12 without replacing a usable binary on failure."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import uuid
ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from scripts.linux_runtime_check import host_errors, pinned_requirements, REVIEWED_QT_VERSION
from modules.platform_services.terminal_library import validate_linux_library
QT_VERSION = REVIEWED_QT_VERSION


def admit_host():
    release = platform.freedesktop_os_release() if sys.platform.startswith("linux") else {}
    errors = host_errors(platform.system(), release, sys.version_info,
                         platform.python_implementation(), platform.machine())
    if errors:
        raise ValueError("; ".join(errors))
    pinned_requirements(ROOT / 'requirements-linux.txt')
    from scripts.qt_runtime_contract import require_qt_runtime
    require_qt_runtime()
    from PySide6.QtCore import qVersion
    if qVersion() != QT_VERSION:
        raise ValueError("The terminal compiler must match the active PySide6 Qt version")


def source_inputs(source):
    if Path(source).is_symlink():
        raise ValueError("Linked terminal source is not accepted")
    rows = {}
    for path in sorted(Path(source).rglob("*")):
        if path.is_symlink():
            raise ValueError("Linked terminal source is not accepted")
        if path.is_file():
            rows[path.relative_to(source).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not rows or "bridge.cpp" not in rows or "CMakeLists.txt" not in rows:
        raise ValueError("Incomplete native terminal source")
    return rows


from scripts.linux_build_process import run_build


def build_linux_terminal(qt_prefix, *, root=ROOT):
    admit_host()  # No build directories or output mutations on a wrong host.
    root = Path(root).resolve()
    source = root / "native/router_terminal"
    qt_prefix = Path(qt_prefix).resolve()
    if not (qt_prefix / "lib/cmake/Qt6/Qt6Config.cmake").is_file():
        raise ValueError("Provide a separate Qt " + QT_VERSION + " development SDK, not Debian's different Qt version")
    inputs = source_inputs(source)
    stage = root / "build/native-terminal-linux" / uuid.uuid4().hex
    for path in (source, stage, root / "bin/router_terminal/libroutermanager_terminal.so"):
        if not path.resolve().is_relative_to(root) or any(parent.is_symlink() for parent in (path, *path.parents) if parent.is_relative_to(root)):
            raise ValueError("Terminal build paths must remain inside the project without links")
    stage.mkdir(parents=True)
    report = dict(status="building", platform="linux-x64", qt_version=QT_VERSION,
                  source_sha256=inputs, native_smoke_passed=False)
    try:
        run_build(["cmake", "-S", str(source), "-B", str(stage),
                   "-DCMAKE_BUILD_TYPE=Release", "-DROUTERMANAGER_QT_VERSION=" + QT_VERSION,
                   "-DCMAKE_PREFIX_PATH=" + str(qt_prefix)],
                  stage, "configure", 120)
        run_build(["cmake", "--build", str(stage), "--config", "Release", "--parallel", "2"],
                  stage, "compile", 1200)
        candidate = validate_linux_library(stage / "libroutermanager_terminal.so")
        smoke = ("import sys; from PySide6.QtWidgets import QApplication; "
                 "from modules.terminal_widgets.smoke_test import run_terminal_smoke; "
                 "app=QApplication([]); run_terminal_smoke(app, sys.argv[1])")
        env = os.environ.copy()
        env.update(QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
        # -c starts in the build stage; only this explicit source root is imported.
        smoke = "sys_path_root=" + repr(str(root)) + "; import sys; sys.path.insert(0, sys_path_root); " + smoke
        run_build([sys.executable, "-c", smoke, str(candidate)], stage, "smoke", 30, env)
        if source_inputs(source) != inputs:
            raise ValueError("Terminal source changed during compilation")
        report.update(status="verified", native_smoke_passed=True,
                      sha256=hashlib.sha256(candidate.read_bytes()).hexdigest())
        target = root / "bin/router_terminal/libroutermanager_terminal.so"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() or target.parent.is_symlink():
            raise ValueError("Linked terminal output is not accepted")
        temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".part")
        try:
            with candidate.open("rb") as incoming, temporary.open("xb") as output:
                import shutil
                shutil.copyfileobj(incoming, output, 1024 * 1024)
                output.flush(); os.fsync(output.fileno())
            temporary.chmod(0o755)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        report["status"] = "installed-and-native-smoke-verified"
        (target.with_suffix(".json")).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return target
    except BaseException as exc:
        report.update(status="failed", error=type(exc).__name__ + ": " + str(exc))
        raise
    finally:
        (stage / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qt-prefix", required=True, type=Path)
    args = parser.parse_args(argv)
    print(build_linux_terminal(args.qt_prefix))


if __name__ == "__main__":
    main()
