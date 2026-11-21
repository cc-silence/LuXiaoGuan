"""Repair curl using the official matching package without replacing libraries."""
import hashlib
import io
import re
import tarfile
import tempfile
from pathlib import Path
from uuid import uuid4
from .command_builder import quote_argument as q
from .signal_manager import SignalError

_SAFE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.+~-]{0,100}')


def candidate_url(device, version):
    from .firmware_profile import canonical_release, release_kind
    release = canonical_release(getattr(device, 'version', ''))
    arch = str(getattr(device, 'package_architecture', ''))
    if (getattr(device, 'distribution', '') != 'OpenWrt' or
            getattr(device, 'package_manager', '') != 'opkg' or
            release_kind(release) != 'stable' or not _SAFE.fullmatch(arch) or
            not _SAFE.fullmatch(version)):
        raise ValueError('Unsupported firmware for matching curl')
    name = 'curl_' + version + '_' + arch + '.ipk'
    return name, 'https://archive.openwrt.org/releases/' + release + '/packages/' + arch + '/packages/' + name


def extract_candidate(data, version, arch):
    if not 1 <= len(data) <= 4 * 1024 * 1024:
        raise ValueError('Invalid curl package size')
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:*') as package:
        def member(name):
            found = [m for m in package.getmembers() if m.name.lstrip('./') == name and m.isfile()]
            if len(found) != 1 or found[0].size > 8 * 1024 * 1024:
                raise ValueError('Invalid package structure')
            return package.extractfile(found[0]).read()
        control_data = member('control.tar.gz')
        payload_data = member('data.tar.gz')
    with tarfile.open(fileobj=io.BytesIO(control_data), mode='r:gz') as archive:
        items=[m for m in archive.getmembers() if m.name.lstrip('./') == 'control' and m.isfile()]
        if len(items) != 1 or items[0].size > 16384: raise ValueError('Invalid control')
        fields={}
        for line in archive.extractfile(items[0]).read().decode('utf-8').splitlines():
            if ': ' in line and not line[0].isspace():
                key,value=line.split(': ',1)
                if key in fields: raise ValueError('Duplicate control field')
                fields[key]=value
    if (fields.get('Package'),fields.get('Version'),fields.get('Architecture')) != ('curl',version,arch):
        raise ValueError('Candidate metadata mismatch')
    with tarfile.open(fileobj=io.BytesIO(payload_data), mode='r:gz') as archive:
        files=[m for m in archive.getmembers() if m.name.lstrip('./') == 'usr/bin/curl' and m.isfile()]
        if len(files) != 1 or files[0].size > 8 * 1024 * 1024: raise ValueError('Invalid executable')
        return archive.extractfile(files[0]).read()


def verify_plan(text, version, *, installed=True):
    actions=[]
    for line in str(text).splitlines():
        line=line.strip()
        if re.match(r'^(Installing|Upgrading|Downgrading|Removing|Reinstalling|Replacing)\b',line):
            actions.append(line)
    pattern = (r'Downgrading curl on \S+ from \S+ to '+re.escape(version)+r'\.{1,3}' if installed else r'Installing curl \('+re.escape(version)+r'\) to \S+\.{1,3}')
    if len(actions)!=1 or not re.fullmatch(pattern,actions[0]):
        raise ValueError('Package plan changes other packages or is not verifiable')


def _local_candidate(device, version, cancel):
    """Read only an exact device and version match from the authenticated bundle."""
    from .bundle_storage_lock import bundle_lock
    from .common_bundle_repository import CommonBundleRepository, BundleCoverageUnavailable
    repo = CommonBundleRepository(kind='common')
    with bundle_lock(repo.root, shared=True):
        try:
            profile = repo.profile(device)
        except BundleCoverageUnavailable:
            return None
        if profile is None:
            return None
        row = repo.package_row(profile, 'curl', version)
        if row is None:
            from .bundle_variants import matching_variant
            row = matching_variant(repo.catalog, device, 'curl', version)
        if row is None:
            return None
        if row['size'] > 4 * 1024 * 1024:
            raise ValueError('Offline curl package exceeds size limit')
        return repo.object_path(row, cancel).read_bytes()


def repair(manager, device, cancel, tell, *, allow_local_bundle=True):
    from .curl_version_diagnostics import query_versions
    from .signal_package_feed import fetch_bytes, download_session
    from .package_manager import compare_opkg_versions
    versions=query_versions(manager.command_runner,cancel)
    version=versions.get('libcurl4',''); current=versions.get('curl','')
    if not version or (current and not compare_opkg_versions(manager.command_runner,current,'>',version,cancel)):
        raise SignalError('environment','\u65e0\u6cd5\u786e\u8ba4\u53ef\u5b89\u5168\u5339\u914d\u7684 curl \u7248\u672c\uff0c\u672a\u4fee\u6539\u8def\u7531\u5668\u3002')
    try: name,url=candidate_url(device,version)
    except ValueError as exc: raise SignalError('environment','\u5f53\u524d\u56fa\u4ef6\u4e0d\u80fd\u786e\u8ba4\u5b98\u65b9\u914d\u5957\u5305\uff0c\u672a\u4fee\u6539\u8def\u7531\u5668\u3002') from exc
    data = _local_candidate(device, version, cancel) if allow_local_bundle else None
    if data is None:
        tell('\u672c\u5730\u79bb\u7ebf\u5305\u6ca1\u6709\u5339\u914d\u73b0\u6709\u8fd0\u884c\u5e93\u7684 curl\uff0c\u6b63\u5728\u5c1d\u8bd5\u8054\u7f51\u83b7\u53d6\u3002','info')
        from .dependency_http import DownloadHTTPError
        from .signal_package_feed import PackageDownloadUnavailable
        try:
            with download_session(tell): data=fetch_bytes(url,timeout=60,cancel_event=cancel,limit=4*1024*1024)
        except DownloadHTTPError as exc:
            if exc.code in (404, 410):
                raise PackageDownloadUnavailable(str(exc)) from exc
            raise
    else:
        tell('\u6b63\u5728\u4f7f\u7528\u79bb\u7ebf\u5305\u4e2d\u4e0e\u73b0\u6709\u8fd0\u884c\u5e93\u7248\u672c\u5339\u914d\u7684 curl\u3002','info')
    try: binary=extract_candidate(data,version,device.package_architecture)
    except (ValueError,tarfile.TarError,UnicodeError) as exc: raise SignalError('environment','\u5019\u9009\u5b89\u88c5\u5305\u65e0\u6cd5\u6821\u9a8c\uff0c\u672a\u4fee\u6539\u8def\u7531\u5668\u3002') from exc
    pm=manager._preflight(512,cancel)
    if pm.name!='opkg': raise SignalError('environment','\u5305\u7ba1\u7406\u5668\u4e0d\u5339\u914d\u3002')
    remote='/tmp/luxiaoguan-curl-'+uuid4().hex
    manager._execute_stage('curl_repair_mkdir','umask 077; mkdir '+q(remote),15,cancel,'\u51c6\u5907\u4fee\u590d\u76ee\u5f55')
    try:
        with tempfile.TemporaryDirectory(prefix='curl-repair-') as local:
            root=Path(local); package=root/name; executable=root/'curl'; package.write_bytes(data);executable.write_bytes(binary)
            upload=manager._package_uploader(bool(getattr(device,'sftp_available',False)),cancel,tell)
            upload(str(package),remote+'/'+name);upload(str(executable),remote+'/curl')
        expected=hashlib.sha256(data).hexdigest(); binary_hash=hashlib.sha256(binary).hexdigest()
        command='test "$(sha256sum '+q(remote+'/'+name)+' | cut -d " " -f 1)" = '+q(expected)+' && test "$(sha256sum '+q(remote+'/curl')+' | cut -d " " -f 1)" = '+q(binary_hash)+' && chmod 700 '+q(remote+'/curl')+' && '+q(remote+'/curl')+' --version'
        output=manager._execute_stage('curl_repair_trial',command,20,cancel,'\u8bd5\u8fd0\u884c\u914d\u5957 curl')
        if not re.search(r'(?im)^curl '+re.escape(version.split('-r')[0])+r'\b',output) or not re.search(r'(?im)^Protocols:.*\bhttps\b',output):
            raise SignalError('environment','\u5019\u9009 curl \u672a\u901a\u8fc7\u8bd5\u8fd0\u884c\uff0c\u672a\u4fee\u6539\u5df2\u5b89\u88c5\u8f6f\u4ef6\u3002')
        config=('umask 077; mkdir '+q(remote+'/no-feeds')+' && { '
                +"awk '$1 == \"dest\" || ($1 == \"option\" && $2 == \"overlay_root\") { print }' /etc/opkg.conf; "
                +'opkg print-architecture; printf '+q('lists_dir ext '+remote+'/no-feeds\n')
                +'; } > '+q(remote+'/opkg.conf'))
        manager._execute_stage('curl_repair_config',config,20,cancel,'\u51c6\u5907\u79bb\u7ebf\u5b89\u88c5\u914d\u7f6e')
        cmd='OPKG_CONF_DIR='+q(remote+'/no-feeds')+' opkg -f '+q(remote+'/opkg.conf')+(' --force-downgrade ' if current else ' ')
        plan=manager._execute_stage('curl_repair_plan',cmd+'--noaction install '+q(remote+'/'+name),90,cancel,'\u9884\u6f14 curl \u4fee\u590d')
        try: verify_plan(plan,version,installed=bool(current))
        except ValueError as exc: raise SignalError('environment','\u9884\u6f14\u53ef\u80fd\u5f71\u54cd\u5176\u4ed6\u8f6f\u4ef6\uff0c\u5df2\u505c\u6b62\u4fee\u590d\u3002') from exc
        manager._execute_stage('curl_repair_install',cmd+'install '+q(remote+'/'+name),180,cancel,'\u5b89\u88c5\u5339\u914d\u7684 curl')
    finally:
        from .offline_cleanup import cleanup_remote
        cleanup_remote(manager._execute,'curl_repair_cleanup','rm -rf '+q(remote),tell)
