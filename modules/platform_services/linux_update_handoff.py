"""Nonblocking ready/exit handshake with our own independent Linux updater process."""
import os
import subprocess
from PySide6.QtCore import QObject,QSocketNotifier,QTimer,Signal
from .linux_update_child import environment
from modules.app_update_services.linux_update_jobs import arm_job,disarm_job


class LinuxUpdateHandoff(QObject):
    activityChanged=Signal()
    def __init__(self,root,stage,appearance,parent,ready,failed):
        super().__init__(parent);self.root=root;self.stage=stage;self.appearance=appearance
        self.ready=ready;self.failed=failed;self.active=False;self.process=None
        self.pipe=None;self.exit_watch=None;self.pidfd=None;self.data=bytearray();self.error=''
        self.deadline=QTimer(self);self.deadline.setSingleShot(True);self.deadline.setInterval(15000)
        self.deadline.timeout.connect(self._timeout)
    def start(self):
        runtime,job=arm_job(self.root,self.stage,appearance=self.appearance)
        self.active=True
        try:
            self.process=subprocess.Popen([str(runtime/'LuXiaoGuan'),'--linux-update-ui',str(job)],cwd=runtime,
                env=environment(),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                start_new_session=True,close_fds=True)
            os.set_blocking(self.process.stdout.fileno(),False)
            self.pipe=QSocketNotifier(self.process.stdout.fileno(),QSocketNotifier.Read,self)
            self.pipe.activated.connect(self._read)
            self.pidfd=os.pidfd_open(self.process.pid,0)
            self.exit_watch=QSocketNotifier(self.pidfd,QSocketNotifier.Read,self)
            self.exit_watch.activated.connect(self._exited)
            self.deadline.start();self.activityChanged.emit()
        except Exception:
            if self.process is not None:
                self.process.kill()
                import threading
                threading.Thread(target=self.process.wait,daemon=True).start()
            self._close();self.active=False
            disarm_job(self.root,self.stage);raise
    def _read(self,*_):
        if not self.active or self.pipe is None:return
        try:block=os.read(self.process.stdout.fileno(),129-len(self.data))
        except BlockingIOError:return
        if not block:
            self.pipe.setEnabled(False);self.error='Linux update helper closed its readiness channel';self.process.kill();return
        self.data.extend(block)
        if self.data==b'ROUTERMANAGER_UPDATE_READY\n':
            self.active=False;self._close();self.activityChanged.emit();self.ready();return
        if len(self.data)>128 or b'\n' in self.data:
            self.error='Invalid Linux update readiness response';self.process.kill()
    def _timeout(self):
        if self.active:self.error='Linux update helper did not become ready';self.process.kill()
    def _exited(self,*_):
        if not self.active:return
        self.process.wait()  # pidfd readiness proves exit; this cannot wait for a live child.
        self.active=False;self._close()
        try:disarm_job(self.root,self.stage)
        except (OSError,ValueError):pass  # A retained pointer is recoverable via the stable launcher.
        self.activityChanged.emit();self.failed(self.error or 'Linux update helper exited before becoming ready')
    def _close(self):
        self.deadline.stop()
        for watcher in (self.pipe,self.exit_watch):
            if watcher is not None:watcher.setEnabled(False);watcher.deleteLater()
        self.pipe=self.exit_watch=None
        if self.pidfd is not None:os.close(self.pidfd);self.pidfd=None
        if self.process is not None and self.process.stdout is not None:self.process.stdout.close()
    def abort_ready(self,message):
        self.error=message
        if self.process.poll() is not None:
            disarm_job(self.root,self.stage);self.failed(message);return
        self.active=True;self.pidfd=os.pidfd_open(self.process.pid,0)
        self.exit_watch=QSocketNotifier(self.pidfd,QSocketNotifier.Read,self)
        self.exit_watch.activated.connect(self._exited)
        self.process.kill();self.activityChanged.emit()

    def stop(self):
        if self.active:self.error='Linux update handoff cancelled';self.process.kill()
