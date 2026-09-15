"""Read the book library off a mounted Kindle.

Two eras of storage live side by side on a Paperwhite 4 and a naive
find for *.azw/*.pdf misses half of it:

  old:    documents/Some Book.azw3        + documents/Some Book.sdr/
  modern: documents/Some Book.sdr/assets/attachables/xxxx.kfx

So a book is keyed by its *sidecar name*, not by the content file, and the
.kfx buried inside the .sdr counts as that book's content.
"""

import os

BOOK_EXTS = {".azw", ".azw3", ".azw6", ".mobi", ".prc", ".pdf", ".txt", ".kfx"}

# Things that live in documents/ but are not the user's books.
SKIP_DIR_NAMES = {"dictionaries"}
SKIP_PREFIXES = ("Personal letter_PSNL",)
SKIP_STEMS = {"My Clippings"}
SKIP_SUBSTRINGS = ("Kindle User", "Kindle_User", "User's Guide")


class Book:
    def __init__(self, key):
        self.key = key            # sidecar/base name, e.g. "Chess Fundamentals - Capablanca"
        self.formats = set()      # {"azw3"} or {"kfx"}
        self.size = 0             # total bytes of content files
        self.files = []           # content files belonging to this book
        self.sdr = None           # path to the .sdr sidecar, if present

    @property
    def title(self):
        return self.key

    @property
    def fmt(self):
        return "+".join(sorted(self.formats)) if self.formats else "-"

    def as_dict(self):
        return {
            "title": self.title,
            "format": self.fmt,
            "size": self.size,
            "files": self.files,
            "sdr": self.sdr,
        }


def _is_skipped(rel_parts, key):
    if any(p in SKIP_DIR_NAMES for p in rel_parts[:-1]):
        return True
    if key in SKIP_STEMS:
        return True
    if key.startswith(SKIP_PREFIXES):
        return True
    return any(s in key for s in SKIP_SUBSTRINGS)


def _book_key(rel_parts, stem):
    """A .kfx inside 'Foo.sdr/assets/attachables/' belongs to book 'Foo'."""
    for part in rel_parts:
        if part.endswith(".sdr"):
            return part[: -len(".sdr")]
    return stem


def scan(documents):
    """Return the books on the device, sorted by title."""
    books = {}

    def book(key):
        return books.setdefault(key, Book(key))

    for dirpath, dirnames, filenames in os.walk(documents):
        rel = os.path.relpath(dirpath, documents)
        rel_parts = [] if rel == "." else rel.split(os.sep)

        if any(p in SKIP_DIR_NAMES for p in rel_parts):
            dirnames[:] = []
            continue

        # A .sdr with no content file of its own is still evidence of a book.
        for d in dirnames:
            if d.endswith(".sdr"):
                key = d[: -len(".sdr")]
                if not _is_skipped(rel_parts + [d], key):
                    book(key).sdr = os.path.join(dirpath, d)

        for name in filenames:
            stem, ext = os.path.splitext(name)
            if ext.lower() not in BOOK_EXTS:
                continue
            key = _book_key(rel_parts, stem)
            if _is_skipped(rel_parts + [name], key):
                continue
            path = os.path.join(dirpath, name)
            b = book(key)
            b.formats.add(ext.lower().lstrip("."))
            b.files.append(path)
            try:
                b.size += os.path.getsize(path)
            except OSError:
                pass

    return sorted(books.values(), key=lambda b: b.key.lower())


def find(documents, pattern):
    """Books whose title contains `pattern`, case-insensitively."""
    needle = pattern.lower()
    return [b for b in scan(documents) if needle in b.key.lower()]


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
