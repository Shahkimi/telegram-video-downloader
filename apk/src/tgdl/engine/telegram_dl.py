"""
Telegram download engine.

fast_download_media is the parallel, fault tolerant downloader from the original script (v2.6.0).
It is unchanged apart from one line: small files use at most 8 connections, never more than the
configured parallel_workers (the app lowers it on phones).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from telethon import errors

from ..util import clean_filename, friendly_error
from .models import DownloadItem, ItemState, ProgressSink, SpeedTracker

log = logging.getLogger("tgdl.download")

async def fast_download_media(client, message, peer, output_path, progress_cb=None, parallel_workers=16, chunk_size=512 * 1024, max_retries=5):
    """
    High-Speed Parallel Downloader.
    Splits the file into contiguous byte ranges and downloads them concurrently through
    Telethon's iter_download, which routes requests to the data center that stores the file,
    and handles timeouts and short flood waits.
    A range that keeps failing raises instead of being skipped, so the file on disk is either
    complete and byte-exact or the download fails. Nothing is ever zero-padded.
    """
    file_size = getattr(message.file, "size", 0) if message.file else 0
    doc = getattr(message.media, 'document', None) if message and message.media else None

    async def single_stream_download():
        if client:
            result = await client.download_media(message, file=output_path, progress_callback=progress_cb)
        else:
            result = await message.download_media(file=output_path, progress_callback=progress_cb)
        if not result or not os.path.exists(result):
            raise IOError("Telegram returned no file data")
        if doc and file_size and os.path.getsize(result) != file_size:
            raise IOError(f"Incomplete file: expected {file_size} bytes, got {os.path.getsize(result)}")
        return result

    # Fallback to standard telethon download_media for non-documents or small files (< 1MB)
    if not client or not doc or not file_size or file_size < 1024 * 1024:
        return await single_stream_download()

    request_size = chunk_size
    total_parts = (file_size + request_size - 1) // request_size

    # Tune workers dynamically based on file size
    if file_size < 50 * 1024 * 1024:
        workers = min(8, parallel_workers)
    else:
        workers = parallel_workers
    workers = max(1, min(workers, total_parts))

    # Split the file into one contiguous range per worker, aligned to request_size
    parts_per_worker = (total_parts + workers - 1) // workers
    ranges = []
    for i in range(workers):
        start = i * parts_per_worker * request_size
        end = min(start + parts_per_worker * request_size, file_size)
        if start < end:
            ranges.append((start, end))

    # Pre-allocate output file so each range can be written at its own offset
    with open(output_path, "wb") as f:
        f.truncate(file_size)

    downloaded_bytes = 0
    file_write_lock = asyncio.Lock()
    current_doc = doc

    async def refresh_document():
        nonlocal current_doc
        fresh_msg = await client.get_messages(peer, ids=message.id)
        fresh_doc = getattr(fresh_msg.media, 'document', None) if fresh_msg and fresh_msg.media else None
        if not fresh_doc or fresh_doc.id != doc.id:
            raise IOError("Message media changed or is no longer available")
        current_doc = fresh_doc

    out_fp = open(output_path, "r+b")

    async def download_range(start, end):
        nonlocal downloaded_bytes
        pos = start
        failures = 0
        while pos < end:
            stream = client.iter_download(
                current_doc,
                offset=pos,
                limit=(end - pos + request_size - 1) // request_size,
                request_size=request_size,
                file_size=file_size
            )
            try:
                async for chunk in stream:
                    chunk = bytes(chunk[:end - pos])
                    if not chunk:
                        break
                    async with file_write_lock:
                        out_fp.seek(pos)
                        out_fp.write(chunk)
                    pos += len(chunk)
                    downloaded_bytes += len(chunk)
                    failures = 0
                    if progress_cb:
                        progress_cb(downloaded_bytes, file_size)
                    if pos >= end:
                        break

                if pos < end:
                    raise IOError(f"Telegram ended the stream early at byte {pos} (expected {end})")

            except (errors.FileReferenceExpiredError, errors.FilerefUpgradeNeededError):
                failures += 1
                if failures > max_retries:
                    raise
                await refresh_document()

            except errors.FloodWaitError as e:
                await asyncio.sleep(e.seconds + 1)

            except Exception as e:
                # CDN-hosted files need decryption that only download_media performs
                if type(e).__name__ == "_CdnRedirect":
                    raise
                failures += 1
                if failures > max_retries:
                    raise
                await asyncio.sleep(min(2 ** failures, 30))

            finally:
                try:
                    await stream.close()
                except Exception:
                    pass

    tasks = [asyncio.ensure_future(download_range(start, end)) for start, end in ranges]
    use_single_stream = False
    try:
        await asyncio.gather(*tasks)
    except Exception as e:
        if type(e).__name__ != "_CdnRedirect":
            raise
        use_single_stream = True
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        out_fp.close()

    if use_single_stream:
        return await single_stream_download()

    actual_size = os.path.getsize(output_path)
    if downloaded_bytes != file_size or actual_size != file_size:
        raise IOError(f"Incomplete file: expected {file_size} bytes, received {downloaded_bytes}, on disk {actual_size}")

    return output_path


# ---------------------------------------------------------------------
# File naming
# ---------------------------------------------------------------------

def build_filename(message: Any) -> str:
    """Caption first line, else the original file name, else the date. Extension is always fixed up."""
    file_name = ""
    text = getattr(message, "text", None)
    if text:
        first_line = text.split("\n")[0].strip()
        if first_line:
            file_name = clean_filename(first_line)[:50].strip()

    f = getattr(message, "file", None)
    if not file_name and f is not None and getattr(f, "name", None):
        file_name = clean_filename(f.name)

    if not file_name:
        msg_date = getattr(message, "date", None)
        date_str = msg_date.strftime("%Y%m%d_%H%M%S") if msg_date else f"msg_{message.id}"
        file_name = f"file_{date_str}"

    ext = getattr(f, "ext", "") if f is not None else ""
    if ext:
        if not ext.startswith("."):
            ext = f".{ext}"
        if not file_name.lower().endswith(ext.lower()):
            file_name = f"{file_name}{ext}"
    elif not os.path.splitext(file_name)[1]:
        file_name = f"{file_name}.bin"
    return file_name


def _same_size(path: str, expected: int) -> bool:
    try:
        return bool(expected) and os.path.getsize(path) == expected
    except OSError:
        return False


def resolve_output_path(
    directory: str,
    name: str,
    expected_size: int,
    msg_id: int,
    reserved: set[str],
    skip_existing: bool = True,
) -> tuple[str, bool]:
    """
    Pick where a file goes. Returns (path, skip).
    Same name and same size already on disk -> skip (when skip_existing).
    Same name but different size, or another download is writing that name -> "name (msg123).ext".
    """
    stem, ext = os.path.splitext(name)

    def variant(n: int) -> str:
        suffix = f" (msg{msg_id})" if n == 1 else f" (msg{msg_id}-{n})"
        return os.path.join(directory, f"{stem}{suffix}{ext}")

    path = os.path.join(directory, name)
    if path not in reserved and not os.path.exists(path):
        return path, False
    if path not in reserved and os.path.exists(path):
        if _same_size(path, expected_size):
            return (path, True) if skip_existing else (path, False)
        if not skip_existing:
            return path, False  # caller asked for fresh copies, overwrite like the old script did

    n = 1
    while True:
        cand = variant(n)
        if cand not in reserved:
            if not os.path.exists(cand):
                return cand, False
            if _same_size(cand, expected_size):
                return (cand, True) if skip_existing else (cand, False)
            if not skip_existing:
                return cand, False
        n += 1


# ---------------------------------------------------------------------
# One message -> one file
# ---------------------------------------------------------------------

def _remove(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _peer_of(item: DownloadItem) -> Any:
    msg = item.message
    return item.peer or getattr(msg, "peer_id", None) or getattr(msg, "input_chat", None) or getattr(msg, "chat_id", None)


async def download_message(
    client: Any,
    item: DownloadItem,
    downloads_dir: str,
    sink: ProgressSink,
    *,
    workers: int = 16,
    chunk_kb: int = 512,
    skip_existing: bool = True,
    reserved: set[str] | None = None,
    max_retries: int = 3,
) -> bool:
    """Download item.message into downloads_dir through a .part file. Updates item and tells sink."""
    reserved = reserved if reserved is not None else set()
    message = item.message
    peer = _peer_of(item)

    try:
        os.makedirs(downloads_dir, exist_ok=True)
    except OSError as exc:
        item.state, item.error, item.finished_at = ItemState.FAILED, f"Cannot create folder {downloads_dir}: {exc}", time.time()
        sink.finished(item)
        return False

    name = item.filename or build_filename(message)
    output_path, skip = resolve_output_path(downloads_dir, name, item.total, message.id, reserved, skip_existing)
    item.filename = os.path.basename(output_path)

    if skip:
        item.state, item.path, item.finished_at = ItemState.SKIPPED, output_path, time.time()
        item.done = item.total = item.total or os.path.getsize(output_path)
        sink.finished(item)
        return True

    part_path = f"{output_path}.part"
    reserved.add(output_path)
    item.state, item.started_at, item.error = ItemState.DOWNLOADING, time.time(), None
    sink.started(item)

    tracker = SpeedTracker()

    def progress_cb(received: int, total: int) -> None:
        if total:
            item.total = total
        item.done = received
        item.speed = tracker.update(received)
        sink.progress(item)

    try:
        for attempt in range(1, max_retries + 1):
            try:
                _remove(part_path)
                item.done, item.speed = 0, 0.0
                tracker = SpeedTracker()

                current = message
                if peer is not None:
                    try:  # fresh copy: file references in old messages expire
                        fresh = await client.get_messages(peer, ids=message.id)
                        if fresh and fresh.media:
                            current = fresh
                    except Exception:  # noqa: BLE001
                        pass

                saved = await fast_download_media(
                    client, current, peer, part_path,
                    progress_cb=progress_cb,
                    parallel_workers=max(1, workers),
                    chunk_size=chunk_kb * 1024,
                )
                if not saved or not os.path.exists(saved):
                    raise IOError("Download produced no file")
                os.replace(saved, output_path)

                item.state, item.path, item.finished_at = ItemState.DONE, output_path, time.time()
                item.done = item.total = item.total or os.path.getsize(output_path)
                item.speed = 0.0
                sink.finished(item)
                return True

            except asyncio.CancelledError:
                _remove(part_path)
                item.state, item.finished_at = ItemState.CANCELLED, time.time()
                sink.finished(item)
                raise
            except Exception as exc:  # noqa: BLE001
                _remove(part_path)
                item.error = friendly_error(exc)
                log.warning("download of message %s failed (attempt %s/%s): %s", message.id, attempt, max_retries, item.error)
                if attempt < max_retries:
                    await asyncio.sleep(1.5)
                    continue
                item.state, item.finished_at = ItemState.FAILED, time.time()
                sink.finished(item)
                return False
        return False
    finally:
        reserved.discard(output_path)
