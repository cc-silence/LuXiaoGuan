"""One bounded, read-only check of the exact authenticated SSH connection."""
import time

COMMAND = "printf '__RM_SSH_ALIVE__\\n'"
MARKER = b"__RM_SSH_ALIVE__\n"


def probe_authenticated_connection(owner, client, timeout=3):
    deadline = time.monotonic() + timeout
    channel = None

    def open_probe(register):
        resource = client.get_transport().open_session(timeout=max(.01, deadline-time.monotonic()))
        register(resource, "exec_request")
        resource.settimeout(max(.01, deadline-time.monotonic()))
        resource.exec_command(COMMAND)
        resource.shutdown_write()
        return resource

    try:
        channel = owner._open_owned(client, open_probe, lambda value: value.close(),
                                    timeout, None, "liveness", isolate_failure=True)
        output = bytearray()
        while time.monotonic() < deadline:
            if channel.recv_ready():
                output.extend(channel.recv(256))
                if len(output) > 256:
                    return False
            if channel.exit_status_ready() and not channel.recv_ready():
                return bytes(output) == MARKER and channel.recv_exit_status() == 0
            time.sleep(.02)
        return False
    except Exception:
        return False
    finally:
        if channel is not None:
            try:
                channel.close()
            except Exception:
                pass
