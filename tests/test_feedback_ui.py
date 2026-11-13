"""Rendered surface, local folder action and persistent-login preference regressions."""
import copy
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
from PySide6.QtCore import QObject, Signal, QPoint, Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout
from tests import test_standalone as standalone
APP = standalone.APP
from modules.main_pages.packet_capture.openwrt.common_bundle_controls import CommonBundleControls
from modules.main_pages.packet_capture.openwrt.common_tool_section import CommonToolSection
from modules.capture_services.openwrt.bundle_status import local_bundle_status
from modules.capture_services.openwrt.common_bundle_download import prepare_archive
from tests.test_common_offline_bundle import fixture
from threading import Event


class BundleService(QObject):
    changed=Signal()
    progress=Signal(object)
    finished=Signal(bool,str)
    offerReady=Signal(object)
    def __init__(self):
        super().__init__()
        self.phase="idle";self.local_status={"installed":False};self.message=""
        self.refresh_local=Mock();self.decline=Mock()


class FolderOwner(QObject):
    activityChanged=Signal()
    def __init__(self,service):
        super().__init__();self.is_busy=False;self.common_bundle_download=service


class FolderTests(unittest.TestCase):
    def test_folder_visibility_and_exact_opened_location(self):
        with TemporaryDirectory() as folder:
            service=BundleService();owner=FolderOwner(service);card=QWidget();layout=QVBoxLayout(card)
            controls=CommonBundleControls(card,layout,owner)
            self.assertTrue(controls.open_button.isHidden())
            service.local_status={"installed":True,"path":folder};service.changed.emit()
            self.assertFalse(controls.open_button.isHidden())
            with patch("modules.main_pages.packet_capture.openwrt.common_bundle_controls.QDesktopServices.openUrl",return_value=True) as opened:
                controls._open_location()
                self.assertTrue(Path(opened.call_args.args[0].toLocalFile()).samefile(folder))
            controls.set_light_theme(True);controls.set_light_theme(False)
            service.local_status={"installed":False};service.changed.emit()
            self.assertTrue(controls.open_button.isHidden())
            controls.dispose();card.deleteLater()

    def test_deleted_directory_refreshes_status_and_reports_reason(self):
        service=BundleService();owner=FolderOwner(service);card=QWidget();layout=QVBoxLayout(card)
        controls=CommonBundleControls(card,layout,owner)
        with TemporaryDirectory() as folder:
            path=Path(folder)/"deleted"
            service.local_status={"installed":True,"path":str(path)};service.changed.emit()
            with patch("modules.main_pages.packet_capture.openwrt.common_bundle_controls.QDesktopServices.openUrl") as opened,patch("modules.main_pages.packet_capture.openwrt.common_bundle_controls.MessageDialog") as message:
                controls._open_location()
                opened.assert_not_called();message.assert_called_once()
                self.assertTrue(controls.open_button.isHidden());service.refresh_local.assert_called_once()
        controls.dispose();card.deleteLater()

    def test_detected_location_is_validated_version_directory(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);f=fixture(root/"source")
            prepared=prepare_archive(f.archive,f.envelope,root/"resources",Event(),public_key=f.public)
            status=local_bundle_status(Event(),root=root/"resources",public_key=f.public)
            self.assertEqual(Path(status["path"]), Path(prepared["content_path"]).resolve())
            self.assertEqual(Path(status["path"]).parent, (root/"resources"/"versions").resolve())
            self.assertEqual(status["revision"], f.payload["sha256"])
            self.assertTrue(Path(status["path"]).is_dir())


class WindowFeedbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # These regressions simulate the synchronous Windows credential backend.
        # Linux worker ordering and stale replies have dedicated async tests.
        cls.sync_ui = patch("modules.main_pages.packet_capture.openwrt.login_credentials.is_linux", return_value=False)
        cls.windows_store = patch("modules.platform_services.credentials.is_windows", return_value=True)
        cls.sync_ui.start();cls.windows_store.start()
        for operation,value in (("read", ""), ("write", True), ("delete", True)):
            backend = patch("modules.platform_services.windows_credentials." + operation + "_generic_password", return_value=value)
            backend.start();cls.addClassCleanup(backend.stop)
        cls.addClassCleanup(cls.sync_ui.stop);cls.addClassCleanup(cls.windows_store.stop)
        standalone.StandaloneTests.setUpClass();cls.window=standalone.StandaloneTests.window

    @classmethod
    def tearDownClass(cls):standalone.StandaloneTests.tearDownClass()

    def test_surface_remains_opaque_while_children_are_invalidated(self):
        window=self.window
        for scale in (100,125,150,175):
            for theme in ("dark.qss","light.qss","dark.qss","light.qss"):
                window.apply_appearance(scale,"default",theme);APP.processEvents()
                window.window_frame.hide();APP.processEvents()
                image=window.grab().toImage();point=window.window_frame.geometry().center()
                self.assertEqual(image.pixelColor(point).alpha(),255,(scale,theme))
                self.assertFalse(window.mask().contains(window.rect().topLeft()), (scale, theme))
                self.assertFalse(window.testAttribute(Qt.WA_TranslucentBackground), (scale, theme))
                window.window_frame.show();APP.processEvents()
        window.apply_appearance(100,"default","light.qss");APP.processEvents()
        window.grab().save(str(Path(__file__).parent/"feedback-light.png"))
        window.apply_appearance(100,"default","dark.qss");APP.processEvents()
        window.grab().save(str(Path(__file__).parent/"feedback-dark.png"))

    def test_empty_password_login_reaches_service_without_missing_password_dialog(self):
        page=self.window.packet_capture_page;service=page.capture_service
        connection=page.openwrt_tab.login_page.connection
        connection.password.clear()
        with patch.object(page,"_save_settings_with_feedback",return_value=True),patch.object(service,"save_openwrt_password_preference"),patch.object(service,"connect_openwrt_async",return_value=True) as connect,patch("modules.main_pages.packet_capture_page.MessageDialog") as dialog:
            page._handle_openwrt_action("connect")
            connect.assert_called_once()
            dialog.assert_not_called()
            self.assertEqual(service.openwrt_password(),"")

    def test_login_has_no_fingerprint_option_and_ignores_old_fingerprints(self):
        page=self.window.packet_capture_page
        connection=page.openwrt_tab.login_page.connection
        self.assertFalse(hasattr(connection, "fingerprint_check"))
        settings={"host":"router.example", "port":22, "remember_host_fingerprint":True}
        page.settings["openwrt"]["trusted_host_fingerprints"]={"router.example:22":"SHA256:old"}
        with patch.object(page.capture_service,"connect_openwrt_async",return_value=True) as connect:
            page._connect_with_saved_credentials(settings)
            connect.assert_called_once_with(settings)

    def test_unchecking_password_deletes_persistence_but_keeps_current_input(self):
        page=self.window.packet_capture_page
        login=page.openwrt_tab.login_page
        connection=login.connection
        service=page.capture_service
        with patch("modules.platform_services.windows_credentials.read_generic_password",return_value=""),patch("modules.platform_services.windows_credentials.write_generic_password") as write,patch("modules.platform_services.windows_credentials.delete_generic_password") as delete:
            connection.remember_password.setChecked(False)
            connection.password.setText("current-session-only")
            connection.remember_password.setChecked(True)
            write.assert_called()
            target=write.call_args.args[0]
            connection.remember_password.setChecked(False)
            delete.assert_called_with(target)
            self.assertEqual(connection.password.text(),"current-session-only")
            self.assertEqual(service.openwrt_password(),"current-session-only")
            self.assertFalse(page.settings["openwrt"]["remember_password"])
            loaded=page.settings_manager.load()
            self.assertFalse(loaded["openwrt"]["remember_password"])
        connection.password.clear()



    def test_deletion_toast_moves_up_fades_and_reuses_one_instance(self):
        page=self.window.packet_capture_page
        login=page.openwrt_tab.login_page
        connection=login.connection
        with patch("modules.platform_services.windows_credentials.read_generic_password",return_value=""),patch("modules.platform_services.windows_credentials.write_generic_password"),patch("modules.platform_services.windows_credentials.delete_generic_password"):
            connection.remember_password.setChecked(True)
            connection.password.setText("keep-this-session")
            connection.remember_password.setChecked(False)
        toast=login._storage_feedback
        self.assertFalse(toast.isHidden())
        self.assertIn("\u5bc6\u7801",toast.text())
        start=toast.pos().y()
        toast._animation.setCurrentTime(900)
        self.assertLess(toast.pos().y(),start)
        self.assertLess(toast._opacity_effect.opacity(),1)
        toast._animation.setCurrentTime(toast._animation.duration())
        self.assertTrue(toast.isHidden())
        self.assertEqual(connection.password.text(),"keep-this-session")
        self.window.apply_appearance(100,"default","light.qss");APP.processEvents()
        login.show_storage_feedback(toast.text())
        self.window.grab().save(str(Path(__file__).parent/"feedback-toast-light.png"))
        self.window.apply_appearance(100,"default","dark.qss");APP.processEvents()
        login.show_storage_feedback(toast.text())
        self.window.grab().save(str(Path(__file__).parent/"feedback-toast-dark.png"))
        toast._animation.setCurrentTime(toast._animation.duration())
        connection.password.clear()

    def test_failed_deletions_never_show_success_toast(self):
        page=self.window.packet_capture_page
        login=page.openwrt_tab.login_page
        connection=login.connection
        with patch("modules.platform_services.windows_credentials.write_generic_password"),patch("modules.platform_services.windows_credentials.read_generic_password",return_value=""):
            connection.remember_password.setChecked(True)
        with patch("modules.platform_services.windows_credentials.delete_generic_password",side_effect=OSError("denied")),patch.object(login,"show_storage_feedback") as toast:
            connection.password.setText("retain-on-failure")
            connection.remember_password.setChecked(False)
            toast.assert_not_called()
            self.assertEqual(connection.password.text(),"retain-on-failure")
            self.assertTrue(connection.remember_password.isChecked())
            self.assertTrue(page.settings["openwrt"]["remember_password"])
        connection.password.clear()


    def test_failed_password_read_does_not_delete_stored_credential(self):
        page=self.window.packet_capture_page;login=page.openwrt_tab.login_page
        connection=login.connection
        with patch("modules.platform_services.windows_credentials.delete_generic_password"):
            connection.remember_password.setChecked(False)
        connection.password.clear()
        with patch("modules.platform_services.windows_credentials.read_generic_password",side_effect=OSError("denied")),patch("modules.platform_services.windows_credentials.delete_generic_password") as delete,patch("modules.platform_services.windows_credentials.write_generic_password") as write:
            connection.remember_password.setChecked(True)
            delete.assert_not_called()
            write.assert_not_called()
            self.assertFalse(connection.remember_password.isChecked())
            self.assertFalse(page.settings["openwrt"]["remember_password"])

    def test_failed_password_write_restores_preference_and_can_retry(self):
        page=self.window.packet_capture_page;login=page.openwrt_tab.login_page
        connection=login.connection
        with patch("modules.platform_services.windows_credentials.delete_generic_password"):
            connection.remember_password.setChecked(False)
        connection.password.setText("write-retry-session")
        with patch("modules.platform_services.windows_credentials.write_generic_password",side_effect=OSError("denied")),patch.object(login,"show_storage_feedback") as toast:
            connection.remember_password.setChecked(True)
            self.assertFalse(connection.remember_password.isChecked())
            self.assertFalse(page.settings["openwrt"]["remember_password"])
            self.assertEqual(connection.password.text(),"write-retry-session")
            toast.assert_not_called()
        with patch("modules.platform_services.windows_credentials.write_generic_password") as write:
            connection.remember_password.setChecked(True)
            write.assert_called_once()
            self.assertTrue(page.settings["openwrt"]["remember_password"])
        with patch("modules.platform_services.windows_credentials.delete_generic_password"):
            connection.remember_password.setChecked(False)
        connection.password.clear()


    def test_cancelling_storage_during_connected_session_does_not_disconnect_or_clear_input(self):
        from dataclasses import replace
        from modules.capture_services.openwrt.models import OpenWrtConnectionState
        page=self.window.packet_capture_page;service=page.capture_service
        login=page.openwrt_tab.login_page;connection=login.connection
        original=service.openwrt_backend.snapshot
        service.openwrt_backend.snapshot=replace(original,connection_state=OpenWrtConnectionState.CONNECTED)
        try:
            login.apply_snapshot(service.openwrt_snapshot)
            with patch("modules.platform_services.windows_credentials.write_generic_password"),patch("modules.platform_services.windows_credentials.read_generic_password",return_value=""),patch("modules.platform_services.windows_credentials.delete_generic_password") as deleted,patch.object(service,"disconnect_openwrt_async") as disconnect:
                connection.password.setText("connected-session")
                connection.remember_password.setChecked(True)
                connection.remember_password.setChecked(False)
                deleted.assert_called()
                disconnect.assert_not_called()
                self.assertEqual(service.openwrt_snapshot.connection_state,OpenWrtConnectionState.CONNECTED)
                self.assertEqual(connection.password.text(),"connected-session")
                self.assertEqual(service.openwrt_password(),"connected-session")
        finally:
            service.openwrt_backend.snapshot=original
            login.apply_snapshot(original)

    def test_repeated_disconnected_snapshot_keeps_typed_password(self):
        page=self.window.packet_capture_page;login=page.openwrt_tab.login_page
        with patch("modules.platform_services.windows_credentials.delete_generic_password"):
            login.connection.remember_password.setChecked(False)
        login.connection.password.setText("not-yet-connected")
        login.apply_snapshot(page.capture_service.openwrt_snapshot)
        self.assertEqual(login.connection.password.text(),"not-yet-connected")
        self.assertEqual(page.capture_service.openwrt_password(),"not-yet-connected")
        login.connection.password.clear()

    def test_rebuilt_login_restores_session_password_without_reading_credentials(self):
        from modules.main_pages.packet_capture.openwrt.router_login_page import RouterLoginPage
        from PySide6.QtCore import QCoreApplication,QEvent
        page=self.window.packet_capture_page;service=page.capture_service
        login=page.openwrt_tab.login_page
        with patch("modules.platform_services.windows_credentials.delete_generic_password"):
            login.connection.remember_password.setChecked(False)
        login.connection.password.setText("session-only-after-rebuild")
        settings=copy.deepcopy(page.settings["openwrt"])
        rebuilt=None
        try:
            with patch("modules.platform_services.windows_credentials.read_generic_password") as read,patch("modules.platform_services.windows_credentials.write_generic_password") as write:
                rebuilt=RouterLoginPage(service,settings,parent=self.window)
                self.assertEqual(rebuilt.connection.password.text(),"session-only-after-rebuild")
                self.assertEqual(service.openwrt_password(),"session-only-after-rebuild")
                self.assertFalse(rebuilt.connection.remember_password.isChecked())
                read.assert_not_called()
                write.assert_not_called()
        finally:
            if rebuilt is not None:
                # Match OpenWrtPage.dispose before deleting the reconstructed page.
                rebuilt.common_tools.dispose()
                rebuilt.dependencies.dispose()
                rebuilt.deleteLater()
                QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
            login.connection.password.clear()

    def test_unchanged_identity_focus_does_not_erase_typed_password(self):
        page=self.window.packet_capture_page;login=page.openwrt_tab.login_page
        with patch("modules.platform_services.windows_credentials.delete_generic_password"):
            login.connection.remember_password.setChecked(False)
        login.connection.password.setText("unchanged-device")
        login.connection.host.editingFinished.emit()
        self.assertEqual(login.connection.password.text(),"unchanged-device")
        login.connection.password.clear()

    def test_real_feedback_export_contains_runtime_and_wireless_environment(self):
        import json,zipfile
        from modules.display_privacy import masker_for
        from modules.log_services import export_logs
        page=self.window.packet_capture_page
        context=page._router_export_context(masker_for([]))
        with TemporaryDirectory() as folder:
            output=Path(folder)/"feedback.zip"
            export_logs(output,"login",[],router_context=context)
            with zipfile.ZipFile(output) as archive:
                data=json.loads(archive.read("router-info.json"))
                self.assertIn("runtime",data)
                self.assertIn("qt_version",data["runtime"])
                self.assertIn("environment",data["signals"])
                self.assertIn("workflows",data["operations"])

    def test_install_rejection_is_shown_in_common_dependency_section(self):
        page=self.window.packet_capture_page
        page.openwrt_tab.tabs.setCurrentIndex(1)
        section=page.openwrt_tab.login_page.common_tools
        section.card.set_expanded(True,animate=False);APP.processEvents()
        with patch("modules.main_pages.packet_capture.openwrt.common_tool_section.MessageDialog") as message:
            section._clicked("terminal")
            message.assert_called_once()
            self.assertTrue(message.call_args.args[1])


if __name__ == "__main__":unittest.main()
