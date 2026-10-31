"""Puts Diagnosia's shared knowledge into Meko, through its documented upload tool.

The flow is the documented one: `knowledgebase_upload_document` gives a presigned URL, the file is
posted to it, and `knowledgebase_document_status` is polled until indexing finishes. Files live in
`knowledge_base/` with their provenance, so what's in Meko can always be traced back and rebuilt.

A file goes up only if it declares no clinical content and passes a scan for anything that looks
like a person's details or a secret. Uploading identical content again changes nothing.
"""
import hashlib
import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

import httpx

from backend.integrations.meko import MekoClient, MekoUnavailable

logger = logging.getLogger("diagnosia.integrations")

FOLDER = Path(__file__).parent / "knowledge_base"
REQUIRED_FIELDS = ("id", "title", "category", "version", "source", "review_status", "clinical_content")
CATEGORIES = {
    "module_documentation", "workflow_instructions", "extraction_templates", "terminology", "referral_workflow",
    "discharge_workflow", "technical_knowledge", "integration_operations",
}
REVIEWED = {"technical_reviewed"}
UPLOAD_TIMEOUT_SECONDS = 30
POLL_SECONDS = 2
POLL_ATTEMPTS = 30
# Anything shaped like a person's details or a credential stops the upload for a person to look at.
FORBIDDEN = {
    "email address": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "phone number": re.compile(r"(?<![\d.])(?:\+?91[ -]?)?[6-9]\d{9}(?!\d|\.\d)"),  # a sentence can end right after one
    "key or token": re.compile(r"\b(?:gsk_|mko_tkn_|AIza|sk-)[A-Za-z0-9_-]{10,}"),
    "connection string": re.compile(r"\b(?:mongodb(?:\+srv)?|postgres(?:ql)?)://\S+:\S+@"),
    "date of birth": re.compile(r"\b(?:DOB|date of birth)\s*[:=]\s*\d", re.IGNORECASE),
}


class KnowledgeRefused(Exception):
    """This file must not be uploaded."""


@dataclass
class Document:
    path: Path
    fields: Dict[str, str]
    data: bytes

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


def parse(path: Path) -> Document:
    # A Windows checkout has CRLF endings, and a file must hash the same on every machine.
    data = path.read_bytes().replace(b"\r\n", b"\n")
    text = data.decode("utf-8")
    if not text.startswith("---\n"):
        raise KnowledgeRefused(f"{path.name}: no provenance header")
    header = text[4:text.index("\n---\n")]
    fields = dict(line.split(": ", 1) for line in header.splitlines() if ": " in line)
    return Document(path, fields, data)


def check(document: Document) -> None:
    """Refuses a file rather than cleaning it, so a person sees why."""
    name = document.path.name
    missing = [field for field in REQUIRED_FIELDS if field not in document.fields]
    if missing:
        raise KnowledgeRefused(f"{name}: missing {missing}")
    if document.fields["category"] not in CATEGORIES:
        raise KnowledgeRefused(f"{name}: unknown category")
    if document.fields["review_status"] not in REVIEWED:
        raise KnowledgeRefused(f"{name}: not reviewed")
    if document.fields["clinical_content"] != "none":
        raise KnowledgeRefused(f"{name}: declares clinical content")
    text = document.data.decode("utf-8")
    for label, pattern in FORBIDDEN.items():
        if pattern.search(text):
            raise KnowledgeRefused(f"{name}: contains something like a {label}")


def corpus(folder: Path = FOLDER) -> List[Document]:
    documents = [parse(path) for path in sorted(folder.glob("*.md"))]
    for document in documents:
        check(document)
    return documents


def _send_file(url: str, reply: dict, document: Document, post: Callable) -> None:
    """The URL is presigned, so the Meko key is never sent with it. Form fields are used if given."""
    fields = reply.get("fields")
    if isinstance(fields, dict):
        response = post(url, data=fields, files={"file": (document.path.name, document.data)}, timeout=UPLOAD_TIMEOUT_SECONDS)
    else:
        headers = reply.get("headers") if isinstance(reply.get("headers"), dict) else {"Content-Type": "text/markdown"}
        response = post(url, content=document.data, headers=headers, timeout=UPLOAD_TIMEOUT_SECONDS)
    if response.status_code >= 300:
        raise MekoUnavailable(f"upload status {response.status_code}")


def _record(db: sqlite3.Connection, document: Document, key: Optional[str], state: str) -> None:
    db.execute(
        """
        INSERT INTO careflow_knowledge (document, version, content_hash, document_key, state, updated_at) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (document) DO UPDATE SET version = excluded.version, content_hash = excluded.content_hash,
            document_key = excluded.document_key, state = excluded.state, updated_at = excluded.updated_at
        """,
        (document.path.name, document.fields["version"], document.content_hash, key, state, datetime.now(timezone.utc).isoformat()),
    )
    db.commit()


def ingest(client: MekoClient, db: sqlite3.Connection, documents: Optional[List[Document]] = None, post: Callable = httpx.post, wait: Callable = time.sleep) -> List[Dict[str, str]]:
    """Uploads each file and waits for indexing. One failure is reported and doesn't stop the rest."""
    results = []
    for document in documents if documents is not None else corpus():
        name = document.path.name
        known = db.execute("SELECT content_hash, state FROM careflow_knowledge WHERE document = ?", (name,)).fetchone()
        if known and known["content_hash"] == document.content_hash and known["state"] == "completed":
            results.append({"document": name, "state": "unchanged"})
            continue
        try:
            reply = client.upload_request(name, document.data)
            if reply.get("already_uploaded"):
                _record(db, document, reply.get("key"), "completed")
                results.append({"document": name, "state": "already_uploaded"})
                continue
            if not reply.get("url") or not reply.get("key"):
                raise MekoUnavailable("upload reply had no URL or key")
            _send_file(reply["url"], reply, document, post)
            state = "indexing"
            for _attempt in range(POLL_ATTEMPTS):
                state = client.document_status(reply["key"]).get("state", "unknown")
                if state in ("completed", "failed", "abandoned", "oversized"):
                    break
                wait(POLL_SECONDS)
            _record(db, document, reply["key"], state)
            results.append({"document": name, "state": state})
        except MekoUnavailable as error:
            logger.warning("Knowledge file %s not uploaded: %s", name, type(error).__name__)
            results.append({"document": name, "state": f"failed: {type(error).__name__}"})
    return results


def seed_memory(client: MekoClient, entries: List[Dict[str, str]]) -> int:
    """Adds reviewed conventions as private memory. Promoting one to everyone is a person's decision,
    made in the Meko portal, because `memory_promote` can't be reversed."""
    for entry in entries:
        for label, pattern in FORBIDDEN.items():
            if pattern.search(entry["text"]):
                raise KnowledgeRefused(f"memory contains something like a {label}")
        client.remember(entry["text"], {"kind": "convention", "provenance": entry["source"], "version": entry["version"]})
    return len(entries)
