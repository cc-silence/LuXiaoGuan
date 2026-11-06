"""Password outcomes live in AP snapshots, separate from captured-file state."""
from dataclasses import replace
from .openwrt.password_events import PasswordTargetUpdate, target_update_for

STATES = frozenset({'queued','verifying','found','no_match','timeout','unconfirmed',
                    'cancelled','not_started','history_skipped','round_pending','skipped','failed'})


def identity(point):
    return (point.bssid.upper(), point.ssid)


def apply_update(service, event):
    if event.state not in STATES:
        return
    snapshot = service.openwrt_backend.snapshot
    points = tuple(replace(p, password_state=event.state, password_note=event.note)
                   if identity(p)==identity(event) and (p.password_state!='found' or event.state=='found')
                   else p for p in snapshot.access_points)
    if points != snapshot.access_points:
        service.openwrt_backend.snapshot = replace(snapshot, access_points=points)
        service.openwrtSnapshotChanged.emit(service.openwrt_backend.snapshot)


def begin(service, targets):
    keys = {identity(p) for p in targets}
    service._password_target_keys = keys
    snapshot = service.openwrt_backend.snapshot
    points = tuple(replace(p,password_state='queued',password_note='')
                   if identity(p) in keys and p.password_state!='found' else p
                   for p in snapshot.access_points)
    service.openwrt_backend.snapshot = replace(snapshot,access_points=points)


def finish(service, cancelled=False):
    keys = getattr(service,'_password_target_keys',set())
    snapshot = service.openwrt_backend.snapshot
    points=[]
    for point in snapshot.access_points:
        if identity(point) in keys:
            state = point.password_state
            if state == 'queued':state = 'not_started'
            elif state in {'verifying','round_pending'}:state = 'cancelled' if cancelled else 'failed'
            if state != point.password_state:
                point=replace(point,password_state=state,password_note='')
        points.append(point)
    service.openwrt_backend.snapshot = replace(snapshot,access_points=tuple(points))
    counts={state:0 for state in STATES}
    for p in points:
        if identity(p) in keys and p.password_state in counts:counts[p.password_state]+=1
    service._password_target_keys=set()
    labels=(('found','\u627e\u5230\u5bc6\u7801'),('no_match','\u672a\u627e\u5230'),
            ('timeout','\u8d85\u65f6'),('unconfirmed','\u672a\u786e\u8ba4'),
            ('history_skipped','\u5386\u53f2\u8df3\u8fc7'),('cancelled','\u5df2\u53d6\u6d88'),
            ('not_started','\u672a\u5f00\u59cb'),('skipped','\u5df2\u8df3\u8fc7'),('failed','\u5931\u8d25'))
    return '\uff1b'.join(label+' '+str(counts[state])+' \u4e2a' for state,label in labels if counts[state])


def scan_states(points):
    return {identity(p):(p.password_state,p.password_note) for p in points if p.password_state}


def restore_scan(point, states):
    if point.password_state == 'found':
        return point
    saved = states.get(identity(point))
    return replace(point,password_state=saved[0],password_note=saved[1]) if saved else point




def complete(service, results):
    updates={identity(e):target_update_for(e,e) for e in results}
    snapshot=service.openwrt_backend.snapshot
    points=[]
    for point in snapshot.access_points:
        update=updates.get(identity(point))
        if update is not None and (point.password_state!='found' or update.state=='found'):
            point=replace(point,password_state=update.state,password_note=update.note)
        points.append(point)
    service.openwrt_backend.snapshot=replace(snapshot,access_points=tuple(points))


def clear_records(service):
    """Forget password UI outcomes after explicit record clearing, not capture state."""
    snapshot=service.openwrt_backend.snapshot
    points=tuple(replace(point,password_state='',password_note='') for point in snapshot.access_points)
    service.openwrt_backend.snapshot=replace(snapshot,access_points=points)
    service.openwrtSnapshotChanged.emit(service.openwrt_backend.snapshot)


def pending_password_targets(points, selected_bssids):
    """Keep selected targets that have no explicit success state."""
    selected = {str(value).upper() for value in selected_bssids}
    return tuple(point for point in points
                 if point.bssid.upper() in selected and point.password_state != 'found')
