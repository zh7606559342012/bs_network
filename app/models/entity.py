# models/entity.py
from typing import Any, Optional
from pydantic import BaseModel


class Response(BaseModel):
    """统一响应结构体"""
    timestamp: str
    code: str
    message: str
    data: Optional[Any] = None
    uuid: Optional[str] = None  # 可选字段

    class Config:
        json_schema_extra = {
            "example": {
                "timestamp": "2026-07-27 10:30:00",
                "code": "200",
                "message": "ok",
                "data": None
            }
        }