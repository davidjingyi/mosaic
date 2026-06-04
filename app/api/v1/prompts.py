"""Prompt version management API routes."""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.v1.deps import require_admin
from app.services import prompt_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/prompts", tags=["Prompt 管理"])


@router.get("/public")
async def list_prompts_public():
    """公开接口：列出所有可用 Prompt（名称和ID，不含完整内容）"""
    prompts = prompt_store.get_prompts()
    return {
        "prompts": [
            {"id": p["id"], "name": p["name"], "is_default": p.get("is_default", False)}
            for p in prompts
        ]
    }


class PromptCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    content: str = Field(..., min_length=1)
    is_default: bool = Field(default=False)


class PromptUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    content: str | None = Field(default=None, min_length=1)
    is_default: bool | None = Field(default=None)


@router.get("")
async def list_prompts():
    """List all prompt versions (without full content for efficiency)."""
    prompts = prompt_store.get_prompts()
    return {
        "prompts": [
            {
                "id": p["id"],
                "name": p["name"],
                "is_default": p.get("is_default", False),
                "created_at": p.get("created_at"),
                "updated_at": p.get("updated_at"),
            }
            for p in prompts
        ]
    }


@router.get("/{prompt_id}")
async def get_prompt(prompt_id: str):
    prompt = prompt_store.get_prompt(prompt_id)
    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt 不存在")
    return prompt


@router.post("")
async def create_prompt(body: PromptCreate, admin=Depends(require_admin)):
    prompt = prompt_store.create_prompt(
        name=body.name,
        content=body.content,
        is_default=body.is_default,
    )
    return {"success": True, "prompt": prompt}


@router.put("/{prompt_id}")
async def update_prompt(prompt_id: str, body: PromptUpdate, admin=Depends(require_admin)):
    updated = prompt_store.update_prompt(
        prompt_id,
        name=body.name,
        content=body.content,
        is_default=body.is_default,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Prompt 不存在")
    return {"success": True, "prompt": updated}


@router.delete("/{prompt_id}")
async def delete_prompt(prompt_id: str, admin=Depends(require_admin)):
    # Prevent deleting the last prompt
    prompts = prompt_store.get_prompts()
    if len(prompts) <= 1:
        raise HTTPException(status_code=400, detail="不能删除最后一个 Prompt")
    success = prompt_store.delete_prompt(prompt_id)
    if not success:
        raise HTTPException(status_code=404, detail="Prompt 不存在")
    return {"success": True}


@router.put("/{prompt_id}/default")
async def set_default_prompt(prompt_id: str, admin=Depends(require_admin)):
    updated = prompt_store.update_prompt(prompt_id, is_default=True)
    if not updated:
        raise HTTPException(status_code=404, detail="Prompt 不存在")
    return {"success": True, "prompt": updated}
