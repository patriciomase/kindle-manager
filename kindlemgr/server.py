"""The paste-a-URL inbox.

A tiny LAN service: paste a link (or hit the bookmarklet) and the article is
fetched, converted and queued. A background thread watches for the Kindle and
flushes the queue onto it the moment it appears, so the device never has to be
plugged in at the time you find something worth reading.

Standard library only -- the heavy dependencies stay inside Docker.
"""

import json
import os
import queue as stdqueue
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

POLL_SECONDS = 5
MAX_JOBS = 30

_jobs = []                     # newest last; each: {id, url, title, state, detail, at}
_jobs_lock = threading.Lock()
_work = stdqueue.Queue()
_job_seq = 0


def _add_job(url):
    global _job_seq
    with _jobs_lock:
        _job_seq += 1
        job = {"id": _job_seq, "url": url, "title": url, "state": "pending",
               "detail": "waiting", "at": time.time()}
        _jobs.append(job)
        del _jobs[:-MAX_JOBS]
    _work.put(job)
    return job


def _update(job, **fields):
    with _jobs_lock:
        job.update(fields)


def _worker():
    while True:
        job = _work.get()
        workdir = tempfile.mkdtemp(prefix="kindle-inbox-")
        try:
            _update(job, state="working", detail="fetching")

            def progress(msg, job=job):
                _update(job, detail=msg)

            path, title, author = convert.prepare(job["url"], workdir, on_event=progress)
            entry = spool.add(path, title, author, source=job["url"])
            _update(job, state="queued", title=title,
                    detail=f"queued · {library.human_size(entry['size'])}")
            _flush_if_connected()
        except Exception as exc:                       # noqa: BLE001 - surfaced in the UI
            _update(job, state="error", detail=str(exc)[:300])
            spool.log(f"ERROR   {job['url']}: {exc}")
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
            _work.task_done()


def _flush_if_connected():
    docs = device.documents_dir()
    if not docs or not spool.pending():
        return []
    delivered, _failed = spool.flush(docs)
    return delivered


def _watcher():
    """Deliver the queue as soon as the Kindle shows up."""
    was_connected = False
    while True:
        try:
            connected = device.is_connected()
            if connected and not was_connected:
                spool.log("kindle connected")
                _flush_if_connected()
            elif connected:
                _flush_if_connected()
            elif was_connected:
                spool.log("kindle disconnected")
            was_connected = connected
        except Exception as exc:                       # noqa: BLE001 - never kill the thread
            spool.log(f"watcher error: {exc}")
        time.sleep(POLL_SECONDS)


def _state():
    with _jobs_lock:
        jobs = [dict(j) for j in reversed(_jobs)]
    return {
        "kindle": device.find_mount(),
        "connected": device.is_connected(),
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

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
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
            delivered = _flush_if_connected()
            return self._json({"delivered": [e["title"] for e in delivered]})

        return self._json({"error": "not found"}, 404)


BOOKMARKLET_OK = """<!doctype html><meta charset="utf-8">
<title>Sent to Kindle</title>
<body style="font:16px system-ui;padding:2rem;text-align:center">
<p>Queued for the Kindle.</p>
<script>setTimeout(function(){window.close()},900)</script>
</body>"""


def serve(host="0.0.0.0", port=8137):
    threading.Thread(target=_worker, daemon=True).start()
    threading.Thread(target=_watcher, daemon=True).start()

    httpd = ThreadingHTTPServer((host, port), Handler)
    spool.log(f"inbox listening on {host}:{port}")
    print(f"kindle inbox → http://{host}:{port}  (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
