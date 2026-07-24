from fastapi import FastAPI
from contextlib import asynccontextmanager
from app.core.config import settings
from app.core.logger import log, setup_logger
from app.core.database import init_redis
from app.routers import oms, gnb
from app.modules.run import start_modules
import uvicorn


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动/关闭事件"""
    log.info(f"###### Monitor Agent starting, version: {settings.app.version} ######")

    # 初始化 Redis
    init_redis()

    # 启动后台模块（后续实现 modules.Run()）
    start_modules()

    yield
    log.info("Monitor Agent shutting down...")


app = FastAPI(
    title="Network Monitor Agent",
    version=settings.app.version,
    lifespan=lifespan
)

# 注册路由
app.include_router(oms.router, prefix="/monitor_agent/v1")
app.include_router(gnb.router, prefix="/monitor_agent/v1/gnb")

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.app.addr,
        port=int(settings.app.port),
        reload=True,  # 开发模式
        log_level="info"
    )