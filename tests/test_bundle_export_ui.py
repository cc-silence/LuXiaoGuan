"""Cascading filters, shared card entry points and real Qt export lifecycle."""
import copy
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
from PySide6.QtCore import Qt,QObject,Signal
from PySide6.QtWidgets import QWidget,QVBoxLayout,QAbstractButton
from PySide6.QtTest import QSignalSpy
from tests.test_standalone import APP
from tests.test_feedback_ui import BundleService,FolderOwner
from tests.test_router_policy_worker import Owner,wait_for
from tests.test_bundle_export import prepared
from modules.capture_services.task_completion import start_capture_task
from modules.capture_services.bundle_export_service import BundleExportService
from modules.capture_services.openwrt.bundle_export import list_export_profiles,export_packages
from modules.main_pages.packet_capture.openwrt.bundle_export_dialog import BundleExportDialog
from modules.main_pages.packet_capture.openwrt.bundle_export_actions import BundleExportActions
from modules.main_pages.packet_capture.openwrt.common_bundle_controls import CommonBundleControls
from modules.ui_scale_manager import UiScaleManager

def options():
    profiles=[]
    for release,target,arch,manager in (
        ("24.10.8","mediatek/filogic","aarch64_cortex-a53","opkg"),
        ("24.10.8","ath79/generic","mips_24kc","opkg"),
        ("25.12.6","mediatek/filogic","aarch64_cortex-a53","apk")):
        profiles.append(dict(release=release,target=target,architecture=arch,package_manager=manager,
                             roots=[dict(name="iw",version="1"),dict(name="tmux",version="2")]))
    return dict(kind="common",revision="a"*64,profiles=profiles)

def select(dialog,release="24.10.8",target="mediatek/filogic"):
    dialog.release.setCurrentIndex(dialog.release.findData(release))
    dialog.target.setCurrentIndex(dialog.target.findData(target))
    dialog.architecture.setCurrentIndex(1)

class ExportUiTests(unittest.TestCase):
    def test_cascade_lists_only_valid_combinations_and_resets_downstream(self):
        dialog=BundleExportDialog(options());self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog.target.isEnabled());self.assertFalse(dialog.export_button.isEnabled())
        select(dialog)
        self.assertEqual(dialog.profile["architecture"],"aarch64_cortex-a53")
        self.assertEqual(set(dialog.checks),{"iw","tmux"})
        self.assertFalse(dialog.export_button.isEnabled())
        dialog.checks["iw"].setChecked(True)
        self.assertTrue(dialog.export_button.isEnabled())
        dialog.release.setCurrentIndex(dialog.release.findData("25.12.6"))
        self.assertIsNone(dialog.profile);self.assertFalse(dialog.checks)
        self.assertEqual(dialog.target.count(),2)
        select(dialog,"25.12.6")
        self.assertIn("APK",dialog.format_label.text())

    def test_multi_selection_emits_exact_profile_without_doing_file_io(self):
        dialog=BundleExportDialog(options());self.addCleanup(dialog.deleteLater)
        select(dialog);spy=QSignalSpy(dialog.exportRequested)
        dialog.all_button.click();dialog.export_button.click()
        self.assertEqual(spy.count(),1)
        self.assertEqual(set(spy.at(0)[1]),{"iw","tmux"})
        self.assertEqual(spy.at(0)[0]["revision"],"a"*64)
        dialog.none_button.click()
        self.assertFalse(dialog.export_button.isEnabled())

    def test_both_cards_have_extract_button_and_preserve_theme_and_busy_state(self):
        for kind in ("common","capture"):
            owner=FolderOwner(BundleService())
            setattr(owner,kind+"_bundle_download",BundleService())
            card=QWidget();layout=QVBoxLayout(card)
            controls=CommonBundleControls(card,layout,owner,kind)
            with patch("modules.main_pages.packet_capture.openwrt.bundle_export_actions.BundleExportActions") as actions:
                controls.extract_button.click()
                self.assertEqual(actions.call_args.args[2],kind)
                actions.return_value.open.assert_called_once()
            self.assertEqual(controls.extract_button.text(),"提取依赖")
            owner.is_busy=True;owner.activityChanged.emit()
            self.assertFalse(controls.extract_button.isEnabled())
            controls.set_light_theme(True);controls.set_light_theme(False)
            controls.dispose();card.deleteLater()

    def test_canceling_native_folder_picker_keeps_choices_and_does_not_export(self):
        owner=FolderOwner(BundleService());card=QWidget()
        actions=BundleExportActions(card,owner,"common")
        actions.service=Mock()
        dialog=BundleExportDialog(options(),card);actions.dialog=dialog
        select(dialog);dialog.checks["iw"].setChecked(True)
        with patch("modules.main_pages.packet_capture.openwrt.bundle_export_actions.QFileDialog.getExistingDirectory",return_value=""):
            actions._choose_destination(dict(revision="a"*64),["iw"])
        actions.service.export.assert_not_called()
        self.assertTrue(dialog.checks["iw"].isChecked())
        dialog.deleteLater();card.deleteLater()

    def test_real_paint_both_themes_and_scales(self):
        from modules.dialog_theme import build_base_stylesheet
        out=Path("build/bundle-export-review/ui");out.mkdir(parents=True,exist_ok=True)
        for light in (False,True):
            for percent in (100,150):
                parent=QWidget();parent.scale_manager=UiScaleManager(percent)
                parent.setProperty("settingsLightTheme",light)
                with patch("modules.common_widgets.common_dialogs.build_current_base_stylesheet",
                           side_effect=lambda cm,scale_manager:build_base_stylesheet(cm,light_theme=light,theme_name="light.qss" if light else "dark.qss",scale_manager=scale_manager)):
                    dialog=BundleExportDialog(options(),parent);select(dialog);dialog.all_button.click()
                    dialog.show();APP.processEvents()
                    for button in (dialog.export_button,dialog.all_button,dialog.none_button):
                        self.assertGreaterEqual(button.width(),button.fontMetrics().horizontalAdvance(button.text()))
                    self.assertTrue(dialog.grab().save(str(out/f"filters-{light}-{percent}.png")))
                    dialog.reject();dialog.deleteLater()
                parent.deleteLater()

class ExportOwner(Owner):
    def _start_openwrt_worker(self,key,operation,cancel,success,failure_slot=None,progress_slot=None,message_slot=None):
        start_capture_task(self,key,operation,cancel,success,failure_slot,progress_slot,message_slot)

class ExportWorkerTests(unittest.TestCase):
    def test_real_signed_files_are_exported_off_main_thread_and_slot_is_released(self):
        with tempfile.TemporaryDirectory() as folder:
            root,public,selection,wanted,_=prepared(Path(folder)/"fixture")
            target=Path(folder)/"output";target.mkdir()
            owner=ExportOwner(NS(),Mock());service=BundleExportService(owner,"common")
            ready=QSignalSpy(service.profilesReady);done=QSignalSpy(service.exported);errors=QSignalSpy(service.failed)
            threads=[]
            def read(kind,cancel,progress):
                threads.append(threading.current_thread() is threading.main_thread())
                return list_export_profiles(kind,cancel,progress,root=root,public_key=public)
            def export(kind,selected,names,destination,cancel,progress):
                threads.append(threading.current_thread() is threading.main_thread())
                return export_packages(kind,selected,names,destination,cancel,progress,root=root,public_key=public)
            with patch("modules.capture_services.openwrt.bundle_export.list_export_profiles",side_effect=read):
                self.assertTrue(service.load());wait_for(ready,1)
            self.assertEqual(ready.count(),1);self.assertFalse(owner.is_busy)
            with patch("modules.capture_services.openwrt.bundle_export.export_packages",side_effect=export):
                self.assertTrue(service.export(selection,wanted,str(target)));wait_for(done,1)
            self.assertEqual(done.count(),1);self.assertEqual(errors.count(),0)
            self.assertEqual(threads,[False,False]);self.assertFalse(owner.is_busy)
            self.assertEqual(done.at(0)[0]["count"],4)
            owner.deleteLater()

    def test_full_actions_chain_opens_selector_then_exports_without_holding_old_worker(self):
        with tempfile.TemporaryDirectory() as folder:
            root,public,selection,wanted,_=prepared(Path(folder)/"fixture")
            target=Path(folder)/"output";target.mkdir()
            owner=ExportOwner(NS(),Mock());parent=QWidget()
            actions=BundleExportActions(parent,owner,"common")
            def read(kind,cancel,progress):
                return list_export_profiles(kind,cancel,progress,root=root,public_key=public)
            def export(kind,selected,names,destination,cancel,progress):
                return export_packages(kind,selected,names,destination,cancel,progress,root=root,public_key=public)
            with patch("modules.capture_services.openwrt.bundle_export.list_export_profiles",side_effect=read):
                actions.open()
                ready=QSignalSpy(actions.service.profilesReady)
                wait_for(ready,1)
            self.assertEqual(ready.count(),1)
            self.assertIsNotNone(actions.dialog)
            self.assertFalse(owner.is_busy)
            dialog=actions.dialog;select(dialog)
            for name in wanted:dialog.checks[name].setChecked(True)
            done=QSignalSpy(actions.service.exported)
            with patch("modules.capture_services.openwrt.bundle_export.export_packages",side_effect=export),patch(
                    "modules.main_pages.packet_capture.openwrt.bundle_export_actions.QFileDialog.getExistingDirectory",return_value=str(target)),patch(
                    "modules.main_pages.packet_capture.openwrt.bundle_export_actions.MessageDialog") as message:
                dialog.export_button.click()
                wait_for(done,1)
                self.assertEqual(done.count(),1)
                message.return_value.open.assert_called_once()
            self.assertIsNone(actions.dialog);self.assertIsNone(actions.progress)
            self.assertFalse(owner.is_busy);self.assertFalse(actions.service.active)
            actions.dispose();parent.deleteLater();owner.deleteLater()

    def test_startup_failure_resets_active_state_and_busy_rejects_new_tasks(self):
        owner=ExportOwner(NS(),Mock());service=BundleExportService(owner,"capture")
        errors=QSignalSpy(service.failed)
        with patch.object(owner,"_start_openwrt_worker",side_effect=RuntimeError("fixture")):
            self.assertFalse(service.load())
        self.assertFalse(service.active);self.assertIsNone(service.cancel_event)
        owner._shutting_down=True
        self.assertFalse(service.load());self.assertEqual(errors.count(),2)
        owner.deleteLater()

if __name__=="__main__":unittest.main()
