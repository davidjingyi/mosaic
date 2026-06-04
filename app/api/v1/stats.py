"""System statistics API for admin dashboard."""
from fastapi import APIRouter, Depends, Request

from app.api.v1.deps import get_manager, require_admin
from app.dependencies import ComponentManager

router = APIRouter(prefix="/stats", tags=["系统统计"])


@router.get("")
async def get_stats(request: Request, admin=Depends(require_admin)):
    manager: ComponentManager = get_manager(request)
    return manager.get_stats()
