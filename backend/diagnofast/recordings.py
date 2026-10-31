"""Patients' recorded answers, encrypted like every other stored document.

They sit under the documents folder, so backups include them. Without a key ring
nothing is kept: the interview carries on with the transcript alone.
"""
import logging
from pathlib import Path
from typing import Iterator, Optional, Tuple

from backend.config import settings
from backend.records import vault
from backend.records.documents import write_private

FOLDER = "intake-audio"
EXTENSIONS = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
    "audio/wav": ".wav",
}
CONTENT_TYPES = {extension: kind for kind, extension in EXTENSIONS.items()}

logger = logging.getLogger("diagnosia.diagnofast")


def _path(intake_id: int, name: str) -> Path:
    return settings.documents_dir / FOLDER / str(intake_id) / f"{Path(name).name}.enc"


def _context(intake_id: int, name: str) -> str:
    return f"intake-audio:{intake_id}:{name}"


def save(intake_id: int, turn: int, data: bytes, content_type: str) -> Optional[str]:
    name = f"{turn}{EXTENSIONS[content_type]}"
    try:
        write_private(_path(intake_id, name), vault.encrypt(data, _context(intake_id, name)))
    except vault.VaultUnavailable:
        logger.warning("Recording not kept for intake %s: no key ring", intake_id)
        return None
    return name


def load(intake_id: int, name: str) -> Tuple[bytes, str]:
    data = vault.decrypt(_path(intake_id, name).read_bytes(), _context(intake_id, name))
    return data, CONTENT_TYPES[Path(name).suffix]


def everything() -> Iterator[Tuple[Path, str]]:
    """Each stored recording with its encryption context, for the operator's key commands."""
    root = settings.documents_dir / FOLDER
    if root.exists():
        for path in sorted(root.glob("*/*.enc")):
            yield path, _context(int(path.parent.name), path.stem)
