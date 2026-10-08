"""Agentic SQL engine: a LangGraph plan -> ReAct loop over the databases listed in databases.toml.

    uv run agent.py "your question"     answer one question
    uv run agent.py                     interactive; follow-ups keep context, /new resets

LLM calls go to one OpenAI-compatible endpoint: the LiteLLM proxy (LLM_BASE_URL, LLM_API_KEY, MODEL = proxy alias,
see litellm/config.yaml) or, without it, OpenRouter directly (OPENROUTER_API_KEY). Settings come from the
environment or .env. Set LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY to trace every run in Langfuse.

LangGraph runs the loop; Pydantic AI produces the typed plan. The prompt holds no schema: it lists the configured
databases and their tables, and the agent looks up columns with describe_tables when it needs them.
"""
from cli.app import main

if __name__ == "__main__":
    main()
