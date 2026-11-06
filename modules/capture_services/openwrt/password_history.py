"""Bounded durable attempt fingerprints; only wrong-key fingerprints and a separate result catalog."""
from contextlib import AbstractContextManager
import hashlib
import hmac
from pathlib import Path
import secrets
import sqlite3
import time

KEY_TARGET='RouterManager/password-attempt-history/v1'
MAX_RECORDS=500000


def history_path():
    from modules.config_manager import WORK_DATA_DIR
    return Path(WORK_DATA_DIR)/'password-attempts.sqlite3'


def _connection(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    connection=sqlite3.connect(path,timeout=15)
    connection.execute('PRAGMA cache_size=-2048')
    connection.execute('CREATE TABLE IF NOT EXISTS attempts (target BLOB NOT NULL, candidate BLOB NOT NULL, PRIMARY KEY(target,candidate)) WITHOUT ROWID')
    return connection


def clear_history(path=None,cancel=None):
    """Background-only local operation; preserve dictionaries and result files."""
    connection=_connection(path or history_path())
    try:
        from .password_results_store import create_schema
        create_schema(connection)
        with connection:
            count=connection.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]
            connection.execute('DELETE FROM attempts')
            from .password_results_store import suppress_exported_results
            suppress_exported_results(connection,cancel)
            connection.execute("DELETE FROM password_dictionaries")
            connection.execute("DELETE FROM password_targets")
        return count
    finally:
        connection.close()


class PasswordAttemptHistory(AbstractContextManager):
    """One worker owns the connection. HMAC key remains in OS secure storage."""
    def __init__(self,path=None,*,key=None):
        self.connection=_connection(path or history_path())
        try:
            self.count=self.connection.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]
            if key is None:
                from modules.platform_services.credentials import read_generic_password,write_generic_password
                saved=read_generic_password(KEY_TARGET)
                if not saved:
                    if self.count:raise RuntimeError('Attempt history secure key is unavailable; clear the attempt records before retrying')
                    saved=secrets.token_hex(32)
                    write_generic_password(KEY_TARGET,'RouterManager',saved)
                    if read_generic_password(KEY_TARGET)!=saved:raise RuntimeError('Attempt history secure-key verification failed')
                key=bytes.fromhex(saved)
            if not isinstance(key,bytes) or len(key)!=32:raise ValueError('Invalid attempt history key')
            self._key=key
            from .password_results_store import PasswordResultCatalog
            self.catalog=PasswordResultCatalog(self)
        except BaseException:
            self.connection.close()
            raise

    def _fingerprints(self,target,password):
        identity=str(target.bssid).upper()+'\0'+str(target.ssid or '')
        network=hmac.digest(self._key,b'target\0'+identity.encode('utf-8'),'sha256')
        candidate=hmac.digest(self._key,b'candidate\0'+network+password.encode('utf-8'),'sha256')
        return network,candidate

    def contains(self,target,password):
        return self.connection.execute('SELECT 1 FROM attempts WHERE target=? AND candidate=?',self._fingerprints(target,password)).fetchone() is not None

    def mark(self,target,password):
        if self.contains(target,password):return
        if self.count>=MAX_RECORDS:raise RuntimeError('Attempt history limit reached; clear attempted records before continuing')
        self.connection.execute('INSERT INTO attempts(target,candidate) VALUES(?,?)',self._fingerprints(target,password))
        self.catalog.confirmed(target,password)
        self.connection.commit()
        self.count+=1

    def mark_success(self,target,password,result_file):
        # Successful credentials belong to result files, never the wrong-key skip list.
        removed=self.connection.execute('DELETE FROM attempts WHERE target=? AND candidate=?',self._fingerprints(target,password)).rowcount
        self.count-=removed
        self.catalog.confirmed(target,password)
        key=self.catalog.remember(target)
        self.connection.execute("UPDATE password_targets SET state='found',result_file=?,updated=? WHERE target=?",
                                (str(result_file),time.time(),key))
        self.connection.commit()

    def repair_success_records(self,targets,cancel=None):
        from .password_results_store import read_password_file
        for target in targets:
            if cancel is not None and cancel.is_set():raise InterruptedError('History repair cancelled')
            key=self.catalog.identity(target)
            row=self.connection.execute("SELECT result_file FROM password_targets WHERE target=? AND result_file<>''",(key,)).fetchone()
            if row is None:continue
            try:ssid,bssid,password=read_password_file(row[0])
            except (OSError,UnicodeError,ValueError):continue
            if (ssid,bssid)==(target.ssid,target.bssid.upper()):
                removed=self.connection.execute('DELETE FROM attempts WHERE target=? AND candidate=?',self._fingerprints(target,password)).rowcount
                self.count-=removed
            password=''
        self.connection.commit()

    def filter_pending(self,target,candidates,cancel=None):
        pending=[]
        for candidate in candidates:
            if cancel is not None and cancel.is_set():raise InterruptedError("History lookup cancelled")
            if not self.contains(target,candidate):pending.append(candidate)
        self.catalog.prepare(target,candidates,pending)
        return tuple(pending)

    def __exit__(self,*args):
        self.connection.close()
        self._key=b''
        self.catalog.membership.clear()
        self.catalog.groups.clear()
        self.catalog.active=None
