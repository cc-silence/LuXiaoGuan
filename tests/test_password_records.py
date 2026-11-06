"""Records, true hidden SSIDs, repeat success and bounded asynchronous UI."""
import csv
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import sleep
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget, QVBoxLayout
from tests.test_file_selection import APP
from modules.capture_services.openwrt.models import AccessPointInfo, OpenWrtSessionSnapshot
from modules.capture_services.openwrt.password_history import PasswordAttemptHistory, clear_history
from modules.capture_services.openwrt.password_results_store import read_password_file
from modules.capture_services.openwrt.password_verification import OpenWrtPasswordVerifier
from modules.capture_services.openwrt.password_rounds import CandidateRound, verify_with_history
from modules.main_pages.packet_capture.openwrt.access_point_table import AccessPointTable
from modules.ui_scale_manager import UiScaleManager

TARGET = AccessPointInfo('02:11:22:33:44:55', ssid='fixture', encryption='WPA2-PSK')


class RecordTests(unittest.TestCase):
    def verifier(self):
        verifier = OpenWrtPasswordVerifier(Mock())
        verifier._backup_configs = Mock()
        verifier._restore = Mock(return_value="")
        verifier._pick_radio = Mock(return_value='owned-radio')
        verifier._setup_environment = Mock()
        return verifier

    def test_success_is_reverified_after_reopen_while_wrong_key_is_skipped(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)/'history.db'
            verifier = self.verifier()
            control = Mock()
            control.attempt.side_effect = [False,True,True]
            def history(): return PasswordAttemptHistory(path,key=b'k'*32)
            with patch('modules.capture_services.openwrt.password_history.PasswordAttemptHistory',side_effect=history), patch('modules.capture_services.openwrt.password_control.open_control',return_value=control):
                for unused in range(2):
                    results = verify_with_history(verifier,(TARGET,),('wrong-pass','right-pass'),'owned',folder,Event(),None,None,rounds=(CandidateRound('fixture.txt',('wrong-pass','right-pass')),))
                    self.assertTrue(results[0].found)
            self.assertEqual([call.args[-1] for call in control.attempt.call_args_list],['wrong-pass','right-pass','right-pass'])
            with history() as records:
                self.assertTrue(records.contains(TARGET,'wrong-pass'))
                self.assertFalse(records.contains(TARGET,'right-pass'))
                row = records.catalog.query(found=True)['rows'][0]
                self.assertEqual(row['dictionaries'],'fixture.txt')
                self.assertEqual(row['progress'],'2/2')
                self.assertEqual(records.catalog.query(found=True)['rows'][0]['password'],'right-pass')
            self.assertNotIn(b'right-pass',path.read_bytes())
            self.assertEqual(clear_history(path),1)
            with history() as records:
                self.assertEqual(records.catalog.query(found=True)['rows'],[])
                records.catalog.import_results(Path(folder)/'\u6700\u7ec8\u7ed3\u679c',Event())
                self.assertEqual(records.catalog.query(found=True)['rows'],[])
                self.assertFalse(records.contains(TARGET,'wrong-pass'))

    def test_same_name_different_mac_keeps_both_password_results(self):
        with TemporaryDirectory() as folder, PasswordAttemptHistory(Path(folder)/'history',key=b'k'*32) as history:
            verifier=self.verifier()
            verifier._attempt_history=history
            control=Mock();control.attempt.return_value=True
            other=replace(TARGET,bssid='02:11:22:33:44:66')
            history.catalog.configure((CandidateRound('fixture',('first-pass','second-pass')),))
            with patch('modules.capture_services.openwrt.password_control.open_control',return_value=control):
                first=verifier._verify_single(TARGET,('first-pass',),0,2,'owned','net',folder,None,None,False)['entry']
                second=verifier._verify_single(other,('second-pass',),1,2,'owned','net',folder,None,None,False)['entry']
            self.assertNotEqual(first.password_file,second.password_file)
            self.assertEqual(read_password_file(first.password_file),(TARGET.ssid,TARGET.bssid,'first-pass'))
            self.assertEqual(read_password_file(second.password_file),(other.ssid,other.bssid,'second-pass'))
            rows=history.catalog.query(found=True)['rows']
            self.assertEqual({row['password'] for row in rows},{'first-pass','second-pass'})


    def test_old_success_fingerprint_repaired_from_saved_file(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)/'history.db'
            directory = Path(folder)/'results'
            directory.mkdir()
            result = directory/'fixture-password.txt'
            result.write_text('name\tmac\tpassword\nfixture\t02:11:22:33:44:55\tright-pass\n',encoding='utf-8')
            with PasswordAttemptHistory(path,key=b'k'*32) as history:
                history.mark(TARGET,'right-pass')
                history.mark(TARGET,'wrong-pass')
                history.catalog.import_results(directory,Event())
                history.repair_success_records((TARGET,))
                self.assertFalse(history.contains(TARGET,'right-pass'))
                self.assertTrue(history.contains(TARGET,'wrong-pass'))
                self.assertEqual(history.count,1)
                self.assertEqual(history.catalog.query(found=True)['rows'][0]['progress'],'1/?')

    def test_confirmed_counts_leave_unknown_out_and_preserve_dictionary_names(self):
        with TemporaryDirectory() as folder, PasswordAttemptHistory(Path(folder)/'history',key=b'k'*32) as history:
            history.catalog.configure((CandidateRound('one.txt',('first-pass','unknown-pass')),CandidateRound('two.txt',('last-pass',))))
            history.filter_pending(TARGET,('first-pass','unknown-pass','last-pass'))
            history.mark(TARGET,'first-pass')
            history.mark(TARGET,'last-pass')
            row=history.catalog.query()['rows'][0]
            self.assertEqual(row['dictionaries'],'one.txt\ntwo.txt')
            self.assertEqual(row['progress'],'2/3')
            history.catalog.interrupted(True)
            self.assertEqual(history.catalog.query()['rows'][0]['state'],'cancelled')

    def test_clear_is_atomic_and_new_success_can_be_recorded_again(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'history'
            result=Path(folder)/'fixture-password.txt'
            result.write_text('header\nfixture\t02:11:22:33:44:55\tright-pass\n',encoding='utf-8')
            with PasswordAttemptHistory(path,key=b'k'*32) as history:
                history.catalog.import_results(folder,Event())
                history.mark(TARGET,'wrong-pass')
            with patch('modules.capture_services.openwrt.password_results_store.suppress_exported_results',side_effect=RuntimeError('fixture rollback')):
                with self.assertRaises(RuntimeError):clear_history(path)
            with PasswordAttemptHistory(path,key=b'k'*32) as history:
                self.assertTrue(history.contains(TARGET,'wrong-pass'))
                self.assertEqual(history.catalog.query(found=True)['total'],1)
            clear_history(path)
            with PasswordAttemptHistory(path,key=b'k'*32) as history:
                history.catalog.import_results(folder,Event())
                self.assertEqual(history.catalog.query(found=True)['total'],0)
                self.assertEqual(history.catalog.query()['total'],0)
                history.mark_success(TARGET,'right-pass',result)
                self.assertEqual(history.catalog.query(found=True)['total'],1)
                self.assertEqual(history.catalog.query()['total'],0)


    def test_password_file_escaping_round_trip(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'result.txt'
            path.write_text('header\nname\\tvalue\t02:11:22:33:44:55\tp\\\\ass\\tword\n',encoding='utf-8')
            self.assertEqual(read_password_file(path),('name\tvalue',TARGET.bssid,'p\\ass\tword'))


class FilterTests(unittest.TestCase):
    def test_arrow_filters_and_literal_hidden_is_preserved(self):
        owner=QWidget()
        owner.scale_manager=UiScaleManager(100)
        table=AccessPointTable(owner)
        QVBoxLayout(owner).addWidget(table)
        points=(replace(TARGET,ssid='hidden',selected=True),replace(TARGET,bssid='02:11:22:33:44:66',ssid='__hidden__',ssid_hidden=True,selected=True),replace(TARGET,bssid='02:11:22:33:44:77',ssid='Other'))
        table.set_access_points(points)
        owner.resize(1000,460)
        owner.show()
        APP.processEvents()
        overlay=table.signal_filter.overlay
        self.assertTrue(overlay.panel.isHidden())
        overlay.toggle.click()
        self.assertFalse(overlay.panel.isHidden())
        overlay.hide_hidden.setChecked(True)
        self.assertFalse(table.isRowHidden(0))
        self.assertTrue(table.isRowHidden(1))
        overlay.only_checked.setChecked(True)
        self.assertTrue(table.isRowHidden(2))
        overlay.search.setPlainText('HIDDEN')
        self.assertFalse(table.isRowHidden(0))
        self.assertEqual(table.selected_bssids(),(points[0].bssid,points[1].bssid))
        overlay.search.clear()
        overlay.only_checked.setChecked(False)
        overlay.hide_hidden.setChecked(False)
        table.set_access_points(tuple(replace(TARGET,bssid='02:11:22:33:'+format(i//256,'02X')+':'+format(i%256,'02X'),ssid='Signal '+str(i)) for i in range(100)))
        APP.processEvents()
        original=overlay.pos()
        table.verticalScrollBar().setValue(table.verticalScrollBar().maximum())
        table.setColumnWidth(0,2000)
        APP.processEvents()
        fixed=overlay.pos()
        table.horizontalScrollBar().setValue(table.horizontalScrollBar().maximum())
        APP.processEvents()
        self.assertEqual(overlay.pos(),fixed)
        table.verticalScrollBar().setValue(0)
        APP.processEvents()
        self.assertEqual(overlay.pos(),fixed)
        self.assertIs(overlay.parent(),table)
        output=Path('build/previews/password-records');output.mkdir(parents=True,exist_ok=True)
        from modules.main_pages.packet_capture.capture_page_styles import CapturePageStyleManager
        from modules.main_pages.base_page_styles import light_base_page_stylesheet,dark_base_page_stylesheet
        owner.setObjectName('packetCapturePage')
        for light in (True,False):
            owner.light_theme=light
            base=light_base_page_stylesheet('',owner.scale_manager) if light else dark_base_page_stylesheet(owner.scale_manager)
            owner.setStyleSheet(base+CapturePageStyleManager(owner)._capture_stylesheet(light))
            table.set_light_theme(light)
            APP.processEvents()
            owner.grab().save(str(output/('fixed-filter-light.png' if light else 'fixed-filter-dark.png')))
        owner.close()
        owner.deleteLater()
        APP.processEvents()

    def test_scanner_flags_empty_ssid_not_a_name(self):
        from modules.capture_services.openwrt.wifi_scanner import OpenWrtWifiScanner
        with TemporaryDirectory() as folder:
            path=Path(folder)/'scan.csv'
            rows=[]
            for index,name in enumerate(('', 'hidden', '__hidden__')):
                row=['']*14
                row[0]='02:11:22:33:44:'+format(index+1,'02X')
                row[3]='1';row[5]='WPA2';row[8]='-50';row[12]=str(len(name));row[13]=name
                rows.append(row)
            with path.open('w',encoding='utf-8',newline='') as handle:
                csv.writer(handle).writerows(rows)
            points=OpenWrtWifiScanner.parse_csv(path,'2.4G')
            self.assertEqual([p.ssid_hidden for p in points],[True,False,False])


class HistoryService(QObject):
    recordsCleared=Signal()
    clear_async=Mock(return_value=True)


class DialogService(QObject):
    openwrtSnapshotChanged=Signal(object)
    def __init__(self):
        super().__init__()
        self.task_controller=NS(busy=False)
        self.openwrt_snapshot=OpenWrtSessionSnapshot(access_points=(TARGET,))
        self.password_history=HistoryService(self)


class ResultsDialogTests(unittest.TestCase):
    def test_async_open_navigation_copy_and_close_during_read(self):
        from modules.main_pages.packet_capture.openwrt.password_results_dialog import PasswordResultsDialog
        from modules.main_pages.packet_capture.capture_page_styles import CapturePageStyleManager
        with TemporaryDirectory() as folder:
            path=Path(folder)/'history'
            with PasswordAttemptHistory(path,key=b'k'*32) as history:
                result=Path(folder)/'fixture-password.txt'
                result.write_text('header\nfixture\t02:11:22:33:44:55\tright-pass\n',encoding='utf-8')
                history.catalog.configure((CandidateRound('fixture.txt',('right-pass',)),))
                history.filter_pending(TARGET,('right-pass',))
                history.mark_success(TARGET,'right-pass',result)
                other=replace(TARGET,bssid='02:11:22:33:44:66',ssid='Unresolved fixture')
                history.filter_pending(other,('right-pass',))
                history.mark(other,'right-pass')
            service=DialogService()
            output=Path('build/previews/password-records')
            output.mkdir(parents=True,exist_ok=True)
            def make_history():return PasswordAttemptHistory(path,key=b'k'*32)
            with patch('modules.capture_services.openwrt.password_history.PasswordAttemptHistory',side_effect=make_history):
                for light in (True,False):
                    owner=QWidget();owner.scale_manager=UiScaleManager(100);owner.light_theme=light
                    from modules.main_pages.base_page_styles import light_base_page_stylesheet,dark_base_page_stylesheet
                    owner.setStyleSheet(light_base_page_stylesheet('',owner.scale_manager) if light else dark_base_page_stylesheet(owner.scale_manager))
                    window=PasswordResultsDialog(service,folder,owner)
                    window.show()
                    for unused in range(200):
                        APP.processEvents()
                        if not window._loading:break
                        sleep(.01)
                    self.assertFalse(window._loading)
                    self.assertEqual(window.table.rowCount(),1)
                    self.assertEqual(window.table.item(0,3).text(),'1/1')
                    self.assertTrue(window.clear_button.property('dangerAction'))
                    window.grab().save(str(output/('light-attempts.png' if light else 'dark-attempts.png')))
                    window.tabs.setCurrentIndex(1)
                    for unused in range(200):
                        APP.processEvents()
                        if not window._loading:break
                        sleep(.01)
                    self.assertEqual(window.table.item(0,2).text(),'right-pass')
                    from PySide6.QtGui import QCursor, QPalette
                    cell = window.table.visualItemRect(window.table.item(0,2))
                    viewport = window.table.viewport()
                    QCursor.setPos(viewport.mapToGlobal(cell.center()))
                    QTest.mouseClick(viewport, Qt.LeftButton, pos=cell.center())
                    APP.processEvents()
                    self.assertEqual(APP.clipboard().text(),'right-pass')
                    self.assertEqual(len(window.table.selectedItems()),1)
                    self.assertTrue(window.copy_feedback.isVisible())
                    color = window.copy_feedback.palette().color(QPalette.WindowText)
                    self.assertGreater(color.green(),color.red())
                    self.assertTrue(window.rect().contains(window.copy_feedback.geometry()))
                    window.copy_feedback._animation.setCurrentTime(250)
                    window.grab().save(str(output/('light-found.png' if light else 'dark-found.png')))
                    window._load(reset=True)
                    window.close()
                    APP.processEvents()
                    owner.close();owner.deleteLater()
                    for unused in range(20):APP.processEvents();sleep(.005)
            APP.clipboard().clear()

    def test_scroll_loads_more_without_paging_controls(self):
        from modules.main_pages.packet_capture.openwrt.password_results_dialog import PasswordResultsDialog
        with TemporaryDirectory() as folder:
            path=Path(folder)/'history'
            with PasswordAttemptHistory(path,key=b'k'*32) as history:
                for index in range(230):
                    history.connection.execute('INSERT INTO password_targets(target,ssid,bssid,state,updated) VALUES(?,?,?,?,?)',
                        (index.to_bytes(8,'big'),'Signal '+str(index),'02:11:22:33:'+format(index//256,'02X')+':'+format(index%256,'02X'),'no_match',index))
                history.connection.commit()
            service=DialogService()
            owner=QWidget();owner.scale_manager=UiScaleManager(100)
            def make_history():return PasswordAttemptHistory(path,key=b'k'*32)
            with patch('modules.capture_services.openwrt.password_history.PasswordAttemptHistory',side_effect=make_history):
                window=PasswordResultsDialog(service,folder,owner)
                window.show()
                for expected in (100,200,230):
                    for unused in range(300):
                        APP.processEvents()
                        if not window._loading and window.table.rowCount()==expected:break
                        sleep(.005)
                    self.assertEqual(window.table.rowCount(),expected)
                    self.assertFalse(hasattr(window,'previous'))
                    self.assertFalse(hasattr(window,'next'))
                    if expected<230:
                        for unused in range(100):
                            APP.processEvents()
                            if window.table.verticalScrollBar().maximum()>0:break
                            sleep(.005)
                        self.assertGreater(window.table.verticalScrollBar().maximum(),0)
                        window.table.verticalScrollBar().setValue(window.table.verticalScrollBar().maximum())
                self.assertFalse(window._has_more)
                window.close();APP.processEvents()
                owner.close();owner.deleteLater();APP.processEvents()


if __name__=='__main__':unittest.main()
