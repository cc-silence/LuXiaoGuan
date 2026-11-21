"""Install a signed local APK curl candidate without replacing shared libraries."""
import hashlib
import re
import tempfile
from pathlib import Path
from uuid import uuid4
from .command_builder import quote_argument as q
from .signal_manager import SignalError


def verify_plan(output, version):
    from .apk_plan import parse_actions
    actions = parse_actions(output)
    if len(actions) != 1 or actions[0][0] not in ('Installing', 'Downgrading') or actions[0][1:] != ('curl', version):
        raise ValueError('APK plan changes other packages or cannot be verified')


def install_local(manager, device, version, cancel, tell):
    from .curl_repair import _local_candidate
    data = _local_candidate(device, version, cancel)
    if data is None:
        return False
    remote = '/tmp/luxiaoguan-curl-' + uuid4().hex
    filename = 'curl-' + version + '.apk'
    manager._execute_stage('curl_apk_mkdir', 'umask 077; mkdir ' + q(remote), 15, cancel, 'Prepare curl candidate')
    try:
        with tempfile.TemporaryDirectory(prefix='curl-apk-') as directory:
            path = Path(directory) / filename
            path.write_bytes(data)
            manager._package_uploader(bool(getattr(device, 'sftp_available', False)), cancel, tell)(str(path), remote + '/' + filename)
        target = remote + '/' + filename
        cmd = 'test "$(sha256sum ' + q(target) + ' | cut -d " " -f 1)" = ' + q(hashlib.sha256(data).hexdigest())
        cmd += ' && mkdir ' + q(remote + '/unpacked') + ' && apk extract --no-network --destination ' + q(remote + '/unpacked') + ' ' + q(target)
        manager._execute_stage('curl_apk_extract', cmd, 30, cancel, 'Verify and extract curl candidate')
        output = manager._execute_stage('curl_apk_trial', q(remote + '/unpacked/usr/bin/curl') + ' --version', 20, cancel, 'Try curl candidate')
        if not re.search(r'(?im)^curl ' + re.escape(version.split('-r')[0]) + r'\b', output) or not re.search(r'(?im)^Protocols:.*\bhttps\b', output):
            raise SignalError('environment', 'curl candidate failed runtime verification')
        base = 'apk --no-network --no-cache --repositories-file /dev/null add '
        plan = manager._execute_stage('curl_apk_plan', base + '--simulate ' + q(target), 90, cancel, 'Preview curl installation')
        try:
            verify_plan(plan, version)
        except ValueError as exc:
            raise SignalError('environment', 'curl installation plan could affect other packages') from exc
        manager._execute_stage('curl_apk_install', base + q(target), 180, cancel, 'Install matching curl')
        return True
    finally:
        from .offline_cleanup import cleanup_remote
        cleanup_remote(manager._execute, 'curl_apk_cleanup', 'rm -rf ' + q(remote), tell)
