"""Initial Wi-Fi replacement: submit once, retain settings, verify on user login."""
import copy
import hashlib
import json
import re
import secrets
from threading import Event
from .initial_reconnect_store import ReconnectStore,user_reconnect_required
from ..configuration_progress import phase


def _operations(review):
    return copy.deepcopy(review['operations'])+[dict(action='delete_section',config='wireless',section=s)
                                                 for s in review.get('delete_defaults',[])]


def _recovery(owner,snapshot,operations):
    from ..lan_client_recovery import recovery_for
    before=snapshot['configs']['network']['lan'];wanted=copy.deepcopy(before)
    for op in operations:
        if op.get('config')=='network' and op.get('section')=='lan':wanted.update(op.get('values',{}))
    return recovery_for(dict(values=before,client_network=owner._client_network),wanted)


def _cleanup_backup(owner,receipt,cancel,state):
    path=receipt.get('file_backup')
    if not path:return
    if not re.fullmatch(r'/root/luxiaoguan-policy\.[A-Za-z0-9]+',path):raise ValueError('Invalid recovery directory')
    from .command_builder import quote_argument
    quoted=quote_argument(path)
    check=owner._run('initial_cleanup_probe','if test -e '+quoted+' || test -L '+quoted+'; then printf present; else printf absent; fi',cancel,8).strip()
    if check=='absent':return
    if check!='present':raise RuntimeError('Recovery directory inspection failed')
    note=owner._cleanup_file_backup(path,receipt['file_token'],state)
    if note:raise RuntimeError(note)


def apply_initial(owner,review,snapshot,cancel,report,backup_note='',store=None):
    if not review.get('await_reconnect') or not user_reconnect_required(review):
        raise ValueError('Invalid user-paced initialization')
    from ..lan_client_recovery import attach_inventory
    live=attach_inventory(dict(review),owner.ssh)
    if not user_reconnect_required(live):
        raise RuntimeError('\u8fde\u63a5\u65b9\u5f0f\u5df2\u53d8\u5316\uff0c\u8bf7\u91cd\u65b0\u9884\u89c8\u521d\u59cb\u5316\u8bbe\u7f6e\u3002')
    routes=lambda r:[a['id'] for a in r['client_network']['adapters'] if a.get('route')]
    if routes(live)!=routes(review):raise RuntimeError('\u7ba1\u7406\u7f51\u5361\u5df2\u53d8\u5316\uff0c\u8bf7\u91cd\u65b0\u9884\u89c8\u3002')
    owner._client_network=live['client_network']
    operations=_operations(review)
    from modules.platform_services.static_network import for_recovery
    migration=for_recovery(_recovery(owner,snapshot,operations))
    store=store or ReconnectStore()
    ap={k:review['new_ap'][k] for k in ('section','radio','ssid','mac')}
    receipt=dict(schema=1,fingerprint=owner.fingerprint,target=review.get('new_address') or owner._apply_host,
                 state='prepared',operations=operations,ap=ap,service=review.get('service'),files={},file_backup=None,
                 file_token=None,migration_plan=migration.plan if migration else None)
    before=copy.deepcopy(snapshot['configs']['network']['lan']);after=copy.deepcopy(before)
    for op in operations:
        if op.get('config')=='network' and op.get('section')=='lan':after.update(op.get('values',{}))
    if before.get('ipaddr')!=after.get('ipaddr'):
        receipt.update(lan_before=before,lan_after=after)
    for entry in review.get('files',[]):
        receipt['files'][entry['path']]=hashlib.sha256(entry['content'].encode('utf-8')).hexdigest()
    store.write(receipt,create=True)
    if cancel.is_set():
        store.remove(owner.fingerprint)
        raise InterruptedError("Cancelled before initialization")
    cancel=Event()  # Once mutations start, expected Wi-Fi loss must not cancel completion.
    sent=False;sid=None;file_backup=None
    try:
        # Keep the existing rollback-protected first phase and verify the new AP.
        current=owner._create_ap(review,snapshot,cancel,report)
        if cancel.is_set():raise InterruptedError('Cancelled before final initialization apply')
        if review.get('files'):
            token=secrets.token_hex(8)
            file_backup=owner._call('files_apply',cancel,files=review['files'],token=token)['backup_dir']
            receipt.update(file_backup=file_backup,file_token=token);store.write(receipt)
        sid=owner._call('session',cancel)['sid']
        owner._call('stage',cancel,sid=sid,hashes=current['hashes'],operations=operations,timeout=20)
        if migration:
            migration.prepare(cancel);migration.applying();migration.activate()
        if cancel.is_set():raise InterruptedError('Cancelled before final initialization apply')
        phase('\u6b63\u5728\u63d0\u4ea4\u521d\u59cb\u5316\u8bbe\u7f6e\uff0c\u5b8c\u6210\u540e\u8bf7\u8fde\u63a5\u65b0 Wi-Fi',rollback=False)
        receipt['state']='sent';store.write(receipt)
        if migration:migration.confirming()
        sent=True
        accepted=owner._call('initial_deferred_apply',Event(),sid=sid,hashes=current['hashes'],timeout=15)
        if accepted.get('submitted') is not True:raise RuntimeError('Incomplete initialization response')
        receipt['state']='acknowledged';store.write(receipt)
    except Exception as exc:
        if sent and getattr(exc,'code',None) in {'pending','changed','values'}:
            # These guards execute before the unchecked native apply call.
            sent=False
            if migration:migration.router_restored()
        if not sent:
            if migration:migration.rollback()
            if file_backup:
                restored=owner._call('files_restore',Event(),backup_dir=file_backup)
                if restored.get('restored') is not True:raise RuntimeError('Initialization file restoration not verified')
                _cleanup_backup(owner,receipt,Event(),'restored')
            store.remove(owner.fingerprint)
            raise
        # A lost reply is not proof of failure. Retain all evidence and never replay.
    finally:
        if sid and not sent:
            try:owner._call('discard',Event(),sid=sid,timeout=5)
            except Exception:pass
    acknowledged=receipt['state']=='acknowledged'
    note=('\u521d\u59cb\u5316\u914d\u7f6e\u5df2\u63d0\u4ea4\uff0c\u7b49\u5f85\u91cd\u65b0\u8fde\u63a5\u6838\u9a8c\u3002' if acknowledged else
          '\u521d\u59cb\u5316\u8bf7\u6c42\u5df2\u53d1\u51fa\uff0c\u56de\u6267\u672a\u786e\u8ba4\uff0c\u8bf7\u91cd\u8fde\u540e\u6838\u9a8c\uff0c\u4e0d\u8981\u91cd\u590d\u63d0\u4ea4\u3002')
    note+='\n\u672c\u6b21\u4e0d\u4f1a\u56e0\u91cd\u8fde\u8d85\u65f6\u81ea\u52a8\u6062\u590d\u65e7\u914d\u7f6e\uff0c\u4e0d\u9700\u8981\u8d76\u5012\u8ba1\u65f6\u3002'
    note+='\nWi-Fi\uff1a'+ap['ssid']+'\n\u5bc6\u7801\uff1a'+review['new_ap']['password']
    note+='\n\u767b\u5f55\u5730\u5740\uff1a'+receipt['target']+'\n\u8fde\u63a5\u65b0 Wi-Fi \u540e\uff0c\u8bf7\u5728\u8f6f\u4ef6\u4e2d\u91cd\u65b0\u767b\u5f55\u8def\u7531\u5668\uff0c\u5c06\u81ea\u52a8\u6838\u9a8c\u3002'+backup_note
    return dict(outcome='reconnect',saved=acknowledged,applied=False,await_reconnect=True,note=note,review=review)


def _complete_static(plan):
    if not plan:return
    from modules.platform_services.static_network import StaticMigration,transition
    tx=StaticMigration(plan)
    if not tx.path.exists():
        # Cleanup may have finished before a later file-cleanup step was interrupted.
        state=tx.driver.read();row=plan['adapter']
        addresses=sorted(set(a for a in row['addresses'] if a!=plan['old_address']) | {plan['new_address']})
        changed=lambda values:list(dict.fromkeys(plan['new_router'] if a==plan['old_router'] else a for a in values))
        if state['addresses']!=addresses or state['gateways']!=changed(row['gateways']) or state['dns']!=changed(row['dns']):
            raise RuntimeError('Static migration receipt is missing; not changing the adapter')
        tx._verify_live(state);return
    with tx.path.open('rb') as stream:raw=stream.read(65537)
    if len(raw)>65536:raise ValueError('Invalid static migration receipt')
    saved=json.loads(raw)
    if saved.get('schema')!=1 or saved.get('plan')!=plan:raise ValueError('Static migration receipt mismatch')
    tx.baseline=saved['baseline'];tx.expected=saved['expected'];tx.pending=saved['pending'];tx.record_owned=True
    tx.router_confirmed=bool(saved.get('router_confirmed'));tx.confirmation_pending=True
    actual=tx.driver.read()
    final=copy.deepcopy(tx.expected)
    final['addresses']=sorted(set(a for a in final['addresses'] if a!=plan['old_address']) | {plan['new_address']})
    if actual==final:
        tx._verify_live(final);tx.path.unlink();return
    if actual!=tx.expected:
        if tx.pending!=final or not transition(actual,tx.expected,final):
            raise RuntimeError('\u7535\u8111\u7f51\u7edc\u5df2\u53d8\u5316\uff0c\u672a\u8986\u76d6\uff1b\u8bf7\u6838\u5bf9\u7535\u8111 IP\u3002')
        tx.expected=actual
    tx.commit()


def verify_pending(owner,cancel,store=None):
    store=store or ReconnectStore();receipt=store.read(owner.fingerprint)
    if receipt is None:return None
    if receipt['state'] not in {'sent','acknowledged'}:
        raise RuntimeError('\u4e0a\u6b21\u521d\u59cb\u5316\u5728\u63d0\u4ea4\u524d\u4e2d\u65ad\uff0c\u8bb0\u5f55\u5df2\u4fdd\u7559\uff0c\u8bf7\u5148\u6838\u5bf9\u5f53\u524d\u914d\u7f6e\u3002')
    owner._current()
    if cancel.is_set():raise InterruptedError('Reconnect verification cancelled')
    ready=owner._call('policy_applied',cancel,operations=receipt['operations'],timeout=15)
    if ready.get('ready') is not True:raise RuntimeError('\u521d\u59cb\u5316\u914d\u7f6e\u5c1a\u672a\u901a\u8fc7\u6838\u9a8c\uff0c\u8bb0\u5f55\u5df2\u4fdd\u7559\u3002')
    if receipt.get('lan_after'):
        from .configuration_runtime import network_ready
        if not network_ready(ready.get('network_state'),receipt['lan_after'],receipt['lan_before']):
            raise RuntimeError('\u65b0 LAN \u5730\u5740\u7684\u8fd0\u884c\u72b6\u6001\u5c1a\u672a\u901a\u8fc7\u6838\u9a8c\uff0c\u8bb0\u5f55\u5df2\u4fdd\u7559\u3002')
    snapshot=owner.read(cancel)
    if any(snapshot['files'].get(p)!=h for p,h in receipt['files'].items()):
        raise RuntimeError('\u521d\u59cb\u5316\u6587\u4ef6\u4e0e\u8ba1\u5212\u4e0d\u4e00\u81f4\uff0c\u672a\u786e\u8ba4\u5b8c\u6210\u3002')
    ap=owner._call('ap_ready',cancel,ap=receipt['ap'],timeout=8)
    if ap.get('ready') is not True:raise RuntimeError('\u65b0 Wi-Fi \u53d1\u5c04\u72b6\u6001\u5c1a\u672a\u901a\u8fc7\u6838\u9a8c\uff0c\u8bb0\u5f55\u5df2\u4fdd\u7559\u3002')
    if receipt.get('service'):
        owner._run('upgrade_disable','/etc/init.d/attendedsysupgrade stop && /etc/init.d/attendedsysupgrade disable && ! /etc/init.d/attendedsysupgrade enabled',cancel,15)
    _complete_static(receipt.get('migration_plan'))
    _cleanup_backup(owner,receipt,cancel,'applied')
    owner._current()
    store.remove(owner.fingerprint)
    return dict(verified=True,note='\u521d\u59cb\u5316\u5df2\u5b8c\u6210\uff1a\u5df2\u6838\u5bf9\u539f\u8def\u7531\u5668\u8eab\u4efd\u3001\u914d\u7f6e\u548c\u65b0 Wi-Fi \u8fd0\u884c\u72b6\u6001\u3002')
