"""Verify every frozen project module against current source, independent of build caches."""
from hashlib import sha256
from pathlib import Path
from PyInstaller.archive.readers import CArchiveReader


def verify_frozen_sources(executable, root):
    root = Path(root).resolve()
    archive = CArchiveReader(str(executable))
    name = next((name for name in archive.toc if name.endswith(".pyz")), None)
    if name is None: raise ValueError("Frozen executable has no Python module archive")
    pyz = archive.open_embedded_archive(name)
    verified, changed = {}, []
    for module in pyz.toc:
        parts = module.split(".")
        if not all(part.isidentifier() for part in parts): continue
        source = root.joinpath(*parts).with_suffix(".py")
        if not source.is_file(): source = root.joinpath(*parts, "__init__.py")
        if not source.is_file(): continue
        if not source.resolve().is_relative_to(root): raise ValueError("Project source escapes build root")
        raw = source.read_bytes()
        actual = pyz.extract(module)
        expected = compile(raw, actual.co_filename, "exec", optimize=0)
        if actual != expected: changed.append(module)
        else: verified[module] = sha256(raw).hexdigest()
    if changed: raise ValueError("Frozen project code differs from source: " + ", ".join(changed[:12]))
    if not verified: raise ValueError("No project modules were verified in the executable")
    return {"frozen_source_modules": len(verified), "frozen_source_sha256": verified}
