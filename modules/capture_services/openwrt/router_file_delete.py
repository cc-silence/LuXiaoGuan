"""Bounded, connection-bound SFTP deletion; never traverse symbolic links."""
import posixpath
import stat
import time

MAX_ENTRIES = 10000
MAX_DEPTH = 64
TIMEOUT = 120


def delete_remote_entry(manager, path, cancel, expected, progress=None):
    """Inspect the tree before deleting children, then remove empty parents."""
    path = manager.path(path)
    if path == '/':
        raise ValueError('\u4e0d\u80fd\u5220\u9664\u6839\u76ee\u5f55\u672c\u8eab\u3002')
    if expected is None:
        raise ValueError('\u8bf7\u5237\u65b0\u5e76\u91cd\u65b0\u786e\u8ba4\u5220\u9664\u76ee\u6807\u3002')
    ssh = manager.ssh
    connection, fingerprint = ssh._client, ssh.host_fingerprint
    deadline = time.monotonic() + TIMEOUT
    removed = 0
    report = progress or (lambda value: None)
    records = []
    directories = {}
    last_report = 0.0

    def check():
        manager.check(cancel, deadline)
        if not ssh.connected or ssh._client is not connection or ssh.host_fingerprint != fingerprint:
            raise ConnectionError('\u8def\u7531\u5668\u8fde\u63a5\u5df2\u65ad\u5f00\u6216\u66f4\u6362\uff0c\u5269\u4f59\u9879\u76ee\u672a\u5220\u9664\u3002')

    def info(target):
        check()
        return sftp.lstat(target)

    def assert_parents(target):
        current = '/'
        for part in posixpath.dirname(target).strip('/').split('/'):
            if not part:
                continue
            current = posixpath.join(current, part)
            value = info(current)
            if not stat.S_ISDIR(value.st_mode):
                raise ValueError('\u7236\u8def\u5f84\u542b\u94fe\u63a5\u6216\u975e\u6587\u4ef6\u5939\uff1a' + current)
            if current in directories and manager.identity(value) != directories[current]:
                raise ValueError('\u6587\u4ef6\u5939\u5df2\u53d8\u5316\uff0c\u8bf7\u5237\u65b0\u540e\u91cd\u8bd5\uff1a' + current)

    def publish(stage, total, name=path, force=False):
        nonlocal last_report
        now = time.monotonic()
        if force or now - last_report >= 0.1:
            report(dict(completed=removed, total=total, name=name, stage=stage))
            last_report = now

    check()
    with ssh.transfer(cancel, timeout=TIMEOUT):
        check()
        sftp = ssh.open_sftp(timeout=10, cancel_event=cancel)
        sftp.get_channel().settimeout(10)
        try:
            assert_parents(path)
            root = info(path)
            if manager.identity(root) != tuple(expected):
                raise ValueError('\u6587\u4ef6\u5df2\u53d8\u5316\uff0c\u8bf7\u5237\u65b0\u540e\u91cd\u65b0\u786e\u8ba4\u3002')
            publish('\u6b63\u5728\u68c0\u67e5\u5220\u9664\u76ee\u6807\u2026', 0, force=True)
            records.append((path, manager.identity(root)))
            stack = [(path, 0)] if stat.S_ISDIR(root.st_mode) else []
            if stack:
                directories[path] = manager.identity(root)
            seen = {path}
            while stack:
                folder, depth = stack.pop()
                assert_parents(folder)
                if manager.identity(info(folder)) != directories[folder]:
                    raise ValueError('\u6587\u4ef6\u5939\u5df2\u53d8\u5316\uff1a' + folder)
                iterator = sftp.listdir_iter(folder, read_aheads=1)
                try:
                    for entry in iterator:
                        check()
                        name = entry.filename
                        if name in ('.', '..'):
                            continue
                        if not name or '/' in name or any(ord(c) < 32 for c in name):
                            raise ValueError('\u76ee\u5f55\u4e2d\u6709\u975e\u6cd5\u6587\u4ef6\u540d\u3002')
                        child = posixpath.join(folder, name)
                        if child in seen:
                            raise ValueError('\u76ee\u5f55\u4e2d\u6709\u91cd\u590d\u9879\u76ee\u3002')
                        if len(records) >= MAX_ENTRIES or depth + 1 > MAX_DEPTH:
                            raise ValueError('\u76ee\u5f55\u8d85\u8fc7\u5220\u9664\u4e0a\u9650\uff0810000 \u9879\u6216 64 \u5c42\uff09\uff0c\u8bf7\u5206\u6279\u5904\u7406\u3002')
                        value = info(child)
                        identity = manager.identity(value)
                        records.append((child, identity))
                        seen.add(child)
                        if stat.S_ISDIR(value.st_mode):
                            directories[child] = identity
                            stack.append((child, depth + 1))
                        publish('\u6b63\u5728\u68c0\u67e5\u6587\u4ef6\u5939\u5185\u5bb9\u2026', 0, child)
                finally:
                    iterator.close()
            # Nothing has been deleted until the complete bounded scan succeeds.
            for target, identity in reversed(records):
                assert_parents(target)
                current = info(target)
                wanted = directories.get(target, identity)
                if manager.identity(current) != wanted:
                    raise ValueError('\u6587\u4ef6\u5df2\u53d8\u5316\uff0c\u505c\u6b62\u5220\u9664\uff1a' + target)
                check()
                if stat.S_ISDIR(current.st_mode):
                    sftp.rmdir(target)
                else:
                    sftp.remove(target)
                removed += 1
                check()
                parent = posixpath.dirname(target)
                if parent in directories:
                    directories[parent] = manager.identity(info(parent))
                publish('\u6b63\u5728\u5220\u9664\u2026', len(records), target, removed == len(records))
            return path
        except Exception as exc:
            # An interrupted READDIR can retain a server handle; reset this SFTP
            # session under its transaction lock, leaving the SSH login intact.
            if ssh._client is connection:
                ssh.drop_sftp()
            if removed:
                message = f'\u5df2\u5220\u9664 {removed} \u4e2a\u5185\u90e8\u9879\u76ee\uff0c\u672a\u5168\u90e8\u5b8c\u6210\uff0c\u5df2\u5220\u9664\u5185\u5bb9\u65e0\u6cd5\u6062\u590d\u3002' + str(exc)
                kind = InterruptedError if isinstance(exc, InterruptedError) else TimeoutError if isinstance(exc, TimeoutError) else RuntimeError
                raise kind(message) from exc
            raise
