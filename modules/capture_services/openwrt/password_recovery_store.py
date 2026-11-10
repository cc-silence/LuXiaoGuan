"""Bounded, atomic recovery metadata. Never store SSIDs, passwords or full config."""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

NAME = re.compile(r'[A-Za-z0-9_]{1,64}')
SID = re.compile(r'[0-9a-f]{32}')
FINGERPRINT = re.compile(r'SHA256:[A-Za-z0-9+/]{43}')
FLAGS = (None,'0','1','true','false','yes','no','on','off')


class RecoveryDirectoryError(ValueError):
    """The configured recovery directory cannot be safely inspected."""


class RecoveryRecordCorrupt(ValueError):
    """A specific bounded file failed content validation."""
    def __init__(self, path, digest, fingerprint):
        super().__init__('Invalid recovery record contents')
        self.path, self.digest, self.fingerprint = path, digest, fingerprint


def root_directory():
    from modules.config_manager import WORK_DATA_DIR
    return Path(WORK_DATA_DIR)/'password-recovery'


def validate(data):
    keys={'schema','session','fingerprint','disabled','devices','baseline','modified','radios'}
    if not isinstance(data,dict) or set(data)!=keys or type(data['schema']) is not int or data['schema']!=1:
        raise ValueError('Invalid recovery schema')
    if not isinstance(data['session'],str) or not SID.fullmatch(data['session']):
        raise ValueError('Invalid recovery session')
    if not isinstance(data['fingerprint'],str) or not FINGERPRINT.fullmatch(data['fingerprint']):
        raise ValueError('Invalid recovery device')
    disabled,devices,baseline=data['disabled'],data['devices'],data['baseline']
    if not isinstance(disabled,dict) or not isinstance(devices,dict) or set(disabled)!=set(devices) or len(disabled)>256:
        raise ValueError('Invalid recovery interfaces')
    if any(not isinstance(n,str) or not NAME.fullmatch(n) or v not in FLAGS for n,v in disabled.items()):
        raise ValueError('Invalid recovery flags')
    if any(not isinstance(v,str) or not NAME.fullmatch(v) for v in devices.values()):
        raise ValueError('Invalid recovery ownership')
    if not isinstance(baseline,dict) or len(baseline)>64:
        raise ValueError('Invalid recovery baseline')
    for name,row in baseline.items():
        if not isinstance(name,str) or not NAME.fullmatch(name) or not isinstance(row,dict) or set(row)!={'up','pending','disabled','sections'}:
            raise ValueError('Invalid recovery radio')
        if any(row[key] is not None and type(row[key]) is not bool for key in ('up','pending','disabled')) or not isinstance(row['sections'],dict) or len(row['sections'])>256:
            raise ValueError('Invalid recovery runtime flags')
        if any(not isinstance(n,str) or not NAME.fullmatch(n) or type(v) is not bool for n,v in row['sections'].items()):
            raise ValueError('Invalid recovery runtime interfaces')
    for key,allowed in (('modified',disabled),('radios',baseline)):
        values=data[key]
        if not isinstance(values,list) or len(values)>256 or any(not isinstance(n,str) for n in values) or len(set(values))!=len(values) or not set(values)<=set(allowed):
            raise ValueError('Invalid recovery changes')
    for radio in data['radios']:
        if baseline[radio]['pending'] is not False or any(type(baseline[radio][key]) is not bool for key in ('up','disabled')):
            raise ValueError('Affected radio recovery state is unavailable')
    if 'wifinet_pw'+data['session'][:8] in devices:
        raise ValueError('Temporary section collides with original configuration')
    if any(devices[name] not in data['radios'] for name in data['modified']):
        raise ValueError('Modified interface is outside affected recovery radios')
    return data


class RecoveryStore:
    def __init__(self,root=None):
        self.root=Path(root) if root is not None else root_directory()

    def _path(self,fingerprint,session):
        if not FINGERPRINT.fullmatch(fingerprint) or not SID.fullmatch(session):
            raise ValueError('Invalid recovery identity')
        return self.root/(hashlib.sha256(fingerprint.encode()).hexdigest()+'-'+session+'.json')

    def _guard(self):
        for path in (self.root.absolute(), *self.root.absolute().parents):
            try:
                attributes = getattr(path.lstat(), 'st_file_attributes', 0)
            except FileNotFoundError:
                attributes = 0
            if path.is_symlink() or attributes & 0x400:
                raise RecoveryDirectoryError('Linked recovery directory')

    def save(self,data):
        validate(data);self._guard();self.root.mkdir(parents=True,exist_ok=True)
        path=self._path(data['fingerprint'],data['session'])
        if path.is_symlink():raise ValueError('Linked recovery record')
        raw=(json.dumps(data,sort_keys=True)+'\n').encode('utf-8')
        if len(raw)>131072:raise ValueError('Recovery record exceeds limit')
        descriptor,temporary=tempfile.mkstemp(prefix='.recovery-',suffix='.tmp',dir=self.root)
        try:
            with os.fdopen(descriptor,'wb') as out:
                out.write(raw);out.flush();os.fsync(out.fileno())
            os.replace(temporary,path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def records(self,fingerprint):
        self._guard()
        if not self.root.exists():return []
        if not FINGERPRINT.fullmatch(fingerprint):raise ValueError('Invalid recovery device')
        prefix=hashlib.sha256(fingerprint.encode()).hexdigest()+'-'
        records=[]
        for index,path in enumerate(self.root.glob(prefix+'*.json')):
            if index>=128:raise ValueError('Recovery record count exceeds limit')
            if path.is_symlink() or not path.is_file() or path.stat().st_size>131072:
                raise ValueError('Invalid recovery file')
            with path.open('rb') as source:
                raw=source.read(131073)
            if len(raw)>131072:raise ValueError('Recovery record exceeds limit')
            try:
                decoded = json.loads(raw.decode('utf-8'))
            except (ValueError, UnicodeDecodeError) as error:
                if re.fullmatch(re.escape(prefix) + r'[0-9a-f]{32}\.json', path.name):
                    raise RecoveryRecordCorrupt(path, hashlib.sha256(raw).hexdigest(), fingerprint) from error
                raise
            if isinstance(decoded, dict) and decoded.get('schema') != 1:
                raise ValueError('Unsupported recovery record schema')
            try:
                data = validate(decoded)
            except (ValueError, TypeError) as error:
                session = decoded.get('session') if isinstance(decoded, dict) else None
                if (isinstance(decoded, dict) and decoded.get('fingerprint') == fingerprint
                        and isinstance(session, str) and SID.fullmatch(session)
                        and path == self._path(fingerprint, session)):
                    raise RecoveryRecordCorrupt(path, hashlib.sha256(raw).hexdigest(), fingerprint) from error
                raise
            if data['fingerprint']!=fingerprint or path!=self._path(fingerprint,data['session']):
                raise ValueError('Recovery identity mismatch')
            records.append(data)
        return records

    def backup_corrupt(self, issue):
        """Move the reviewed corrupt record to a unique backup after rechecking it."""
        if not isinstance(issue, RecoveryRecordCorrupt):
            raise ValueError('Invalid recovery review')
        self._guard()
        path = issue.path
        prefix = hashlib.sha256(issue.fingerprint.encode()).hexdigest() + '-'
        if path.parent != self.root or not re.fullmatch(re.escape(prefix) + r'[0-9a-f]{32}\.json', path.name):
            raise ValueError('Recovery review changed')
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 131072:
            raise ValueError('Recovery record changed')
        if getattr(path.lstat(), 'st_file_attributes', 0) & 0x400:
            raise ValueError('Recovery record changed')
        with path.open('rb') as source:
            raw = source.read(131073)
        if len(raw) > 131072 or hashlib.sha256(raw).hexdigest() != issue.digest:
            raise ValueError('Recovery record changed')
        backup = self.root / 'recovery-backups'
        if backup.is_symlink():
            raise ValueError('Invalid recovery backup directory')
        backup.mkdir(exist_ok=True)
        if backup.is_symlink() or getattr(backup.lstat(), 'st_file_attributes', 0) & 0x400:
            raise ValueError('Invalid recovery backup directory')
        destination = backup / (path.name + '.' + uuid.uuid4().hex + '.bak')
        path.rename(destination)
        return destination

    def remove(self,data):
        self._guard();self._path(data['fingerprint'],data['session']).unlink(missing_ok=True)
