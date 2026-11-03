"""Install only the built-in roots, after downloaded bundles did not match."""
import tempfile
from pathlib import Path
from uuid import uuid4
from .builtin_base_repository import BuiltinBaseRepository, ROOTS
from .signal_package_feed import parse_installed, default_cache_dir
from .package_manager import compare_opkg_versions
from .command_builder import quote_argument as q
from .signal_manager import SignalError

def install_builtin(manager, wanted, device, cache_dir, sftp_available, cancel, tell, uploader=None):
    wanted = tuple(wanted)
    if not wanted or not set(wanted).issubset(ROOTS):
        return False
    if cancel is not None and cancel.is_set():
        raise SignalError('cancelled', 'Installation cancelled')
    repository = BuiltinBaseRepository()
    try:
        profile = repository.profile(device, cancel)
        if profile is None:
            return False
        cache = Path(cache_dir or default_cache_dir())
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='builtin-base-', dir=cache) as directory:
            if profile['package_manager'] == 'opkg':
                installed = parse_installed(manager._execute_stage(
                    'dependency_list_installed', 'opkg list-installed', 30, cancel,
                    '\u8bfb\u53d6\u5df2\u88c5\u8f6f\u4ef6\u5305'))
                compare = lambda a, op, b: compare_opkg_versions(manager.command_runner, a, op, b, cancel)
                try:
                    selected = repository.ipk_selection(profile, wanted, installed, compare)
                except ValueError as exc:
                    tell('\u5185\u7f6e\u5c0f\u5305\u4e0d\u542b\u6240\u9700\u914d\u5957\u5e93\uff0c\u7ee7\u7eed\u5176\u4ed6\u5b89\u88c5\u8def\u7ebf\uff1a' + str(exc), 'warning')
                    return False
                rows = {r['name']: r for r in profile['packages']}
                paths = tuple(repository.materialize(rows[p.name], directory, cancel) for p in selected)
                if paths:
                    tell('\u4f7f\u7528\u8f6f\u4ef6\u5185\u7f6e\u7684\u57fa\u7840\u79bb\u7ebf\u5305\uff0c\u65e0\u9700\u4e0b\u8f7d\u3002', 'info')
                    manager._install_offline(wanted, device, cache, sftp_available, cancel, tell, uploader,
                        local_paths=paths, local_packages={p.name:p.version for p in selected})
            else:
                rows = [r for r in profile['packages'] if r['name'] in wanted]
                paths = [repository.materialize(r, directory, cancel) for r in rows]
                install_apk_files(manager, rows, paths, sftp_available, cancel, tell, uploader)
        return True
    except InterruptedError as exc:
        raise SignalError('cancelled', str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise SignalError('environment', 'Built-in dependency resource: ' + str(exc)) from exc

def install_apk_files(manager, rows, paths, sftp_available, cancel, tell, uploader=None):
    """No indexes, network, forced replacements or disabled signature checks."""
    from .package_space import required_kb
    from .apk_plan import parse_actions
    from .offline_cleanup import cleanup_remote
    package_manager = manager._preflight(required_kb(r['installed_size'] for r in rows), cancel)
    if package_manager.name != 'apk':
        raise ValueError('Package manager changed')
    remote = '/tmp/luxiaoguan-base.' + uuid4().hex
    manager._execute_stage('builtin_mkdir', 'umask 077; mkdir ' + q(remote), 20, cancel,
                           '\u51c6\u5907\u5185\u7f6e\u5305\u5b89\u88c5')
    try:
        upload = uploader or manager._package_uploader(sftp_available, cancel, tell)
        arguments = []
        for row, path in zip(rows, paths):
            if cancel is not None and cancel.is_set():
                raise SignalError('cancelled', 'Installation cancelled')
            target = remote + '/' + row['filename']
            upload(str(path), target)
            manager._execute_stage('builtin_digest',
                "printf '%s\\n' " + q(row['sha256'] + '  ' + target) + ' | sha256sum -c -',
                20, cancel, '\u6821\u9a8c\u5185\u7f6e\u5305')
            arguments.append(q(target))
        command = 'apk --no-network --no-cache --repositories-file /dev/null add '
        plan = manager._execute_stage('builtin_apk_plan', command + '--simulate ' + ' '.join(arguments),
                                      60, cancel, '\u9884\u6f14\u5185\u7f6e\u5305\u5b89\u88c5')
        expected = {r['name']:r['version'] for r in rows}
        package_manager.verify_plan(plan, expected)
        actions = parse_actions(plan)
        if any(verb != 'Installing' or expected.get(name) != version for verb, name, version in actions):
            raise ValueError('Built-in APK plan changes packages outside the verified roots')
        if cancel is not None and cancel.is_set():
            raise SignalError('cancelled', 'Installation cancelled')
        if actions:
            tell('\u4f7f\u7528\u8f6f\u4ef6\u5185\u7f6e\u7684\u57fa\u7840\u79bb\u7ebf\u5305\uff0c\u65e0\u9700\u4e0b\u8f7d\u3002', 'info')
            manager._execute_stage('builtin_apk_install', command + ' '.join(arguments),
                                   180, cancel, '\u5b89\u88c5\u5185\u7f6e\u57fa\u7840\u7ec4\u4ef6')
    finally:
        cleanup_remote(manager._execute, 'builtin_cleanup', 'rm -rf ' + q(remote), tell)
