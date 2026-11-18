"""LLM vendors: which environment variable holds each one's key, and how to reach it.

Anthropic uses its own SDK. The others are reached through their OpenAI-compatible
endpoints with the OpenAI SDK. A model is available only when its vendor's key is set.
Keys live in .env (see .env.example); they are never stored in the database.

Local servers (Ollama, LM Studio, MLX's mlx_lm.server on Apple Silicon, or any OpenAI-compatible
server such as llama.cpp or vLLM) need
no key: their models are available while the server answers.
"""
import os
import time
from dataclasses import dataclass

import httpx

_reach_cache: dict[str, tuple[float, bool]] = {}


def server_up(base_url: str, ttl: float = 15.0) -> bool:
    """Whether a local OpenAI-compatible server answers GET /models (cached briefly)."""
    now = time.monotonic()
    hit = _reach_cache.get(base_url)
    if hit and now - hit[0] < ttl:
        return hit[1]
    try:
        ok = httpx.get(base_url.rstrip("/") + "/models", timeout=0.4).status_code < 500
    except httpx.HTTPError:
        ok = False
    _reach_cache[base_url] = (now, ok)
    return ok


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
    local: bool = False  # a server on this machine or network: no key, available while it answers

    def key(self) -> str | None:
        if self.local:
            return os.getenv(self.key_env) if self.key_env else None
        for env in (self.key_env, *self.alt_key_envs):
            if os.getenv(env):
                return os.getenv(env)
        return None

    def available(self) -> bool:
        if self.local:
            return bool(self.url()) and server_up(self.url())
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
    Provider("ollama", "Ollama (local)", "", "openai_compat", "http://localhost:11434/v1", "OLLAMA_BASE_URL", local=True),
    Provider("lmstudio", "LM Studio (local)", "", "openai_compat", "http://localhost:1234/v1", "LMSTUDIO_BASE_URL",
             local=True),
    Provider("mlx", "MLX (local, Apple Silicon)", "", "openai_compat", "http://localhost:8080/v1", "MLX_BASE_URL",
             local=True),
    Provider("local", "Local server (OpenAI-compatible)", "LOCAL_LLM_API_KEY", "openai_compat", None, "LOCAL_LLM_BASE_URL",
             local=True),
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
