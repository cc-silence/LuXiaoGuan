"""Offline-only extraction of exact catalog profiles into original package names."""
import hashlib
import json
import sys
import re
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path
from .bundle_storage_lock import locked_bundle
from .bundle_dependencies import closure
from .bundle_specs import bundle_spec
from .bundle_dependency_rules import requirements
from .common_bundle_repository import CommonBundleRepository,safe_path,bundle_root

class ExportGuard:
    def __init__(self,cancel,seconds=300):
        self.cancel=cancel
        self.deadline=time.monotonic()+seconds

    def check(self):
        if self.cancel.is_set():
            raise InterruptedError("提取已取消。")
        if time.monotonic()>self.deadline:
            raise TimeoutError("提取超时，已停止导出。")


def _repository(kind,guard,root=None,public_key=None):
    guard.check()
    repo=CommonBundleRepository(root=root,public_key=public_key,kind=kind)
    if not repo._load():
        raise ValueError("请先下载并准备好本地离线依赖包，再提取。")
    guard.check()
    return repo


@locked_bundle
def list_export_profiles(kind,cancel,progress=None,*,root=None,public_key=None):
    guard=ExportGuard(cancel)
    repo=_repository(kind,guard,root,public_key)
    profiles=[]
    for profile in repo.catalog["profiles"]:
        guard.check()
        packages={r["name"]:r for r in profile["packages"]}
        profiles.append({**{k:profile[k] for k in ("release","target","architecture","package_manager")},
                         "roots":[dict(name=n,version=packages[n]["version"]) for n in sorted(profile["roots"])]})
    return dict(kind=kind,revision=repo.revision,profiles=profiles)


def _profile(repo,selection):
    if selection.get("revision")!=repo.revision:
        raise ValueError("本地离线包已更新，请重新打开提取窗口选择。")
    keys=("release","target","architecture","package_manager")
    matches=[p for p in repo.catalog["profiles"] if all(p[k]==selection.get(k) for k in keys)]
    if len(matches)!=1:
        raise ValueError("所选系统、平台或架构不在本地清单中，请重新选择。")
    return matches[0]


def _export_rows(profile,wanted):
    if not isinstance(wanted,(tuple,list)) or not wanted or any(not isinstance(n,str) for n in wanted):
        raise ValueError("请至少选择一个主依赖。")
    if not set(wanted).issubset(profile["roots"]):
        raise ValueError("选择的主依赖不属于当前离线包。")
    rows,firmware=closure(profile["packages"],sorted(set(wanted)))
    # Preserve official names; reject Windows collisions rather than rename packages.
    names=set()
    for row in rows:
        name=row["filename"]
        stem=name.split(".",1)[0].upper()
        if (len(name)>240 or stem in {"CON","PRN","AUX","NUL"} or re.fullmatch(r"(?:COM|LPT)[1-9]",stem)
                or name.casefold() in names):
            raise ValueError("软件包原名存在文件名冲突，未执行提取："+name)
        names.add(name.casefold())
    return sorted(rows,key=lambda r:r["name"]),firmware


def _copy_verified(source,target,row,guard,report,completed,total):
    digest=hashlib.sha256()
    size=0
    with source.open("rb") as src,target.open("xb") as dst:
        while True:
            guard.check()
            block=src.read(1024*1024)
            if not block:
                break
            size+=len(block)
            if size>row["size"]:
                raise ValueError("软件包大小异常："+row["filename"])
            dst.write(block);digest.update(block)
            report(dict(stage="正在提取："+row["filename"],completed=completed+size,total=total))
    if size!=row["size"] or digest.hexdigest()!=row["sha256"]:
        raise ValueError("软件包校验失败："+row["filename"])


@locked_bundle
def export_packages(kind,selection,wanted,destination,cancel,progress=None,*,root=None,public_key=None):
    guard=ExportGuard(cancel)
    report=progress or (lambda _value:None)
    report(dict(stage="正在核对离线包和配套依赖……",completed=0,total=0))
    repo=_repository(kind,guard,root,public_key)
    profile=_profile(repo,selection)
    rows,firmware=_export_rows(profile,wanted)
    folder=Path(destination).resolve(strict=True)
    if not folder.is_dir():
        raise ValueError("请选择用于存放提取结果的文件夹。")
    resources={repo.root.resolve(),bundle_root("common").parent.resolve()}
    if any(folder==resource or resource in folder.parents for resource in resources):
        raise ValueError("请另选导出位置，不能放入软件管理的离线资源目录。")
    total=sum(r["size"] for r in rows)
    if shutil.disk_usage(folder).free<total+1024*1024:
        raise ValueError("导出位置剩余空间不足。")
    guard.check()
    temporary=Path(tempfile.mkdtemp(prefix=".lxg-export-",dir=folder))
    label="OpenWrt_"+profile["release"]+"_"+profile["target"].replace("/","_")+"_"+profile["architecture"]+"_"+kind
    target=folder/(label+"_"+datetime.now().strftime("%Y%m%d_%H%M%S")+"_"+temporary.name[-8:])
    try:
        completed=0
        for row in rows:
            guard.check()
            source=repo.object_source(row)
            _copy_verified(source,temporary/row["filename"],row,guard,report,completed,total)
            completed+=row["size"]
        firmware_conditions=[
            dict(package=row["name"],requirement=dependency)
            for row in rows for dependency in row["depends"]
            if any(r["name"] in firmware for r in requirements(dependency))]
        metadata=dict(schema=1,kind=kind,distribution="OpenWrt",revision=selection["revision"],
                      **{k:profile[k] for k in ("release","target","architecture","package_manager")},
                      selected_roots=sorted(set(wanted)),firmware_required=firmware,firmware_conditions=firmware_conditions,
                      packages=[{k:r[k] for k in ("name","version","filename","sha256","size")} for r in rows])
        (temporary/"export.json").write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        lines=["OpenWrt 离线依赖提取清单",
               "系统版本："+profile["release"],"固件平台："+profile["target"],
               "处理器架构："+profile["architecture"],"安装包格式："+profile["package_manager"],
               "选择的主依赖："+", ".join(sorted(set(wanted))),"",
               "已提取的软件包（含配套依赖）：",*[r["filename"] for r in rows],"",
               "由匹配固件自带的基础组件（本次不导出）：",", ".join(firmware) or "无"]
        if firmware_conditions:
            lines.extend(["","固件基础组件的原始依赖条件：",*[row["package"]+": "+json.dumps(row["requirement"],ensure_ascii=False) for row in firmware_conditions]])
        (temporary/"依赖清单.txt").write_text("\n".join(lines)+"\n",encoding="utf-8")
        guard.check()
        if target.exists():
            raise FileExistsError("导出目录已存在，请重试选择一个新目录。")
        temporary.rename(target)
        return dict(path=str(target),count=len(rows),size=total,firmware_required=firmware)
    finally:
        # Only remove this invocation's absolute staging path; retain both causes.
        original=sys.exc_info()[1]
        if temporary.exists() and temporary.parent==folder and temporary.name.startswith(".lxg-export-"):
            try:
                shutil.rmtree(temporary)
            except OSError as cleanup:
                reason=str(original) if original is not None else str(cleanup)
                raise RuntimeError("提取未完成："+reason+"\n临时目录未能清理，请核对："+str(temporary)) from (original or cleanup)
