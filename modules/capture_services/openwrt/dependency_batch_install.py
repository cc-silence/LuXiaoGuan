"""Independent package transactions with bounded partial-failure reporting."""
from .signal_manager import SignalError
from .models import DependencyState
from .hcx_policy import installation_satisfied

def must_stop(error, runner, cancel, connection):
    if cancel is not None and cancel.is_set():
        return True
    ssh = getattr(runner, 'ssh_client', None)
    if ssh is not None and (ssh.connected is False or getattr(ssh, '_client', None) is not connection):
        return True
    if not isinstance(error, SignalError) or error.code in {'cancelled','internal','disconnected'}:
        return True
    text = (str(error) + '\n' + getattr(error, 'package_diagnostic', '')).lower()
    markers = ('signature', 'checksum', 'untrusted', 'database is locked', 'unable to lock',
               'could not lock', 'no space left', 'read-only file system', 'not root',
               '\u7b7e\u540d', '\u6821\u9a8c\u5931\u8d25', '\u7a7a\u95f4', '\u6743\u9650',
               '\u8d85\u65f6', 'timed out', 'channel closed')
    return any(marker in text for marker in markers)

def install_packages(manager, checked, device, cache_dir, sftp_available, cancel, tell, uploader=None):
    from .signal_package_feed import download_session
    with download_session(tell):
        return _install_packages(manager, checked, device, cache_dir, sftp_available, cancel, tell, uploader)


def _install_packages(manager, checked, device, cache_dir, sftp_available, cancel, tell, uploader=None):
    from .common_bundle_installer import install_from_local
    from .installation_sources import install_with_sources, report_verified
    runner = manager.command_runner
    ssh = getattr(runner, 'ssh_client', None)
    connection = getattr(ssh, '_client', None)
    failures, sources = [], []
    initial = checked()
    wanted = list(manager._missing_packages(initial))
    owners = {name: {item.key for item in initial if name in item.missing_packages} for name in wanted}
    completed = set()

    def failed(package, error):
        if must_stop(error, runner, cancel, connection):
            raise error
        failures.append((package, error))
        tell(package + '\uff1a' + str(error) + '\uff1b\u7ee7\u7eed\u5176\u4ed6\u72ec\u7acb\u9879\u76ee\u3002', 'warning')

    if 'hcxdumptool' in wanted:
        try:
            if manager._try_install_modern_hcxdumptool(device,sftp_available,cancel,tell,cache_dir,uploader):
                completed.add('hcxdumptool')
        except Exception as error:
            failed('hcxdumptool', error)
            completed.add('hcxdumptool')
    for index, package in enumerate(wanted):
        if cancel is not None and cancel.is_set():
            raise SignalError('cancelled', '\u4f9d\u8d56\u5b89\u88c5\u5df2\u53d6\u6d88\u3002')
        if ssh is not None and (ssh.connected is False or getattr(ssh,'_client',None) is not connection):
            raise SignalError('disconnected', '\u8fde\u63a5\u5df2\u53d8\u5316\uff0c\u5df2\u505c\u6b62\u5b89\u88c5\u3002')
        if index and package not in set(manager._missing_packages(checked())):
            continue
        if package in completed:
            continue
        batch = (package,)
        try:
            source = install_with_sources(
                lambda: install_from_local(manager,batch,device,cache_dir,sftp_available,cancel,tell,uploader),
                lambda: manager._install_online(batch,cancel,tell),
                lambda: manager._install_offline(batch,device,cache_dir,sftp_available,cancel,tell,uploader), tell)
            if source not in sources:
                sources.append(source)
        except Exception as error:
            failed(package, error)
    result = checked(final=True)
    ready = all(installation_satisfied(item) for item in result if item.key != 'converter')
    report_verified(' / '.join(sources), ready, tell)
    if failures:
        # A later independent transaction may have supplied a previously missing library.
        failures = [(name,error) for name,error in failures if not all(
            any(item.key==key and installation_satisfied(item) for item in result)
            for key in owners[name])]
    if failures:
        error = SignalError('partial_install', '\u5df2\u7ee7\u7eed\u5904\u7406\u5176\u4ed6\u9879\u76ee\uff1b\u4ee5\u4e0b\u4f9d\u8d56\u4ecd\u672a\u5b89\u88c5\u6210\u529f\uff1a'
                            + '\u3001'.join(name for name,_ in failures))
        from modules.log_services import sanitized_text
        from modules.display_privacy import Masker
        detail = '\n\n'.join(name+'\n'+(getattr(exc,'package_diagnostic','') or str(exc))[-4096:]
                             for name,exc in failures)[:32768]
        error.package_diagnostic = sanitized_text(detail, Masker())
        raise error
    return result
