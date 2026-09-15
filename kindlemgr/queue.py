"""The spool.

A book converted while the Kindle is unplugged waits here until it is next
connected. That is what makes "paste a URL now, read it later" work: nothing
in the pipeline needs the device to be present except the final copy.
"""

import json
import os
import shutil
import time
import uuid

STATE_DIR = os.environ.get(
    "KINDLE_STATE",
    os.path.join(
        os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
        "kindle-manager",
    ),
)
QUEUE_DIR = os.path.join(STATE_DIR, "queue")
SENT_DIR = os.path.join(STATE_DIR, "sent")
LOG_PATH = os.path.join(STATE_DIR, "kindle-manager.log")


def _ensure():
    os.makedirs(QUEUE_DIR, exist_ok=True)
    os.makedirs(SENT_DIR, exist_ok=True)


def log(message):
    _ensure()
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_PATH, "a", encoding="utf-8") as fh:
        fh.write(f"{stamp}  {message}\n")


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def add(book_path, title, author="", source=""):
    """Move a finished .azw3 into the spool. Returns its queue entry."""
    _ensure()
    item_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    filename = os.path.basename(book_path)
    dest = os.path.join(QUEUE_DIR, item_id + os.path.splitext(filename)[1])
    shutil.move(book_path, dest)
    entry = {
        "id": item_id,
        "title": title,
        "author": author,
        "source": source,
        "filename": filename,
        "path": dest,
        "size": os.path.getsize(dest),
        "added": time.time(),
    }
    _write(os.path.join(QUEUE_DIR, item_id + ".json"), entry)
    log(f"queued  {title}")
    return entry


def pending():
    """Queue entries, oldest first."""
    _ensure()
    items = []
    for name in os.listdir(QUEUE_DIR):
        if not name.endswith(".json"):
            continue
        entry = _read(os.path.join(QUEUE_DIR, name))
        if entry and os.path.exists(entry.get("path", "")):
            items.append(entry)
    return sorted(items, key=lambda e: e["added"])


def sent(limit=25):
    """Recently delivered entries, newest first."""
    _ensure()
    items = []
    for name in os.listdir(SENT_DIR):
        if name.endswith(".json"):
            entry = _read(os.path.join(SENT_DIR, name))
            if entry:
                items.append(entry)
    items.sort(key=lambda e: e.get("delivered", e["added"]), reverse=True)
    return items[:limit]


def remove(item_id):
    """Drop a queued item without delivering it."""
    meta = os.path.join(QUEUE_DIR, item_id + ".json")
    entry = _read(meta)
    if not entry:
        return False
    for path in (entry.get("path"), meta):
        try:
            os.remove(path)
        except OSError:
            pass
    log(f"dropped {entry.get('title', item_id)}")
    return True


def flush(documents, on_event=None):
    """Copy every queued book onto the device. Returns (delivered, failed)."""
    delivered, failed = [], []
    for entry in pending():
        dest = os.path.join(documents, entry["filename"])
        try:
            shutil.copyfile(entry["path"], dest)
            os.sync()
        except OSError as exc:
            failed.append((entry, str(exc)))
            log(f"FAILED  {entry['title']}: {exc}")
            if on_event:
                on_event("failed", entry, str(exc))
            continue

        entry["delivered"] = time.time()
        entry["dest"] = dest
        _write(os.path.join(SENT_DIR, entry["id"] + ".json"), entry)
        for path in (entry["path"], os.path.join(QUEUE_DIR, entry["id"] + ".json")):
            try:
                os.remove(path)
            except OSError:
                pass
        delivered.append(entry)
        log(f"sent    {entry['title']}")
        if on_event:
            on_event("sent", entry, None)
    return delivered, failed
