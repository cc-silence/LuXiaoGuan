"""Retry channel refusal only when replay cannot duplicate router mutations."""
READ_ONLY_COMMANDS = frozenset({
    "snapshot_wireless", "probe_phy_channel", "read_monitor_channel",
    "read_monitor_status", "read_monitor_link",
})


def connection_identity(client):
    return (getattr(client, "_client", None),
            getattr(client, "connection_generation", None),
            getattr(client, "host_fingerprint", None))


def channel_closed(exc):
    from paramiko.ssh_exception import SSHException
    return isinstance(exc, (SSHException, OSError, EOFError)) and any(
        text in str(exc).lower() for text in ("channel closed", "channel is not open"))


def can_retry(command_name, exc, client, identity):
    if not channel_closed(exc) or not client.connected:
        return False
    if connection_identity(client) != identity:
        return False
    stage = getattr(exc, "ssh_stage", "unknown")
    return stage == "channel_open" or (
        command_name in READ_ONLY_COMMANDS and stage in ("exec_request", "read_channel", "execute"))


def legacy_retry(exc, client, identity):
    # Preserve the existing owner-authorized single Channel-closed replay.
    # Do not extend that legacy exception to the newly handled error spelling.
    return ("channel closed" in str(exc).lower() and client.connected
            and connection_identity(client) == identity)
