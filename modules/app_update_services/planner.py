"""\u6839\u636e\u672c\u673a\u6587\u4ef6\u5185\u5bb9\u751f\u6210\u66f4\u65b0\u8ba1\u5212\uff0c\u4e0d\u4f9d\u8d56\u4e2d\u95f4\u7248\u672c\u3002"""
import hashlib
import json
from pathlib import Path
from .catalog import managed_path, BASELINE


def sha256_file(path, cancel=lambda:False):
    result=hashlib.sha256()
    with Path(path).open("rb") as stream:
        while True:
            if cancel(): raise InterruptedError("Update cancelled")
            block=stream.read(1024*1024)
            if not block: break
            result.update(block)
    return result.hexdigest()

def safe_target(root, relative):
    """\u7981\u6b62\u901a\u8fc7\u7b26\u53f7\u94fe\u63a5\u6216\u76ee\u5f55\u8054\u63a5\u5199\u51fa\u5b89\u88c5\u76ee\u5f55\u3002"""
    root=Path(root).resolve(); target=root/managed_path(relative)
    cursor=target
    while cursor!=root:
        if cursor.is_symlink() or (cursor.exists() and getattr(cursor.stat(),"st_file_attributes",0)&0x400):
            raise ValueError("Linked update target: "+relative)
        cursor=cursor.parent
    if not target.resolve().is_relative_to(root): raise ValueError("Escaping target")
    if target.exists() and not target.is_file(): raise ValueError("Update target is not a file: "+relative)
    return target

def baseline(root):
    path=Path(root)/BASELINE
    if not path.is_file(): return {}
    if path.stat().st_size>8*1024*1024: raise ValueError("Installation baseline too large")
    data=json.loads(path.read_text(encoding="utf-8"))
    return {managed_path(item["path"]):item["sha256"] for item in data["files"]}

class FingerprintCache:
    """复用未变文件的校验结果，安装事务不使用缓存。"""
    def __init__(self):
        self.entries = {}

    @staticmethod
    def identity(path):
        stat = path.stat()
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns,
                stat.st_ctime_ns, getattr(stat, 'st_file_attributes', 0))

    def digest(self, path, cancel):
        if cancel(): raise InterruptedError('Update cancelled')
        key = str(path)
        before = self.identity(path)
        cached = self.entries.get(key)
        if cached is not None and cached[0] == before:
            return cached[1]
        digest = sha256_file(path, cancel)
        if before != self.identity(path):
            self.entries.pop(key, None)
            raise ValueError('File changed while checking update: ' + key)
        if len(self.entries) >= 12000: self.entries.clear()
        self.entries[key] = (before, digest)
        return digest


def preview_plan(root, catalog):
    """比较官方基线给出预计下载量，不作为安装依据。"""
    old = baseline(root)
    files = [item for item in catalog['files'] if old.get(item['path']) != item['sha256']]
    return {'files': files, 'removed': [], 'conflicts': [], 'unchanged': len(catalog['files'])-len(files),
            'download_bytes': sum(item['blob_size'] for item in files),
            'version': catalog['version'], 'estimated': True}


def make_plan(root,catalog,cancel=lambda:False,cache=None):
    """\u4ec5\u66f4\u65b0\u53d8\u5316\u6587\u4ef6\uff1b\u7528\u6237\u4fee\u6539\u7684\u53d7\u7ba1\u8d44\u6e90\u5217\u4e3a\u51b2\u7a81\u3002"""
    old=baseline(root); changes=[]; conflicts=[]; removed=[]; unchanged=0
    for item in catalog["files"]:
        target=safe_target(root,item["path"])
        if cancel(): raise InterruptedError('Update cancelled')
        local=(cache.digest(target,cancel) if cache else sha256_file(target,cancel)) if target.is_file() else None
        if local==item["sha256"]: unchanged+=1; continue
        # Unchanged official, non-critical resources retain local customizations.
        if local and old.get(item["path"])==item["sha256"] and item["path"].split("/")[0] in {"user_scripts","images","themes"}:
            unchanged+=1; continue
        if local and ((item["path"] in old and local!=old[item["path"]]) or
                      (item["path"] not in old and item["path"].split("/")[0] in {"user_scripts","images","themes"})):
            conflicts.append(item["path"]); continue
        changes.append(dict(item,before=local))
    for relative in catalog.get("retired",[]):
        target=safe_target(root,relative)
        if target.is_file():
            local=cache.digest(target,cancel) if cache else sha256_file(target,cancel)
            if old.get(relative)!=local: conflicts.append(relative)
            else: removed.append({"path":relative,"before":local})
    return {"files":changes,"removed":removed,"conflicts":conflicts,"unchanged":unchanged,
            "download_bytes":sum(item["blob_size"] for item in changes),"version":catalog["version"]}
