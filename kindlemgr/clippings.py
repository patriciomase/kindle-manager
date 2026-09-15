"""Parse documents/My Clippings.txt into per-book highlight files.

The file is a flat log of records separated by a line of '='. Each record is:

    Book Title (Author Name)
    - Your Highlight on page 12 | Location 173-174 | Added on Monday, 21 July 2026 22:04:11
    <blank>
    the highlighted text
    ==========

Kindle writes the metadata line in the device's language, so the kind of
record is detected from keywords in several languages rather than a fixed
English phrase.
"""

import os
import re

SEPARATOR = "=========="

KINDS = {
    "highlight": ("highlight", "subrayado", "destaque", "surlignement"),
    "note": ("note", "nota", "notiz"),
    "bookmark": ("bookmark", "marcador", "lesezeichen", "signet"),
}


def _kind(meta_line):
    low = meta_line.lower()
    for kind, words in KINDS.items():
        if any(w in low for w in words):
            return kind
    return "highlight"


def _split_title_author(line):
    line = line.strip().lstrip("﻿")
    m = re.match(r"^(.*)\(([^()]*)\)\s*$", line)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return line, ""


def _location(meta_line):
    m = re.search(r"([Ll]ocation|[Pp]osición|[Pp]osicion)\s+([\d\-]+)", meta_line)
    if m:
        return m.group(2)
    m = re.search(r"(page|página|pagina)\s+([\w\-]+)", meta_line, re.I)
    return m.group(2) if m else ""


def parse(text):
    """Return a list of clipping dicts in file order."""
    clippings = []
    for block in text.split(SEPARATOR):
        lines = block.strip("\r\n").splitlines()
        lines = [ln.rstrip("\r") for ln in lines]
        while lines and not lines[0].strip():
            lines.pop(0)
        if len(lines) < 2:
            continue
        title, author = _split_title_author(lines[0])
        if not title:
            continue
        meta = lines[1].strip()
        body = "\n".join(lines[2:]).strip()
        if not body and _kind(meta) != "bookmark":
            continue
        clippings.append({
            "title": title,
            "author": author,
            "kind": _kind(meta),
            "location": _location(meta),
            "meta": meta.lstrip("- ").strip(),
            "text": body,
        })
    return clippings


def parse_file(path):
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        return parse(fh.read())


def slugify(name):
    slug = re.sub(r"[^\w\s-]", "", name, flags=re.UNICODE).strip()
    slug = re.sub(r"[\s_-]+", "-", slug)
    return (slug[:80] or "untitled").strip("-")


def export(clippings, outdir):
    """Write one markdown file per book. Returns [(path, count), ...]."""
    by_book = {}
    for c in clippings:
        by_book.setdefault((c["title"], c["author"]), []).append(c)

    os.makedirs(outdir, exist_ok=True)
    written = []
    for (title, author), items in sorted(by_book.items()):
        path = os.path.join(outdir, slugify(title) + ".md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"# {title}\n")
            if author:
                fh.write(f"\n*{author}*\n")
            fh.write(f"\n{len(items)} clippings\n")
            for c in items:
                loc = f" — loc. {c['location']}" if c["location"] else ""
                fh.write(f"\n---\n\n**{c['kind']}**{loc}\n\n")
                if c["text"]:
                    fh.write("> " + c["text"].replace("\n", "\n> ") + "\n")
        written.append((path, len(items)))
    return written
