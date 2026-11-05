"""Signal-driven Linux updater window from the isolated old runtime."""
from pathlib import Path
from PySide6.QtCore import QThread,Signal,Qt
from PySide6.QtWidgets import QApplication
from modules.common_widgets.task_progress_dialog import TaskProgressDialog
from modules.platform_services.linux_update_files import RootedFiles
from .linux_update_jobs import stage_location,read_job
from .linux_update_worker import run_job,worker_lease


class InstallDialog(TaskProgressDialog):
    def request_cancel(self):
        if not self.running:super().request_cancel()


class InstallThread(QThread):
    progress=Signal(object)
    result=Signal(object)
    def __init__(self,path,parent=None,recover=False):super().__init__(parent);self.path=path;self.outcome=None;self.recover=recover
    def run(self):
        try:self.outcome=run_job(self.path,recover=self.recover,notify=self.progress.emit)
        except Exception as exc:self.outcome=dict(status='failed',safe_to_restart=False,error_type=type(exc).__name__)
        self.result.emit(self.outcome)


def main(argv,*,recover=False):
    if len(argv)!=2:return 2
    root,stage=stage_location(argv[1]);app=QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    from modules.ui_scale_manager import UiScaleManager
    from PySide6.QtWidgets import QWidget
    from modules.app_update_services.linux_update_jobs import private_job
    with RootedFiles(stage.parent) as cache,worker_lease(cache,'linux-ui.lock'):
        with RootedFiles(root) as installed,RootedFiles(stage) as staged:
            job=private_job(staged,installed.identity,stage)
            look=staged.read_json('linux-appearance.json',4096) if staged.state('linux-appearance.json') is not None else {}
        owner=QWidget()
        owner.scale_manager=UiScaleManager(look.get('scale',100))
        owner.scale_manager.apply_application_font(app)
        dialog=InstallDialog('路小管更新','',owner,show_immediately=False,theme_name=look.get('theme','dark.qss'))
        dialog.cancel_button.setEnabled(False)
        thread=InstallThread(argv[1],owner,recover=recover)
        phases={'waiting':'正在等待软件安全退出',
                'checking':'正在核对更新文件',
                'preparing':'正在准备更新',
                'applying':'正在安装更新',
                'validating':'正在检查新版本能否启动',
                'rolling_back':'正在恢复原版本',
                'complete':'更新已安装',
                'rolled_back':'原版本已恢复'}
        def progress(value):
            text=phases.get(value['phase'],'正在处理更新，请稍候')
            if value['total']:
                dialog.update_progress(value)
                dialog.message.setText(text+' '+str(value['completed'])+'/'+str(value['total']))
            else:dialog.update_stage(text)
        thread.progress.connect(progress,Qt.QueuedConnection)
        def finished():
            result=thread.outcome or {};ok=result.get('status') in {'applied','recovered'} and not result.get('restart_failed')
            message='更新已完成' if ok else ('更新未完成，已恢复原版本。' if result.get('safe_to_restart') else '更新未完成，已保留恢复记录。请重新打开软件启动入口进行恢复。')
            if result.get('restart_failed'):message='已完成文件处理，但未能自动重开软件。请使用启动入口手动打开。'
            dialog.finish(ok,message)
            if ok:app.quit()
            else:dialog.cancel_button.setEnabled(True)
        thread.finished.connect(finished,Qt.QueuedConnection)
        dialog.finished.connect(lambda *_:app.quit() if not thread.isRunning() else None)
        dialog.show()
        if not recover:
            import os
            try:os.write(1,b'ROUTERMANAGER_UPDATE_READY\n')
            except OSError:pass
        thread.start();code=app.exec()
        # Closing the window cannot stop a transaction or destroy a live QThread.
        if thread.isRunning():raise RuntimeError('Updater UI exited before its worker')
        if not thread.wait(5000):raise RuntimeError('Updater thread did not finish releasing native resources')
        result=thread.outcome or {};thread.deleteLater();owner.deleteLater()
        return 0 if result.get('safe_to_restart') and not result.get('restart_failed') else 1
