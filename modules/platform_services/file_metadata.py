"""本地文件元数据规则；不进行额外磁盘读取。"""
import os
import stat


def local_entry_hidden(name,info):
    """根据现有 stat 结果识别 Windows 隐藏/系统属性及点开头文件。"""
    attributes=getattr(info,'st_file_attributes',0)
    return str(name).startswith('.') or bool(attributes & (stat.FILE_ATTRIBUTE_HIDDEN|stat.FILE_ATTRIBUTE_SYSTEM))


def same_local_path(left,right):
    """按本地平台的大小写和分隔符规则比较路径，不访问文件内容。"""
    return os.path.normcase(os.path.normpath(str(left)))==os.path.normcase(os.path.normpath(str(right)))
