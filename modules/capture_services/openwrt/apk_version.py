"""Offline APK ordering using the upstream version.c token grammar.

Reference: https://github.com/alpinelinux/apk-tools/blob/master/src/version.c
Only syntactically valid package versions are accepted; unknown forms stop export.
"""
import re

_SUFFIX={name:index for index,name in enumerate(("alpha","beta","pre","rc","","cvs","svn","git","hg","p"),1)}

def tokens(version):
    if not isinstance(version,str) or not 1<=len(version)<=256:
        raise ValueError("Invalid APK version")
    first=re.match(r"[0-9]+",version)
    if first is None:
        raise ValueError("Invalid APK version: "+version)
    result=[(0,first.group())]
    pos=first.end()
    state=0
    while pos<len(version):
        tail=version[pos:]
        char=tail[0]
        if char=="." or char.isdigit() and char.isascii():
            if state>1 and state!=3:
                raise ValueError("Invalid APK version: "+version)
            kind=4 if state==3 else 1
            match=re.match(r"[0-9]+",tail[1:] if char=="." else tail)
            if match is None or char=="." and state>1:
                raise ValueError("Invalid APK version: "+version)
            text=match.group()
            pos+=len(text)+(char==".")
        elif "a"<=char<="z":
            if state>1:
                raise ValueError("Invalid APK version: "+version)
            kind,text=2,char
            pos+=1
        elif char=="_":
            match=re.match(r"_([a-z]+)",tail)
            if state>4 or match is None or match.group(1) not in _SUFFIX:
                raise ValueError("Invalid APK version: "+version)
            kind,text=3,match.group(1)
            pos+=match.end()
        elif char=="~":
            match=re.match(r"~([0-9a-fA-F]+)",tail)
            if state>=5 or match is None:
                raise ValueError("Invalid APK version: "+version)
            kind,text=5,match.group(1)
            pos+=match.end()
        elif char=="-":
            match=re.match(r"-r([0-9]+)",tail)
            if state>=6 or match is None:
                raise ValueError("Invalid APK version: "+version)
            kind,text=6,match.group(1)
            pos+=match.end()
        else:
            raise ValueError("Invalid APK version: "+version)
        if kind in {0,1,4,6} and int(text)>2**64-1:
            raise ValueError("APK version number exceeds limit")
        result.append((kind,text));state=kind
    return result+[(7,"")]


def compare(left,right,fuzzy=False):
    a,b=tokens(left),tokens(right)
    for (ak,av),(bk,bv) in zip(a,b):
        if ak!=bk:
            if bk==7 and fuzzy:return 0
            if ak==3 and _SUFFIX[av]<_SUFFIX[""]:return -1
            if bk==3 and _SUFFIX[bv]<_SUFFIX[""]:return 1
            return -1 if ak>bk else 1
        if ak==7:return 0
        if ak in {0,4,6} or ak==1 and not (av.startswith("0") or bv.startswith("0")):
            av,bv=int(av),int(bv)
        elif ak==3:
            av,bv=_SUFFIX[av],_SUFFIX[bv]
        if av!=bv:return (av>bv)-(av<bv)
    return 0


def matches(actual,mask,expected):
    if type(mask) is not int or mask<0 or mask&~31 or mask&7==6:
        raise ValueError("Unsupported APK dependency condition")
    conflict=bool(mask&16)
    positive=mask&15
    if not expected or positive&7==7:
        satisfied=True
    elif actual is None:
        # An unversioned APK virtual provider has an unspecified version.
        satisfied=bool(positive&7)
    else:
        result=compare(actual,expected,fuzzy=bool(positive&8))
        satisfied=bool(positive&({-1:2,0:1,1:4}[result]))
    return not satisfied if conflict else satisfied
