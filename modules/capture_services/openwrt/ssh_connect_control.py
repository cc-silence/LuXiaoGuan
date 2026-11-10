"""Cancellable TCP and SSH waits, scoped to one login socket."""
from contextlib import contextmanager
import errno
import select
import socket
from threading import Event, Thread
import time


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise InterruptedError("SSH login cancelled")


def _connect_socket(host, port, timeout, cancel):
    check_cancel(cancel)
    addresses = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    errors = {}
    pending = {errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY,
               10035, 10036, 10037}
    for family, kind, proto, _, address in addresses:
        check_cancel(cancel)
        sock = socket.socket(family, kind, proto)
        try:
            sock.setblocking(False)
            code = sock.connect_ex(address)
            deadline = time.monotonic() + timeout
            while code not in (0, errno.EISCONN):
                check_cancel(cancel)
                if code not in pending:
                    raise OSError(code, "TCP connection failed")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("TCP connection timed out")
                _, writable, failed = select.select([], [sock], [sock], min(0.05, remaining))
                if writable or failed:
                    code = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
            check_cancel(cancel)
            sock.settimeout(timeout)
            return sock
        except OSError as exc:
            sock.close()
            check_cancel(cancel)
            if isinstance(exc, TimeoutError):
                raise
            errors[address] = exc
        except BaseException:
            sock.close()
            raise
    from paramiko.ssh_exception import NoValidConnectionsError
    raise NoValidConnectionsError(errors)


def connection_errors(error):
    from paramiko.ssh_exception import NoValidConnectionsError
    return tuple(error.errors.values()) if isinstance(error, NoValidConnectionsError) else (error,)


def access_denied(error):
    errors = connection_errors(error)
    return bool(errors) and all(
        isinstance(item, OSError) and (getattr(item, 'winerror', None) == 10013
                                      or item.errno in (errno.EACCES, 10013))
        for item in errors)


def _open_login_socket(host, port, timeout, cancel, network_repair):
    try:
        return _connect_socket(host, port, timeout, cancel)
    except OSError as error:
        check_cancel(cancel)
        if network_repair is None or not access_denied(error):
            raise
        # Only a local access denial can request policy repair. Authentication
        # and host identity are checked later, once, over the returned socket.
        network_repair()
        check_cancel(cancel)
        return _connect_socket(host, port, timeout, cancel)


@contextmanager
def login_socket(host, port, timeout, cancel, network_repair=None):
    if cancel is None and network_repair is None:
        yield None
        return
    cancel = cancel if cancel is not None else Event()
    sock = _open_login_socket(host, port, timeout, cancel, network_repair)
    done = Event()

    def interrupt():
        while not done.wait(0.05):
            if cancel.is_set():
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                sock.close()
                return

    watcher = Thread(target=interrupt, name="RouterLoginCancel", daemon=True)
    watcher_started = False
    succeeded = False
    try:
        watcher.start()
        watcher_started = True
        check_cancel(cancel)
        yield sock
        check_cancel(cancel)
        succeeded = True
    except Exception as exc:
        if cancel.is_set():
            raise InterruptedError("SSH login cancelled") from exc
        raise
    finally:
        done.set()
        if watcher_started:
            watcher.join()
        if not succeeded:
            sock.close()
