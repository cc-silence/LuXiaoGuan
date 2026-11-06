"""Local result catalog: names and counts, never plaintext credentials in SQLite."""
from collections import Counter, defaultdict
import hashlib
from pathlib import Path
import re
import time
from types import SimpleNamespace

PAGE_SIZE = 100
MAX_TARGETS = 10000
MAX_DICTIONARIES = 50000
MAX_IGNORED_RESULTS = 500000


def create_schema(connection):
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS password_targets (
            target BLOB PRIMARY KEY, ssid TEXT NOT NULL, bssid TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'not_started', result_file TEXT NOT NULL DEFAULT '', updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS password_dictionaries (
            target BLOB NOT NULL, dictionary BLOB NOT NULL, name TEXT NOT NULL,
            total INTEGER NOT NULL, confirmed INTEGER NOT NULL DEFAULT 0,
            updated REAL NOT NULL, PRIMARY KEY(target,dictionary)) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS password_ignored_results (path_hash BLOB PRIMARY KEY, stamp BLOB NOT NULL) WITHOUT ROWID;
    """)


def result_signature(path):
    path=Path(path)
    stat=path.stat()
    identity=hashlib.sha256(str(path.absolute()).encode("utf-8")).digest()
    stamp=hashlib.sha256(f"{stat.st_mtime_ns}:{stat.st_size}".encode("ascii")).digest()
    return identity,stamp


def suppress_exported_results(connection,cancel=None):
    count=connection.execute("SELECT COUNT(*) FROM password_ignored_results").fetchone()[0]
    for (path,) in connection.execute("SELECT result_file FROM password_targets WHERE result_file<>''").fetchall():
        if cancel is not None and cancel.is_set():raise InterruptedError("Record cleanup cancelled")
        try:identity,stamp=result_signature(path)
        except OSError:continue
        exists=connection.execute("SELECT 1 FROM password_ignored_results WHERE path_hash=?",(identity,)).fetchone()
        if exists is None and count>=MAX_IGNORED_RESULTS:raise RuntimeError("Result cleanup history limit reached")
        connection.execute("INSERT OR REPLACE INTO password_ignored_results(path_hash,stamp) VALUES(?,?)",(identity,stamp))
        count+=int(exists is None)


def read_password_file(path):
    path = Path(path)
    if path.stat().st_size > 65536:
        raise ValueError('Result file exceeds size limit')
    lines = path.read_text(encoding='utf-8-sig').splitlines()
    if len(lines) < 2:
        raise ValueError('Invalid result file')
    values = lines[1].split('\t')
    if len(values) != 3:
        raise ValueError('Invalid result file')
    def decode(value):
        return re.sub(r'\\([\\trn])', lambda m: {'\\':'\\', 't':'\t', 'r':'\r', 'n':'\n'}[m[1]], value)
    ssid, bssid, password = map(decode, values)
    if not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', bssid):
        raise ValueError('Invalid result identity')
    return ssid, bssid.upper(), password


class PasswordResultCatalog:
    def __init__(self, history):
        self.history = history
        self.connection = history.connection
        create_schema(self.connection)
        self.groups = {}
        self.membership = {}
        self.active = None

    def identity(self, target):
        return self.history._fingerprints(target, '')[0]

    def configure(self, rounds):
        self.groups.clear()
        self.membership.clear()
        for group in rounds:
            digest = hashlib.sha256()
            for candidate in group.candidates:
                digest.update(candidate.encode('utf-8') + b'\0')
            key = digest.digest()
            self.groups[key] = (group.name or '\u672c\u6b21\u5b57\u5178', len(group.candidates))
            for candidate in group.candidates:
                self.membership[candidate] = key

    def remember(self, target):
        key = self.identity(target)
        self.connection.execute("""INSERT INTO password_targets(target,ssid,bssid,updated) VALUES(?,?,?,?)
            ON CONFLICT(target) DO UPDATE SET updated=excluded.updated""",
            (key, target.ssid, target.bssid.upper(), time.time()))
        return key

    def prepare(self, target, candidates, pending):
        if not self.groups:
            from .password_rounds import CandidateRound
            self.configure((CandidateRound('', tuple(candidates)),))
        key = self.remember(target)
        pending = set(pending)
        known = Counter()
        touched = set()
        for candidate in candidates:
            group = self.membership.get(candidate)
            if group is not None:
                touched.add(group)
                known[group] += int(candidate not in pending)
        for group, (name, total) in self.groups.items():
            self.connection.execute("""INSERT OR IGNORE INTO password_dictionaries
                (target,dictionary,name,total,confirmed,updated) VALUES(?,?,?,?,0,?)""",
                (key, group, name, total, time.time()))
        for group in touched:
            self.connection.execute('UPDATE password_dictionaries SET confirmed=?,updated=? WHERE target=? AND dictionary=?',
                                    (known[group], time.time(), key, group))
        self.connection.execute("UPDATE password_targets SET state=CASE WHEN result_file='' THEN 'verifying' ELSE 'found' END WHERE target=?", (key,))
        self.active = key
        self._prune()
        self.connection.commit()

    def confirmed(self, target, candidate):
        group = self.membership.get(candidate)
        if group is None:
            return
        key = self.identity(target)
        self.connection.execute('UPDATE password_dictionaries SET confirmed=MIN(total,confirmed+1),updated=? WHERE target=? AND dictionary=?',
                                (time.time(), key, group))

    def result(self, target, entry):
        from .password_events import target_update_for
        key = self.remember(target)
        state = target_update_for(target, entry).state
        self.connection.execute("""UPDATE password_targets SET
            state=CASE WHEN result_file<>'' THEN 'found' ELSE ? END,
            result_file=CASE WHEN ?<>'' THEN ? ELSE result_file END,updated=? WHERE target=?""",
            (state, entry.password_file, entry.password_file, time.time(), key))
        self.connection.commit()
        self.active = None

    def interrupted(self, cancelled):
        if self.active is not None:
            self.connection.execute("UPDATE password_targets SET state=? WHERE target=? AND result_file=''",
                                    ('cancelled' if cancelled else 'failed', self.active))
            self.connection.commit()
        self.active = None

    def _prune(self):
        # Only catalog metadata is bounded here; confirmed fingerprints stay intact.
        self.connection.execute('DELETE FROM password_targets WHERE target IN (SELECT target FROM password_targets ORDER BY updated DESC LIMIT -1 OFFSET ?)', (MAX_TARGETS,))
        self.connection.execute('DELETE FROM password_dictionaries WHERE target NOT IN (SELECT target FROM password_targets)')
        self.connection.execute('DELETE FROM password_dictionaries WHERE (target,dictionary) IN (SELECT target,dictionary FROM password_dictionaries ORDER BY updated DESC LIMIT -1 OFFSET ?)', (MAX_DICTIONARIES,))

    def import_results(self, directory, cancel):
        directory = Path(directory)
        if not directory.is_dir():
            return
        known = {row[0] for row in self.connection.execute("SELECT result_file FROM password_targets WHERE result_file<>''")}
        for index, path in enumerate(directory.glob('*-password.txt')):
            if index >= MAX_TARGETS or cancel.is_set():
                break
            if str(path) in known or path.is_symlink():
                continue
            try:
                identity,stamp=result_signature(path)
                ignored=self.connection.execute('SELECT stamp FROM password_ignored_results WHERE path_hash=?',(identity,)).fetchone()
                if ignored is not None and ignored[0]==stamp:continue
                ssid, bssid, password = read_password_file(path)
            except (OSError, UnicodeError, ValueError):
                continue
            password = ''
            key = self.remember(SimpleNamespace(ssid=ssid, bssid=bssid))
            self.connection.execute("UPDATE password_targets SET state='found',result_file=? WHERE target=?", (str(path),key))
        self._prune()
        self.connection.commit()

    def query(self, *, found=False, page=0, cancel=None):
        where = "WHERE result_file<>''" if found else "WHERE result_file=''"
        count = self.connection.execute('SELECT COUNT(*) FROM password_targets '+where).fetchone()[0]
        page = max(0, int(page))
        data = self.connection.execute('SELECT target,ssid,bssid,state,result_file FROM password_targets '+where+' ORDER BY updated DESC LIMIT ? OFFSET ?', (PAGE_SIZE,page*PAGE_SIZE)).fetchall()
        rows=[]
        for key,ssid,bssid,state,result_file in data:
            if cancel is not None and cancel.is_set():
                break
            groups = self.connection.execute('SELECT name,confirmed,total FROM password_dictionaries WHERE target=? ORDER BY updated,dictionary', (key,)).fetchall()
            known = self.connection.execute('SELECT COUNT(*) FROM attempts WHERE target=?', (key,)).fetchone()[0]
            password = ''
            if found:
                try:
                    file_ssid,file_bssid,password = read_password_file(result_file)
                    if (file_ssid,file_bssid) != (ssid,bssid):
                        password = ''
                except (OSError, UnicodeError, ValueError):
                    pass
            rows.append(dict(ssid=ssid,bssid=bssid,state=state,dictionaries='\n'.join(r[0] for r in groups),
                             progress=f'{sum(r[1] for r in groups)}/{sum(r[2] for r in groups)}' if groups else f'{known}/?',password=password))
        return dict(rows=rows,total=count,page=page,page_size=PAGE_SIZE)
