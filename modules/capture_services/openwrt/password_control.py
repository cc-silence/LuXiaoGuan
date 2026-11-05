"""Owned, bounded supplicant control session for online password verification."""
from collections import deque
import json
import hashlib
import re
import time
from .command_builder import quote_argument
from .ssh_client import _close_resource


class ControlUnavailable(RuntimeError):
    """Native interactive control is unavailable before any candidate is submitted."""


def _event(line):
    line=re.sub(r"^<\d+>", "", line)
    match=re.fullmatch(r"CTRL-EVENT-SSID-TEMP-DISABLED id=(\d+) .* reason=WRONG_KEY",line)
    if match:return ('wrong_key',int(match[1]))
    return None


def _line(text):
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text).strip().lstrip('> ').strip()


class PasswordControl:
    """One SSH channel; runtime-only profiles; native events tied to fresh IDs."""
    def __init__(self, ssh_client, interface, cancel, *, clock=time.monotonic, journal=None):
        self.cancel=cancel
        self.journal=journal
        self.clock=clock
        self.buffer=''
        self.events=deque(maxlen=128)
        self.network=None
        self.streams=()
        self.channel=None
        try:
            command='HOME=/dev/null; export HOME; exec wpa_cli -i '+quote_argument(interface)
            *self.streams,self.channel=ssh_client.exec_command(command,timeout=8,cancel_event=cancel)
            self.request('ping')
            if not any(row.split(' ',1)[0]=='pmksa_flush' for row in self.request('help')):
                raise ControlUnavailable('Native authentication cache control unavailable')
            if self.request('pmksa_flush')!=['OK']:
                raise ControlUnavailable('Supplicant cache control unavailable')
        except Exception:
            self.close()
            raise

    def check_cancel(self):
        if self.cancel is not None and self.cancel.is_set():
            raise InterruptedError('Password verification cancelled')

    def _readline(self, deadline):
        while True:
            self.check_cancel()
            if '\n' in self.buffer:
                line,self.buffer=self.buffer.split('\n',1)
                return _line(line)
            if self.clock()>=deadline:raise TimeoutError('Supplicant control response timed out')
            if self.channel.recv_ready():
                data=self.channel.recv(4096)
                if not data:raise ConnectionError('Supplicant control ended')
                self.buffer+=data.decode('utf-8',errors='replace').replace('\r','\n')
                if len(self.buffer)>65536:raise RuntimeError('Supplicant control output exceeds limit')
            elif self.channel.closed or self.channel.exit_status_ready():
                raise ConnectionError('Supplicant control ended')
            else:
                if self.channel.recv_stderr_ready():self.channel.recv_stderr(4096)
                if self.cancel is not None:self.cancel.wait(.025)
                else:time.sleep(.025)

    def request(self, command, timeout=5):
        self.check_cancel()
        if '\n' in command or '\r' in command or '\0' in command:
            raise ValueError('Invalid supplicant control command')
        self.channel.sendall((command+'\n'+('' if command=='ping' else 'ping\n')).encode('utf-8'))
        deadline=self.clock()+timeout
        rows=[]
        size=0
        while True:
            line=self._readline(deadline)
            if line=='PONG':return rows
            if line.startswith('<') or line.startswith('CTRL-EVENT-'):
                event=_event(line)
                if event:self.events.append(event)
                continue
            # Native line editing redraws the already typed command prefix when
            # an event interrupts input (for example '> pi' while sending ping).
            # Discard only exact prefixes of the commands sent in this request.
            if line and not command.startswith(line) and not 'ping'.startswith(line):
                size+=len(line)
                if size>65536:raise RuntimeError('Supplicant control reply exceeds limit')
                rows.append(line)

    def ok(self, command):
        rows=self.request(command)
        if rows!=['OK']:raise RuntimeError('Supplicant rejected a verification setting')

    def attempt(self, ssid, bssid, password, timeout=30):
        self.check_cancel()
        self.ok('disconnect')
        self.ok('pmksa_flush')
        rows=self.request('add_network')
        if len(rows)!=1 or not rows[0].isdigit():raise RuntimeError('Cannot create a verification network')
        new=int(rows[0])
        old=self.network
        self.network=new
        # Allocate before removing the predecessor: native IDs remain increasing.
        if old is not None:
            if new<=old:raise RuntimeError('Supplicant reused a verification network ID')
            self.ok(f'remove_network {old}')
        self.events.clear()
        self.ok(f'set_network {new} ssid '+ssid.encode('utf-8').hex())
        self.ok(f'set_network {new} bssid '+bssid)
        self.ok(f'set_network {new} key_mgmt WPA-PSK')
        self.ok(f'set_network {new} proto RSN')
        if len(password)==64 and re.fullmatch('[0-9a-fA-F]{64}',password):value=password
        else:value=hashlib.pbkdf2_hmac('sha1',password.encode('utf-8'),ssid.encode('utf-8'),4096,32).hex()
        self.ok(f'set_network {new} psk '+value)
        self.ok(f'select_network {new}')
        started=self.clock()
        deadline=started+timeout
        failed=False
        try:
            while True:
                self.check_cancel()
                # STATUS must identify this attempt, not an address from an old lease.
                status=dict(line.split('=',1) for line in self.request('status',min(5,max(.01,deadline-self.clock()))) if '=' in line)
                if (status.get('wpa_state')=='COMPLETED' and status.get('id')==str(new)
                        and status.get('bssid','').lower()==bssid.lower()):return self.outcome(True,'authenticated',started)
                if ('wrong_key',new) in self.events:return self.outcome(False,'wrong_key',started)
                if self.clock()>=deadline:return self.outcome(False,'authentication_unconfirmed',started)
                event_deadline=min(deadline,self.clock()+1)
                try:
                    while True:
                        line=self._readline(event_deadline)
                        event=_event(line)
                        if event:self.events.append(event)
                        if event==('wrong_key',new):return self.outcome(False,'wrong_key',started)
                        if 'CTRL-EVENT-CONNECTED' in line:break
                except TimeoutError:
                    pass
        except BaseException:
            failed=True
            raise
        finally:
            # Abnormal exits close this channel before restoring the owned STA.
            if not failed and (self.cancel is None or not self.cancel.is_set()):self.ok(f'disable_network {new}')

    def outcome(self, found, reason, started):
        if self.journal is not None:
            self.journal.record('workflow',operation='password_auth',stage='verify',
                status='timeout' if reason=='authentication_unconfirmed' else 'completed',
                reason=reason,elapsed_seconds=self.clock()-started)
        return found

    def close(self):
        if self.channel is not None:
            try:
                if self.network is not None and not self.channel.closed:
                    self.channel.sendall(f'disconnect\nremove_network {self.network}\nquit\n'.encode('ascii'))
            except Exception:
                pass
            _close_resource(self.channel)
        for stream in self.streams:_close_resource(stream)
        self.channel=None
        self.streams=()


def open_control(runner, section, cancel):
    """Resolve runtime ownership from netifd; never infer wlan names from radio IDs."""
    result=runner.run('password_control_probe',
        'command -v wpa_cli >/dev/null 2>&1 || exit 127; ubus call network.wireless status',
        timeout=10,cancel_event=cancel)
    if result.cancelled:raise InterruptedError('Password verification cancelled')
    if result.timed_out:raise TimeoutError('Password control capability check timed out')
    if result.exit_code==127:return None
    if result.exit_code!=0:raise RuntimeError('Cannot inspect wireless runtime ownership')
    try:data=json.loads(result.stdout)
    except (ValueError,TypeError):raise RuntimeError('Invalid wireless runtime ownership') from None
    if not isinstance(data,dict):raise RuntimeError('Invalid wireless runtime ownership')
    matches=[]
    for radio in data.values():
        if not isinstance(radio,dict):continue
        for interface in radio.get('interfaces',[]):
            if isinstance(interface,dict) and interface.get('section')==section:
                name=interface.get('ifname','')
                if isinstance(name,str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}',name):matches.append(name)
    if len(matches)!=1:raise RuntimeError('Cannot uniquely resolve verification interface')
    try:return PasswordControl(runner.ssh_client,matches[0],cancel,journal=getattr(runner,'operation_diagnostics',None))
    except TimeoutError as exc:raise ControlUnavailable('Native control handshake unavailable') from exc
