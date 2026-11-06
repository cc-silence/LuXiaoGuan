"""SSH terminal negotiation for owned automation channels (RFC 4254)."""


def request_control_pty(channel, *, term, width, height):
    """Disable line buffering and input echoes before executing a client.

    Paramiko 5.0's public get_pty sends an empty terminal-modes string.
    Keep the small protocol adapter here; use the same channel request/ack
    lifecycle so the SSH owner's cancellation and timeout cleanup still apply.
    No external terminal utility or remote shell configuration is needed.
    """
    from paramiko.common import cMSG_CHANNEL_REQUEST
    from paramiko.message import Message
    from paramiko.ssh_exception import SSHException

    if channel.closed or channel.eof_received or channel.eof_sent or not channel.active:
        raise SSHException("Channel is not open")
    modes = Message()
    for opcode in (51, 53):  # RFC 4254: ICANON and ECHO.
        modes.add_byte(bytes((opcode,)))
        modes.add_int(0)
    modes.add_byte(b"\x00")
    request = Message()
    request.add_byte(cMSG_CHANNEL_REQUEST)
    request.add_int(channel.remote_chanid)
    request.add_string("pty-req")
    request.add_boolean(True)
    request.add_string(term)
    for size in (width, height, 0, 0):
        request.add_int(size)
    request.add_string(modes.asbytes())
    channel._event_pending()
    channel.transport._send_user_message(request)
    channel._wait_for_event()
