"""Deploy the checked selected-ifindex fix without replacing firmware packages."""
import hashlib,json,re,struct
from pathlib import Path
from uuid import uuid4
from .command_builder import quote_argument as q
from .command_runner import require_command_completion
from .hcx_policy import supports_modern_hcx

REMOTE='/root/.luxiaoguan-tools/hcx-ifindex1'
ARCHITECTURES={
 'aarch64_cortex-a53':'aarch64','aarch64_cortex-a72':'aarch64',
 'arm_cortex-a15_neon-vfpv4':'arm','arm_cortex-a7_neon-vfpv4':'arm',
 'arm_cortex-a9':'arm','arm_cortex-a9_vfpv3':'arm','arm_cortex-a9_vfpv3-d16':'arm',
 'mips_24kc':'mips','mipsel_24kc':'mipsel','x86_64':'x86_64',
}


def local_variant(device,root=None):
    from modules.config_manager import BASE_DIR
    root=Path(root) if root is not None else BASE_DIR/'bin/hcx_compat'
    arch=ARCHITECTURES.get(str(getattr(device,'package_architecture','')))
    if arch is None:raise RuntimeError("\u5f53\u524d\u5904\u7406\u5668\u7f3a\u5c11\u5df2\u6838\u5bf9\u7684\u6293\u5305\u517c\u5bb9\u7ec4\u4ef6\uff0c\u672a\u4fee\u6539\u7f51\u5361\u3002")
    metadata=root/'manifest.json'
    if metadata.is_symlink() or metadata.stat().st_size>65536:raise ValueError('Invalid compatibility manifest file')
    manifest=json.loads(metadata.read_text(encoding='utf-8'))
    if manifest.get('schema')!=1 or manifest.get('version')!='6.3.4-rm-ifindex1' or manifest.get('source',{}).get('commit')!='8dad077bfee93281bdf8f1115ba0c047d22230d7':
        raise ValueError('Invalid hcx compatibility manifest')
    row=manifest['variants'][arch]
    if row['file']!=arch+'/hcxdumptool' or not re.fullmatch(r'[a-f0-9]{64}',row['sha256']):
        raise ValueError('Invalid hcx compatibility identity')
    path=root/row['file']
    if type(row.get('size')) is not int or not 0<row['size']<=4*1024*1024 or path.is_symlink() or path.stat().st_size!=row['size']:
        raise ValueError('Invalid compatibility artifact size')
    raw=path.read_bytes()
    if len(raw)!=row['size'] or hashlib.sha256(raw).hexdigest()!=row['sha256'] or len(raw)<64 or raw[:4]!=b'\x7fELF':
        raise ValueError('Invalid hcx compatibility bytes')
    machine={'x86_64':(2,1,62),'aarch64':(2,1,183),'arm':(1,1,40),'mips':(1,2,8),'mipsel':(1,1,8)}[arch]
    endian='little' if raw[5]==1 else 'big'
    if (raw[4],raw[5],int.from_bytes(raw[18:20],endian))!=machine:
        raise ValueError('Wrong hcx compatibility CPU architecture')
    order='<' if raw[5]==1 else '>'
    phoff=struct.unpack_from(order+('Q' if raw[4]==2 else 'I'),raw,32 if raw[4]==2 else 28)[0]
    phsize,phnum=struct.unpack_from(order+'HH',raw,54 if raw[4]==2 else 42)
    if not 0<phnum<=64 or phsize!=(56 if raw[4]==2 else 32) or phoff+phsize*phnum>len(raw):
        raise ValueError('Invalid compatibility ELF program headers')
    for index in range(phnum):
        if struct.unpack_from(order+'I',raw,phoff+phsize*index)[0]==3:
            raise ValueError('Compatibility artifact must not require a dynamic loader')
    return path,row


def ensure_compatible_engine(runner,ssh,device,cancel=None, *, source_bin=None):
    if not supports_modern_hcx(device):return ''
    bound=getattr(ssh,'_client',None);fingerprint=getattr(ssh,'host_fingerprint','')
    def current():
        if cancel is not None and cancel.is_set():raise InterruptedError('Compatibility preparation cancelled')
        if not ssh.connected or not fingerprint or ssh.host_fingerprint!=fingerprint or getattr(ssh,'_client',None) is not bound:
            raise RuntimeError('Connection changed during compatibility preparation')
    def run(name,command,timeout=12):
        current();result=runner.run(name,command,timeout=timeout,cancel_event=cancel)
        require_command_completion(result);current()
        if result.exit_code!=0:raise RuntimeError('Hcx compatibility preparation failed: '+name)
        return result.stdout.strip()
    current()
    if source_bin is None:
        candidate=run('hcx_compat_candidate',
            "if [ -x /root/.winbox-tools/bin/hcxdumptool ] && /root/.winbox-tools/bin/hcxdumptool -h 2>&1 | grep -q -- '-w'; then printf /root/.winbox-tools/bin/hcxdumptool; elif command -v hcxdumptool >/dev/null 2>&1; then command -v hcxdumptool; elif [ -x /root/.winbox-tools/bin/hcxdumptool ]; then printf /root/.winbox-tools/bin/hcxdumptool; else exit 1; fi")
        source_bin=candidate
    # Keep firmware-native old tools and their existing execution path intact.
    help_result=runner.run('hcx_compat_source',q(source_bin)+' -h 2>&1',timeout=8,cancel_event=cancel)
    require_command_completion(help_result);current()
    if help_result.exit_code!=0:raise RuntimeError('Could not verify the installed capture tool version')
    if not re.search(r'\b6\.3\.4(?:[- \r\n]|$)',help_result.stdout or ''):return ''
    path,row=local_variant(device)
    from .capture_takeover import takeover_transaction
    with takeover_transaction(cancel):
        sha=row['sha256'];directory='/root/.luxiaoguan-tools'
        check=('command -v sha256sum >/dev/null 2>&1 || exit 81; '
            'test ! -L '+q(directory)+' && test ! -L '+q(REMOTE)+' || exit 82; '
            'if test -x '+q(REMOTE)+' && test "$(sha256sum '+q(REMOTE)+' | cut -c1-64)" = '+q(sha)+'; then printf READY; else printf MISSING; fi')
        state=run('hcx_compat_check',check)
        if state=='READY':return REMOTE
        if state!='MISSING':raise RuntimeError('Invalid compatibility cache response')
        need=(row['size']+1023)//1024+1024
        run('hcx_compat_prepare','test ! -L '+q(directory)+' && mkdir -p '+q(directory)+' && chmod 700 '+q(directory)+' && '
            "free=$(df -kP /root | awk 'NR==2 {print $4}'); case \"$free\" in ''|*[!0-9]*) exit 83;; esac; [ \"$free\" -ge "+str(need)+' ]')
        stage=directory+'/.hcx-ifindex1-'+uuid4().hex
        from .workspace import CaptureWorkspaceManager
        primary=None
        try:
            current();CaptureWorkspaceManager(ssh,runner).upload_atomic(path,stage,cancel_event=cancel);current()
            run('hcx_compat_publish','test ! -L '+q(directory)+' && test ! -L '+q(stage)+' && '
                'test "$(sha256sum '+q(stage)+' | cut -c1-64)" = '+q(sha)+' && '
                'chmod 700 '+q(stage)+' && '+q(stage)+" -h 2>&1 | grep -q '6.3.4-rm-ifindex1' && "+
                'mv -f '+q(stage)+' '+q(REMOTE)+' && printf READY',timeout=20)
        except Exception as exc:
            primary=exc
            raise
        finally:
            if ssh.connected and getattr(ssh,'_client',None) is bound and ssh.host_fingerprint==fingerprint:
                try:
                    cleanup=runner.run('hcx_compat_stage_cleanup','rm -f '+q(stage)+' '+q(stage+'.part'),timeout=8)
                    require_command_completion(cleanup)
                    if cleanup.exit_code!=0:raise RuntimeError('Compatibility staging cleanup unconfirmed')
                except Exception as cleanup_error:
                    journal=getattr(runner,'operation_diagnostics',None)
                    if journal is not None:journal.record('recovery',operation='hcx_compat_stage_cleanup',status='failed',exception_type=type(cleanup_error).__name__)
                    if primary is None:raise
        if run('hcx_compat_verify',check)!='READY':raise RuntimeError('Compatibility tool verification failed')
        return REMOTE
