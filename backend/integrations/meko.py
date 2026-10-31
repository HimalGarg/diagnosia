"""Shared agent context from Meko, through its documented MCP server.

Tool names and arguments are read from the server's own schema at run time, so nothing here is
guessed: a tool that isn't offered, or an argument it doesn't accept, is refused before sending.
Meko is supplemental context. It is never proof of identity, consent, approval or clinical truth,
and every failure is reported as unavailable, never as an empty result.

Only technical workflow text goes in. In a datapack, the conversation bucket is visible to every
member, so conversations aren't used for anything about a case. The API key is only ever sent to
the MCP server, never to an upload URL.
"""
import base64
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from backend.config import settings

logger = logging.getLogger("diagnosia.integrations")

PROTOCOL_VERSION = "2025-06-18"
TIMEOUT_SECONDS = 15
MAX_ATTEMPTS = 2
AGENT_ID = "diagnosia-careflow"
USER_AGENT = "diagnosia-careflow/1.0"
JSON_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}
CONTEXT_REF = re.compile(r"^[A-Za-z0-9_:.-]{1,120}$")
ACCESS_ERRORS = {"datapack_access_denied", "FORBIDDEN", "UNAUTHORIZED"}


class MekoUnavailable(Exception):
    """Meko couldn't be reached, refused, or didn't answer usefully."""


class MekoRejected(MekoUnavailable):
    """Meko refused this key or this datapack. Retrying won't help until someone changes that."""


class MekoUnsupported(MekoUnavailable):
    """The server doesn't offer that tool or argument, so nothing was sent."""


@dataclass
class ContextResult:
    available: bool
    items: List[Any]
    reason: str = ""


class MekoClient:
    """A small MCP client over streamable HTTP: initialize, tools/list and tools/call."""

    def __init__(self, url: str, api_key: str, datapack_id: str, post=None):
        self.url = url
        self.api_key = api_key
        self.datapack_id = datapack_id
        self._post = post or httpx.post
        self._session: Optional[str] = None
        self._tools: Optional[Dict[str, dict]] = None
        self._next_id = 0
        self._protocol = PROTOCOL_VERSION
        self._conversation: Optional[str] = None

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": self._protocol,
            "User-Agent": USER_AGENT,
        }
        if self._session:
            headers["Mcp-Session-Id"] = self._session
        return headers

    def _rpc(self, method: str, params: dict) -> dict:
        self._next_id += 1
        body = {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params}
        failure = "no attempt made"
        for _attempt in range(MAX_ATTEMPTS):
            try:
                response = self._post(self.url, headers=self._headers(), json=body, timeout=TIMEOUT_SECONDS)
            except httpx.HTTPError as error:
                failure = type(error).__name__
                continue
            if response.status_code in (401, 403):
                raise MekoRejected(f"status {response.status_code}")  # retrying won't help
            if response.status_code == 404 and self._session:
                self._session = None  # the server forgot the session, so the next call starts a new one
                self._tools = None
                raise MekoUnavailable("session expired")
            if response.status_code >= 500 or response.status_code == 429:
                failure = f"status {response.status_code}"
                continue
            if response.status_code >= 400:
                raise MekoUnavailable(f"status {response.status_code}")
            self._session = response.headers.get("mcp-session-id", self._session)
            message = _message_from(response)
            if "error" in message:
                raise MekoUnavailable(f"error {message['error'].get('code')}")
            return message.get("result", {})
        raise MekoUnavailable(failure)

    def _connect(self) -> None:
        if self._tools is not None:
            return
        started = self._rpc("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "diagnosia", "version": "1.0"}})
        self._protocol = started.get("protocolVersion", PROTOCOL_VERSION)
        try:
            self._post(self.url, headers=self._headers(), json={"jsonrpc": "2.0", "method": "notifications/initialized"}, timeout=TIMEOUT_SECONDS)
        except httpx.HTTPError as error:
            raise MekoUnavailable(type(error).__name__) from error
        tools = self._rpc("tools/list", {}).get("tools", [])
        self._tools = {tool["name"]: tool.get("inputSchema", {}) for tool in tools}

    def tools(self) -> List[str]:
        self._connect()
        return sorted(self._tools)

    def _check(self, tool: str, arguments: Dict[str, Any]) -> None:
        """Checks a call against the schema the server gave, so a changed tool fails here and clearly."""
        if tool not in self._tools:
            raise MekoUnsupported(f"tool not offered: {tool}")
        schema = self._tools[tool]
        allowed = schema.get("properties")
        if allowed is None:
            return
        unknown = set(arguments) - set(allowed)
        if unknown:
            raise MekoUnsupported(f"{tool} doesn't accept: {sorted(unknown)}")
        missing = set(schema.get("required", [])) - set(arguments)
        if missing:
            raise MekoUnsupported(f"{tool} needs: {sorted(missing)}")
        for name, value in arguments.items():
            kind = JSON_TYPES.get(allowed[name].get("type")) if isinstance(allowed[name].get("type"), str) else None
            if kind is not None and not isinstance(value, kind):
                raise MekoUnsupported(f"{tool}: {name} has the wrong type")

    def call(self, tool: str, **arguments: Any) -> Any:
        self._connect()
        self._check(tool, arguments)
        result = self._rpc("tools/call", {"name": tool, "arguments": arguments})
        if result.get("isError"):
            raise MekoUnavailable(f"{tool} reported an error")
        found = _content_of(result)
        if isinstance(found, dict) and found.get("error"):
            # Meko reports most failures as an ordinary result, so they have to be looked for.
            if found["error"] in ACCESS_ERRORS:
                raise MekoRejected(f"{tool}: {found['error']}")
            raise MekoUnavailable(f"{tool}: {found['error']}")
        return found

    def _conversation_id(self) -> str:
        """Meko needs one on nearly every call, for tracing. One per process works across datapacks and
        spares the monthly conversation limit. Nothing is ever written into it."""
        if self._conversation is None:
            created = self.call("conversation_create", title="Diagnosia service", agent_id=AGENT_ID)
            identifier = created.get("id") if isinstance(created, dict) else None
            if not identifier:
                raise MekoUnavailable("no conversation id")
            self._conversation = identifier
        return self._conversation

    def _scoped(self, tool: str, datapack_id: Optional[str] = None, **arguments: Any) -> Any:
        """The datapack comes from server configuration or from SQLite after the access gate, never
        from a request."""
        return self.call(tool, datapack_id=datapack_id or self.datapack_id, conversation_id=self._conversation_id(), **arguments)

    def describe_datapack(self) -> Any:
        return self._scoped("datapack_describe", include_status=True)

    def knowledge(self, query: str, limit: int = 5) -> ContextResult:
        try:
            return ContextResult(True, _as_list(self._scoped("knowledgebase_search", query=query, limit=limit, agent_id=AGENT_ID)))
        except MekoUnavailable as error:
            logger.warning("Meko knowledge search unavailable: %s", type(error).__name__)
            return ContextResult(False, [], "Shared context is unavailable right now.")

    def remember(self, text: str, metadata: Dict[str, Any], run_id: Optional[str] = None) -> Any:
        """Saved under this agent's name. Only fixed or reviewed text ever reaches this method."""
        arguments: Dict[str, Any] = {"text": text, "agent_id": AGENT_ID, "metadata": json.dumps(metadata)}
        if run_id:
            arguments["run_id"] = run_id
        return self._scoped("memory_add", **arguments)

    def search_memory(self, query: str, limit: int = 5) -> List[Any]:
        return _as_list(self._scoped("memory_search", query=query, limit=limit, agent_id=AGENT_ID))

    def save_handoff(self, handoff: dict, knowledge_found: int = 0) -> str:
        """Raises on failure, so the delivery worker can retry it. Returns a safe reference to the note."""
        text = (
            f"Handoff {handoff['handoff_id']}: {handoff['source_module']} to {handoff['target_module']}. "
            f"{handoff['summary']} Human review required: {handoff['human_review_required']}."
        )
        saved = self.remember(
            text,
            {"schema_version": handoff["schema_version"], "kind": "handoff", "provenance": "diagnosia-careflow", "knowledge_found": knowledge_found},
            handoff["correlation_id"],
        )
        return _reference_from(saved, handoff["handoff_id"])

    def create_datapack(self, name: str) -> str:
        """Names are unique across all of Meko, and a created datapack uses up a slot for good."""
        created = self.call("datapack_create", name=name, conversation_id=self._conversation_id())
        identifier = created.get("datapack_id") if isinstance(created, dict) else None
        if not identifier:
            raise MekoUnavailable("no datapack id")
        return identifier

    def keep_conversations_out_of_search(self, datapack_id: str) -> None:
        self.call("datapack_update", datapack_id=datapack_id, conversation_search_opt_out=True, conversation_id=self._conversation_id())

    def delete_datapack(self, datapack_id: str) -> None:
        """Permanent. Takes the id it's given, so it can never fall back to the configured datapack."""
        self.call("datapack_delete", datapack_id=datapack_id, conversation_id=self._conversation_id())

    def search(self, datapack_id: str, query: str, limit: int = 10) -> List[Any]:
        """Raises on failure, because finding nothing and being unable to look must never read the same."""
        return _as_list(self._scoped("knowledgebase_search", datapack_id, query=query, limit=limit, agent_id=AGENT_ID))

    def upload_request(self, filename: str, data: bytes, datapack_id: Optional[str] = None) -> dict:
        """Asks for a presigned URL. Meko samples the first 3,072 bytes against the file's extension."""
        found = self._scoped(
            "knowledgebase_upload_document",
            datapack_id,
            filename=filename,
            size=len(data),
            content_hash=hashlib.sha256(data).hexdigest(),
            content_head_base64=base64.b64encode(data[:3072]).decode(),
        )
        if not isinstance(found, dict):
            raise MekoUnavailable("unexpected upload reply")
        return found

    def document_status(self, document_key: str, datapack_id: Optional[str] = None) -> dict:
        found = self._scoped("knowledgebase_document_status", datapack_id, document_key=document_key)
        if not isinstance(found, dict):
            raise MekoUnavailable("unexpected status reply")
        return found

    def delete_document(self, document_id: str, datapack_id: Optional[str] = None) -> Any:
        """Permanent. Called by an operator's explicit command, or to replace a corrected pack item."""
        return self._scoped("knowledgebase_delete_document", datapack_id, document_id=document_id)


def _reference_from(saved: Any, fallback: str) -> str:
    """A short reference to what was saved. Never text from a reply that might not be an identifier."""
    if isinstance(saved, dict):
        for key in ("id", "memory_id"):
            value = saved.get(key)
            if isinstance(value, str) and CONTEXT_REF.match(value):
                return f"meko:{value}"
    return f"meko:{fallback}"


def _message_from(response: httpx.Response) -> dict:
    """A streamable HTTP server may answer with plain JSON or a one-message event stream."""
    kind = response.headers.get("content-type", "")
    try:
        if "text/event-stream" not in kind:
            return response.json()
        for line in response.text.splitlines():
            if line.startswith("data:") and line[5:].strip():
                return json.loads(line[5:])
    except ValueError as error:
        raise MekoUnavailable("unreadable reply") from error
    raise MekoUnavailable("empty reply")


def _content_of(result: dict) -> Any:
    found = result.get("structuredContent")
    if isinstance(found, dict) and set(found) == {"result"} and isinstance(found["result"], str):
        return _decoded(found["result"])  # how Meko's server wraps a tool's JSON text
    if found is not None:
        return found
    return _decoded("\n".join(part.get("text", "") for part in result.get("content", []) if part.get("type") == "text"))


def _decoded(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return text


def _as_list(found: Any) -> List[Any]:
    if isinstance(found, list):
        return found
    if isinstance(found, dict):
        for key in ("results", "documents", "items", "chunks"):
            if isinstance(found.get(key), list):
                return found[key]
    return [found] if found else []


_client: Optional[MekoClient] = None


def client() -> Optional[MekoClient]:
    """None unless switched on and configured. The key and datapack stay on the server."""
    if not (settings.meko_enabled and settings.meko_datapack_id):
        return None
    return _shared()


def pack_client() -> Optional[MekoClient]:
    """For patient packs, which need no configured datapack: each patient's comes from SQLite."""
    return _shared() if settings.meko_patient_packs_enabled else None


def _shared() -> Optional[MekoClient]:
    global _client
    if not settings.meko_api_key:
        return None
    if _client is None:
        _client = MekoClient(settings.meko_url, settings.meko_api_key, settings.meko_datapack_id)
    return _client


def reset() -> None:
    global _client
    _client = None
