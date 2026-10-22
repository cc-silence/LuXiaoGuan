"""更新脚本控制台策略；需要输入时保留窗口，无交互脚本继续后台运行。"""
import ast
from functools import lru_cache
from pathlib import Path
import re
import subprocess

def script_needs_console(script):
    """只读识别输入语句，缓存布尔结果；文件变化自动失效。"""
    path=Path(script)
    try:
        st=path.stat()
        return _classify(str(path.resolve()),st.st_mtime_ns,st.st_size)
    except OSError:
        return False

@lru_cache(maxsize=128)
def _classify(path,modified,size):
    suffix=Path(path).suffix.lower()
    if suffix not in (".bat",".cmd",".py"):
        return False
    if size>2*1024*1024:
        return True
    raw=Path(path).read_bytes()
    if suffix==".py":
        try:
            tree=ast.parse(raw)
        except SyntaxError:
            return True
        return any(isinstance(node,ast.Call) and (
            isinstance(node.func,ast.Name) and node.func.id in ("input","getpass") or
            isinstance(node.func,ast.Attribute) and node.func.attr=="getpass")
            for node in ast.walk(tree))
    try:
        text=raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text=raw.decode("gb18030", errors="replace")
    for line in text.splitlines():
        line=line.strip()
        if not line or line.casefold().startswith(("rem ","rem\t","::")):
            continue
        for match in re.finditer(r"(?:^|[&|])\s*@?(choice\b|pause\b|set\s+/p\b)",line,re.I):
            # set /p 从文件读取变量不是用户交互，不因此打开窗口。
            if match[1].casefold().startswith("set") and "<" in line[match.end():]:
                continue
            return True
    return False

def run_visible_script(command,workspace,timeout=600):
    """在独立控制台继承标准输入输出，避免捕获管道让 choice/set /p 无法工作。"""
    return subprocess.run(command,cwd=str(workspace),timeout=timeout,
                          creationflags=subprocess.CREATE_NEW_CONSOLE)
