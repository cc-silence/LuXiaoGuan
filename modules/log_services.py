"""\u65e5\u5fd7\u5bfc\u51fa\uff1a\u6709\u754c\u5feb\u7167\u3001\u8131\u654f\u9644\u4ef6\u548c\u539f\u5b50\u53d1\u5e03\u3002"""
import hashlib
import getpass
import json
import os
import re
import stat
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal
from modules.display_privacy import Masker
from modules.release_identity import PACKAGE_DIRECTORY

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_INFO_BYTES = 8 * 1024 * 1024
MAX_ATTACHMENTS = 256
MAX_REFERENCES = 4096
MAX_TOTAL_BYTES = 20 * 1024 * 1024
_SECRET = re.compile(r'\b(?:token|authorization|api[_-]?key|secret)\b[\s\"\']*[:=]|--(?:token|authorization|api[_-]?key|secret)\b|\b(?:e?ssid|ssid_hex)\b[\s\"\']*[:=]|\bssid\s+\S+|\bkey\b[\s\"\']*[:=]|password|passwd|passphrase|wpa[ _-]?psk|(?:^|[\s"\'])psk[\s"\':=]|(?:^|[\s"\'])key[\s"\':=]|(?:^|\s)pin\s*[:=]|candidate|argv|command(?:_line)?\s*[:=]|--(?:password|key|pin)\b|\u5bc6\u7801\s*[:=\uff1a]|\u5019\u9009\s*[:=\uff1a]', re.I)
_URL_SECRET = re.compile(r'([a-z][a-z0-9+.-]{0,31}://)[^\s/]+:[^\s/]+@', re.I)
_TOKEN = re.compile(r'((?:token|authorization|api[_-]?key|secret)\s*[:=]\s*)\S+', re.I)


def sanitized_text(text, masker):
    """\u5bfc\u51fa\u59cb\u7ec8\u8131\u654f\uff1b\u654f\u611f\u5b57\u6bb5\u6574\u884c\u79fb\u9664\u3002"""
    lines = []
    private_block = False
    for line in str(text).splitlines():
        if re.search(r'BEGIN .*PRIVATE KEY', line, re.I):
            private_block = True
            lines.append('[private key omitted]')
            continue
        if private_block:
            if re.search(r'END .*PRIVATE KEY', line, re.I):
                private_block = False
            continue
        if _SECRET.search(line):
            lines.append('[sensitive field omitted]')
        else:
            line = _URL_SECRET.sub(r'\1[credentials omitted]@', line)
            line = _TOKEN.sub(r'\1[omitted]', line)
            from modules.diagnostic_redaction import mask_diagnostic_literals
            lines.append(mask_diagnostic_literals(line, masker))
    return '\n'.join(lines)


@dataclass(frozen=True)
class DiagnosticReference:
    path: Path
    root: Path
    kind: str = 'text'


class DiagnosticReferences:
    """\u53ea\u4fdd\u7559\u663e\u5f0f\u5173\u8054\u7684\u8bca\u65ad\u8def\u5f84\u3002"""
    def __init__(self):
        self.clear()

    def clear(self):
        self.references = {}
        self.omitted = 0

    def add(self, path, root=None, kind='text'):
        path = Path(path)
        root = Path(root) if root else path.parent
        key = (str(path), str(root), kind)
        if key in self.references:
            ref, count = self.references[key]
            self.references[key] = (ref, count + 1)
        elif len(self.references) < MAX_REFERENCES:
            self.references[key] = (DiagnosticReference(path, root, kind), 1)
        else:
            self.omitted += 1

    def snapshot(self):
        return tuple(self.references.values()), self.omitted


def _check_path(ref, directory=False):
    path, root = Path(os.path.abspath(ref.path)), Path(os.path.abspath(ref.root))
    if not path.is_relative_to(root):
        raise ValueError('path outside registered root')
    if str(path).startswith(('\\\\', '\\\\?\\', '\\\\.\\')):
        raise ValueError('network/device paths are not supported')
    # Inspect every component before resolve, since resolve hides junctions.
    for component in (path, *path.parents):
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('link or reparse point')
    info = path.stat()
    if directory:
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('not a session directory')
        return path, info
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('not a regular file')
    if info.st_size > MAX_FILE_BYTES:
        raise ValueError('attachment exceeds 2 MiB')
    return path, info


def _read_reference(ref, check, masker):
    if ref.kind not in ('text', 'ssh'):
        raise ValueError('unsupported diagnostic format')
    if ref.kind == 'text' and ref.path.suffix.lower() not in ('.txt', '.log'):
        raise ValueError('unsupported text format')
    if ref.kind == 'ssh' and ref.path.name != 'ssh-failure.json':
        raise ValueError('unsupported structured diagnostic')
    path, before = _check_path(ref)
    check()
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError('file identity changed')
        if getattr(opened, 'st_file_attributes', 0) & 0x400:
            raise ValueError('opened reparse point')
        chunks, size = [], 0
        while True:
            check()
            chunk = stream.read(min(65536, MAX_FILE_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                raise ValueError('attachment grew beyond 2 MiB')
    _check_path(ref)
    raw = b''.join(chunks)
    text = raw.decode('utf-8-sig')
    if '\x00' in text:
        raise ValueError('binary diagnostic')
    if ref.kind == 'ssh':
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError('invalid SSH diagnostic')
        allowed = {'stage', 'kind', 'action', 'elapsed_seconds', 'exception_type'}
        failures = data.get('failures', [])
        if not isinstance(failures, list):
            raise ValueError('invalid SSH failures')
        records = []
        for record in failures[:12]:
            if not isinstance(record, dict):
                raise ValueError('invalid SSH failure record')
            records.append({key: value for key, value in record.items() if key in allowed
                            and (value is None or isinstance(value, (str, int, float, bool)))})
        records = [{k: sanitized_text(v, masker) if isinstance(v, str) else v for k, v in record.items()} for record in records]
        data = {'connected': bool(data.get('connected', False)), 'failures': records}
        text = json.dumps(data, ensure_ascii=False, indent=2)
    return raw, text


def _expand_references(references, check, skipped, masker):
    """\u53ea\u5c55\u5f00\u5df2\u5f15\u7528\u4f1a\u8bdd\u7684\u767d\u540d\u5355\u8bca\u65ad\uff0c\u4e0d\u9012\u5f52\u626b\u63cf\u3002"""
    from modules.config_manager import CAPTURE_WORK_DIR
    allowed_roots = {Path(os.path.abspath(CAPTURE_WORK_DIR)),
                     Path(os.path.abspath(Path(tempfile.gettempdir()) / 'winbox-capture-work'))}
    for ref, count in references:
        check()
        if ref.kind != 'session':
            yield ref, count
            continue
        try:
            if Path(os.path.abspath(ref.root)) not in allowed_roots:
                raise ValueError('unregistered session root')
            directory, _ = _check_path(ref, directory=True)
            with os.scandir(directory) as children:
                for number, child in enumerate(children):
                    check()
                    if number >= MAX_REFERENCES:
                        raise ValueError('session directory entry limit exceeded')
                    name = child.name
                    if name == 'ssh-failure.json':
                        kind = 'ssh'
                    elif re.fullmatch(r'[A-Za-z0-9_-]{1,32}-wireless-diag\.txt', name) or name == 'takeover-restore-audit.txt':
                        kind = 'text'
                    else:
                        continue
                    yield DiagnosticReference(Path(child.path), directory, kind), count
        except (OSError, ValueError) as exc:
            if isinstance(exc, (InterruptedError, TimeoutError)):
                raise
            skipped.append({'file': masker.text(ref.path.name), 'reason': sanitized_text(str(exc), masker)})


def _router_context_bytes(text, masker):
    """导出边界再核对设备白名单并脱敏，调用方不能通过附加信息泄露配置。"""
    if not text:
        return b""
    if len(text.encode("utf-8"))>512*1024:
        raise ValueError("router diagnostic context exceeds budget")
    data=json.loads(text)
    if not isinstance(data,dict) or data.get("schema_version")!=1:
        raise ValueError("unsupported router diagnostic context")
    def fields(source,strings=(),numbers=()):
        if not isinstance(source,dict):
            raise ValueError("invalid router diagnostic object")
        result={key:sanitized_text(source[key],masker) for key in strings
                if isinstance(source.get(key),str)}
        for key in ('captured_at','device_observed_at'):
            if key in strings and isinstance(source.get(key),str):
                try:
                    datetime.fromisoformat(source[key])
                    result[key] = source[key]
                except ValueError:
                    pass
        if 'feature' in strings and re.fullmatch(r'[a-z_]{1,48}', str(source.get('feature',''))):
            result['feature'] = source['feature']
        result.update({key:source[key] for key in numbers if key in source
                       and (source[key] is None or isinstance(source[key],(int,float,bool)))})
        return result
    result=fields(data,("captured_at","connection_state","note","pmkid_tool","app_version","device_information_source","device_observed_at"),("schema_version",))
    device=data.get("device")
    result["device"]=None
    if device is not None:
        result["device"]=fields(device,("model","board_name","distribution","version","revision","target","architecture",
            "package_architecture","package_manager","kernel_version"),
            ("memory_mb","free_memory_mb","free_storage_mb","sftp_available"))
        radios=device.get("radios",[])
        if not isinstance(radios,list) or len(radios)>256:
            raise ValueError("invalid radio diagnostic list")
        rows=[]
        for radio in radios:
            row=fields(radio,("name","driver"),("channel","free","monitor_supported","interface_count"))
            row["bands"]=[sanitized_text(value,masker) for value in radio.get("bands",[])[:8] if isinstance(value,str)]
            rows.append(row)
        result["device"]["radios"]=rows
        dependencies=data.get("dependencies",[])
        if not isinstance(dependencies,list) or len(dependencies)>64:
            raise ValueError("invalid dependency diagnostic list")
        result["dependencies"]=[]
        for item in dependencies:
            row=fields(item,("feature","state","detail"))
            packages=item.get("missing_packages",[])
            if not isinstance(packages,list) or len(packages)>64:
                raise ValueError("invalid missing package list")
            row["missing_packages"]=[sanitized_text(value,masker) for value in packages if isinstance(value,str)]
            result["dependencies"].append(row)
    if "radio_selection" in data:
        from modules.capture_services.openwrt.radio_diagnostics import sanitized_radio_evidence
        result["radio_selection"] = sanitized_radio_evidence(data["radio_selection"], masker)
    if "operations" in data:
        from modules.capture_services.openwrt.operation_diagnostics import sanitized_operation_evidence
        result["operations"] = sanitized_operation_evidence(data["operations"], masker)
    if "runtime" in data:
        from modules.capture_services.openwrt.runtime_diagnostics import sanitized_runtime_evidence
        result["runtime"] = sanitized_runtime_evidence(data["runtime"])
    if "signals" in data:
        from modules.capture_services.openwrt.signal_diagnostics import sanitized_signal_evidence
        result["signals"] = sanitized_signal_evidence(data["signals"])
    return json.dumps(result,ensure_ascii=False,indent=2).encode("utf-8")


def export_logs(destination, category, entries, masker=None, references=(), omitted=0,
                cancel=None, app_version='', timeout=60, router_context='', history_device=None):
    """\u540e\u53f0\u5bfc\u51fa\u51bb\u7ed3\u5feb\u7167\uff0c\u5931\u8d25\u4e0d\u8986\u76d6\u539f\u76ee\u6807\u3002"""
    from modules.log_history import HistoryMasker
    export_masker = HistoryMasker()
    export_masker.identities = masker or Masker()
    masker = export_masker
    deadline = time.monotonic() + timeout
    def check():
        if cancel is not None and cancel.is_set():
            raise InterruptedError('export cancelled')
        if time.monotonic() >= deadline:
            raise TimeoutError('export deadline exceeded')
    destination = Path(destination)
    check()
    info = '\n'.join(f'[{level}] {sanitized_text(text, masker)}' for text, level in entries)
    payload = info.encode('utf-8')
    if len(payload) > MAX_INFO_BYTES:
        payload = payload[-MAX_INFO_BYTES:].decode('utf-8', errors='ignore').encode('utf-8')
    context_bytes=_router_context_bytes(router_context,masker)
    used = len(payload) + len(context_bytes) + 1024 * 1024
    index, skipped, attachments, seen = [], [], [], {}
    radio_evidence = json.loads(context_bytes).get('radio_selection') if context_bytes else None
    if radio_evidence and radio_evidence.get('attempts'):
        evidence_bytes = json.dumps(radio_evidence, ensure_ascii=False, indent=2).encode('utf-8')
        index.append({'file': 'radio-selection.json', 'aliases': ['radio_selection'], 'references': 1})
        attachments.append(('radio-selection.json', evidence_bytes))
        used += len(evidence_bytes)
    context = json.loads(context_bytes) if context_bytes else {}
    for key, filename in (("operations", "operation-diagnostics.json"), ("signals", "signal-state.json")):
        evidence = context.get(key)
        if evidence:
            evidence_bytes = json.dumps(evidence, ensure_ascii=False, indent=2).encode("utf-8")
            index.append({"file": filename, "aliases": [key], "references": 1})
            attachments.append((filename, evidence_bytes))
            used += len(evidence_bytes)
    for ref, count in _expand_references(references, check, skipped, masker):
        check()
        alias = sanitized_text(ref.path.name, masker)
        try:
            raw, text = _read_reference(ref, check, masker)
            digest = hashlib.sha256(raw).hexdigest()
            if digest in seen:
                entry = seen[digest]
                entry['aliases'].append(alias)
                entry['references'] += count
                continue
            data = sanitized_text(text, masker).encode('utf-8')
            if len(attachments) >= MAX_ATTACHMENTS or used + len(data) > MAX_TOTAL_BYTES:
                raise ValueError('attachment count or total budget exceeded')
            name = f'diagnostics/diagnostic-{len(attachments) + 1:03d}{ref.path.suffix.lower()}'
            record = {'file': name, 'aliases': [alias], 'references': count}
            seen[digest] = record
            index.append(record)
            attachments.append((name, data))
            used += len(data)
        except (OSError, ValueError, UnicodeError) as exc:
            if isinstance(exc, (InterruptedError, TimeoutError)):
                raise
            skipped.append({'file': alias, 'reason': sanitized_text(str(exc), masker)})
    from modules.log_history import current_history
    from modules.log_history_export import history_attachments
    if category == 'auto-capture' and history_device is None:
        history_device = getattr(current_history(), 'auto_capture_device', '')
    for name, data in history_attachments(current_history(), category, history_device, masker):
        check()
        if used + len(data) > MAX_TOTAL_BYTES or len(attachments) >= MAX_ATTACHMENTS:
            skipped.append({'file': name, 'reason': 'history export budget exceeded'})
            continue
        attachments.append((name, data))
        index.append({'file': name, 'aliases': ['run_history'], 'references': 1})
        used += len(data)
    # Reserve bounds apply to metadata as well; never emit arbitrary source paths.
    metadata = json.dumps({'attachments': index, 'skipped': skipped,
                           'references_omitted': omitted,
                           'router_information': 'router-info.json' if context_bytes else None}, ensure_ascii=False, indent=2).encode('utf-8')
    if len(metadata) > 1024 * 1024:
        raise ValueError('diagnostic index exceeds metadata budget')
    readme = (f'{PACKAGE_DIRECTORY} {app_version}\nCategory: {log_category_label(category)}\n'
              'Export is always redacted. Only explicitly referenced diagnostics are included.\n').encode('utf-8')
    if len(payload) + len(context_bytes) + sum(len(data) for _, data in attachments) + len(metadata) + len(readme) > MAX_TOTAL_BYTES:
        raise ValueError('total export budget exceeded')
    fd, temporary = tempfile.mkstemp(prefix='.luxiaoguan-log-', suffix='.part', dir=destination.parent)
    os.close(fd)
    try:
        check()
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('information.txt', payload)
            if context_bytes:
                archive.writestr('router-info.json',context_bytes)
            for name, data in attachments:
                check()
                archive.writestr(name, data)
            archive.writestr('diagnostic-index.json', metadata)
            archive.writestr('README.txt', readme)
        check()
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {'zip_path': str(destination), 'info_lines': len(entries),
            'diagnostics_included': len(attachments), 'skipped': skipped}


def log_category_label(category):
    return '\u81ea\u52a8\u6293\u5305' if category == 'auto-capture' else str(category)


def build_export_filename(category):
    category = log_category_label(category)
    category = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(category))[:48]
    try:
        username = getpass.getuser()
    except (OSError, KeyError, ImportError):
        username = 'user'
    username = re.sub(r'[<>:"/\\\\|?*\x00-\x1f]', '_', str(username)).strip(' .')[:48] or 'user'
    return f'{username}_{PACKAGE_DIRECTORY}_{category}_{datetime.now():%Y%m%d_%H%M%S}.zip'


class ExportWorker(QThread):
    """\u540e\u53f0\u53ea\u5904\u7406\u51bb\u7ed3\u5feb\u7167\uff0c\u4e0d\u6301\u6709\u9875\u9762\u6216\u521b\u5efa\u5f39\u7a97\u3002"""
    completed = Signal(object)
    failed = Signal(str)
    def __init__(self, arguments, parent, export_operation=None):
        super().__init__(parent)
        self.arguments = arguments
        self.export_operation = export_operation
        self.cancel_event = threading.Event()

    def run(self):
        try:
            self.completed.emit((self.export_operation or export_logs)(**self.arguments, cancel=self.cancel_event))
        except Exception as exc:
            self.failed.emit(f'{type(exc).__name__}: {exc}')
        finally:
            self.arguments = None
            self.export_operation = None


class LogExportService(QObject):
    """\u5e38\u9a7b\u6240\u6709\u8005\u7ba1\u7406\u5355\u4e2a\u5bfc\u51fa\u4efb\u52a1\u548c\u9000\u51fa\u53d6\u6d88\u3002"""
    activityChanged = Signal()
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = None

    @property
    def active(self):
        return self.worker is not None

    def start(self, export_operation=None, **arguments):
        if self.active:
            return False
        self.worker = ExportWorker(arguments, self, export_operation)
        self.worker.completed.connect(self.completed)
        self.worker.failed.connect(self.failed)
        self.worker.finished.connect(self._finished)
        self.activityChanged.emit()
        self.worker.start()
        return True

    def cancel(self):
        if self.worker is not None:
            self.worker.cancel_event.set()

    def _finished(self):
        worker, self.worker = self.worker, None
        worker.deleteLater()
        self.activityChanged.emit()


def defer_close_for_export(window, event):
    """\u72ec\u7acb\u7a97\u53e3\u9000\u51fa\u5148\u53d6\u6d88\u5bfc\u51fa\uff0c\u7b49\u5de5\u4f5c\u7ebf\u7a0b\u56de\u6536\u3002"""
    service = getattr(window, 'log_export_service', None)
    if service is None or not service.active:
        return False
    event.ignore()
    if not getattr(window, '_export_close_pending', False):
        window._export_close_pending = True
        service.activityChanged.connect(window.close)
        service.cancel()
    return True
