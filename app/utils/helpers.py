# utils/helpers.py
from datetime import datetime
from typing import Any, Optional
from app.models.entity import Response
import time
import random

def gen_id() -> int:
    """简单生成唯一 ID（对应 Go 的 utils.GenID）"""
    return int(time.time() * 1000) + random.randint(0, 999)

def success_response(
    message: str = "ok",
    data: Optional[Any] = None,
    code: str = "200"
) -> dict:
    """生成成功响应"""
    return Response(
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        code=code,
        message=message,
        data=data
    ).model_dump()


def error_response(
    message: str = "error",
    data: Optional[Any] = None,
    code: str = "500"
) -> dict:
    """生成错误响应"""
    return Response(
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        code=code,
        message=message,
        data=data
    ).model_dump()