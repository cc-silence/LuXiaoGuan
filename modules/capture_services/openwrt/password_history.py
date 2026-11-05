"""Bounded durable attempt fingerprints; no plaintext passwords or Wi-Fi names."""
from contextlib import AbstractContextManager
import hashlib
import hmac
from pathlib import Path
import secrets
import sqlite3

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


def clear_history(path=None):
    """Background-only local operation; preserve dictionaries and result files."""
    connection=_connection(path or history_path())
    try:
        with connection:
            count=connection.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]
            connection.execute('DELETE FROM attempts')
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
        self.connection.commit()
        self.count+=1

    def __exit__(self,*args):
        self.connection.close()
        self._key=b''
