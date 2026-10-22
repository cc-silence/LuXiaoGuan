"""从同固件官方包提取兼容工具，不替换系统文件。"""
import hashlib
import gzip
import io
import re
import tarfile
import time
from uuid import uuid4
from .signal_package_feed import normalize_profile, fetch_index, fetch_bytes
from .command_runner import require_command_completion
from .command_builder import quote_argument
from .hcx_capabilities import read_hcx_capabilities, SUPPORTED_HCX_VERSIONS
from .compatible_store import ROOT, prepare_store, read_selection, publish_selection, validate_binary
from .bundled_compatible_tool import BundledTool, match_bundled_tool, verify_modern_interface_api

LIMIT = 8*1024*1024


class _ArchiveReader:
    """限制解压后的总读取量，包括 tar 扩展头。"""
    def __init__(self, stream):
        self.stream=stream
        self.remaining=2*LIMIT+1024*1024
    def read(self, size=-1):
        requested=min(size,self.remaining+1) if size>=0 else self.remaining+1
        value=self.stream.read(requested)
        self.remaining-=len(value)
        if self.remaining<0:
            raise ValueError('工具包解压后超过安全上限，已拒绝安装。')
        return value


def archive_files(blob):
    """只读有界 tar 成员，不解压到文件系统。"""
    files={}
    budget=0
    buffer=io.BytesIO(blob)
    stream=gzip.GzipFile(fileobj=buffer) if blob.startswith(b'\x1f\x8b') else buffer
    with stream, tarfile.open(fileobj=_ArchiveReader(stream),mode='r|') as archive:
        for index, member in enumerate(archive):
            if index>=256: raise ValueError('Archive contains too many members')
            name=member.name
            while name.startswith('./'): name=name[2:]
            if name.startswith('/') or '..' in name.split('/') or member.issym() or member.islnk():
                raise ValueError('Unsafe package member')
            if member.isdir(): continue
            if not member.isfile() or member.size<0 or member.size>LIMIT:
                raise ValueError('Unsupported package member')
            budget+=member.size
            if budget>2*LIMIT or name in files: raise ValueError('Package budget or duplicate member')
            stream=archive.extractfile(member)
            data=stream.read(member.size+1)
            if len(data)!=member.size: raise ValueError('Truncated package')
            files[name]=data
    return files


def unpack_tool(blob, package, profile):
    """校验包身份和 ELF，忽略且不执行安装脚本。"""
    outer=archive_files(blob)
    if outer.get('debian-binary',b'').strip()!=b'2.0': raise ValueError('工具包格式不支持，旧工具未替换。')
    control=archive_files(outer['control.tar.gz'])
    fields={}
    for line in control['control'].decode('utf-8').splitlines():
        if ':' in line:
            key,_,value=line.partition(':'); fields[key]=value.strip()
    if (fields.get('Package')!='hcxdumptool' or fields.get('Version')!=package.version
            or fields.get('Architecture')!=profile.package_arch):
        raise ValueError('工具包版本或架构与当前路由器不匹配，已拒绝安装。')
    data=archive_files(outer['data.tar.gz'])
    binaries=[value for name,value in data.items() if name in {'usr/bin/hcxdumptool','usr/sbin/hcxdumptool'}]
    if len(binaries)!=1 or not binaries[0].startswith(b'\x7fELF'):
        raise ValueError('Package does not contain exactly one ELF tool')
    return binaries[0], fields.get('Depends','')


class CompatibleToolInstaller:
    """独立持久安装，校验与原子发布成功前不改变工具选择。"""
    def __init__(self, backend):
        self.backend=backend
        self.candidate=None

    def check(self, cancel):
        """只读核对固件和官方源，不跨版本找包。"""
        self.candidate=None
        device=self.backend.snapshot.device
        profile=normalize_profile(device.version,device.package_architecture,device.package_manager)
        bundled=match_bundled_tool(device)
        if bundled is not None:
            ssh=self.backend.ssh_client
            self.candidate=(profile,bundled,ssh.host_fingerprint,ssh._client)
            return dict(version=bundled.version,release=profile.release,architecture=profile.package_arch,
                source='\u8def\u5c0f\u7ba1 随包静态工具（上游 6.3.4 源码）',
                note='这份新版工具使用现代无线接口，不需要更换路由器的系统库。\n'
                     '会另装一份，保留原工具。支持被动采集，及手动过滤中勾选目标的主动 PMKID 请求。\n'
                     '安装前后会校验文件并自检；实际无线采集仍需启动验证。')
        deadline=time.monotonic()+120
        def fetch(url, **options):
            remaining=deadline-time.monotonic()
            if remaining <= 0: raise TimeoutError('检查官方软件源超时，请检查电脑网络后重试。')
            options['timeout']=min(20,remaining)
            return fetch_bytes(url, **options)
        index=fetch_index(profile,fetch=fetch,cancel_event=cancel,feeds=("packages",),require_complete=True)
        if not index:
            raise RuntimeError('无法读取官方软件源，未能确认兼容包；请检查电脑网络后重试。')
        package=index.get('hcxdumptool')
        context=f"OpenWrt {profile.release} / {profile.package_arch}"
        if package is None:
            raise RuntimeError(f"已完整读取 {context} 的 packages 官方源，未找到 hcxdumptool 包。旧工具未更改，请暂用常规握手抓包。")
        identity=re.fullmatch(r'(\d+\.\d+\.\d+)-(?:r)?[0-9]+',package.version)
        if identity is None or identity[1] not in SUPPORTED_HCX_VERSIONS:
            raise RuntimeError(f"{context} 的官方源提供 hcxdumptool {package.version}，\u8def\u5c0f\u7ba1 的兼容安装目前适配 6.2.4、6.3.4 和 6.3.5；该版本尚未适配，未执行安装，旧工具未更改。请暂用常规握手抓包。")
        ssh=self.backend.ssh_client
        self.candidate=(profile,package,ssh.host_fingerprint,ssh._client)
        return dict(version=package.version,release=profile.release,architecture=profile.package_arch,
            note=('会另装一份工具，保留路由器原来的工具和系统文件。\n'
                  '工具会永久保存，路由器重启后会重新检查并使用，无需重复安装。\n'
                  + ('注意：找到的是官方旧版，不代表比现有工具更新。安装同版本不能解决无线接口不支持造成的启动失败，安装后仍需验证能否使用。' if identity[1]=='6.2.4' else '安装完成后仍需验证能否使用，不能保证解决无线接口不兼容的问题。')))

    def install(self, cancel):
        """下载校验、并存上传、帮助自检后才切换内存选择。"""
        if not self.candidate: raise RuntimeError('请先检查兼容工具包。')
        profile,package,fingerprint,connection=self.candidate
        ssh=self.backend.ssh_client
        if not fingerprint or ssh.host_fingerprint!=fingerprint or ssh._client is not connection:
            raise RuntimeError('连接已变化，请重新检查兼容包。')
        with ssh.transfer(cancel,timeout=40):
            sftp=ssh.open_sftp(timeout=10,cancel_event=cancel)
            sftp.get_channel().settimeout(10)
            record=read_selection(sftp)
            reusable=bool(record and record.get('release')==profile.release
                and record.get('architecture')==profile.package_arch
                and record.get('version')==package.version.split('-')[0]
                and record.get('package_version')==package.version and record.get('package_sha256')==package.sha256)
            executable=validate_binary(sftp,record,cancel) if reusable else ''
        if reusable:
            capability=read_hcx_capabilities(self.backend.command_runner,cancel,executable=executable)
            if capability.version != record['version']:
                raise ValueError('已安装工具的版本自检失败，旧工具未替换。')
            for band in ('2.4G','5G'):
                capability.capture_options(band,False,True,'capture.bpf')
            if isinstance(package,BundledTool):
                verify_modern_interface_api(self.backend.command_runner,executable,cancel)
            with ssh.transfer(cancel,timeout=30):
                sftp=ssh.open_sftp(timeout=10,cancel_event=cancel)
                sftp.get_channel().settimeout(10)
                if cancel.is_set() or ssh._client is not connection or ssh.host_fingerprint != fingerprint:
                    raise InterruptedError('Installation cancelled or connection changed')
                record['enabled']=True
                publish_selection(sftp,record,cancel)
            if ssh._client is connection and ssh.host_fingerprint == fingerprint:
                self.backend.pmkid_capture.tool_selection=(connection,executable)
            return dict(version=capability.version,path=executable,note='已安装的工具复检通过，已启用；无需重复下载或安装。')
        if isinstance(package,BundledTool):
            binary=package.read()
            dependencies=''
        else:
            blob=fetch_bytes(package.url,timeout=60,cancel_event=cancel,limit=LIMIT)
            if len(blob)!=package.size or hashlib.sha256(blob).hexdigest()!=package.sha256:
                raise ValueError('工具包校验失败，已停止安装，旧工具未替换。')
            binary,dependencies=unpack_tool(blob,package,profile)
        runner=self.backend.command_runner
        for dependency in dependencies.split(','):
            dependency=dependency.strip()
            if not dependency: continue
            match=re.fullmatch(r'([A-Za-z0-9_.+-]+)(?: \((>=|<=|=|>|<) ([A-Za-z0-9_.+~:-]+)\))?',dependency)
            if not match: raise ValueError('Unsupported dependency constraint')
            name,operator,version=match.groups()
            command='opkg status '+quote_argument(name)
            result=runner.run('compatible_dependency_check',command,timeout=10,cancel_event=cancel)
            require_command_completion(result)
            if result.exit_code or not re.search(r'^Status: .* installed$',result.stdout,re.M):
                raise RuntimeError('缺少依赖 '+name+'，本次不修改系统依赖，旧工具保持原样。')
            if operator:
                installed=re.search(r'^Version: (.+)$',result.stdout,re.M)
                if not installed: raise RuntimeError('Missing dependency version')
                result=runner.run('compatible_dependency_version','opkg compare-versions '+quote_argument(installed[1])+' '+quote_argument(operator)+' '+quote_argument(version),timeout=10,cancel_event=cancel)
                require_command_completion(result)
                if result.exit_code: raise RuntimeError('系统依赖版本不匹配，已停止安装，旧工具保持原样。')
        result=runner.run('compatible_tool_space',"df -Pk /root | tail -n 1 | awk '{print $4}'",timeout=10,cancel_event=cancel)
        require_command_completion(result)
        if result.exit_code or not result.stdout.strip().isdigit() or int(result.stdout.strip())<8192:
            raise RuntimeError('路由器持久存储空间不足，已停止安装，旧工具保持原样。')
        directory=ROOT+'/tool-'+uuid4().hex
        executable=directory+'/hcxdumptool'
        created=False
        deadline=time.monotonic()+180
        if cancel.is_set() or ssh._client is not connection or ssh.host_fingerprint != fingerprint:
            raise InterruptedError('Installation cancelled or connection changed')
        try:
            with ssh.transfer(cancel,timeout=60):
                sftp=ssh.open_sftp(timeout=10,cancel_event=cancel)
                sftp.get_channel().settimeout(10)
                prepare_store(sftp)
                sftp.mkdir(directory,mode=0o700)
                created=True
                with sftp.open(executable,'wx') as target:
                    for offset in range(0,len(binary),65536):
                        if cancel.is_set(): raise InterruptedError('安装已取消')
                        if time.monotonic() >= deadline: raise TimeoutError('安装超时，已停止')
                        target.write(binary[offset:offset+65536])
                    target.flush()
                digest=hashlib.sha256()
                received=0
                with sftp.open(executable,'rb') as source:
                    while True:
                        if cancel.is_set(): raise InterruptedError('安装已取消')
                        if time.monotonic() >= deadline: raise TimeoutError('安装超时，已停止')
                        chunk=source.read(65536)
                        if not chunk: break
                        received+=len(chunk)
                        if received>len(binary): raise ValueError("Uploaded binary grew unexpectedly")
                        digest.update(chunk)
                if digest.hexdigest()!=hashlib.sha256(binary).hexdigest(): raise ValueError('上传后校验失败，旧工具未替换。')
                sftp.chmod(executable,0o700)
            capabilities=read_hcx_capabilities(runner,cancel,executable=executable)
            if capabilities.version != package.version.split('-')[0]: raise ValueError('新工具版本自检失败，旧工具未替换。')
            for band in ('2.4G','5G'):
                capabilities.capture_options(band,False,True,'capture.bpf')
            if isinstance(package,BundledTool):
                verify_modern_interface_api(runner,executable,cancel)
            if cancel.is_set() or ssh._client is not connection: raise InterruptedError('Installation cancelled or connection changed')
            # 自检通过后原子保存选择，失败时不会覆盖上一份有效记录。
            with ssh.transfer(cancel,timeout=30):
                sftp=ssh.open_sftp(timeout=10,cancel_event=cancel)
                sftp.get_channel().settimeout(10)
                if cancel.is_set() or ssh._client is not connection or ssh.host_fingerprint != fingerprint:
                    raise InterruptedError('Installation cancelled or connection changed')
                publish_selection(sftp,dict(schema=1,enabled=True,directory=directory.rsplit('/',1)[1],
                    release=profile.release,architecture=profile.package_arch,version=capabilities.version,
                    package_version=package.version,package_sha256=package.sha256,
                    size=len(binary),sha256=hashlib.sha256(binary).hexdigest()),cancel)
                created=False
            if ssh._client is connection and ssh.host_fingerprint == fingerprint:
                self.backend.pmkid_capture.tool_selection=(connection,executable)
            return dict(version=capabilities.version,path=executable,note=f'PMKID 工具 {capabilities.version} 安装成功，原工具已保留。可开始 PMKID 采集；能否正常采集以启动结果为准。')
        except BaseException as primary:
            if created and ssh._client is connection and ssh.connected:
                try:
                    with ssh.transfer(timeout=10):
                        sftp=ssh.open_sftp(timeout=5)
                        sftp.remove(executable)
                        sftp.rmdir(directory)
                except Exception as cleanup:
                    from modules.capture_services.capture_logging import LOGGER
                    LOGGER.warning('Compatible staging cleanup failed type=%s', type(cleanup).__name__)
                    primary.args = (str(primary)+'; 本次未完成安装的目录未能清理：'+directory+'；旧工具未替换。',)
            elif created:
                primary.args = (str(primary)+'; 连接已断开，本次未完成安装的目录保留在 '+directory+'；旧工具未替换。',)
            raise


    def restore(self, device, cancel=None):
        """登录后台只读核验持久记录；固件或文件变化时保留原工具。"""
        ssh=self.backend.ssh_client
        connection=ssh._client
        fingerprint=ssh.host_fingerprint
        self.backend.pmkid_capture.tool_selection=None
        with ssh.transfer(cancel,timeout=40):
            sftp=ssh.open_sftp(timeout=10,cancel_event=cancel)
            sftp.get_channel().settimeout(10)
            record=read_selection(sftp)
            if not record or not record['enabled']:
                return False
            profile=normalize_profile(device.version,device.package_architecture,device.package_manager)
            if record.get('release') != profile.release or record.get('architecture') != profile.package_arch:
                raise ValueError('路由器系统已变化，原兼容工具未启用，请重新检查工具包。')
            if record.get('version') not in SUPPORTED_HCX_VERSIONS:
                raise ValueError('已保存工具的版本未适配，已保留原工具。')
            executable=validate_binary(sftp,record,cancel)
        capabilities=read_hcx_capabilities(self.backend.command_runner,cancel,executable=executable)
        if capabilities.version != record['version']:
            raise ValueError('已保存工具的版本自检失败，已保留原工具。')
        for band in ('2.4G','5G'):
            capabilities.capture_options(band,False,True,'capture.bpf')
        if (cancel is not None and cancel.is_set()) or ssh._client is not connection or ssh.host_fingerprint != fingerprint:
            raise InterruptedError('Tool restore cancelled or connection changed')
        self.backend.pmkid_capture.tool_selection=(connection,executable)
        return True

    def reset(self, cancel):
        """保存切回原工具的选择；保留已安装文件，不执行卸载。"""
        ssh=self.backend.ssh_client
        connection=ssh._client
        fingerprint=ssh.host_fingerprint
        with ssh.transfer(cancel,timeout=30):
            sftp=ssh.open_sftp(timeout=10,cancel_event=cancel)
            sftp.get_channel().settimeout(10)
            record=read_selection(sftp)
            if record:
                record['enabled']=False
                if ssh._client is not connection or ssh.host_fingerprint != fingerprint:
                    raise InterruptedError('Connection changed')
                publish_selection(sftp,record,cancel)
        if ssh._client is connection and ssh.host_fingerprint == fingerprint:
            self.backend.pmkid_capture.tool_selection=None
        return dict(note='已切回路由器原有工具，重启后仍保持这个选择。兼容工具文件已保留。')
