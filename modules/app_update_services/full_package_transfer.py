"""Cancelable complete-ZIP download with signed identity and disk-safe staging."""
from pathlib import Path
import threading,time
from PySide6.QtCore import QThread,Signal
from .manifest import verify_manifest
from .trust import PUBLIC_KEY
from .locations import CACHE_DIRECTORY,cache_lock,validate_stage,linked
from .transaction import atomic_json
from .planner import make_plan
from .full_package import prepare_package

class FullPackageTransfer(QThread):
    result=Signal(object)
    failed=Signal(str)
    progress=Signal(int,int,str)
    preview=Signal(object)
    download=True
    def __init__(self,root,manifest,parent=None,channels=None):
        super().__init__(parent);self.root=Path(root);self.manifest=manifest;self.channels=channels
        self.cancel_event=threading.Event()
    def cancel(self):self.cancel_event.set()
    def run(self):
        try:
            from modules.platform_services.app_update_support import require_native_updater
            require_native_updater()
            release=verify_manifest(self.manifest,PUBLIC_KEY)
            stage=validate_stage(self.root/CACHE_DIRECTORY/release.sha256,self.root)
            stage.parent.mkdir(parents=True,exist_ok=True)
            from .cache import prune_completed
            prune_completed(self.root,current_version=release.version,keep_stage=stage,cancel=self.cancel_event.is_set)
            with cache_lock(stage.parent) as acquired:
                if not acquired:raise ValueError('Another update is downloading or installing')
                stage.mkdir(exist_ok=True)
                for name in ('manifest.json','catalog.json','package.zip'):
                    if linked(stage/name):raise ValueError('Linked update staging file')
                (stage/'manifest.json').write_bytes(self.manifest)
                import shutil
                if shutil.disk_usage(stage).free<release.size+128*1024**2:
                    raise ValueError('Not enough space to download the full package')
                from modules.platform_services.app_update_install import prepare_helper
                prepare_helper(self.root,stage)
                from modules.capture_services.openwrt.common_bundle_download import _receive
                def progress(value):self.progress.emit(value['completed'],release.size,'download')
                _receive(release.url,stage/'package.zip',release.size,self.cancel_event,progress,time.monotonic()+900,channels=self.channels,expected_sha=release.sha256)
                self.progress.emit(release.size,release.size,'extract')
                catalog=prepare_package(stage/'package.zip',release,stage,self.root,cancel=self.cancel_event.is_set)
                plan=make_plan(self.root,catalog,self.cancel_event.is_set)
                if plan['conflicts']:raise ValueError('Local edited files conflict: '+', '.join(plan['conflicts']))
                atomic_json(stage/'catalog.json',catalog)
                plan.update(stage=str(stage),full_package=True,download_bytes=release.size)
                if not self.cancel_event.is_set():self.result.emit(plan)
        except InterruptedError:pass
        except Exception as exc:
            if not self.cancel_event.is_set():self.failed.emit(str(exc))
