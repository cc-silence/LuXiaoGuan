"""Shortcut shell integration uses isolated temporary desktops, never the user's desktop."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from modules.platform_services.desktop_shortcut import (
    ShortcutSpec, shortcut_spec, shortcut_state, shell_arguments, DESCRIPTION)
from modules.release_identity import APPLICATION_EXECUTABLE
from modules.desktop_shell.desktop_shortcut_controller import DesktopShortcutController
APP = QApplication.instance() or QApplication([])


def wait_until(predicate, seconds=15):
    end = time.monotonic() + seconds
    while not predicate() and time.monotonic() < end:
        APP.processEvents()
        time.sleep(.005)
    if not predicate():
        raise AssertionError("Shortcut operation did not finish")


class ShortcutIdentityTests(unittest.TestCase):
    def test_source_and_frozen_entrypoints(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/"launch.pyw").touch()
            icons=root/"images/icons";icons.mkdir(parents=True);(icons/"app-router.ico").touch()
            host=root/"python.exe";host.touch();(root/"pythonw.exe").touch()
            spec=shortcut_spec(root,root,host,False)
            self.assertEqual(spec.target,root/"pythonw.exe")
            self.assertIn("launch.pyw",spec.arguments)
            exe=root/APPLICATION_EXECUTABLE;exe.touch()
            frozen=shortcut_spec(root,root,exe,True)
            self.assertEqual(frozen.target,exe);self.assertEqual(frozen.arguments,"")
            self.assertEqual(frozen.icon,str(exe)+",0")

    def test_valid_outdated_and_foreign_are_distinct(self):
        spec=ShortcutSpec(Path("Desktop/test.lnk"),Path("C:/app/LuXiaoGuan.exe"),"",Path("C:/app"),"C:/app/LuXiaoGuan.exe,0")
        self.assertEqual(shortcut_state(spec,{"exists":False}),"missing")
        observed=dict(exists=True,target=str(spec.target),arguments="",working_directory=str(spec.working_directory),icon=spec.icon)
        self.assertEqual(shortcut_state(spec,observed),"valid")
        observed["target"]="C:/old/LuXiaoGuan.exe"
        self.assertEqual(shortcut_state(spec,observed),"outdated")
        observed["target"]="C:/other/tool.exe"
        self.assertEqual(shortcut_state(spec,observed),"foreign")
        with self.assertRaises(PermissionError):shell_arguments(spec,"create",observed)
        self.assertTrue(shell_arguments(spec,"create",observed,True))

    def test_paths_are_encoded_as_data(self):
        value="C:/\u4e2d\u6587 user's folder/$test"
        spec=ShortcutSpec(Path(value+"/test.lnk"),Path(value+"/LuXiaoGuan.exe"),"",Path(value),value+"/app.ico,0")
        arguments=shell_arguments(spec,"inspect")
        script=base64.b64decode(arguments[-1]).decode("utf-16-le")
        self.assertNotIn(value,script)
        self.assertIn("FromBase64String",script)


@unittest.skipUnless(sys.platform=="win32","Windows shell required")
class ShortcutShellTests(unittest.TestCase):
    def test_actual_inspect_create_and_repair_in_redirected_desktop(self):
        with tempfile.TemporaryDirectory() as folder:
            desktop=Path(folder)/"\u6d4b\u8bd5 user's desktop";desktop.mkdir()
            controller=DesktopShortcutController()
            inspections=[];completed=[];errors=[]
            controller.inspected.connect(inspections.append)
            controller.completed.connect(completed.append)
            controller.failed.connect(errors.append)
            spec=shortcut_spec(desktop=desktop)
            self.assertTrue(controller.inspect(spec))
            self.assertFalse(controller.inspect(spec))
            wait_until(lambda:inspections or errors)
            self.assertFalse(errors,errors)
            self.assertEqual(shortcut_state(spec,inspections[-1]),"missing")
            controller.create(inspections[-1])
            wait_until(lambda:completed or errors)
            self.assertFalse(errors,errors)
            self.assertEqual(shortcut_state(spec,completed[-1]),"valid")
            self.assertTrue(spec.path.is_file())
            controller.inspect(spec)
            wait_until(lambda:len(inspections)==2 or errors)
            self.assertFalse(errors,errors)
            self.assertEqual(inspections[-1]["fingerprint"],completed[-1]["fingerprint"])
            # Move the expected install root; ownership remains identified by description.
            moved_entry=desktop/"launch.pyw";moved_entry.touch()
            moved=ShortcutSpec(spec.path,spec.target,subprocess.list2cmdline([str(moved_entry)]),desktop,spec.icon)
            controller.inspect(moved)
            wait_until(lambda:len(inspections)==3 or errors)
            self.assertEqual(shortcut_state(moved,inspections[-1]),"outdated")
            controller.create(inspections[-1])
            wait_until(lambda:len(completed)==2 or errors)
            self.assertFalse(errors,errors)
            self.assertEqual(shortcut_state(moved,completed[-1]),"valid")
            self.assertFalse(list(desktop.glob("*.part")))
            controller.deleteLater()


class RedirectedDesktopTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform=="win32","Windows registry required")
    def test_unavailable_redirected_desktop_is_not_replaced_by_home_desktop(self):
        import winreg
        from modules.platform_services.windows_user_paths import current_user_desktop
        with tempfile.TemporaryDirectory() as folder:
            destination=Path(folder)/"OneDrive user's desktop"
            with patch.object(winreg,"OpenKey"),patch.object(winreg,"QueryValueEx",return_value=(str(destination),winreg.REG_EXPAND_SZ)):
                self.assertEqual(current_user_desktop(),destination)
                spec=shortcut_spec()
                self.assertEqual(spec.path.parent,destination)
            controller=DesktopShortcutController();errors=[]
            controller.failed.connect(errors.append)
            self.assertTrue(controller.inspect(spec))
            wait_until(lambda:errors)
            self.assertFalse(controller.active)
            controller.deleteLater()
            self.assertFalse(destination.exists())

    @unittest.skipUnless(sys.platform=="win32","Windows shell required")
    def test_changed_shortcut_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            controller=DesktopShortcutController()
            observed=[];errors=[];done=[]
            controller.inspected.connect(observed.append);controller.failed.connect(errors.append)
            controller.completed.connect(done.append)
            spec=shortcut_spec(desktop=Path(folder))
            controller.inspect(spec);wait_until(lambda:observed or errors)
            self.assertFalse(errors,errors)
            spec.path.write_bytes(b"another file appeared after inspection")
            controller.create(observed[0]);wait_until(lambda:errors or done)
            self.assertTrue(errors);self.assertFalse(done)
            self.assertEqual(spec.path.read_bytes(),b"another file appeared after inspection")
            self.assertFalse(list(Path(folder).glob(".luxiaoguan-shortcut-*")))
            controller.deleteLater()
