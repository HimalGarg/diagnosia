"""AI calls for the clinical tools, through OpenAI-compatible APIs: Groq and Google Gemini.

Each call returns a validated Pydantic object, so the tools never parse free text.
Models come from Settings. Text requests use strict schema output where the model
supports it. Images, and Gemini, use JSON mode, and the answer is still checked
against the schema here.

With both providers set up, a failed call moves on to the other one, or providers
take turns, as Settings say. Any OpenAI-compatible service can stand in for Groq
by setting AI_BASE_URL.
"""
import itertools
import json
import logging
import re
import threading
import time
from typing import List, Optional, Tuple, Type, TypeVar, Union

import httpx
from pydantic import BaseModel, ValidationError

from backend import settings_store
from backend.config import settings
from backend.integrations import execution

logger = logging.getLogger("diagnosia")

Schema = TypeVar("Schema", bound=BaseModel)

REQUEST_TIMEOUT_SECONDS = 120
MAX_ATTEMPTS = 2
MAX_RETRY_WAIT_SECONDS = 8
RETRY_STATUSES = (429, 500, 502, 503, 504)
# Models Groq documents as guaranteeing a schema. Others get JSON mode and our own check.
STRICT_MODELS = {"openai/gpt-oss-20b", "openai/gpt-oss-120b", "qwen/qwen3.8-27b", "meta-models/muse-spark-open"}


class AIUnavailable(Exception):
    """The AI service couldn't be reached."""


class AINotConfigured(AIUnavailable):
    """No API key has been set up."""


class AIRateLimited(AIUnavailable):
    """The free plan's limit was reached."""


class AIDeclined(Exception):
    """The AI declined the request, or returned nothing usable."""


def strict_schema(schema: Type[BaseModel]) -> dict:
    """Pydantic's schema in the shape strict mode requires: inlined, closed, all required."""
    full = schema.model_json_schema()
    definitions = full.pop("$defs", {})

    def clean(node):
        if isinstance(node, list):
            return [clean(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return clean(definitions[node["$ref"].rsplit("/", 1)[-1]])
        result = {key: clean(value) for key, value in node.items() if key not in ("title", "default")}
        if result.get("type") == "object":
            result["additionalProperties"] = False
            result["required"] = list(result.get("properties", {}))
        return result

    return clean(full)


def _has_image(content: Union[str, list]) -> bool:
    return isinstance(content, list) and any(part.get("type") == "image" for part in content)


def _message_content(content: Union[str, list]) -> Union[str, List[dict]]:
    """Tools describe images in one neutral form, converted here for the provider."""
    if isinstance(content, str):
        return content
    parts = []
    for part in content:
        if part["type"] == "image":
            source = part["source"]
            parts.append({"type": "image_url", "image_url": {"url": f"data:{source['media_type']};base64,{source['data']}"}})
        else:
            parts.append(part)
    return parts


def build_request(system: str, content: Union[str, list], schema: Type[Schema], model: str, max_tokens: int) -> dict:
    body = {"model": model, "temperature": 0, "max_completion_tokens": max_tokens, "stream": False}
    shape = strict_schema(schema)
    if model in STRICT_MODELS and not _has_image(content):
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": schema.__name__, "strict": True, "schema": shape},
        }
    else:
        system = f"{system}\n\nReply with one JSON object matching this JSON Schema, and nothing else:\n{json.dumps(shape)}"
        body["response_format"] = {"type": "json_object"}
    body["messages"] = [{"role": "system", "content": system}, {"role": "user", "content": _message_content(content)}]
    return body


def _post(body: dict, key: str, timeout: int, base_url: Optional[str] = None, attempts: int = MAX_ATTEMPTS) -> httpx.Response:
    url = f"{base_url or settings.ai_base_url}/chat/completions"
    wait = 1.0
    failure: Exception = AIUnavailable("no attempt made")
    for attempt in range(attempts):
        if attempt:
            time.sleep(wait)
        try:
            response = httpx.post(
                url, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=timeout
            )
        except httpx.HTTPError as error:
            failure = AIUnavailable(type(error).__name__)
            continue
        if response.status_code not in RETRY_STATUSES:
            return response
        if response.status_code == 429:
            failure = AIRateLimited("rate limit")
            try:
                wait = float(response.headers.get("retry-after", "1"))
            except ValueError:
                wait = 1.0
            if wait > MAX_RETRY_WAIT_SECONDS:
                break  # a daily limit: waiting here wouldn't help
        else:
            failure = AIUnavailable(f"status {response.status_code}")
    raise failure


def _parse(text: Optional[str], schema: Type[Schema]) -> Schema:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    try:
        return schema.model_validate_json(cleaned)
    except (ValidationError, ValueError) as error:
        raise AIDeclined(type(error).__name__) from error


_turns = itertools.count()
_turns_lock = threading.Lock()


def _providers_in_order(tool: str) -> List[str]:
    """Providers with a key, first choice first. Round robin moves the starting point each call.

    A tool pinned to a provider keeps it first, and only falls back when that one fails."""
    names = settings_store.provider_order(None, tool)
    pinned = tool in settings_store.READING_TOOLS and settings_store.get(f"ai.provider.{tool}") != "default"
    if len(names) > 1 and not pinned and settings_store.get("ai.strategy") == "round_robin":
        with _turns_lock:
            shift = next(_turns) % len(names)
        names = names[shift:] + names[:shift]
    return names


def _attempts(tool: str) -> List[Tuple[str, str]]:
    """Each (provider, model) to try, in order. Gemini may appear twice, with a backup model."""
    attempts = []
    for provider in _providers_in_order(tool):
        if provider == "gemini":
            attempts += [("gemini", model) for model in settings_store.gemini_models(tool)]
        else:
            attempts.append(("groq", settings_store.model_for(tool)))
    return attempts


def _ask_model(
    provider: str, model: str, system: str, content: Union[str, list], schema: Type[Schema], max_tokens: int, timeout: int, attempts: int
) -> Schema:
    key = settings_store.api_key(None, provider)
    base_url = settings.gemini_base_url if provider == "gemini" else None
    response = _post(build_request(system, content, schema, model, max_tokens), key, timeout, base_url, attempts)
    if response.status_code in (401, 403):
        raise AIUnavailable(f"status {response.status_code}")
    if response.status_code != 200:
        # 400 covers a schema the model couldn't meet, and 413 a request too big for the plan.
        raise AIDeclined(f"status {response.status_code}")
    try:
        choice = response.json()["choices"][0]
        message = choice["message"]
    except (ValueError, KeyError, IndexError, TypeError) as error:
        raise AIDeclined("unexpected reply") from error
    if choice.get("finish_reason") not in ("stop", None) or message.get("refusal"):
        raise AIDeclined(f"finished with {choice.get('finish_reason')}")
    return _parse(message.get("content"), schema)


def ask(
    system: str,
    content: Union[str, list],
    schema: Type[Schema],
    tool: str,
    max_tokens: int = 6000,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
) -> Schema:
    """Calls the model chosen for this tool, and the next choice if that one fails."""
    attempts = _attempts(tool)
    if not attempts:
        raise AINotConfigured("no API key")
    # With another choice waiting, a slow first one costs more than it's worth retrying.
    tries = MAX_ATTEMPTS if len(attempts) == 1 else 1
    failure: Exception = AIUnavailable("no attempt made")
    run = execution.AgentRun(tool)
    for provider, model in attempts:
        try:
            answer = _ask_model(provider, model, system, content, schema, max_tokens, timeout, tries)
            run.tried(provider, model, "succeeded")
            run.finish("succeeded")
            return answer
        except (AIUnavailable, AIDeclined) as error:
            failure = error
            run.tried(provider, model, type(error).__name__)
            if len(attempts) > 1:
                logger.warning("%s %s failed (%s), trying the next choice", provider, model, type(error).__name__)
    run.finish("failed")
    raise failure
