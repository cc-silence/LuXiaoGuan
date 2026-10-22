"""下载调试消息进入标准 DEBUG 通道，正常使用不生成流水日志文件。"""

import logging


def log(message):
    """供现有下载调用链记录调试信息，不创建或删除任何用户日志。"""
    logging.getLogger(__name__).debug("%s", message)
