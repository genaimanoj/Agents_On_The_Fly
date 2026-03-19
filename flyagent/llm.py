"""LLM factory — wraps Google Generative AI SDK."""

from __future__ import annotations

import os
from typing import Any

import google.generativeai as genai

from flyagent.config import ModelConfig

_configured = False


def _ensure_configured() -> None:
    global _configured
    if not _configured:
        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "GOOGLE_API_KEY (or GEMINI_API_KEY) not set. Add it to .env"
            )
        genai.configure(api_key=api_key)
        _configured = True


def create_model(
    model_cfg: ModelConfig,
    system_instruction: str = "",
) -> genai.GenerativeModel:
    _ensure_configured()
    gen_config = genai.types.GenerationConfig(
        temperature=model_cfg.temperature,
        max_output_tokens=model_cfg.max_output_tokens,
    )
    return genai.GenerativeModel(
        model_name=model_cfg.model,
        generation_config=gen_config,
        system_instruction=system_instruction or None,
    )


async def generate(
    model: genai.GenerativeModel,
    prompt: str | list[dict[str, Any]],
) -> str:
    """Send a prompt and return the text response."""
    response = await model.generate_content_async(prompt)
    return response.text


async def chat_turn(
    chat: Any,
    message: str,
) -> str:
    """Send one message in an existing chat and return the response text."""
    response = await chat.send_message_async(message)
    return response.text
