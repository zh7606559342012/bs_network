from loguru import logger
import sys
import os
from app.core.config import settings

def setup_logger():
    log_path = os.path.join(settings.log.log_path, "agent.log")
    os.makedirs(settings.log.log_path, exist_ok=True)

    # 移除默认处理器
    logger.remove()

    # 文件日志
    logger.add(
        log_path,
        rotation="10 MB",
        retention="30 days",
        level=settings.log.log_level.upper(),
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{function}:{line} - {message}",
        enqueue=True,
    )

    # 控制台日志（开发环境）
    logger.add(
        sys.stderr,
        level=settings.log.log_level.upper(),
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level}</level> | {name}:{function}:{line} - <level>{message}</level>",
    )

    logger.info("###### Monitor Agent Logger Initialized ######")
    return logger


log = setup_logger()