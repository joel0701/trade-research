"""LLM vendors: which environment variable holds each one's key, and how to reach it.

Anthropic uses its own SDK. The others are reached through their OpenAI-compatible
endpoints with the OpenAI SDK. A model is available only when its vendor's key is set.
Keys live in .env (see .env.example); they are never stored in the database.
"""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    slug: str
    name: str
    key_env: str
    api: str  # "anthropic" | "openai_compat" | "mock"
    base_url: str | None = None
    base_url_env: str | None = None
    max_tokens_param: str = "max_tokens"
    stream_usage: bool = True  # supports stream_options={"include_usage": True}
    alt_key_envs: tuple[str, ...] = ()

    def key(self) -> str | None:
        for env in (self.key_env, *self.alt_key_envs):
            if os.getenv(env):
                return os.getenv(env)
        return None

    def available(self) -> bool:
        return self.api == "mock" or bool(self.key())  # the mock needs no key

    def url(self) -> str | None:
        return (os.getenv(self.base_url_env) if self.base_url_env else None) or self.base_url


PROVIDERS: dict[str, Provider] = {p.slug: p for p in [
    Provider("anthropic", "Anthropic", "ANTHROPIC_API_KEY", "anthropic", alt_key_envs=("ANTHROPIC_AUTH_TOKEN",)),
    Provider("openai", "OpenAI", "OPENAI_API_KEY", "openai_compat", "https://api.openai.com/v1", "OPENAI_BASE_URL",
             max_tokens_param="max_completion_tokens"),
    Provider("google", "Google (Gemini)", "GOOGLE_API_KEY", "openai_compat",
             "https://generativelanguage.googleapis.com/v1beta/openai/", "GOOGLE_BASE_URL",
             alt_key_envs=("GEMINI_API_KEY",)),
    Provider("meta", "Meta (Llama API)", "LLAMA_API_KEY", "openai_compat", "https://api.llama.com/compat/v1/",
             "LLAMA_BASE_URL", stream_usage=False),
    Provider("xai", "xAI (Grok)", "XAI_API_KEY", "openai_compat", "https://api.x.ai/v1", "XAI_BASE_URL"),
    Provider("mistral", "Mistral", "MISTRAL_API_KEY", "openai_compat", "https://api.mistral.ai/v1", "MISTRAL_BASE_URL",
             stream_usage=False),
    Provider("deepseek", "DeepSeek", "DEEPSEEK_API_KEY", "openai_compat", "https://api.deepseek.com/v1",
             "DEEPSEEK_BASE_URL"),
    Provider("mock", "Mock (no cost)", "", "mock"),
]}


def provider_of(model_config: dict) -> Provider:
    """Model tools without a provider are Claude models (older databases)."""
    slug = model_config.get("provider", "anthropic")
    if slug not in PROVIDERS:
        raise ValueError(f"Unknown provider {slug!r}; expected one of {', '.join(PROVIDERS)}")
    return PROVIDERS[slug]


def model_available(model_config: dict) -> bool:
    try:
        return provider_of(model_config).available()
    except ValueError:
        return False
