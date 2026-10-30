"""Claude calls for the Prescription Analyzer and DiagnoFast.

Each call returns a validated Pydantic object, so the tools never parse free text.
"""
from functools import lru_cache
from typing import Type, TypeVar, Union

import anthropic
from pydantic import BaseModel

from backend.config import settings

Schema = TypeVar("Schema", bound=BaseModel)

# If Claude's safety checks decline a request, which can happen on medical text,
# the API retries it on another model instead of failing.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AIUnavailable(Exception):
    """The AI service couldn't be reached or isn't set up."""


class AIDeclined(Exception):
    """The AI declined the request, or returned nothing usable."""


@lru_cache(maxsize=1)
def get_client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def ask(
    system: str,
    content: Union[str, list],
    schema: Type[Schema],
    effort: str = "medium",
    max_tokens: int = 16000,
) -> Schema:
    try:
        response = get_client().beta.messages.parse(
            model=settings.llm_model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_format=schema,
            output_config={"effort": effort},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
    except (anthropic.APIError, TypeError) as error:  # TypeError: no API key is set
        raise AIUnavailable(str(error)) from error

    if response.stop_reason in ("refusal", "max_tokens") or response.parsed_output is None:
        raise AIDeclined(f"Stopped with {response.stop_reason}")
    return response.parsed_output
