"""Public distribution names, independent of stable credential/product identity."""
from pathlib import Path
DISPLAY_NAME = "\u8def\u5c0f\u7ba1"
PACKAGE_DIRECTORY = "LuXiaoGuan"
ARCHIVE_PREFIX = "LuXiaoGuan_v"
# Stable filename: part of the signed update contract shipped to older clients.
APPLICATION_EXECUTABLE = "LuXiaoGuan.exe"
APPLICATION_ICON = "images/icons/app-router.png"
LEGACY_APPLICATION_EXECUTABLE = "RouterManager.exe"
LEGACY_APPLICATION_EXECUTABLES = ("\u8def\u5c0f\u7ba1.exe", LEGACY_APPLICATION_EXECUTABLE)
PUBLIC_UPDATE_EXECUTABLES = frozenset((APPLICATION_EXECUTABLE, LEGACY_APPLICATION_EXECUTABLE))
APPLICATION_EXECUTABLES = frozenset((APPLICATION_EXECUTABLE, *LEGACY_APPLICATION_EXECUTABLES))


def installed_executable(root, name=None):
    root=Path(root)
    if name is not None:
        if name not in APPLICATION_EXECUTABLES:raise ValueError("Unrecognized application executable")
        candidates=[name]
    else:candidates=[APPLICATION_EXECUTABLE,*LEGACY_APPLICATION_EXECUTABLES]
    for candidate in candidates:
        path=root/candidate
        if path.is_file() and not path.is_symlink():return path
    raise FileNotFoundError("The application executable is missing")


def archive_root(names):
    roots={name.split("/",1)[0] for name in names if name}
    if len(roots)!=1 or not roots.issubset({PACKAGE_DIRECTORY,"RouterManager"}):
        raise ValueError("Unexpected application archive root")
    return roots.pop()+"/"
