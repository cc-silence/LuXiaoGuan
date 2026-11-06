"""Bounded recovery receipts and identity-bound reconciliation of policy backups."""
import hashlib
import re
from threading import Event
from .initial_reconnect_store import ReconnectStore

_PATH = re.compile(r'/root/luxiaoguan-policy\.[A-Za-z0-9]+')
_TOKEN = re.compile(r'[a-f0-9]{16}')


class PolicyRecoveryStore(ReconnectStore):
    def __init__(self, folder=None):
        if folder is None:
            from modules.config_services.config_store import user_config_dir
            folder = user_config_dir() / 'policy-recovery'
        super().__init__(folder)


def _store(owner):
    return getattr(owner.manager, '_policy_recovery_store', None) or PolicyRecoveryStore()


def _receipt(owner, store):
    return store.read(owner.fingerprint) or dict(schema=1, fingerprint=owner.fingerprint, tasks=[])


def remember(owner, directory, token, before, files):
    if not _PATH.fullmatch(directory) or not _TOKEN.fullmatch(token):
        raise ValueError('Invalid policy recovery identity')
    store = _store(owner)
    receipt = _receipt(owner, store)
    tasks = receipt['tasks']
    if len(tasks) >= 20:
        raise RuntimeError('Policy recovery receipt limit reached')
    task = dict(directory=directory, token=token, hashes=dict(before['hashes']),
                original={op['path']: op['expected'] for op in files},
                written={op['path']: hashlib.sha256(op['content'].encode('utf-8')).hexdigest() for op in files})
    tasks[:] = [row for row in tasks if row['directory'] != directory] + [task]
    store.write(receipt, create=not store.path(owner.fingerprint).exists())


def forget(owner, directory):
    store = _store(owner)
    receipt = store.read(owner.fingerprint)
    if receipt is None:
        return
    receipt['tasks'] = [row for row in receipt['tasks'] if row['directory'] != directory]
    if receipt['tasks'] or receipt.get('pending'):
        store.write(receipt)
    else:
        store.remove(owner.fingerprint)


def reconcile(owner, cancel, *, confirmed=False, exclude=(), expected=None):
    """Only a verified accepted snapshot or a tracked complete restoration permits cleanup."""
    owner._current()
    if cancel.is_set():
        raise InterruptedError('Policy recovery inspection cancelled')
    store = _store(owner)
    receipt = _receipt(owner, store)
    if not confirmed and not receipt.get('confirmed') and not receipt['tasks']:
        return None
    snapshot = owner.read(cancel)
    proof = dict(hashes=snapshot['hashes'], files=snapshot['files'])
    if confirmed:
        if expected is None or proof != expected:
            raise RuntimeError('Configuration changed after successful verification')
        receipt['confirmed'] = proof
        receipt['pending'] = True
        store.write(receipt, create=not store.path(owner.fingerprint).exists())
    accepted = receipt.get('confirmed') == proof
    inventory = owner._call('files_inventory', cancel, timeout=10)
    rows = inventory.get('directories')
    if rows == {}:
        rows = []  # Lua jsonc encodes an empty table as an object.
    if not isinstance(rows, list) or len(rows) > 20:
        raise ValueError('Invalid policy recovery inventory')
    tasks = {row['directory']: row for row in receipt['tasks']}
    remaining = dict(tasks)
    cleaned, retained, reasons = [], [], {}
    for index, row in enumerate(rows):
        if cancel.is_set():
            raise InterruptedError('Policy recovery inspection cancelled')
        owner._current()
        path, token = row.get('directory', ''), row.get('token', '')
        if not _PATH.fullmatch(path):
            raise ValueError('Invalid policy recovery inventory identity')
        if not _TOKEN.fullmatch(token):
            reasons[path] = 'ownership_unverified'
            retained.append(path)
            continue
        if path in exclude:
            continue
        task = tasks.get(path)
        state = None
        journal = row.get('journal')
        if task and token == task['token'] and snapshot['hashes'] == task['hashes']:
            if all(snapshot['files'].get(p) == h for p, h in task['original'].items()):
                state = 'restored'
        if accepted and (not task or token == task['token']) and isinstance(journal, list) and journal:
            if all(isinstance(op, dict) and op.get('written') and
                   snapshot['files'].get(op.get('path')) == op['written'] for op in journal):
                state = 'applied'
        if state is None:
            reasons[path] = 'configuration_or_file_unverified'
            retained.append(path)
            continue
        try:
            value = owner._call('files_cleanup', cancel, timeout=10, backup_dir=path,
                                token=token, state=state, hashes=snapshot['hashes'], reconcile=True)
            if value.get('cleaned') is not True:
                raise RuntimeError('Incomplete recovery cleanup result')
        except Exception as exc:
            if cancel.is_set():
                raise InterruptedError('Policy recovery inspection cancelled')
            owner._current()
            reasons[path] = getattr(exc, 'code', None) or type(exc).__name__
            retained.append(path)
            if getattr(exc, 'code', None) != 'file_cleanup':
                retained.extend(item['directory'] for item in rows[index+1:])
                break
            continue
        cleaned.append(path)
        remaining.pop(path, None)
    # A lost cleanup reply may leave a receipt for a directory already removed.
    present = {row['directory'] for row in rows}
    receipt['tasks'] = [row for path, row in remaining.items() if path in present]
    receipt['pending'] = bool(retained or receipt['tasks'])
    owner._current()
    if receipt['pending']:
        store.write(receipt)
    else:
        store.remove(owner.fingerprint)
    if not cleaned and not retained:
        return None
    parts = []
    if cleaned:
        parts.append('已核对并清理历史恢复副本：' + '、'.join(cleaned))
    if retained:
        parts.append('以下恢复副本尚未确认可清理，已保留：' + '、'.join(path+' ['+reasons.get(path, 'inspection_stopped')+']' for path in retained))
    return dict(verified=not retained, cleaned=cleaned, retained=retained, retained_reasons=reasons, note='\n'.join(parts))


def after_verified(owner, expected, exclude=()):
    """Cleanup trouble never invalidates successfully verified settings."""
    try:
        return reconcile(owner, Event(), confirmed=True, exclude=exclude, expected=expected)
    except Exception as exc:
        owner._current()
        return _inspection_warning(exc)


def needs_reconcile(manager, fingerprint):
    try:
        store = getattr(manager, '_policy_recovery_store', None) or PolicyRecoveryStore()
        receipt = store.read(fingerprint)
    except (OSError, ValueError):
        # Queue the separate inspection so receipt trouble is reported without
        # invalidating the normal read-only router status or rewriting evidence.
        return True
    return bool(receipt and (receipt.get('tasks') or receipt.get('pending')))


def _inspection_warning(error):
    code = re.sub(r'[^A-Za-z0-9_]', '', str(getattr(error, 'code', None) or type(error).__name__))[:64]
    return dict(verified=False, error_code=code,
                note='历史恢复副本核对或清理未完成，请重新登录核对：[' + code + ']')


def inspect_for_login(owner, cancel):
    """Cleanup failure is separate from initialization verification; cancellation is not."""
    try:
        return reconcile(owner, cancel)
    except Exception as error:
        if cancel.is_set() or isinstance(error, InterruptedError):
            raise
        owner._current()
        return _inspection_warning(error)
