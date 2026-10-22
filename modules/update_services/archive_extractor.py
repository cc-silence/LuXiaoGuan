"""归档解压服务。

主要作用：从 StrategyExecutor 中提取的归档解压相关纯文件操作，
包括 zip/7z/tar/tar.gz 解压、嵌套 tar 处理、解压后文件可读性检查、
按模式提取源项目和复制解压内容。

所有函数不依赖 Qt，没有 UI 交互，方便独立测试。
"""

import subprocess
import time
from pathlib import Path
from shutil import copy2, rmtree, copytree, Error as CopyError

from modules.platform_services.windows_archive_tools import resolve_7zip_executable
from modules.platform_services.runtime import subprocess_creationflags
from modules.update_services.file_inventory import iter_files

try:
    import zipfile
    HAS_ZIPFILE = True
except ImportError:
    HAS_ZIPFILE = False



def extract_archive(archive_path, target_dir):
    """解压文件到目标目录。

    支持 zip、7z、tar、tar.gz、tgz。`.gz` 如果解出单个 `.tar`，会继续自动解一次。
    这样可以覆盖 Tor 内核这类旧脚本里"先解 gz 再解 tar"的流程。

    参数：
        archive_path: 压缩包路径
        target_dir: 目标目录的 Path 对象
    返回值：
        bool，解压成功返回 True
    副作用：
        在目标目录创建解压后的文件和目录
    """
    path = Path(archive_path)
    suffix = path.suffix.lower()
    lower_name = path.name.lower()
    target_dir.mkdir(parents=True, exist_ok=True)

    if suffix == ".zip" and HAS_ZIPFILE:
        with zipfile.ZipFile(path, "r") as zf:
            zf.extractall(target_dir)
        return True

    if suffix == ".7z":
        # 只有 7z 格式才加载重型解压库，安装器和 ZIP 更新不承担该导入开销。
        try:
            import py7zr
        except ImportError:
            py7zr = None
        if py7zr is not None:
            with py7zr.SevenZipFile(path, "r") as szf:
                szf.extractall(target_dir)
            return True

    if suffix == ".tar" or lower_name.endswith((".tar.gz", ".tgz")):
        import tarfile
        with tarfile.open(path, "r:*") as tf:
            tf.extractall(target_dir)
        return True

    # py7zr 不支持的格式交给统一工具解析器，优先使用 WinBox 随包 7-Zip。
    try:
        seven_zip = resolve_7zip_executable()
        if seven_zip:
            result = subprocess.run(
                [str(seven_zip), "x", str(path), f"-o{target_dir}", "-y"],
                capture_output=True, text=True, timeout=120,
                creationflags=subprocess_creationflags(),
            )
            if result.returncode == 0:
                extract_nested_tar(target_dir)
                return True
    except Exception:
        pass

    return False


def extract_nested_tar(target_dir):
    """处理 7-Zip 解 `.gz` 后留下单个 `.tar` 的情况。"""
    contents = list(Path(target_dir).iterdir())
    if len(contents) != 1 or contents[0].suffix.lower() != ".tar":
        return
    tar_path = contents[0]
    import tarfile
    try:
        with tarfile.open(tar_path, "r:*") as tf:
            tf.extractall(target_dir)
        tar_path.unlink(missing_ok=True)
    except (OSError, tarfile.TarError):
        pass


def verify_extracted_files_readable(temp_dir):
    """检查解压后的所有文件是否可读。

    Windows Defender 等杀毒软件可能在解压后立即隔离某些文件（尤其是 Go 编译的 .exe），
    文件保留占位但实际不可读。后续 copy2 会因此失败（Errno 22）。
    此方法提前检测这种情况，给出明确提示。

    参数：
        temp_dir: 临时解压目录的 Path 对象
    返回值：
        (success, message) 元组，success=False 时 message 包含不可读文件名和建议
    """
    unreadable = []
    for item in iter_files(temp_dir):
        try:
            with open(item, "rb"):
                pass
        except OSError:
            unreadable.append(item.name)
    if unreadable:
        detail = "\n".join(f"  • {f}" for f in unreadable)
        return False, (
            "解压成功但以下文件无法读取，可能被 Windows Defender "
            "或其他杀毒软件拦截隔离：\n"
            f"{detail}\n\n"
            "建议：在 Windows Defender 中添加排除路径，"
            "或暂时关闭实时防护后重试。"
        )
    return True, ""


def extract_source_items(temp_dir, extract_mode):
    """按解压模式返回需要复制或移动的源项目。

    参数：
        temp_dir: 临时解压目录的 Path 对象
        extract_mode: 解压模式，取值 "flatten_top_folder"（默认）/"keep_structure"/"flatten_all_files"
    返回值：
        源项目的 Path 列表
    注意：
        调用方（_copy_extracted_contents）负责根据模式决定复制行为，
        此函数只返回源项目列表，不执行实际复制。
    """
    mode = extract_mode or "flatten_top_folder"
    contents = list(Path(temp_dir).iterdir())
    if mode == "flatten_top_folder" and len(contents) == 1 and contents[0].is_dir():
        return list(contents[0].iterdir())
    return contents


def _copy_with_retry(copy_func, args=(), kwargs=None, attempts=3, delay=0.5):
    """执行复制操作，遇到 PermissionError 时做少量重试。

    参数：
        copy_func: 复制函数，例如 shutil.copy2 或 shutil.copytree。
        args / kwargs: 传给复制函数的位置参数和关键字参数。
        attempts: 总尝试次数。
        delay: 每次重试前的等待秒数。
    返回值：None；全部重试仍失败时抛出最后一次异常。
    副作用：复制文件；最坏情况下会短暂阻塞（约 attempts * delay 秒）。

    背景：有些软件运行时会把引擎 DLL 注入到其它进程（如 Proxifier），
    软件退出后注入进程释放 DLL 存在时序差，短时间内文件仍被映射导致
    PermissionError。这里做少量重试作为容错，不做无限等待。
    """

    for attempt in range(attempts):
        try:
            copy_func(*args, **(kwargs or {}))
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)


def _copy_tree_with_retry(source, target, **kwargs):
    """正常时保留标准库快速复制；仅在失败后补复制失败的文件。"""
    try:
        return copytree(source, target, **kwargs)
    except CopyError as exc:
        remaining = []
        for failed_source, failed_target, reason in exc.args[0]:
            if not Path(failed_source).is_file():
                remaining.append((failed_source, failed_target, reason))
                continue
            try:
                # 首次目录复制已经尝试过一次，单文件最多再尝试两次。
                _copy_with_retry(copy2, (failed_source, failed_target), attempts=2)
            except OSError as retry_error:
                remaining.append((failed_source, failed_target, str(retry_error)))
        if remaining:
            raise CopyError(remaining) from exc
        return target


def _copy_file_with_retry(source, target):
    """只重试被占用的单个文件，保留已完成的复制和文件时间属性。"""
    _copy_with_retry(copy2, (source, target))
    return target


def copy_extracted_contents(temp_dir, target_dir, extract_mode):
    """把临时解压目录内容复制到目标目录。

    extract_mode 说明：
    - keep_structure：保留压缩包目录结构。
    - flatten_top_folder：只有一个顶层目录时去掉外层目录。
    - flatten_all_files：递归复制所有文件到目标目录，丢弃目录结构。

    参数：
        temp_dir: 临时解压目录的 Path 对象
        target_dir: 目标目录的 Path 对象
        extract_mode: 解压模式
    副作用：
        会在 target_dir 下创建文件和目录，同名目标会被覆盖
    """
    temp_dir = Path(temp_dir)
    target_dir = Path(target_dir)
    mode = extract_mode or "flatten_top_folder"
    if mode == "flatten_all_files":
        for item in iter_files(temp_dir):
            _copy_file_with_retry(item, target_dir / item.name)
        return

    for item in extract_source_items(temp_dir, mode):
        dest = target_dir / item.name
        if item.is_dir():
            if dest.exists():
                # 使用合并模式（dirs_exist_ok=True）而非先 rmtree 再复制：
                # 避免删除用户手动放入的额外文件（如 v2rayN bin/naiveproxy/）。
                # 同名文件会被覆盖，但仅在旧目录存在的额外文件得以保留。
                # 如果用户需要完全干净的目录，应配置 delete_old_dir=true。
                _copy_tree_with_retry(item, dest, dirs_exist_ok=True)
            else:
                _copy_tree_with_retry(item, dest)
        else:
            _copy_file_with_retry(item, dest)
