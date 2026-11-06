"""Build only the verified 6.3.4 interface-index correction, using Zig 0.13.0."""
import hashlib,json,os,subprocess,tarfile,shutil
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
DEST=ROOT/'bin/hcx_compat'
WORK=Path(os.environ.get('ROUTERMANAGER_HCX_BUILD_ROOT',str(ROOT/'build/hcx-compat-build')))
TOOLCHAIN=Path(os.environ.get('ROUTERMANAGER_HCX_TOOLCHAIN',str(ROOT/'build/hcx-toolchain')))
TARGETS={
 'x86_64':('x86_64-linux-musl',None,[]),
 'aarch64':('aarch64-linux-musl','arm64',[]),
 'arm':('arm-linux-musleabi','armel',['-mcpu=baseline']),
 'mips':('mips-linux-musl','mips',['-mcpu=mips32r2+soft_float']),
 'mipsel':('mipsel-linux-musl','mipsel',['-mcpu=mips32r2+soft_float']),
}


def prepare():
    meta=json.loads((HERE/'source.json').read_text())
    raw=(HERE/'upstream-6.3.4.tar.gz').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=meta['sha256']:raise ValueError('Upstream source hash mismatch')
    source=WORK/'source';source.mkdir(parents=True,exist_ok=True)
    with tarfile.open(HERE/'upstream-6.3.4.tar.gz') as archive:
        for member in archive.getmembers():
            parts=Path(member.name).parts[1:]
            if not parts:continue
            relative=Path(*parts)
            if relative.is_absolute() or '..' in parts:raise ValueError('Invalid archive entry')
            target=source/relative
            if member.isdir():target.mkdir(parents=True,exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(archive.extractfile(member).read())
            else:raise ValueError('Source links are not allowed')
    c=source/'hcxdumptool.c'
    text=c.read_text()
    old='\t\t\tif((ifpresentlist + ii)->wiphy == wiphy)\n\t\t\t\t{\n\t\t\t\t(ifpresentlist + ii)->index = ifindex;'
    new=old.replace('(ifpresentlist + ii)->index = ifindex;',
        '/* Preserve the explicitly selected interface on a shared wiphy. */\n'
        '\t\t\t\tif((ifaktindex != 0) && ((ifpresentlist + ii)->index == ifaktindex) && (ifindex != ifaktindex)) break;\n'
        '\t\t\t\t(ifpresentlist + ii)->index = ifindex;')
    if text.count(old)!=1:raise ValueError('Upstream enumeration layout changed')
    c.write_text(text.replace(old,new),encoding='utf-8')
    header=source/'include/byteorder.h'
    original=header.read_text(encoding='utf-8')
    start=original.index('#define __hcxbswab16(x)')
    end=original.index('/*---------------------------------------------------------------------------*/',start)
    fixed=('#define __hcxbswab16(x) __builtin_bswap16((u16)(x))\n'
           '#define __hcxbswab32(x) __builtin_bswap32((u32)(x))\n'
           '#define __hcxbswab64(x) __builtin_bswap64((u64)(x))\n')
    header.write_text(original[:start]+fixed+original[end:],encoding='utf-8')
    return source,meta


def build_one(item,source):
    name,(target,arch,flags)=item
    zig=TOOLCHAIN/('zig-windows-x86_64-0.13.0/zig.exe' if os.name=='nt' else 'zig-linux-x86_64-0.13.0/zig')
    output=DEST/name/'hcxdumptool';output.parent.mkdir(parents=True,exist_ok=True)
    command=[str(zig),'cc','-target',target,'-O2','-std=gnu99','-static','-s',
        '-DVERSION_TAG="6.3.4-rm-ifindex1"','-DVERSION_YEAR="2024"','-DHCXSTATUSOUT','-DHCXNMEAOUT']+flags
    if arch:
        headers=list((TOOLCHAIN/('win-headers' if os.name=='nt' else 'headers')/arch/'usr').glob('*/include'))
        if len(headers)!=1:raise ValueError('Missing target kernel headers')
        isolated=WORK/'kernel-headers'/name;isolated.mkdir(parents=True,exist_ok=True)
        for sub in ('linux','asm','asm-generic'):
            link=isolated/sub
            if os.name=='nt':shutil.copytree(headers[0]/sub,link,dirs_exist_ok=True)
            elif not link.exists():link.symlink_to(headers[0]/sub,target_is_directory=True)
        command+=['-idirafter',str(isolated)]
    else:
        if os.name=='nt':
            command+=['-idirafter',str(TOOLCHAIN/'win-headers/x86_64/usr/include'),'-idirafter',str(TOOLCHAIN/'win-headers/x86_64/usr/include/x86_64-linux-gnu')]
        else:command+=['-idirafter','/usr/include','-idirafter','/usr/include/x86_64-linux-gnu']
    command+=['-o',str(output),str(source/'hcxdumptool.c')]
    env=dict(os.environ,ZIG_GLOBAL_CACHE_DIR=str(WORK/'zig-cache'),ZIG_LOCAL_CACHE_DIR=str(WORK/'zig-local'))
    log=WORK/(name+'.log')
    print('building',name,flush=True)
    with log.open('w',encoding='utf-8') as stream:
        result=subprocess.run(command,env=env,stdout=stream,stderr=subprocess.STDOUT,text=True,timeout=1200)
    if result.returncode:raise RuntimeError(name+': '+log.read_text(encoding='utf-8')[-3000:])
    raw=output.read_bytes()
    print('built',name,len(raw),flush=True)
    return name,dict(file=name+'/hcxdumptool',sha256=hashlib.sha256(raw).hexdigest(),size=len(raw),target=target)


def main():
    source,meta=prepare();DEST.mkdir(parents=True,exist_ok=True)
    rows={}
    for item in TARGETS.items():
        name,row=build_one(item,source);rows[name]=row
    zigroot=TOOLCHAIN/('zig-windows-x86_64-0.13.0' if os.name=='nt' else 'zig-linux-x86_64-0.13.0')
    for src,name in ((source/'license.txt','hcxdumptool-LICENSE.txt'),(zigroot/'LICENSE','Zig-LICENSE.txt'),(zigroot/'lib/libc/musl/COPYRIGHT','musl-COPYRIGHT.txt')):
        shutil.copyfile(src,DEST/name)
    (DEST/'manifest.json').write_text(json.dumps(dict(schema=1,version='6.3.4-rm-ifindex1',source=meta,corrections=['selected-interface-index','single-evaluation-byte-swaps'],variants=rows),indent=2)+'\n')

if __name__=='__main__':main()
