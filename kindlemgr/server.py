"""The paste-a-URL inbox.

A tiny LAN service: paste a link (or hit the bookmarklet) and the article is
fetched, converted and queued. The device never has to be plugged in at the
time you find something worth reading -- you deliver the queue when you next
connect it, with the button on the page or `kindle-flush`.

Delivery is manual by design: polling for a device you connect once a month
burns thousands of wake-ups for nothing. Set KINDLE_AUTOFLUSH=1 if you would
rather it deliver by itself; it then checks every AUTOFLUSH_SECONDS, which is
cheap now that mount detection is two file reads (see device.py).

Standard library only -- the heavy dependencies stay inside Docker.
"""

import json
import os
import queue as stdqueue
import re
import shutil
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import convert, device, library
from . import queue as spool

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(HERE, "web")

AUTOFLUSH = os.environ.get("KINDLE_AUTOFLUSH", "") not in ("", "0", "no", "false")
AUTOFLUSH_SECONDS = int(os.environ.get("KINDLE_AUTOFLUSH_SECONDS", 30))
MAX_JOBS = 30
MAX_UPLOAD = 200 * 1024 * 1024

_jobs = []                     # newest last; each: {id, url, title, state, detail, at}
_jobs_lock = threading.Lock()
_work = stdqueue.Queue()
_job_seq = 0


def _add_job(url, title=None, upload_dir=None):
    """Queue a conversion. `url` is anything convert.prepare accepts -- for an
    upload it is the saved file, and `upload_dir` is removed once it is done."""
    global _job_seq
    with _jobs_lock:
        _job_seq += 1
        job = {"id": _job_seq, "url": url, "title": title or url, "state": "pending",
               "detail": "waiting", "at": time.time(), "upload_dir": upload_dir}
        _jobs.append(job)
        del _jobs[:-MAX_JOBS]
    _work.put(job)
    return job


def _update(job, **fields):
    with _jobs_lock:
        job.update(fields)


def _upload_title(job):
    """An uploaded file a person named ("Meditaciones.epub", unlike the
    machine-made pg2680.epub) is titled by its name: exports such as
    Wikisource's put a chapter title like "Índice" in the metadata."""
    if not job["upload_dir"]:
        return None
    stem = os.path.splitext(job["title"])[0].strip()
    machine_made = re.fullmatch(r"[\w.-]*\d[\w.-]*", stem, re.ASCII)
    return stem if stem and not machine_made else None


def _worker():
    while True:
        job = _work.get()
        workdir = tempfile.mkdtemp(prefix="kindle-inbox-")
        try:
            _update(job, state="working", detail="fetching")

            def progress(msg, job=job):
                _update(job, detail=msg)

            path, title, author = convert.prepare(
                job["url"], workdir, title=_upload_title(job), on_event=progress)
            source = f"upload: {job['title']}" if job["upload_dir"] else job["url"]
            entry = spool.add(path, title, author, source=source)
            _update(job, state="queued", title=title,
                    detail=f"queued · {library.human_size(entry['size'])}")
            if AUTOFLUSH:
                _flush_if_connected()
        except Exception as exc:                       # noqa: BLE001 - surfaced in the UI
            _update(job, state="error", detail=str(exc)[:300])
            spool.log(f"ERROR   {job['url']}: {exc}")
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
            if job["upload_dir"]:
                shutil.rmtree(job["upload_dir"], ignore_errors=True)
            _work.task_done()


def _flush_if_connected():
    docs = device.documents_dir()
    if not docs or not spool.pending():
        return []
    delivered, _failed = spool.flush(docs)
    return delivered


def _watcher():
    """Optional auto-delivery. Off unless KINDLE_AUTOFLUSH is set."""
    was_connected = False
    while True:
        try:
            connected = device.is_connected()
            if connected:
                if not was_connected:
                    spool.log("kindle connected")
                _flush_if_connected()
            elif was_connected:
                spool.log("kindle disconnected")
            was_connected = connected
        except Exception as exc:                       # noqa: BLE001 - never kill the thread
            spool.log(f"watcher error: {exc}")
        time.sleep(AUTOFLUSH_SECONDS)


def _state():
    with _jobs_lock:
        jobs = [dict(j) for j in reversed(_jobs)]
    return {
        "kindle": device.find_mount(),
        "connected": device.is_connected(),
        "autoflush": AUTOFLUSH,
        "queue": [
            {"id": e["id"], "title": e["title"], "author": e.get("author", ""),
             "size": e["size"], "human_size": library.human_size(e["size"]),
             "source": e.get("source", ""), "added": e["added"]}
            for e in spool.pending()
        ],
        "sent": [
            {"id": e["id"], "title": e["title"],
             "human_size": library.human_size(e["size"]),
             "delivered": e.get("delivered")}
            for e in spool.sent(10)
        ],
        "jobs": jobs,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "kindle-manager"

    def log_message(self, fmt, *args):      # quieter than the default
        pass

    # -- helpers --
    def _send(self, code, body, content_type="text/html; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, code=200):
        self._send(code, json.dumps(data, ensure_ascii=False), "application/json; charset=utf-8")

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw or "{}")
        except ValueError:
            return dict(urllib.parse.parse_qsl(raw.decode("utf-8", "replace")))

    # -- routes --
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if parsed.path in ("/", "/index.html"):
            try:
                with open(os.path.join(WEB_DIR, "index.html"), "rb") as fh:
                    return self._send(200, fh.read())
            except OSError:
                return self._send(500, "web/index.html is missing")

        if parsed.path == "/api/state":
            return self._json(_state())

        if parsed.path == "/add":
            url = (params.get("u") or params.get("url") or [""])[0]
            if not url:
                return self._send(400, "<p>missing ?u=&lt;url&gt;</p>")
            _add_job(url)
            return self._send(200, BOOKMARKLET_OK)

        return self._send(404, "not found")

    def _upload(self):
        """Raw file body, name in X-Filename (URL-encoded) -- no multipart parsing."""
        name = os.path.basename(urllib.parse.unquote(self.headers.get("X-Filename") or ""))
        length = int(self.headers.get("Content-Length") or 0)
        ext = os.path.splitext(name)[1].lower()
        if ext not in convert.CONVERTIBLE | convert.PASSTHROUGH:
            return self._json({"error": f"can't use '{ext or name}' files"}, 400)
        if not 0 < length <= MAX_UPLOAD:
            return self._json({"error": "empty or too large"}, 413)

        upload_dir = tempfile.mkdtemp(prefix="kindle-upload-")
        path = os.path.join(upload_dir, name)
        remaining = length
        with open(path, "wb") as fh:
            while remaining:
                chunk = self.rfile.read(min(remaining, 1 << 20))
                if not chunk:
                    break
                fh.write(chunk)
                remaining -= len(chunk)
        if remaining:
            shutil.rmtree(upload_dir, ignore_errors=True)
            return self._json({"error": "upload interrupted"}, 400)
        return self._json(_add_job(path, title=name, upload_dir=upload_dir))

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/upload":
            return self._upload()
        data = self._body()

        if parsed.path == "/api/add":
            url = (data.get("url") or "").strip()
            if not url:
                return self._json({"error": "no url"}, 400)
            return self._json(_add_job(url))

        if parsed.path == "/api/delete":
            item_id = (data.get("id") or "").strip()
            return self._json({"removed": spool.remove(item_id)})

        if parsed.path == "/api/flush":
            docs = device.documents_dir()
            if not docs:
                return self._json({"error": "no Kindle connected"}, 409)
            delivered, failed = spool.flush(docs)
            return self._json({
                "delivered": [e["title"] for e in delivered],
                "failed": [e["title"] for e, _err in failed],
            })

        return self._json({"error": "not found"}, 404)


BOOKMARKLET_OK = """<!doctype html><meta charset="utf-8">
<title>Sent to Kindle</title>
<body style="font:16px system-ui;padding:2rem;text-align:center">
<p>Queued for the Kindle.</p>
<script>setTimeout(function(){window.close()},900)</script>
</body>"""


def serve(host="0.0.0.0", port=8137):
    threading.Thread(target=_worker, daemon=True).start()
    if AUTOFLUSH:
        threading.Thread(target=_watcher, daemon=True).start()

    httpd = ThreadingHTTPServer((host, port), Handler)
    spool.log(f"inbox listening on {host}:{port}")
    mode = "auto-delivering" if AUTOFLUSH else "deliver with the button or kindle-flush"
    print(f"kindle inbox → http://{host}:{port}  ({mode}; Ctrl-C to stop)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
