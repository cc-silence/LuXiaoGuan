"""Publish an already authenticated transport; never connect or approve a host key here."""
from dataclasses import replace
from .openwrt.models import OpenWrtConnectionState


def publish_verified_connection(owner,ssh,connection,fingerprint):
    if connection is None or not ssh.connected or ssh._client is not connection or ssh.host_fingerprint!=fingerprint:
        return False
    backend=owner.openwrt_backend
    if not hasattr(backend,'snapshot'):return False
    backend.snapshot=replace(backend.snapshot,connection_state=OpenWrtConnectionState.CONNECTED,
        connection_detail='局域网连接已恢复',host_fingerprint=fingerprint,last_error='')
    owner.openwrtSnapshotChanged.emit(backend.snapshot)
    return True
