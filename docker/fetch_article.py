"""Fetch a web article and write clean, Kindle-ready HTML.

Usage: fetch-article <url> <output.html>
Prints a JSON blob of metadata (title, author, site, date) on stdout.
"""

import html
import json
import re
import sys

import trafilatura
from trafilatura.settings import use_config

TEMPLATE = """<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<title>{title}</title>
<meta name="author" content="{author}">
</head>
<body>
{heading}{body}
<hr>
<p><small>Source: <a href="{url}">{url}</a></small></p>
</body>
</html>
"""


def main():
    if len(sys.argv) != 3:
        print("usage: fetch-article <url> <output.html>", file=sys.stderr)
        return 2
    url, out_path = sys.argv[1], sys.argv[2]

    config = use_config()
    config.set("DEFAULT", "DOWNLOAD_TIMEOUT", "30")

    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        print(f"could not fetch {url}", file=sys.stderr)
        return 1

    body = trafilatura.extract(
        downloaded,
        output_format="html",
        include_images=True,
        include_links=True,
        include_tables=True,
        favor_precision=True,
        config=config,
    )
    if not body:
        print("no readable article found at that URL", file=sys.stderr)
        return 1

    # trafilatura wraps its output in its own <html><body>; drop it so the
    # document we hand Calibre is not nested inside itself.
    body = re.sub(r"^\s*<html[^>]*>\s*<body[^>]*>", "", body, flags=re.I)
    body = re.sub(r"</body>\s*</html>\s*$", "", body, flags=re.I).strip()

    try:
        meta = trafilatura.extract_metadata(downloaded)
    except Exception:
        meta = None

    title = (getattr(meta, "title", None) or url)[:180]
    author = getattr(meta, "author", None) or getattr(meta, "sitename", None) or ""
    site = getattr(meta, "sitename", None) or ""
    date = getattr(meta, "date", None) or ""
    lang = getattr(meta, "language", None) or "en"

    byline = " · ".join(p for p in (author, site, date) if p)

    # Many articles already open with their own <h1>; only add one if not.
    heading = ""
    if not re.match(r"\s*<h1", body, re.I):
        heading = f"<h1>{html.escape(title)}</h1>\n"
        if byline:
            heading += f"<p><i>{html.escape(byline)}</i></p>\n"

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(TEMPLATE.format(
            lang=html.escape(lang, quote=True),
            title=html.escape(title),
            author=html.escape(author, quote=True),
            heading=heading,
            body=body,
            url=html.escape(url, quote=True),
        ))

    json.dump({"title": title, "author": author, "site": site,
               "date": date, "language": lang}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
