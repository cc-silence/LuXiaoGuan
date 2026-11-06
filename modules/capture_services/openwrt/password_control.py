"""Owned, bounded supplicant control session for online password verification."""
from collections import deque
import json
import hashlib
import re
import time
from .command_builder import quote_argument
from .ssh_client import _close_resource
from .password_control_diagnostics import ControlEvidence, REQUESTS


_REQUESTS = REQUESTS


class AuthenticationTimeout(TimeoutError):
    """No conclusive authentication result; this is not a wrong-key verdict."""


class ControlRequestTimeout(TimeoutError):
    """Safe request metadata only, never credentials or remote output."""
    def __init__(self, operation):
        self.operation = operation if operation in _REQUESTS else 'unknown'
        super().__init__('Supplicant control request timed out: ' + self.operation)


class ControlUnavailable(RuntimeError):
    """Native interactive control is unavailable before any candidate is submitted."""


def _event(line):
    line=re.sub(r"^<\d+>", "", line)
    match=re.fullmatch(r"CTRL-EVENT-SSID-TEMP-DISABLED id=(\d+) .* reason=WRONG_KEY",line)
    if match:return ('wrong_key',int(match[1]))
    return None


def _line(text):
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text).strip().lstrip('> ').strip()


def _reply_line(line, command):
    """Separate native input redraws from exact reply/event grammar.

    edit_redraw() can print a partially typed command without a newline.
    The following daemon reply is then appended directly to that prefix.
    """
    operation = command.split(" ", 1)[0]
    def payload(value):
        if value == "PONG" or re.match(r"^(?:<\d+>)?CTRL-EVENT-", value):
            return True
        if operation == "status":
            return re.fullmatch(r"(?:wpa_state|id|bssid)=.*", value) is not None
        if operation == "add_network":
            return value.isdigit() or value == "FAIL"
        if operation in _REQUESTS - {"ping", "help", "status"}:
            return value in {"OK", "FAIL", "UNKNOWN COMMAND"}
        return False
    if payload(line):
        return line
    for typed in (command, "ping"):
        for length in range(len(typed), 0, -1):
            prefix = typed[:length]
            if line.startswith(prefix) and payload(line[length:]):
                return line[length:]
    return line


class PasswordControl:
    """One SSH channel; runtime-only profiles; native events tied to fresh IDs."""
    def __init__(self, ssh_client, interface, cancel, *, clock=time.monotonic, journal=None, transport="pipe"):
        self.cancel=cancel
        self.journal=journal
        self.clock=clock
        self.buffer=''
        self.events=deque(maxlen=128)
        self.network=None
        self._redraw_reported=False
        self.streams=()
        self.channel=None
        self._evidence=ControlEvidence(journal,transport,clock)
        self._evidence.begin('control_open')
        try:
            command='HOME=/dev/null; export HOME; exec wpa_cli -i '+quote_argument(interface)
            if transport == "pty":
                # The SSH owner negotiates no-echo, noncanonical terminal modes
                # before exec; OpenWrt need not provide an external stty applet.
                self.channel=ssh_client.open_control_stream(command,cancel_event=cancel,timeout=8)
            else:
                *self.streams,self.channel=ssh_client.exec_command(command,timeout=8,cancel_event=cancel)
            self._evidence.emit('completed')
            self._evidence.begin('control_ping')
            self.request('ping')
            self._evidence.emit('completed')
            self._evidence.begin('control_help')
            help_rows=self.request('help')
            self._evidence.cache_supported=any(row.split(' ',1)[0]=='pmksa_flush' for row in help_rows)
            if not self._evidence.cache_supported:
                raise ControlUnavailable('Native authentication cache control unavailable')
            self._evidence.emit('completed')
            self._evidence.begin('control_cache')
            if self.request('pmksa_flush')!=['OK']:
                raise ControlUnavailable('Supplicant cache control unavailable')
            self._evidence.emit('completed')
            self._evidence.begin('control_disconnect')
            self.ok('disconnect')
            self._evidence.emit('completed')
        except Exception as error:
            error.native_reason = self._evidence.failed(error,self.channel)
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
                line=_line(line)
                self._evidence.observe(line)
                return line
            if self.clock()>=deadline:raise TimeoutError('Supplicant control response timed out')
            if self.channel.recv_ready():
                data=self.channel.recv(4096)
                if not data:raise ConnectionError('Supplicant control ended')
                self.buffer+=data.decode('utf-8',errors='replace').replace('\r','\n')
                if len(self.buffer)>65536:raise RuntimeError('Supplicant control output exceeds limit')
            elif self.channel.closed or self.channel.exit_status_ready():
                raise ConnectionError('Supplicant control ended')
            else:
                if self.channel.recv_stderr_ready():
                    stderr=self.channel.recv_stderr(4096).decode('utf-8',errors='replace')
                    for row in stderr.splitlines():self._evidence.observe(row)
                if self.cancel is not None:self.cancel.wait(.025)
                else:time.sleep(.025)

    def request(self, command, timeout=5):
        operation=command.split(' ',1)[0]
        if operation not in _REQUESTS:operation='unknown'
        started=self.clock()
        self._evidence.requested(command)
        try:
            rows=self._request(command,timeout)
            self._evidence.response(rows)
            return rows
        except TimeoutError as exc:
            if self.journal is not None:
                self.journal.record('workflow',operation='password_auth',stage='verify',
                    status='timeout',reason='native_rpc_'+operation+'_timeout',
                    elapsed_seconds=self.clock()-started,**self._evidence.fields())
            raise ControlRequestTimeout(operation) from exc
        except Exception as error:
            if self.journal is not None:
                self.journal.record('workflow',operation='password_auth',stage='verify',
                    status='failed',reason='native_control_request_failed',
                    elapsed_seconds=self.clock()-started,exception_type=type(error).__name__,
                    **self._evidence.fields())
            raise

    def _request(self, command, timeout=5):
        self.check_cancel()
        if '\n' in command or '\r' in command or '\0' in command:
            raise ValueError('Invalid supplicant control command')
        self.channel.sendall((command+'\n'+('' if command=='ping' else 'ping\n')).encode('utf-8'))
        deadline=self.clock()+timeout
        rows=[]
        size=0
        while True:
            raw = self._readline(deadline)
            line = _reply_line(raw, command)
            if line != raw and not self._redraw_reported:
                self._redraw_reported = True
                if self.journal is not None:
                    self.journal.record("workflow",operation="password_auth",stage="capabilities",
                        status="completed",reason="native_redraw_reply_recovered")
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
                self._evidence.partial(line)

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
        if reason == "authentication_unconfirmed":
            raise AuthenticationTimeout("Authentication result was not confirmed before the deadline")
        return found

    def close(self):
        if self.channel is not None:
            try:
                if not self.channel.closed:
                    commands = f'disconnect\nremove_network {self.network}\n' if self.network is not None else ''
                    self.channel.sendall((commands+'quit\n').encode('ascii'))
            except Exception:
                pass
            _close_resource(self.channel)
        for stream in self.streams:_close_resource(stream)
        self.channel=None
        self.streams=()


def open_control(runner, section, cancel):
    """Resolve runtime ownership from netifd; never infer wlan names from radio IDs."""
    journal=getattr(runner,'operation_diagnostics',None)
    result=runner.run('password_control_probe',
        'command -v wpa_cli >/dev/null 2>&1 || exit 127; ubus call network.wireless status',
        timeout=10,cancel_event=cancel)
    if result.cancelled:raise InterruptedError('Password verification cancelled')
    if result.timed_out:raise TimeoutError('Password control capability check timed out')
    if result.exit_code==127:
        if journal is not None:
            journal.record('workflow',operation='password_auth',stage='control_identity',
                           status='failed',reason='native_tool_missing',runtime_match_count=0)
        return None
    if result.exit_code!=0:raise RuntimeError('Cannot inspect wireless runtime ownership')
    try:data=json.loads(result.stdout)
    except (ValueError,TypeError):raise RuntimeError('Invalid wireless runtime ownership') from None
    if not isinstance(data,dict):raise RuntimeError('Invalid wireless runtime ownership')
    matches=[]
    runtime={}
    for radio in data.values():
        if not isinstance(radio,dict):continue
        for interface in radio.get('interfaces',[]):
            if isinstance(interface,dict) and interface.get('section')==section:
                name=interface.get('ifname','')
                if isinstance(name,str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}',name):
                    matches.append(name)
                    runtime=dict(runtime_radio_up=radio.get('up'),runtime_radio_pending=radio.get('pending'),
                                 runtime_radio_disabled=radio.get('disabled'))
    if journal is not None:
        journal.record('workflow',operation='password_auth',stage='control_identity',
                       status='completed' if len(matches)==1 else 'failed',
                       reason='native_identity_available' if len(matches)==1 else 'native_interface_ambiguous',
                       runtime_match_count=len(matches),**runtime)
    if len(matches)!=1:raise RuntimeError('Cannot uniquely resolve verification interface')
    # Negotiate before any candidate. A rejected or incompatible terminal never
    # triggers a retry of an already submitted password.
    from .command_runner import _transport_errors
    last_reason = 'native_initialization_failed'
    for mode in ('pipe','pty'):
        try:
            control=PasswordControl(runner.ssh_client,matches[0],cancel,journal=journal,transport=mode)
        except (TimeoutError,ControlUnavailable,ConnectionError)+_transport_errors() as exc:
            last_reason = getattr(exc, "native_reason", "native_initialization_failed")
            if cancel is not None and cancel.is_set():raise InterruptedError('Password verification cancelled') from exc
            if not runner.ssh_client.connected:raise
            if journal is not None:
                journal.record('workflow',operation='password_auth',stage='capabilities',status='failed',reason='native_'+mode+'_unavailable')
        else:
            if journal is not None:
                journal.record('workflow',operation='password_auth',stage='capabilities',status='completed',reason='native_'+mode+'_ready')
            return control
    error = ControlUnavailable('Native interactive control unavailable in terminal and pipe modes')
    error.native_reason = last_reason
    raise error
