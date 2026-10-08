"""Runtime settings, read from the environment and the project's .env file.

LLM calls go to one OpenAI-compatible endpoint: the LiteLLM proxy (LLM_BASE_URL, LLM_API_KEY, MODEL = proxy alias,
see infra/litellm/config.yaml) or, without it, OpenRouter directly (OPENROUTER_API_KEY). Langfuse tracing is on when
its keys are set and the server answers.
"""
import os
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

env_file = ROOT / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.strip().startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1")
LLM_API_KEY = os.environ.get("LLM_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
MODEL = os.environ.get("MODEL", "anthropic/claude-haiku-4.5")


def reachable(url):
    try:
        urllib.request.urlopen(url, timeout=2)
        return True
    except Exception:
        return False


LANGFUSE_KEYS = bool(os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"))
# Tracing is on when keys are set and Langfuse answers; if it's down the agent just runs untraced
TRACING = LANGFUSE_KEYS and reachable(os.environ.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com") +
                                      "/api/public/health")
