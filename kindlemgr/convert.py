"""Turn whatever the user pasted into an AZW3 file.

Accepted sources: a local file, a Project Gutenberg id or URL, a direct link
to an ebook file, or the URL of any web article.

Dependencies live in Docker images built on demand, so no box needs Calibre or
Python libraries installed to use this -- except that a natively installed
`ebook-convert` is preferred when present, because it is much faster to start.
"""

import json
import os
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCKER_DIR = os.path.join(HERE, "docker")

FETCH_IMAGE = "kindle-manager-fetch"
CALIBRE_IMAGE = "kindle-manager-calibre"

# Formats Calibre can reflow into a good AZW3.
CONVERTIBLE = {".epub", ".html", ".htm", ".txt", ".mobi", ".rtf", ".fb2", ".docx", ".azw", ".prc"}
# Formats that go to the device untouched.
PASSTHROUGH = {".azw3", ".pdf", ".kfx"}
# Extensions that mean "this URL is a book to download". Deliberately excludes
# .html/.htm: a link ending in .html is an article to extract, not a file to
# grab verbatim -- grabbing it verbatim ships the site's navigation and ads.
DOWNLOADABLE = (CONVERTIBLE | PASSTHROUGH) - {".html", ".htm"}

# The PW4 is 300 ppi, 1072x1448 -- kindle_pw3 is Calibre's profile for it.
OUTPUT_PROFILE = "kindle_pw3"

ILLEGAL_IN_VFAT = r'[\\/:*?"<>|\x00-\x1f]'


class ConvertError(Exception):
    pass


def _run(cmd, **kwargs):
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    return subprocess.run(cmd, **kwargs)


def have_docker():
    return shutil.which("docker") is not None and _run(["docker", "info"]).returncode == 0


def ensure_image(tag, dockerfile, on_event=None):
    """Build a Docker image the first time it is needed."""
    if _run(["docker", "image", "inspect", tag]).returncode == 0:
        return
    if on_event:
        on_event(f"building {tag} (first run, this takes a few minutes)")
    result = _run([
        "docker", "build", "-f", os.path.join(DOCKER_DIR, dockerfile), "-t", tag, DOCKER_DIR,
    ])
    if result.returncode != 0:
        raise ConvertError(f"could not build {tag}:\n{result.stderr[-2000:]}")


def safe_filename(title, author=""):
    name = f"{title} - {author}" if author else title
    name = re.sub(ILLEGAL_IN_VFAT, "-", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return (name[:120] or "Untitled") + ".azw3"


DC = "{http://purl.org/dc/elements/1.1/}"


def epub_metadata(path):
    """Read (title, author) out of an EPUB's OPF. Returns ("", "") if unreadable.

    Downloads land on disk as pg33870.epub and similar, so the filename makes a
    terrible title -- the book knows its own name, so ask it.
    """
    try:
        with zipfile.ZipFile(path) as zf:
            container = ET.fromstring(zf.read("META-INF/container.xml"))
            rootfile = container.find(".//{*}rootfile")
            opf_path = rootfile.get("full-path") if rootfile is not None else None
            if not opf_path:
                return "", ""
            opf = ET.fromstring(zf.read(opf_path))
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError):
        return "", ""

    def text(tag):
        el = opf.find(f".//{DC}{tag}")
        return (el.text or "").strip() if el is not None and el.text else ""

    title = text("title")
    # Wikisource exports credit the author's wiki page: "Autor:Marco Aurelio".
    author = re.sub(r"^(?:Autor|Author):", "", text("creator"))
    # Gutenberg writes "Capablanca, Jose Raul" -- flip it back to reading order.
    if author.count(",") == 1:
        last, first = (part.strip() for part in author.split(","))
        if last and first:
            author = f"{first} {last}"
    return title, author


# --- source classification ---------------------------------------------------

GUTENBERG_EBOOK = re.compile(r"gutenberg\.org/(?:ebooks|cache/epub)/(\d+)")


def gutenberg_url(book_id):
    return f"https://www.gutenberg.org/cache/epub/{book_id}/pg{book_id}-images.epub"


def classify(target):
    """Return (kind, value) where kind is file | gutenberg | download | article."""
    if os.path.exists(target):
        return "file", os.path.abspath(target)
    if target.isdigit():
        return "gutenberg", target
    if not re.match(r"^https?://", target, re.I):
        raise ConvertError(f"not a file, a Gutenberg id, or a URL: {target}")

    m = GUTENBERG_EBOOK.search(target)
    if m:
        return "gutenberg", m.group(1)

    path = urllib.parse.urlparse(target).path
    ext = os.path.splitext(path)[1].lower()
    if ext in DOWNLOADABLE:
        return "download", target
    return "article", target


def _download(url, dest, on_event=None):
    if on_event:
        on_event(f"downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "kindle-manager/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=60) as response, open(dest, "wb") as fh:
            shutil.copyfileobj(response, fh)
    except Exception as exc:
        raise ConvertError(f"download failed: {exc}")
    return dest


def fetch_article(url, workdir, on_event=None):
    """Extract a readable article into HTML. Returns (path, metadata)."""
    if not have_docker():
        raise ConvertError(
            "Docker is required to read web articles (it runs the extractor).\n"
            "Install it, or pass a file / Gutenberg id instead."
        )
    ensure_image(FETCH_IMAGE, "Dockerfile.fetch", on_event)
    if on_event:
        on_event(f"extracting article from {url}")

    out_name = "article.html"
    result = _run([
        "docker", "run", "--rm",
        "-v", f"{workdir}:/work",
        "-u", f"{os.getuid()}:{os.getgid()}",
        FETCH_IMAGE, url, f"/work/{out_name}",
    ])
    if result.returncode != 0:
        raise ConvertError(result.stderr.strip() or "article extraction failed")

    try:
        meta = json.loads(result.stdout)
    except ValueError:
        meta = {}
    return os.path.join(workdir, out_name), meta


def to_azw3(src, out_path, title, author="", on_event=None):
    """Convert `src` to AZW3 at `out_path` using Calibre."""
    args = [
        "--output-profile", OUTPUT_PROFILE,
        "--title", title,
    ]
    if author:
        args += ["--authors", author]

    if shutil.which("ebook-convert"):
        if on_event:
            on_event("converting with local Calibre")
        result = _run(["ebook-convert", src, out_path] + args)
    else:
        if not have_docker():
            raise ConvertError(
                "Neither Calibre nor Docker is available -- one of them has to be."
            )
        ensure_image(CALIBRE_IMAGE, "Dockerfile.calibre", on_event)
        if on_event:
            on_event("converting with Calibre (Docker)")
        workdir = os.path.dirname(os.path.abspath(out_path))
        src_abs = os.path.abspath(src)
        if os.path.dirname(src_abs) != workdir:
            shutil.copy(src_abs, workdir)
            src_abs = os.path.join(workdir, os.path.basename(src_abs))
        result = _run([
            "docker", "run", "--rm",
            "-v", f"{workdir}:/work",
            "-u", f"{os.getuid()}:{os.getgid()}",
            CALIBRE_IMAGE,
            f"/work/{os.path.basename(src_abs)}",
            f"/work/{os.path.basename(out_path)}",
        ] + args)

    if result.returncode != 0 or not os.path.exists(out_path):
        raise ConvertError((result.stderr or result.stdout or "").strip()[-2000:]
                           or "conversion failed")
    return out_path


def prepare(target, workdir, title=None, author=None, on_event=None):
    """Resolve any source into a finished AZW3. Returns (path, title, author)."""
    kind, value = classify(target)
    meta = {}

    if kind == "gutenberg":
        src = _download(gutenberg_url(value), os.path.join(workdir, f"pg{value}.epub"), on_event)
    elif kind == "download":
        name = os.path.basename(urllib.parse.urlparse(value).path) or "download"
        src = _download(value, os.path.join(workdir, name), on_event)
    elif kind == "article":
        src, meta = fetch_article(value, workdir, on_event)
    else:
        src = value

    if os.path.splitext(src)[1].lower() == ".epub" and not (title and author):
        epub_title, epub_author = epub_metadata(src)
        title = title or epub_title
        author = author or epub_author

    title = title or meta.get("title") or os.path.splitext(os.path.basename(src))[0]
    author = author or meta.get("author") or ""

    ext = os.path.splitext(src)[1].lower()
    out_name = safe_filename(title, author)
    out_path = os.path.join(workdir, out_name)

    if ext in PASSTHROUGH:
        # Already in a format the Kindle reads over USB -- just name it properly.
        final = os.path.join(workdir, re.sub(r"\.azw3$", ext, out_name))
        if os.path.abspath(src) != os.path.abspath(final):
            shutil.copy(src, final)
        return final, title, author

    if ext not in CONVERTIBLE:
        raise ConvertError(
            f"don't know how to handle '{ext}'. EPUB does not work over USB; "
            "convertible inputs are: " + ", ".join(sorted(CONVERTIBLE))
        )

    to_azw3(src, out_path, title, author, on_event)
    return out_path, title, author
