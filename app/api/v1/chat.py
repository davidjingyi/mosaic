"""Chat API routes with streaming support and session persistence."""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.v1.deps import get_engine, get_session_store
from app.core.engine import RAGEngine
from app.core.user_auth import verify_user_token, record_user_activity
from app.services.session_store import SessionStore

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["对话"])


class ChatRequest(BaseModel):
    query: str = Field(..., max_length=2000)
    history: list[dict] = Field(default_factory=list, max_length=50)
    stream: bool = Field(default=True)
    session_id: str | None = Field(default=None)
    prompt_id: str | None = Field(default=None)


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("")
async def chat(request: Request, body: ChatRequest):
    engine: RAGEngine = get_engine(request)
    store: SessionStore = get_session_store(request)

    # Track user activity from auth token; also resolve identity for session isolation
    username = ""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        username = verify_user_token(auth_header[7:]) or ""
        if username:
            record_user_activity(username)

    # Resolve or create session
    session_id = body.session_id
    if session_id:
        sess = store.get(session_id)
        if not sess:
            raise HTTPException(status_code=404, detail="会话不存在")
        # 背靠背隔离：用户只能访问自己的会话
        if sess.username and sess.username != username:
            raise HTTPException(status_code=403, detail="无权访问他人会话")
    else:
        sess = store.create(username=username)
        session_id = sess.session_id

    # Build history from session if not provided
    history = body.history if body.history is not None else sess.messages

    # Record user message
    sess.add_message("user", body.query)
    store._maybe_save()

    if body.stream:
        async def event_generator():
            assistant_text = ""
            async for chunk in engine.chat(
                query=body.query,
                history=history,
                stream=True,
                session_id=session_id,
                prompt_id=body.prompt_id,
            ):
                try:
                    data = json.loads(chunk)
                except json.JSONDecodeError:
                    logger.error("Invalid JSON chunk from engine: %s", chunk[:200])
                    yield _sse({"error": "服务器返回格式错误", "done": True})
                    break
                if data.get("done"):
                    # Record assistant message and persist immediately
                    sess.add_message("assistant", assistant_text)
                    store._save()
                if "chunk" in data:
                    assistant_text += data["chunk"]
                yield _sse(data)

        return StreamingResponse(event_generator(), media_type="text/event-stream")
    else:
        chunks = []
        async for chunk in engine.chat(
            query=body.query,
            history=history,
            stream=False,
            session_id=session_id,
            prompt_id=body.prompt_id,
        ):
            chunks.append(chunk)
        # In non-stream mode, engine yields a single complete JSON string
        full_response = "".join(chunks)
        # Try to extract the last valid JSON object (in case of partial output)
        result = None
        for candidate in [full_response, full_response.rsplit('}{', 1)[-1] if '}{' in full_response else full_response]:
            try:
                result = json.loads(candidate)
                break
            except json.JSONDecodeError:
                continue
        if result is not None:
            answer = result.get("answer", "")
            sess.add_message("assistant", answer)
            store._save()
            return {**result, "session_id": session_id}
        else:
            sess.add_message("assistant", full_response)
            store._save()
            return {"answer": full_response, "sources": [], "session_id": session_id}
