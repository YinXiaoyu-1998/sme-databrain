import os

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI


SUPPORTED_PROVIDER = "openai-compatible"


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required for OpenAI-compatible LLM configuration")
    return value


def build_llm() -> BaseChatModel:
    provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    if provider != SUPPORTED_PROVIDER:
        raise RuntimeError(
            f"LLM_PROVIDER must be '{SUPPORTED_PROVIDER}'. "
            f"Received: {provider or '<empty>'}"
        )

    return ChatOpenAI(
        model=_required_env("LLM_MODEL"),
        api_key=_required_env("OPENAI_COMPATIBLE_API_KEY"),
        base_url=_required_env("OPENAI_COMPATIBLE_BASE_URL"),
        temperature=0,
        max_retries=2,
    )
