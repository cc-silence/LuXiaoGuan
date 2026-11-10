"""One foreground read-only log stream shared by capture tasks on a connection."""
import codecs
from functools import wraps
from inspect import signature
import re
import socket
from threading import Event, RLock, Thread
from modules.capture_services.capture_logging import LOGGER
import time

COMMAND = "command -v logread >/dev/null 2>&1 || { printf '__RM_LOG_UNAVAILABLE__\\n'; exit 127; }; exec logread -f -l 40"
MAX_LIFETIME = 24 * 60 * 60
_RELEVANT = re.compile(r'kern\.|panic|oom-kill|out of memory|killed process|call trace|watchdog|firmware.*(?:crash|fail)|soft lockup|hard lockup', re.I)


class CaptureLogRun:
    def __init__(self, owner, connection):
        self.owner, self.connection = owner, connection
        self.epoch = owner.runner.operation_diagnostics.epoch
        self.stop = Event()
        self.ready = Event()
        self.done = Event()
        self.channel = None
        self.users = set()
        self.thread = Thread(target=self._work, name='CaptureSystemLog', daemon=True)

    def record(self, bucket, **row):
        try:
            self.owner.runner.operation_diagnostics.capture_evidence(bucket, row, self.epoch)
        except Exception as exc:
            LOGGER.warning("Capture diagnostic record failed type=%s", type(exc).__name__)

    def line(self, text):
        if '__RM_LOG_UNAVAILABLE__' in text:
            self.record('stream_states', state='unsupported')
        elif _RELEVANT.search(text):
            self.record('system_lines', text=text)

    def _work(self):
        decoder = codecs.getincrementaldecoder('utf-8')('replace')
        partial = ''
        state = 'ended'
        deadline = time.monotonic() + MAX_LIFETIME
        try:
            self.record('stream_states', state='starting')
            ssh = self.owner.runner.ssh_client
            self.channel = ssh.open_diagnostic_stream(self.connection, COMMAND, self.stop, timeout=4)
            self.ready.set()
            self.record('stream_states', state='running')
            while not self.stop.is_set():
                if time.monotonic() >= deadline:
                    state = 'deadline'
                    break
                if ssh._client is not self.connection:
                    break
                if self.channel.recv_ready():
                    try: chunk = self.channel.recv(8192)
                    except socket.timeout: continue
                    if not chunk: break
                    partial += decoder.decode(chunk)
                    while '\n' in partial:
                        line, partial = partial.split('\n', 1)
                        self.line(line.rstrip('\r'))
                    if len(partial) > 8192:
                        self.line(partial[:512] + ' [oversized line omitted]')
                        partial = ''
                elif self.channel.exit_status_ready() or self.channel.closed:
                    break
                else:
                    # Wait only for local channel input/cancellation; no remote polling.
                    self.stop.wait(0.05)
            if self.stop.is_set(): state = 'cancelled'
        except Exception as exc:
            state = 'cancelled' if self.stop.is_set() else 'failed'
            self.record('stream_states', state=state, text=type(exc).__name__)
        finally:
            self.ready.set()
            partial += decoder.decode(b'', final=True)
            if partial: self.line(partial)
            channel = self.channel
            if channel is not None:
                try:
                    # Foreground PTY: interrupt this logger only, then close its channel.
                    if not channel.closed and not channel.exit_status_ready():
                        channel.send('\x03')
                except Exception as exc:
                    LOGGER.debug("Diagnostic interrupt failed type=%s", type(exc).__name__)
                finally:
                    try: channel.close()
                    except Exception as exc:
                        self.record('stream_states', state='failed', text=type(exc).__name__)
            self.record('stream_states', state=state)
            self.done.set()


class CaptureLogLease:
    def __init__(self, manager, run, token):
        self.manager, self.run, self.token = manager, run, token

    def close(self):
        with self.manager.lock:
            self.run.users.discard(self.token)
            last = not self.run.users
            if last:
                self.run.stop.set()
                if self.manager.run is self.run: self.manager.run = None
        # Only capture worker finalizers call this. Never wait on a GUI callback.
        if last:
            if not self.run.done.wait(5):
                self.run.record('stream_states', state='failed', text='cleanup deadline exceeded')


class CaptureLogManager:
    def __init__(self, runner):
        self.runner = runner
        self.lock = RLock()
        self.run = None

    def acquire(self, cancel=None):
        ssh = self.runner.ssh_client
        connection = getattr(ssh, '_client', None)
        if connection is None or not ssh.connected or (cancel is not None and cancel.is_set()):
            return None
        with self.lock:
            run = self.run
            if run is None or run.connection is not connection or run.stop.is_set():
                if run is not None: run.stop.set()
                run = self.run = CaptureLogRun(self, connection)
                run.thread.start()
            token = object()
            run.users.add(token)
        deadline = time.monotonic() + 4.5
        lease = CaptureLogLease(self, run, token)
        while not run.ready.wait(0.05):
            if cancel is not None and cancel.is_set():
                lease.close()
                return None
            if time.monotonic() >= deadline:
                break
        return lease


def with_capture_logs(function):
    """Keep stream lifetime around the existing capture worker, including cleanup."""
    parameters = signature(function)
    @wraps(function)
    def wrapped(self, *args, **kwargs):
        runner = getattr(self, 'command_runner', None)
        if runner is None: runner = getattr(self, 'runner', None)
        manager = getattr(runner, 'capture_logs', None)
        lease = None
        try:
            if isinstance(manager, CaptureLogManager):
                bound = parameters.bind(self, *args, **kwargs)
                cancel = bound.arguments.get('cancel_event', getattr(self, '_stop_event', None))
                lease = manager.acquire(cancel)
        except Exception as exc:
            LOGGER.warning("Capture diagnostic startup failed type=%s", type(exc).__name__)
        try:
            return function(self, *args, **kwargs)
        finally:
            if lease is not None:
                try: lease.close()
                except Exception as exc:
                    LOGGER.warning("Capture diagnostic cleanup failed type=%s", type(exc).__name__)
    return wrapped
