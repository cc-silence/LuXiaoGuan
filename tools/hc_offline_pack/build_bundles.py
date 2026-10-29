#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
构建 hcxdumptool 离线安装包集（OpenWrt 22.03 / 23.05 / 24.10 / 25.12 x mipsel_24kc / aarch64_cortex-a53）。

流程：索引 -> 依赖闭包 -> 下载 -> SHA256 校验 -> 生成 install.order / SHA256SUMS.txt / manifest.json
镜像优先级：阿里云 > 清华 > 官方（22.03.7 已 EOL，清华没有，阿里云有）。
"""
import gzip
import hashlib
import io
import json
import os
import re
import sys
import tarfile
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP = os.path.join(ROOT, "work", "hcprobe")
OUT = os.path.join(ROOT, "tools", "hc_offline_pack")

MIRRORS = [
    "https://mirrors.aliyun.com/openwrt",
    "https://mirrors.tuna.tsinghua.edu.cn/openwrt",
    "https://downloads.openwrt.org",
]

REL = [
    {"ver": "22.03.7", "branch": "22.03", "mode": "ipk"},
    {"ver": "23.05.6", "branch": "23.05", "mode": "ipk"},
    {"ver": "24.10.8", "branch": "24.10", "mode": "ipk"},
    {"ver": "25.12.6", "branch": "25.12", "mode": "apk"},
]
ARCHES = ["mipsel_24kc", "aarch64_cortex-a53"]
FEEDS = ["base", "packages"]

SKIP = {"libc", "kernel", "libgcc", "libgcc1", "libpthread", "librt", "libdl",
        "base-files", "libstdcpp"}

# apk 时代（25.12+）：feed 的 index.json 只给 name->version，没有 depends。
# 实测（2026-10-20，在 99.1 上用 `apk add --root … --initdb` 求解 25.12.6 的包）：
#   hcxdumptool-6.3.4-r1 要求 libc + libpcap1 + libopenssl3；
#   但 index.json 里的名字是清理后的 libpcap / libopenssl，实际文件名还是 libpcap1-*/libopenssl3-*。
APK_ALIAS = {"libopenssl3": "libopenssl", "libpcap1": "libpcap"}
APK_PKGS = [("libpcap1", "base"), ("libopenssl3", "base"), ("hcxdumptool", "packages")]
APK_SKIP = {"libc", "libgcc1"}   # 固件自带（libc 在 targets/<target>/packages，不在通用 feed）


def log(*a):
    print(*a, flush=True)


def fetch(rel_path: str, dst: str = None) -> bytes:
    """按镜像顺序取 releases/<...> 下的文件；返回内容，成功则写到 dst。"""
    last = None
    for m in MIRRORS:
        url = f"{m}/releases/{rel_path}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/8.5.0"})
            with urllib.request.urlopen(req, timeout=90) as r:
                data = r.read()
            if len(data) < 800:      # 404 页面之类
                raise ValueError(f"too small ({len(data)} B)")
            if dst:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, "wb") as f:
                    f.write(data)
            return data
        except Exception as e:       # noqa: BLE001
            last = e
            log(f"    [镜像失败] {url.split('/openwrt')[0]} -> {e}")
    raise RuntimeError(f"所有镜像都失败: {rel_path} ({last})")


def read_gz(path: str) -> str:
    with gzip.open(path, "rb") as f:
        return f.read().decode("utf-8", "replace")


def parse_index_opkg(path: str) -> dict:
    out = {}
    for st in read_gz(path).split("\n\n"):
        if not st.strip():
            continue
        kv = {}
        for line in st.splitlines():
            if ":" in line and not line.startswith(" "):
                k, v = line.split(":", 1)
                kv[k.strip()] = v.strip()
        if "Package" in kv:
            out[kv["Package"]] = kv
    return out


def dep_candidates(depends: str):
    for part in (depends or "").split(","):
        part = re.sub(r"\(.*?\)", "", part).strip()
        cands = [c.strip() for c in part.split("|") if c.strip()]
        if cands:
            yield cands


def resolve(root_name: str, index: dict):
    seen, order, missing = set(), [], []

    def visit(name, chain):
        if name in seen or name in SKIP:
            return
        if name not in index:
            missing.append(("|".join([name]), chain))
            return
        seen.add(name)
        for cands in dep_candidates(index[name].get("Depends", "")):
            pick = next((c for c in cands if c in index), None)
            if pick is None:
                if not any(c in SKIP for c in cands):
                    missing.append(("|".join(cands), chain + [name]))
                continue
            visit(pick, chain + [name])
        order.append(name)

    visit(root_name, [])
    return order, missing


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ipk_control(path: str) -> dict:
    raw = open(path, "rb").read()
    blob = None
    if raw[:8] == b"!<arch>\n":
        off = 8
        while off + 60 <= len(raw):
            hdr = raw[off:off + 60]
            name = hdr[:16].decode("ascii", "replace").strip().rstrip("/")
            try:
                size = int(hdr[48:58].decode("ascii", "replace").strip())
            except ValueError:
                break
            body = raw[off + 60:off + 60 + size]
            if name.startswith("control.tar"):
                blob = body
                break
            off += 60 + size + (size & 1)
    else:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
            for m in tf.getmembers():
                if m.name.startswith("control.tar"):
                    blob = tf.extractfile(m).read()
                    break
    if blob is None:
        return {}
    tf = tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz")
    for m in tf.getmembers():
        if m.name.endswith("control"):
            kv = {}
            for line in tf.extractfile(m).read().decode("utf-8", "replace").splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    kv[k.strip()] = v.strip()
            return kv
    return {}


def build_opkg(rel, arch):
    idx, feed_of = {}, {}
    for feed in FEEDS:
        p = os.path.join(TMP, f"{rel['ver']}_{arch}_{feed}.Packages.gz")
        if not os.path.exists(p):
            log(f"   索引 {feed}: 下载")
            fetch(f"{rel['ver']}/packages/{arch}/{feed}/Packages.gz", p)
        part = parse_index_opkg(p)
        idx.update(part)
        for n in part:
            feed_of[n] = feed
    log(f"   索引: base+packages 共 {len(idx)} 个包")

    order, missing = resolve("hcxdumptool", idx)
    if missing:
        log(f"   [!] 未解析依赖: {missing}")

    outdir = os.path.join(OUT, rel["branch"], arch)
    pkgdir = os.path.join(outdir, "pkgs")
    os.makedirs(pkgdir, exist_ok=True)

    recs = []
    for name in order:
        st = idx[name]
        fname = st["Filename"]
        feed = feed_of.get(name, "packages")
        dst = os.path.join(pkgdir, fname)
        if not os.path.exists(dst):
            log(f"   下载 {fname}")
            fetch(f"{rel['ver']}/packages/{arch}/{feed}/{fname}", dst)
        got = sha256(dst)
        want = st.get("SHA256sum", "")
        ctl = ipk_control(dst)
        recs.append({
            "name": name, "version": st.get("Version"), "file": fname, "feed": feed,
            "sha256": got, "sha256_index": want,
            "sha_ok": (not want) or got.lower() == want.lower(),
            "depends_index": st.get("Depends", ""),
            "depends_control": ctl.get("Depends", ""),
            "size": os.path.getsize(dst),
        })
    return recs


def build_apk(rel, arch):
    base = {}
    for feed in FEEDS:
        p = os.path.join(TMP, f"{rel['ver']}_{arch}_{feed}.index.json")
        if not os.path.exists(p):
            log(f"   索引 {feed}: 下载")
            fetch(f"{rel['ver']}/packages/{arch}/{feed}/index.json", p)
        j = json.load(open(p, encoding="utf-8"))
        for n, v in j["packages"].items():
            base[n] = (v, feed)

    outdir = os.path.join(OUT, rel["branch"], arch)
    pkgdir = os.path.join(outdir, "pkgs")
    os.makedirs(pkgdir, exist_ok=True)

    recs = []
    for name, feed in APK_PKGS:
        key = APK_ALIAS.get(name, name)
        if key not in base:
            log(f"   [!] index 里没有 {key}")
            continue
        ver, feed_real = base[key]
        fname = f"{name}-{ver}.apk"          # 文件名用 libpcap1-/libopenssl3- 前缀
        dst = os.path.join(pkgdir, fname)
        if not os.path.exists(dst):
            log(f"   下载 {fname}")
            fetch(f"{rel['ver']}/packages/{arch}/{feed_real}/{fname}", dst)
        recs.append({"name": name, "version": ver, "file": fname, "feed": feed_real,
                     "sha256": sha256(dst), "size": os.path.getsize(dst),
                     "depends_recipe": "" if name != "hcxdumptool" else "libc,libpcap1,libopenssl3"})
    return recs


def write_meta(rel, arch, recs):
    outdir = os.path.join(OUT, rel["branch"], arch)
    pkgdir = os.path.join(outdir, "pkgs")
    with open(os.path.join(outdir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"release": rel["ver"], "branch": rel["branch"], "arch": arch,
                   "mode": rel["mode"], "packages": recs}, f, ensure_ascii=False, indent=2)

    with open(os.path.join(outdir, "install.order"), "w", encoding="utf-8", newline="\n") as f:
        for r in recs:
            f.write(f"{r['name']} {r['file']} {r['version']}\n")

    with open(os.path.join(outdir, "SHA256SUMS.txt"), "w", encoding="utf-8", newline="\n") as f:
        for r in recs:
            f.write(f"{r['sha256']}  {r['file']}\n")

    lines = [f"# {rel['ver']} / {arch} / {rel['mode']}",
             f"# 源: OpenWrt {rel['ver']} 官方 feed（base + packages）",
             "# 安装顺序（依赖在前）："]
    for r in recs:
        flag = "" if r.get("sha_ok", True) else "   << SHA256 与索引不符！"
        dep = r.get("depends_index") or r.get("depends_recipe") or "-"
        lines.append(f"  {r['name']:<14s} {r['version']:<12s} {r['size']:>9d} B  依赖: {dep}{flag}")
    lines.append(f"# 合计 {len(recs)} 个包 / {sum(r['size'] for r in recs)} B")
    lines.append("# 注: libc/kernel 属固件自带，不打包")
    with open(os.path.join(outdir, "deps.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def main():
    jobs = [(rel, arch) for rel in REL for arch in ARCHES]
    log(f"共 {len(jobs)} 个组合")

    def work(job):
        rel, arch = job
        log(f"=== {rel['ver']} {arch} ({rel['mode']}) ===")
        recs = build_opkg(rel, arch) if rel["mode"] == "ipk" else build_apk(rel, arch)
        write_meta(rel, arch, recs)
        for r in recs:
            log(f"   {r['name']:<14s} {r['version']:<12s} {r['size']:>9d} B  sha_ok={r.get('sha_ok', True)}")
        return rel, arch, recs

    results = []
    with ThreadPoolExecutor(max_workers=3) as ex:
        for r in ex.map(work, jobs):
            results.append(r)

    log("\n==== 汇总 ====")
    total = 0
    for rel, arch, recs in results:
        sz = sum(x["size"] for x in recs)
        total += sz
        log(f"{rel['ver']:<9s} {arch:<22s} {len(recs)} 包  {sz:>9d} B")
    log(f"合计 {total} B ({total/1048576:.1f} MB)")

    # 根级总校验单
    root_sums = []
    for rel, arch, recs in results:
        for r in recs:
            root_sums.append(f"{r['sha256']}  {rel['branch']}/{arch}/pkgs/{r['file']}")
    with open(os.path.join(OUT, "SHA256SUMS.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(sorted(root_sums)) + "\n")
    log("已写 SHA256SUMS.txt / install.order / manifest.json / deps.txt")


if __name__ == "__main__":
    main()
