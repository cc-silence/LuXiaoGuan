"""Recover exact originals from the router's signed opkg feeds."""
import gzip
import io
import re
import shlex
from urllib.parse import urlsplit

from .command_builder import quote_argument as q
from .dependency_snapshot_archive import MAX_PACKAGE

MAX_INDEX = 32 * 1024 * 1024


def feed_urls(config):
    urls = []
    for line in config.splitlines():
        try:
            parts = shlex.split(line, comments=True)
        except ValueError:
            raise ValueError('Invalid configured package source') from None
        if not parts or parts[0] not in ('src', 'src/gz'):
            continue
        if len(parts) != 3:
            raise ValueError('Unsupported configured package source')
        parsed = urlsplit(parts[2])
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise ValueError('Configured package source must use HTTPS')
        item = (parts[2].rstrip('/'), parts[0] == 'src/gz')
        if item not in urls:
            urls.append(item)
    if not urls or len(urls) > 64:
        raise ValueError('No usable configured package sources')
    return urls


def exact_rows(index, name, version):
    rows = []
    for stanza in index.split('\n\n'):
        fields = dict(line.split(':', 1) for line in stanza.splitlines()
                      if ':' in line and not line[:1].isspace())
        fields = {key: value.strip() for key, value in fields.items()}
        if fields.get('Package') != name or fields.get('Version') != version:
            continue
        filename, digest, size = (fields.get('Filename', ''), fields.get('SHA256sum', ''), fields.get('Size', ''))
        if (not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.+~-]*\.ipk', filename)
                or not re.fullmatch(r'[0-9a-fA-F]{64}', digest)
                or not size.isdigit() or not 0 < int(size) <= MAX_PACKAGE):
            raise ValueError('Invalid exact package metadata')
        rows.append((filename, digest.lower(), int(size)))
    return rows


def fetch_configured_original(source, pkg):
    from .signal_package_feed import download_session, fetch_bytes
    from .dependency_snapshot_archive import digest_file
    task = source.task
    config = task.reader.run('original_feeds',
        'cat /etc/opkg.conf; for f in /etc/opkg/*.conf; do test ! -f "$f" || cat "$f"; done', 30)
    feeds = feed_urls(config)
    root = source.local / 'configured-feeds'
    root.mkdir(exist_ok=True)
    matches = []
    with download_session():
        for number, (base, compressed) in enumerate(feeds):
            task.guard.check()
            try:
                raw = fetch_bytes(base + ('/Packages.gz' if compressed else '/Packages'),
                                  timeout=30, cancel_event=task.guard.cancel, limit=MAX_INDEX)
                signature = fetch_bytes(base + '/Packages.sig', timeout=30,
                                        cancel_event=task.guard.cancel, limit=65536)
                if compressed:
                    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                        raw = stream.read(MAX_INDEX + 1)
                if len(raw) > MAX_INDEX:
                    raise ValueError('Package index exceeds size limit')
                index_file = root / ('index-' + str(number))
                signature_file = root / ('signature-' + str(number))
                index_file.write_bytes(raw)
                signature_file.write_bytes(signature)
                remote_index = source.remote + '/feed-index'
                remote_signature = source.remote + '/feed-signature'
                task.transfer.upload(index_file, remote_index)
                task.transfer.upload(signature_file, remote_signature)
                task.reader.run('original_signature', 'opkg-key verify ' + q(remote_signature) + ' ' + q(remote_index), 30)
                for filename, digest, size in exact_rows(raw.decode('utf-8'), pkg.name, pkg.version):
                    matches.append((base, filename, digest, size))
            except (InterruptedError, TimeoutError):
                raise
            except Exception:
                task.guard.check()
                continue
        if not matches:
            raise ValueError('No signed configured source contains the exact installed package: ' + pkg.name + ' ' + pkg.version)
        installed_hash = str(pkg.metadata.get('SHA256sum', '')).lower()
        if installed_hash and any(row[2] != installed_hash for row in matches):
            raise ValueError('Original package differs from installed package metadata')
        if len({(row[2], row[3]) for row in matches}) != 1:
            raise ValueError('Conflicting exact package versions in configured sources')
        errors = []
        for base, filename, digest, size in matches:
            try:
                raw = fetch_bytes(base + '/' + filename, timeout=60,
                                  cancel_event=task.guard.cancel, limit=MAX_PACKAGE)
                path = source.local / ('configured-original-' + pkg.name + '.ipk')
                path.write_bytes(raw)
                if len(raw) != size or digest_file(path, task.guard.check) != digest:
                    raise ValueError('Original package checksum mismatch')
                source.provenance[pkg.name] = dict(source=base + '/' + filename,
                                                  version=pkg.version, sha256=digest)
                return path
            except (InterruptedError, TimeoutError):
                raise
            except Exception as exc:
                errors.append(str(exc))
        raise ValueError('Unable to retrieve verified original: ' + '; '.join(errors[:3]))
