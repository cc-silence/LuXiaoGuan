"""Versioned requirements and virtual providers for offline graph selection."""
import re
from . import apk_version,opkg_version

_NAME=r"[A-Za-z0-9][A-Za-z0-9+_.-]*"
_IPK=re.compile(r"("+_NAME+r")(?:\s*\(\s*(<<|<=|>=|>>|=|<|>)\s*([^()\s]+)\s*\))?")

def requirements(expression):
    if isinstance(expression,dict):
        name=expression.get("name","")
        mask=expression.get("match",0)
        version=expression.get("version","")
        if not re.fullmatch(_NAME,name.lstrip("!")) or type(mask) is not int or not isinstance(version,str):
            raise ValueError("Invalid APK dependency")
        negative=name.startswith("!") or bool(mask&16)
        return [dict(name=name.lstrip("!"),version=version,operator=mask&~16,apk=True,negative=negative)]
    if not isinstance(expression,str):
        raise ValueError("Invalid dependency")
    result=[]
    for part in expression.split("|"):
        negative=part.strip().startswith("!")
        match=_IPK.fullmatch(part.strip().lstrip("!"))
        if match is None:raise ValueError("Invalid package dependency: "+expression)
        name,operator,version=match.groups()
        result.append(dict(name=name,operator=operator,version=version or "",apk=False,negative=negative))
    return result


def matches(requirement,row,provided=None):
    if not requirement["version"]:
        return True
    version=row["version"]
    if provided is not None and requirement["apk"]:
        version=provided.get("version") or None if isinstance(provided,dict) else None
    if requirement["apk"]:
        return apk_version.matches(version,requirement["operator"],requirement["version"])
    return opkg_version.satisfies(version,{"<<":"<",">>":">"}.get(requirement["operator"],requirement["operator"]),requirement["version"])
