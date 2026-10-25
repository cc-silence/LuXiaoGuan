"""Shared, version-aware graph selection for verified offline catalogs."""
import re
from .bundle_dependency_rules import requirements,matches

FIRMWARE_OWNED={
    "libc","libpthread","librt","kernel","base-files","busybox",
    "opkg","apk-mbedtls","apk-openssl","apk",
}

def dependency_name(expression):
    return requirements(expression)[0]["name"]

class MissingPackagesError(ValueError):
    """The local graph cannot satisfy a dependency, including its version."""
    def __init__(self,packages):
        self.packages=tuple(sorted(packages))
        super().__init__("Missing required packages or matching versions: "+", ".join(self.packages))

class _GraphLimit(ValueError):
    pass

def closure(rows,roots):
    names,providers={},{}
    for row in rows:
        names.setdefault(row["name"],row)
        for expression in row.get("provides",[]):
            for requirement in requirements(expression):
                providers.setdefault(requirement["name"],[]).append((row,expression))
    selected,required={},set()
    negatives=[]
    attempts=0

    def check_conflicts():
        for source,requirement in negatives:
            candidates=[]
            if requirement["name"] in names:
                candidates.append((names[requirement["name"]],None))
            candidates.extend(providers.get(requirement["name"],[]))
            for row,provided in candidates:
                if row["name"] not in selected or requirement["apk"] and source==row["name"]:
                    continue
                if matches(requirement,row,provided):
                    raise ValueError("Conflicting package requirements: "+source+" / "+row["name"])

    def visit(expression):
        nonlocal attempts
        attempts+=1
        if attempts>8192:raise _GraphLimit("Dependency search exceeds limit")
        options=requirements(expression)
        if all(option["negative"] for option in options):
            return
        errors=[]
        for requirement in options:
            if requirement["negative"]:
                continue
            name=requirement["name"]
            if name in FIRMWARE_OWNED:
                required.add(name)
                return
            if name.startswith("kmod-"):
                raise ValueError("Common tools unexpectedly require kernel module: "+name)
            candidates=[]
            if name in names:candidates.append((names[name],None))
            candidates.extend(providers.get(name,[]))
            for row,provided in candidates:
                if row["name"].startswith("kmod-"):
                    errors.append(ValueError("Common tools unexpectedly require kernel module: "+row["name"]))
                    continue
                if not matches(requirement,row,provided):
                    continue
                if row["name"] in selected:
                    return
                if len(selected)>=256:raise _GraphLimit("Dependency closure exceeds limit")
                previous=dict(selected),set(required),len(negatives)
                try:
                    selected[row["name"]]=row
                    for dependency in row["depends"]:
                        options2=requirements(dependency)
                        negatives.extend((row["name"],r) for r in options2 if r["negative"])
                        if any(not r["negative"] for r in options2):visit(dependency)
                    check_conflicts()
                    return
                except _GraphLimit:
                    raise
                except ValueError as exc:
                    errors.append(exc)
                    selected.clear();selected.update(previous[0])
                    required.clear();required.update(previous[1])
                    del negatives[previous[2]:]
        if len(errors)==1:raise errors[0]
        if errors and all(isinstance(exc,MissingPackagesError) for exc in errors):
            raise MissingPackagesError({name for exc in errors for name in exc.packages})
        if errors:raise ValueError("; ".join(str(exc) for exc in errors))
        label=str(expression)
        raise MissingPackagesError([label])

    roots=tuple(roots)
    absent=[r for r in roots if all(q["name"] not in names and q["name"] not in providers and q["name"] not in FIRMWARE_OWNED for q in requirements(r))]
    if absent:raise MissingPackagesError(absent)
    for name in roots:visit(name)
    check_conflicts()
    for row in selected.values():
        if not re.fullmatch("[0-9a-f]{64}",row["sha256"]) or row["size"]<=0:
            raise ValueError("Missing official size or checksum: "+row["name"])
    return list(selected.values()),sorted(required)
