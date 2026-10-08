"""The planning step: the Plan the model must return, and the Pydantic AI agent that produces and validates it."""
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.litellm import LiteLLMProvider

from config.settings import LLM_API_KEY, LLM_BASE_URL, MODEL


class Plan(BaseModel):
    """Plan for answering the question, written before running any query."""
    thought: str = Field(description="What is really being asked, how vague terms are interpreted (concrete "
                                     "thresholds), and what could go wrong")
    sub_problems: list[str] = Field(default_factory=list,
                                    description="The question broken into small, independently answerable parts")
    steps: list[str] = Field(description="Ordered steps; each names the tool/database, what it fetches or computes, "
                                         "and which earlier result it depends on")


def pydantic_model(name=None):
    """A Pydantic AI model on the same gateway as the LangGraph agent."""
    return OpenAIChatModel(name or MODEL, provider=LiteLLMProvider(api_base=LLM_BASE_URL, api_key=LLM_API_KEY))


def build_planner(instructions):
    # Pydantic AI validates the plan and, if a field is missing or malformed, sends the error back for a retry
    return Agent(pydantic_model(), output_type=Plan, instructions=instructions, retries=2,
                 model_settings={"temperature": 0, "max_tokens": 16384}, name="planner")
