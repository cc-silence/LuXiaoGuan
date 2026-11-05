"""Platform selection, native admission, and failure-before-replacement regressions."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from modules.platform_services import terminal_library as library
from scripts import build_linux_terminal as builder


def elf(machine=62, bits=2, kind=3):
    header = bytearray(64)
    header[:7] = bytes([127, 69, 76, 70, bits, 1, 1])
    header[16:18] = kind.to_bytes(2, "little")
    header[18:20] = machine.to_bytes(2, "little")
    return header


class NativeTerminalPlatformTests(unittest.TestCase):
    def test_windows_path_is_preserved_and_linux_never_selects_dll(self):
        with patch.object(library, "is_windows", return_value=True):
            self.assertEqual(library.terminal_library_path("bundle"), Path("bundle/bin/router_terminal/winbox_terminal.dll"))
        with patch.object(library, "is_windows", return_value=False), patch.object(library, "is_linux", return_value=True), patch.object(library.platform, "machine", return_value="x86_64"), patch.object(library.struct, "calcsize", return_value=8):
            self.assertEqual(library.terminal_library_path("bundle").name, "libroutermanager_terminal.so")
            with patch.object(library.platform, "machine", return_value="aarch64"):
                with self.assertRaises(OSError): library.terminal_library_path("bundle")

    def test_other_architecture_windows_binary_and_non_library_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "terminal.so"
            for data in (b"MZ" + bytes(64), elf(machine=183), elf(bits=1), elf(kind=2), b"short"):
                path.write_bytes(data)
                with self.subTest(data=data[:20]), self.assertRaises(ValueError):
                    library.validate_linux_library(path)
            path.write_bytes(elf())
            self.assertEqual(library.validate_linux_library(path), path)

    def test_linux_loader_rejects_cross_platform_binary_before_cdll(self):
        from modules.terminal_widgets.native_view import NativeTerminalView
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "terminal.so"; path.write_bytes(b"MZ" + bytes(64))
            with patch("modules.platform_services.runtime.is_linux", return_value=True), patch("ctypes.CDLL") as load:
                with self.assertRaises(ValueError): NativeTerminalView._load_library(path)
                load.assert_not_called()


class NativeTerminalBuildTests(unittest.TestCase):
    def test_wrong_host_stops_before_creating_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(builder, "admit_host", side_effect=ValueError("wrong host")):
                with self.assertRaises(ValueError): builder.build_linux_terminal(root / "qt", root=root)
            self.assertEqual(list(root.iterdir()), [])

    def test_compile_and_smoke_failure_preserve_previous_binary(self):
        for fail_phase in ("configure", "compile", "smoke"):
            with self.subTest(phase=fail_phase), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                source = root / "native/router_terminal"; source.mkdir(parents=True)
                (source / "bridge.cpp").write_text("source", encoding="utf-8")
                (source / "CMakeLists.txt").write_text("cmake", encoding="utf-8")
                qt = root / "qt/lib/cmake/Qt6"; qt.mkdir(parents=True)
                (qt / "Qt6Config.cmake").write_text("sdk", encoding="utf-8")
                target = root / "bin/router_terminal/libroutermanager_terminal.so"
                target.parent.mkdir(parents=True); target.write_bytes(b"previous verified library")
                def run(command, stage, phase, timeout, env=None):
                    if phase == fail_phase: raise RuntimeError("synthetic build failure")
                    if phase == "compile": (stage / target.name).write_bytes(elf())
                with patch.object(builder, "admit_host"), patch.object(builder, "run_build", side_effect=run):
                    with self.assertRaisesRegex(RuntimeError, "synthetic"):
                        builder.build_linux_terminal(root / "qt", root=root)
                self.assertEqual(target.read_bytes(), b"previous verified library")
                self.assertFalse(target.with_suffix(".json").exists())
                reports = list((root / "build/native-terminal-linux").glob("*/report.json"))
                self.assertEqual(len(reports), 1)
                self.assertIn('"status": "failed"', reports[0].read_text(encoding="utf-8"))

    def test_missing_sdk_never_tries_system_qt(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(builder, "admit_host"), patch.object(builder, "run_build") as run:
            with self.assertRaises(ValueError): builder.build_linux_terminal(Path(folder), root=Path(folder))
            run.assert_not_called()


class NativeCompilerCleanupTests(unittest.TestCase):
    def test_timeout_reaps_group_even_if_cmake_parent_exits_first(self):
        import signal
        import subprocess
        from unittest.mock import Mock
        process = Mock(pid=123)
        process.wait.side_effect = [subprocess.TimeoutExpired("cmake", 1), 0, 0]
        with tempfile.TemporaryDirectory() as folder, patch.object(builder.subprocess, "Popen", return_value=process), patch.object(builder.os, "killpg", create=True) as kill, patch.object(builder.signal, "SIGKILL", 9, create=True):
            with self.assertRaises(subprocess.TimeoutExpired):
                builder.run_build(["cmake"], Path(folder), "compile", 1)
        self.assertEqual(kill.call_args_list[0].args, (123, signal.SIGTERM))
        self.assertEqual(kill.call_args_list[1].args, (123, 9))
        self.assertEqual(process.wait.call_count, 3)

    def test_keyboard_cancel_also_terminates_compiler_children(self):
        from unittest.mock import Mock
        process = Mock(pid=456)
        process.wait.side_effect = [KeyboardInterrupt(), 0, 0]
        with tempfile.TemporaryDirectory() as folder, patch.object(builder.subprocess, "Popen", return_value=process), patch.object(builder.os, "killpg", create=True) as kill, patch.object(builder.signal, "SIGKILL", 9, create=True):
            with self.assertRaises(KeyboardInterrupt):
                builder.run_build(["cmake"], Path(folder), "compile", 1)
        self.assertEqual(kill.call_count, 2)
        self.assertEqual(process.wait.call_count, 3)
