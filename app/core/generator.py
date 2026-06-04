"""LLM generator with OpenAI-compatible streaming API."""
import asyncio
import json
import logging
from typing import Any, AsyncGenerator

import httpx

from app.config import LLMConfig
from app.core.interfaces import Generator
from app.services import prompt_store

logger = logging.getLogger(__name__)


class OpenAIGenerator(Generator):
    """Generator using OpenAI-compatible API (Kimi, OpenAI, Ollama, etc.)."""

    def __init__(self, config: LLMConfig):
        self.config = config
        self._client: httpx.AsyncClient | None = None
        self._pending_count = 0
        self._lock = asyncio.Lock()
        self._client_lock = asyncio.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    headers = {
                        "Authorization": f"Bearer {self.config.api_key}",
                        "Content-Type": "application/json",
                    }
                    if self.config.api_id:
                        headers["x-api-id"] = self.config.api_id
                    self._client = httpx.AsyncClient(
                        base_url=self.config.base_url,
                        headers=headers,
                        timeout=httpx.Timeout(120.0, connect=10.0),
                        limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
                    )
        return self._client

    async def _close_client(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _build_messages(
        self, query: str, contexts: list[str], history: list[dict[str, Any]] | None,
        prompt_id: str | None = None,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        # Use specified prompt, default prompt, or fallback to config
        if prompt_id:
            target = prompt_store.get_prompt(prompt_id)
        else:
            target = prompt_store.get_default_prompt()
        system_prompt = target["content"] if target else self.config.system_prompt
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if history:
            for msg in history[-10:]:
                messages.append({"role": msg["role"], "content": msg["content"]})
        if contexts:
            context_text = "\n\n---\n\n".join(
                f"[段落 {i + 1}]\n{ctx}" for i, ctx in enumerate(contexts)
            )
            user_msg = (
                f"根据以下上下文信息回答问题：\n\n{context_text}"
                f"\n\n---\n\n问题：{query}\n\n请基于上述上下文回答。"
            )
        else:
            user_msg = query
        messages.append({"role": "user", "content": user_msg})
        return messages

    async def generate(
        self,
        query: str,
        contexts: list[str],
        history: list[dict[str, Any]] | None = None,
        prompt_id: str | None = None,
    ) -> AsyncGenerator[str, None]:
        async with self._lock:
            self._pending_count += 1
        try:
            async for chunk in self._generate(query, contexts, history, prompt_id):
                yield chunk
        finally:
            async with self._lock:
                self._pending_count -= 1

    async def _generate(
        self,
        query: str,
        contexts: list[str],
        history: list[dict[str, Any]] | None = None,
        prompt_id: str | None = None,
    ) -> AsyncGenerator[str, None]:
        client = await self._get_client()
        messages = self._build_messages(query, contexts, history, prompt_id)
        payload = {
            "model": self.config.model_name,
            "messages": messages,
            "stream": True,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "top_p": self.config.top_p,
        }
        logger.debug(
            "LLM request: base_url=%s model=%s messages_count=%d",
            self.config.base_url,
            self.config.model_name,
            len(messages),
        )
        try:
            async with client.stream("POST", "/chat/completions", json=payload) as response:
                logger.debug("LLM response status: %d", response.status_code)
                if response.status_code != 200:
                    error_text = await response.aread()
                    logger.error("API error %d: %s", response.status_code, error_text)
                    yield f"\n[API错误: {response.status_code}]"
                    return
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or line == "data: [DONE]":
                        continue
                    if line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                            delta = data.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                        except json.JSONDecodeError as e:
                            logger.warning("SSE parse error: %s", e)
                            continue
                        except Exception as e:
                            logger.warning("SSE unexpected error: %s", e)
                            continue
        except httpx.HTTPError as e:
            logger.error("HTTP error: %s", e)
            yield f"\n[网络错误: {e}]"
        except Exception as e:
            logger.error("Generation error: %s", e)
            yield f"\n[生成错误: {e}]"

    async def generate_non_stream(
        self,
        query: str,
        contexts: list[str],
        history: list[dict[str, Any]] | None = None,
        prompt_id: str | None = None,
    ) -> str:
        chunks = []
        async for chunk in self.generate(query, contexts, history, prompt_id):
            chunks.append(chunk)
        return "".join(chunks)

    async def reload(self, config: LLMConfig) -> None:
        # Wait up to 30s for pending requests to complete before closing client
        for _ in range(300):
            async with self._lock:
                if self._pending_count == 0:
                    break
            await asyncio.sleep(0.1)
        else:
            logger.warning("Generator reload timed out waiting for pending requests")
        await self._close_client()
        self.config = config
