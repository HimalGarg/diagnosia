"""Downloads ISIC 2019 in parallel, resumes after any interruption, and proves the result is intact.

One connection to S3 can be very slow, while several together do much better. Progress is saved per
chunk, so a stopped run carries on from where it was. A file that was partly downloaded some other
way is kept: the bytes already there count as done, and the final check covers them too.

The check rebuilds S3's multipart ETag from the file's own bytes, so a corrupt download can't pass.

Run from the project folder:  python -m backend.skin.training.download_isic
"""
import hashlib
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Tuple

import httpx

from backend.skin.training.prepare_split import DATA_DIR

URL = "https://isic-archive.s3.us-east-1.amazonaws.com/challenges/2019/ISIC_2019_Training_Input.zip"
TARGET = DATA_DIR / "ISIC_2019_Training_Input.zip"
PROGRESS = TARGET.with_suffix(".progress.json")
CHUNK_BYTES = 32 * 1024 * 1024
WORKERS = 8
TIMEOUT_SECONDS = 60
PART_BYTES = 50 * 1024 * 1024  # the part size that reproduces this object's 187-part ETag


def expected() -> Tuple[int, str]:
    response = httpx.head(URL, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    return int(response.headers["content-length"]), response.headers["etag"].strip('"')


def etag_of(path: Path, part_bytes: int = PART_BYTES) -> str:
    """S3's multipart ETag: the MD5 of each part's MD5, then the number of parts."""
    digests: List[bytes] = []
    with path.open("rb") as file:
        while True:
            part = file.read(part_bytes)
            if not part:
                break
            digests.append(hashlib.md5(part).digest())
    return f"{hashlib.md5(b''.join(digests)).hexdigest()}-{len(digests)}"


def chunks_for(size: int, have: int) -> List[Tuple[int, int]]:
    """Whole chunks still needed. Bytes already on disk from an earlier run are kept."""
    start = have - have % CHUNK_BYTES
    return [(offset, min(offset + CHUNK_BYTES, size) - 1) for offset in range(start, size, CHUNK_BYTES)]


def fetch(start: int, end: int) -> bytes:
    delay = 2.0
    while True:
        try:
            response = httpx.get(URL, headers={"Range": f"bytes={start}-{end}"}, timeout=TIMEOUT_SECONDS)
            if response.status_code == 206 and len(response.content) == end - start + 1:
                return response.content
        except httpx.HTTPError:
            pass
        time.sleep(delay)
        delay = min(delay * 2, 60)


def main() -> int:
    size, etag = expected()
    if PROGRESS.exists():
        saved = json.loads(PROGRESS.read_text())
        base, done = saved["base"], {tuple(item) for item in saved["done"]}
    else:
        # Chunks arrive out of order, so after this point the file's size says nothing about what's
        # complete. What was contiguous at the start is saved once, and only that is trusted.
        base, done = min(TARGET.stat().st_size if TARGET.exists() else 0, size), set()
        print(f"Keeping {base} bytes already on disk.", flush=True)
    todo = [chunk for chunk in chunks_for(size, base) if chunk not in done]
    print(f"{len(todo)} chunks to fetch of {size} bytes.", flush=True)
    if not TARGET.exists():
        TARGET.touch()
    lock = threading.Lock()
    started = time.monotonic()
    fetched = {"bytes": 0}

    def work(chunk: Tuple[int, int]) -> None:
        data = fetch(*chunk)
        with lock:
            with TARGET.open("r+b") as file:
                file.seek(chunk[0])
                file.write(data)
            done.add(chunk)
            fetched["bytes"] += len(data)
            PROGRESS.write_text(json.dumps({"base": base, "done": sorted(done)}))
            rate = fetched["bytes"] / max(time.monotonic() - started, 1) / 1024 / 1024
            print(f"{len(done)} chunks done, {rate:.2f} MB/s", flush=True)

    with ThreadPoolExecutor(WORKERS) as pool:
        list(pool.map(work, todo))
    if TARGET.stat().st_size != size:
        print("The size doesn't match.", file=sys.stderr)
        return 1
    print("Checking integrity.", flush=True)
    found = etag_of(TARGET)
    if found != etag:
        print(f"The file failed its integrity check: {found} against {etag}.", file=sys.stderr)
        return 1
    PROGRESS.unlink(missing_ok=True)
    print("Download verified against S3's ETag.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
