"""ZIP projection of frozen run history, using the shared export budget."""
import json
from datetime import datetime, timezone
import re

def history_attachments(store, category, device=None, masker=None):
    from modules.log_services import sanitized_text, _router_context_bytes
    if masker is None:
        from modules.log_history import HistoryMasker
        masker = HistoryMasker()
    if store is None:
        return []
    try:
        snapshot = store.snapshot(category, device)
    except (OSError, TimeoutError) as exc:
        return [('run-history.json', json.dumps(dict(storage_error=type(exc).__name__)).encode())]
    attachments = []
    runs = snapshot.pop('runs')
    metadata = dict(snapshot, retention_days=7, bounded_streams=True, runs=[])
    for index, run in enumerate(runs):
        prefix = 'current-run' if index == 0 else 'previous-run'
        rows = run.pop('records')
        metadata['runs'].append(dict(run, directory=prefix, empty=not rows))
        streams = {}
        for text,level,channel,kind,device,created in rows:
            directory = prefix + ('/device-' + device[:16] if device else '')
            if kind in ('context', 'attachment'):
                name = re.sub('[^A-Za-z0-9_-]', '_', channel)[:64]
                suffix = '.json' if kind == 'context' else '.txt'
                if kind == 'context':
                    try:
                        data = _router_context_bytes(text,masker)
                    except (ValueError, TypeError):
                        metadata.setdefault('invalid_contexts', []).append(directory + '/' + name + suffix)
                        continue
                else:
                    data = sanitized_text(text,masker).encode('utf-8')
                attachments.append((directory + '/' + name + suffix, data))
            else:
                stamp = datetime.fromtimestamp(created,timezone.utc).isoformat(timespec='seconds')
                streams.setdefault((directory, channel), []).append('[' + stamp + '][' + level + '] ' + sanitized_text(text,masker))
        for (directory, channel), lines in streams.items():
            name = re.sub('[^A-Za-z0-9_-]', '_', channel)[:64] or 'information'
            attachments.append((directory + '/' + name + '.txt', '\n'.join(lines).encode('utf-8')))
    if len(runs) < 2:
        metadata['previous_run_note'] = 'No previous application run within retention period'
    attachments.append(('run-history.json', json.dumps(metadata,ensure_ascii=True,indent=2).encode()))
    return attachments
