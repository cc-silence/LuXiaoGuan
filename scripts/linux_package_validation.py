"""Verify an entire Unix ZIP against its baseline before native acceptance or signing."""
import json
import zipfile
from modules.app_update_services.catalog import BASELINE
from modules.app_update_services.linux_install_plan import catalog_files
from modules.release_identity import archive_root
from scripts.linux_release_layout import REQUIRED_RUNTIME_FILES, verify_zip
from modules.platform_services.linux_update_admission import ADMISSION,NATIVE_MODULES


def verify_archive(archive,version):
    with zipfile.ZipFile(archive) as zipped:
        prefix=archive_root(zipped.namelist());info=zipped.getinfo(prefix+BASELINE)
        if info.file_size>8*1024*1024:raise ValueError('Excessive Linux baseline')
        raw=zipped.read(info);catalog=json.loads(raw);entries=catalog_files(catalog)
        if catalog['version']!=version:raise ValueError('Linux archive version differs')
        required = REQUIRED_RUNTIME_FILES | {ADMISSION, 'resources/app-update-channel.json'}
        if not required.issubset(entries) or not any(n.startswith('resources/libpython3.10.so') for n in entries):
            raise ValueError('Required Linux runtime resource is missing')
        for name in required:
            if entries[name]['kind']!='file':raise ValueError('Required Linux resource must be a file')
        for name in ('LuXiaoGuan','bin/router_terminal/libroutermanager_terminal.so'):
            with zipped.open(prefix+name) as source:header=source.read(20)
            if len(header)!=20 or header[:7]!=b'\x7fELF\x02\x01\x01' or int.from_bytes(header[18:20],'little')!=62:
                raise ValueError('Foreign Linux runtime binary')
        if entries[ADMISSION]['size']>65536:raise ValueError('Excessive updater admission')
        admission=json.loads(zipped.read(prefix+ADMISSION))
        if (admission.get('schema')!=1 or admission.get('protocol')!=1 or admission.get('platform')!='linux-x64'
                or admission.get('native_checks_passed') is not True or set(admission.get('modules',[]))!=set(NATIVE_MODULES)):
            raise ValueError('Linux updater native checks are missing')
    verify_zip(archive,list(entries.values()),raw)
    return catalog
