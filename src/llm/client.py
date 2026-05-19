from collections.abc import AsyncIterator
from typing import Any

from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage

from src.config import settings


def build_llm(
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    streaming: bool = True,
) -> ChatOpenAI:
    return ChatOpenAI(
        model=model or settings.openai_model,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        temperature=temperature if temperature is not None else settings.temperature,
        max_tokens=max_tokens or settings.max_tokens,
        streaming=streaming,
    )


async def call_llm_stream(
    llm: ChatOpenAI,
    messages: list[BaseMessage],
) -> AsyncIterator[str]:
    full = ""
    async for chunk in llm.astream(messages):
        content = chunk.content if hasattr(chunk, "content") else str(chunk)
        if content:
            full += content
            yield content


async def call_llm(
    llm: ChatOpenAI,
    messages: list[BaseMessage],
) -> str:
    result = ""
    async for chunk in call_llm_stream(llm, messages):
        result += chunk
    return result


def build_messages(system_prompt: str, user_prompt: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
