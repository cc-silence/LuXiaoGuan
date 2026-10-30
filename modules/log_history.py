"""Bounded asynchronous, redacted history of application runs."""
from concurrent.futures import Future
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import queue
import re
import sqlite3
import threading
import time
from uuid import uuid4
from modules.error_logs import RETENTION_DAYS, default_error_log_dir, regular_file

MAX_RECORD = 64 * 1024
MAX_HISTORY_BYTES = 48 * 1024 * 1024
MAX_STREAM_BYTES = 2 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
_active = None

def current_history():
    return _active

def install_history(store):
    global _active
    _active = store

class HistoryMasker:
    """Mask known identities and anonymize unknown network addresses."""
    def __init__(self):
        self.identities = None
        self.salt = uuid4().bytes

    @property
    def pattern(self):
        return getattr(self.identities,'pattern',None)

    @property
    def overflow(self):
        return getattr(self.identities,'overflow',False)

    def text(self, value):
        from modules.display_privacy import Masker
        text = re.sub(r'\x1b\[[0-9;?]*[a-zA-Z]', '', str(value or ''))
        # hcx tables can contain newly discovered names absent from UI snapshots.
        text = re.sub(r'(?m)^(\s*\d+[a-zA-Z]?\s+\d{2}:\d{2}:\d{2}\s+[+ ]{9}\s*[0-9a-fA-F]{12})[^\r\n]*', r'\1 [network name omitted]', text)
        text = re.sub(r'(\bAP:\s*)[^\r\n(]+(?=\s*\()', r'\1[network name omitted]', text)
        from modules.diagnostic_redaction import mask_diagnostic_literals
        text = mask_diagnostic_literals(text,self.identities or Masker())
        text = text.replace(str(Path.home()), '{USER_HOME}')
        pattern = r'(?<![\w:])(?:\d{1,3}\.){3}\d{1,3}(?![\w:])|(?<![\w:])(?:[0-9a-fA-F]{0,4}:){2,}[0-9a-fA-F:.%]*(?!\w)'
        import ipaddress
        def address(match):
            try:
                ipaddress.ip_address(match[0])
            except ValueError:
                return match[0]
            return 'address-' + hashlib.sha256(self.salt + match[0].encode()).hexdigest()[:8]
        text = re.sub(pattern, address, text)
        return re.sub(r'(?<![0-9A-Fa-f])[0-9A-Fa-f]{32,128}(?![0-9A-Fa-f])', '[identifier omitted]', text)

class LogHistory:
    """A single writer owns all disk IO. Producers never wait for the disk."""
    def __init__(self, directory=None, version='', clock=time.time):
        self.directory = Path(directory) if directory else default_error_log_dir().parent / 'log-history'
        self.version, self.clock = str(version), clock
        self.run_id = uuid4().hex
        self.masker = HistoryMasker()
        self.last_error = ''
        self.dropped = 0
        self._closed = False
        self._closing = False
        self._private_streams = set()
        self._redaction_overflow = False
        self._pending_bytes = 0
        self._budget_lock = threading.Lock()
        self._redaction_lock = threading.Lock()
        self._queue = queue.Queue(maxsize=128)
        self._thread = threading.Thread(target=self._work, name='log-history', daemon=True)
        self._thread.start()

    def _validate(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        for path in (self.directory, *self.directory.parents):
            info = path.lstat()
            if path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise OSError('Log history directory contains a link')
        for name in ('history.sqlite', 'history.sqlite-wal', 'history.sqlite-shm', 'history.sqlite-journal'):
            path = self.directory / name
            if (path.exists() or path.is_symlink()) and regular_file(path) is None:
                raise OSError('Unsafe history database')

    def _submit(self, operation, *args, wait=False):
        if self._closed or (self._closing and operation != 'close'):
            if wait:
                raise OSError('History closed')
            return False
        future = Future() if wait else None
        cost = sum(len(value.encode('utf-8')) if isinstance(value,str) else len(value)*256 if isinstance(value,tuple) else 64 for value in args)
        with self._budget_lock:
            if not wait and self._pending_bytes + cost > 8 * 1024 * 1024:
                self.dropped += 1
                return False
            self._pending_bytes += cost
        try:
            self._queue.put((operation, args, future, cost), timeout=5 if wait else 0)
        except queue.Full:
            with self._budget_lock:
                self._pending_bytes -= cost
            self.dropped += 1
            if wait:
                raise OSError('History queue full')
            return False
        return future.result(timeout=15) if wait else True

    def append(self, category, text, level='info', *, channel='', device='', kind='log'):
        from modules.log_services import sanitized_text
        if len(str(text)) > 256 * 1024:
            text = '[oversized log record omitted]'
        # Private key blocks may arrive split across stream chunks.
        stream = (category, channel, device)
        with self._redaction_lock:
            if self._redaction_overflow:
                text = '[privacy stream budget exceeded]'
            private = stream in self._private_streams
            lines = []
            for line in str(text).splitlines():
                if re.search('BEGIN .*PRIVATE KEY', line, re.I):
                    private = True
                    lines.append('[private key omitted]')
                elif private:
                    if re.search('END .*PRIVATE KEY', line, re.I):
                        private = False
                else:
                    lines.append(line)
            if private:
                if len(self._private_streams) < 256:
                    self._private_streams.add(stream)
                else:
                    self._redaction_overflow = True
            else:
                self._private_streams.discard(stream)
        raw = sanitized_text('\n'.join(lines), self.masker).encode('utf-8')
        if len(raw) > MAX_RECORD:
            raw = raw[:8192] + b'\n[...truncated...]\n' + raw[-48000:]
        return self._submit('append', str(category)[:64], raw.decode('utf-8','ignore'),
                            str(level)[:24], str(channel)[:64], str(device)[:64], kind)

    def context(self, category, context, *, device=''):
        from modules.log_services import _router_context_bytes
        try:
            text = _router_context_bytes(context,self.masker).decode('utf-8')
            return self._submit('context',str(category)[:64],text,str(device)[:64])
        except (ValueError, TypeError, UnicodeError) as exc:
            self.last_error = type(exc).__name__
            return False

    def references(self, category, references, *, device='', channel=''):
        return self._submit('references',str(category)[:64],tuple(references)[:256],str(device)[:64],str(channel)[:64])

    def reset_channel(self, category, channel, device=''):
        return self._submit('reset',category,channel,device)

    def snapshot(self, category, device=None):
        return self._submit('snapshot',category,device,wait=True)

    def flush(self):
        return self._submit('flush',wait=True)

    def close(self):
        if self._closed or self._closing:
            return
        self._closing = True
        try:
            self._submit('close',wait=True)
        except (OSError, TimeoutError):
            # A broken diagnostic disk must not prevent application shutdown.
            pass
        finally:
            self._closed = True
            self._thread.join(timeout=5)

    def _cleanup(self, db):
        cutoff = self.clock() - RETENTION_DAYS * 86400
        db.execute('DELETE FROM records WHERE created < ?', (cutoff,))
        db.execute('DELETE FROM runs WHERE started < ? AND id != ? AND NOT EXISTS (SELECT 1 FROM records WHERE run=runs.id)', (cutoff,self.run_id))
        total = db.execute('SELECT coalesce(sum(length(CAST(text AS BLOB))),0) FROM records').fetchone()[0]
        if total > MAX_HISTORY_BYTES:
            for ident,size,run in db.execute('SELECT id,length(CAST(text AS BLOB)),run FROM records ORDER BY id').fetchall():
                db.execute('DELETE FROM records WHERE id=?',(ident,))
                total -= size
                db.execute('UPDATE runs SET omitted=omitted+1 WHERE id=?',(run,))
                if total <= MAX_HISTORY_BYTES * .8:
                    break
        excess = db.execute('SELECT id,run FROM records ORDER BY id DESC LIMIT -1 OFFSET 32768').fetchall()
        for ident,run in excess:
            db.execute('DELETE FROM records WHERE id=?',(ident,))
            db.execute('UPDATE runs SET omitted=omitted+1 WHERE id=?',(run,))
        db.execute('PRAGMA incremental_vacuum(256)')

    def _insert(self, db, category, text, level, channel, device, kind):
        db.execute('INSERT INTO records(run,category,text,level,channel,device,kind,created) VALUES(?,?,?,?,?,?,?,?)',
                   (self.run_id,category,text,level,channel,device,kind,self.clock()))
        self._stream_counts = getattr(self, '_stream_counts', {})
        key = (category,channel,device,kind)
        self._stream_counts[key] = self._stream_counts.get(key,0) + 1
        if self._stream_counts[key] % 32 and len(text.encode('utf-8')) < MAX_RECORD:
            return
        if len(self._stream_counts) > 512:
            self._stream_counts.clear()
        rows = db.execute('SELECT id,length(CAST(text AS BLOB)) FROM records WHERE run=? AND category=? AND channel=? AND device=? AND kind=? ORDER BY id',
                          (self.run_id,category,channel,device,kind)).fetchall()
        size, count = sum(row[1] for row in rows), len(rows)
        if size > MAX_STREAM_BYTES or count > 2048:
            removed = 0
            for ident,length in rows[24:-1]:
                db.execute('DELETE FROM records WHERE id=?',(ident,))
                size -= length
                count -= 1
                removed += 1
                if size < MAX_STREAM_BYTES * .8 and count <= 2048:
                    break
            db.execute('UPDATE runs SET omitted=omitted+? WHERE id=?',(removed,self.run_id))

    def _snapshot(self, db, category, device):
        runs = db.execute('SELECT id,started,version,ended,omitted FROM runs ORDER BY (id=?) DESC,rowid DESC LIMIT 2',(self.run_id,)).fetchall()
        if category == 'auto-capture' and device == '':
            latest = db.execute("SELECT device FROM records WHERE category=? AND device!='' AND run IN (?,?) ORDER BY id DESC LIMIT 1",(category,runs[0][0],runs[-1][0])).fetchone()
            device = latest[0] if latest else ''
        result, used = [], 0
        for ident,started,version,ended,trimmed in runs:
            sql, args = 'SELECT text,level,channel,kind,device,created FROM records WHERE run=? AND category=?', [ident,category]
            if device is not None:
                sql += ' AND device=?'
                args.append(device)
            rows = db.execute(sql + ' ORDER BY id DESC LIMIT 8192',args).fetchall()
            kept, omitted = [], 0
            for row in rows:
                size = len(row[0].encode('utf-8'))
                if used + size > MAX_SNAPSHOT_BYTES:
                    omitted += 1
                    continue
                used += size
                kept.append(row)
            result.append(dict(run_id=ident,started=datetime.fromtimestamp(started,timezone.utc).isoformat(),
                               version=version,ended=bool(ended),records=list(reversed(kept)),omitted=omitted,stream_records_trimmed=trimmed))
        return dict(runs=result,dropped=self.dropped,storage_error=self.last_error)

    def _copy_references(self, db, category, references, device, channel_prefix):
        from modules.log_services import _expand_references, _read_reference, sanitized_text
        skipped = []
        deadline = time.monotonic() + 3
        def check():
            if time.monotonic() > deadline:
                raise TimeoutError('Diagnostic copy deadline')
        for ref,_count in _expand_references(tuple((ref,1) for ref in references),check,skipped,self.masker):
            try:
                _raw,text = _read_reference(ref,check,self.masker)
                raw = sanitized_text(text,self.masker).encode('utf-8')
                if len(raw) > MAX_RECORD:
                    raw = raw[:8192] + b'\n[...truncated...]\n' + raw[-48000:]
                channel = (channel_prefix + '-' if channel_prefix else '') + 'attachment-' + hashlib.sha256(str(ref.path).encode()).hexdigest()[:16]
                db.execute('DELETE FROM records WHERE run=? AND category=? AND channel=? AND device=?',(self.run_id,category,channel,device))
                self._insert(db,category,raw.decode('utf-8','ignore'),'diagnostic',channel,device,'attachment')
            except (OSError, ValueError, UnicodeError) as exc:
                skipped.append(dict(reason=type(exc).__name__))
        if skipped:
            self._insert(db,category,json.dumps(skipped),'diagnostic','attachment-notes',device,'log')

    def _work(self):
        db = None
        try:
            self._validate()
            db = sqlite3.connect(self.directory / 'history.sqlite',timeout=2)
            db.execute('PRAGMA auto_vacuum=INCREMENTAL')
            db.execute('PRAGMA secure_delete=ON')
            db.execute('PRAGMA max_page_count=16384')
            db.execute('CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,started REAL,version TEXT,ended INTEGER DEFAULT 0)')
            if 'omitted' not in {row[1] for row in db.execute('PRAGMA table_info(runs)')}:
                db.execute('ALTER TABLE runs ADD COLUMN omitted INTEGER DEFAULT 0')
            db.execute('CREATE TABLE IF NOT EXISTS records(id INTEGER PRIMARY KEY,run TEXT,category TEXT,text TEXT,level TEXT,channel TEXT,device TEXT,kind TEXT,created REAL)')
            db.execute('CREATE INDEX IF NOT EXISTS record_stream ON records(run,category,channel,device,kind,id)')
            db.execute('INSERT INTO runs(id,started,version) VALUES(?,?,?)',(self.run_id,self.clock(),self.version))
            self._cleanup(db)
            db.commit()
        except (OSError,sqlite3.Error) as exc:
            self.last_error = type(exc).__name__
            if db is not None:
                db.close()
            db = None
        last_cleanup = self.clock()
        pending = 0
        reported_dropped = 0
        while True:
            operation,args,future,cost = self._queue.get()
            try:
                if db is None:
                    raise OSError('History unavailable: ' + self.last_error)
                if self.clock()-last_cleanup >= 3600 or operation == 'snapshot' or getattr(self, '_writes', 0) >= 256:
                    self._cleanup(db)
                    last_cleanup = self.clock()
                    self._writes = 0
                self._writes = getattr(self, '_writes', 0) + 1
                if self.dropped > reported_dropped:
                    db.execute('UPDATE runs SET omitted=omitted+? WHERE id=?',(self.dropped-reported_dropped,self.run_id))
                    reported_dropped = self.dropped
                result = None
                if operation == 'append':
                    self._insert(db,*args)
                elif operation == 'context':
                    category,text,device = args
                    db.execute("DELETE FROM records WHERE run=? AND category=? AND device=? AND kind='context'",(self.run_id,category,device))
                    self._insert(db,category,text,'diagnostic','router-info',device,'context')
                elif operation == 'reset':
                    category,channel,device = args
                    db.execute("DELETE FROM records WHERE run=? AND category=? AND device=? AND (channel=? OR substr(channel,1,?)=?)",(self.run_id,category,device,channel,len(channel)+1,channel+'-'))
                elif operation == 'references':
                    self._copy_references(db,*args)
                elif operation == 'snapshot':
                    result = self._snapshot(db,*args)
                elif operation == 'close':
                    db.execute('UPDATE runs SET ended=1 WHERE id=?',(self.run_id,))
                pending += 1
                if future or self._queue.empty() or pending >= 32:
                    db.commit()
                    pending = 0
                if future:
                    future.set_result(result)
            except Exception as exc:
                self.last_error = type(exc).__name__
                if db is not None:
                    db.rollback()
                if future:
                    future.set_exception(OSError('History operation failed: ' + self.last_error))
            finally:
                with self._budget_lock:
                    self._pending_bytes -= cost
                self._queue.task_done()
            if operation == 'close':
                if db is not None:
                    db.close()
                return
