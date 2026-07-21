from fastapi import APIRouter
router = APIRouter()
# TODO: 实现 GNB 相关接口（HandlerAddGnb, HandlerDelGnb 等）
@router.post("/")
async def add_gnb():
    return {"message": "GNB add endpoint - to be implemented"}
@router.delete("/")
async def del_gnb():
    return {"message": "GNB delete endpoint - to be implemented"}