"""Tests run against a fake Kindle tree — no hardware needed (KINDLE_DIR)."""

import os
import shutil
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kindlemgr import clippings, convert, device, library  # noqa: E402

CLIPPINGS_SAMPLE = """﻿Chess Fundamentals (José Raúl Capablanca)
- Your Highlight on page 12 | Location 173-174 | Added on Monday, 21 July 2026 22:04:11

A knight on the rim is dim.
==========
Chess Fundamentals (José Raúl Capablanca)
- Your Note on page 12 | Location 174 | Added on Monday, 21 July 2026 22:05:00

remember this one
==========
El Eternauta (Héctor Germán Oesterheld)
- La subrayado en la página 5 | posición 40-41 | Añadido el lunes, 21 de julio de 2026

Nevaba sobre Buenos Aires.
==========
"""


def build_fake_kindle(root):
    docs = os.path.join(root, "documents")
    os.makedirs(docs)

    # old era: content file at top level + sidecar
    with open(os.path.join(docs, "Chess Fundamentals - Capablanca.azw3"), "wb") as fh:
        fh.write(b"x" * 2048)
    os.makedirs(os.path.join(docs, "Chess Fundamentals - Capablanca.sdr"))

    # modern era: kfx buried inside the sidecar
    attach = os.path.join(docs, "El Eternauta.sdr", "assets", "attachables")
    os.makedirs(attach)
    with open(os.path.join(attach, "abc123.kfx"), "wb") as fh:
        fh.write(b"y" * 4096)

    # things that are not books
    os.makedirs(os.path.join(docs, "dictionaries"))
    with open(os.path.join(docs, "dictionaries", "oxford.azw"), "wb") as fh:
        fh.write(b"z" * 999)
    with open(os.path.join(docs, "Kindle User's Guide.azw"), "wb") as fh:
        fh.write(b"g" * 100)
    os.makedirs(os.path.join(docs, "Personal letter_PSNL!ABC.sdr"))
    with open(os.path.join(docs, "My Clippings.txt"), "w", encoding="utf-8") as fh:
        fh.write(CLIPPINGS_SAMPLE)
    return docs


class LibraryTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="fake-kindle-")
        self.docs = build_fake_kindle(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_lists_both_storage_eras(self):
        titles = [b.title for b in library.scan(self.docs)]
        self.assertEqual(titles, ["Chess Fundamentals - Capablanca", "El Eternauta"])

    def test_kfx_inside_sdr_is_attributed_to_its_book(self):
        book = library.find(self.docs, "eternauta")[0]
        self.assertEqual(book.fmt, "kfx")
        self.assertEqual(book.size, 4096)
        self.assertTrue(book.sdr.endswith("El Eternauta.sdr"))

    def test_skips_dictionaries_guide_clippings_and_receipts(self):
        titles = " ".join(b.title for b in library.scan(self.docs))
        for unwanted in ("oxford", "User's Guide", "My Clippings", "PSNL"):
            self.assertNotIn(unwanted, titles)

    def test_find_is_case_insensitive_substring(self):
        self.assertEqual(len(library.find(self.docs, "CHESS")), 1)
        self.assertEqual(library.find(self.docs, "nope"), [])


class ClippingsTest(unittest.TestCase):
    def test_parses_title_author_kind_and_location(self):
        items = clippings.parse(CLIPPINGS_SAMPLE)
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]["title"], "Chess Fundamentals")
        self.assertEqual(items[0]["author"], "José Raúl Capablanca")
        self.assertEqual(items[0]["kind"], "highlight")
        self.assertEqual(items[0]["location"], "173-174")
        self.assertEqual(items[1]["kind"], "note")

    def test_detects_spanish_highlight(self):
        items = clippings.parse(CLIPPINGS_SAMPLE)
        self.assertEqual(items[2]["kind"], "highlight")
        self.assertEqual(items[2]["title"], "El Eternauta")

    def test_export_writes_one_file_per_book(self):
        out = tempfile.mkdtemp(prefix="clips-")
        try:
            written = clippings.export(clippings.parse(CLIPPINGS_SAMPLE), out)
            self.assertEqual(len(written), 2)
            names = sorted(os.path.basename(p) for p, _ in written)
            self.assertEqual(names, ["Chess-Fundamentals.md", "El-Eternauta.md"])
            with open(written[0][0], encoding="utf-8") as fh:
                body = fh.read()
            self.assertIn("A knight on the rim is dim.", body)
        finally:
            shutil.rmtree(out, ignore_errors=True)


class ConvertTest(unittest.TestCase):
    def test_classifies_sources(self):
        self.assertEqual(convert.classify("33870"), ("gutenberg", "33870"))
        self.assertEqual(
            convert.classify("https://www.gutenberg.org/ebooks/33870"),
            ("gutenberg", "33870"),
        )
        self.assertEqual(
            convert.classify("https://example.com/book.epub")[0], "download")
        self.assertEqual(
            convert.classify("https://example.com/posts/why-zig")[0], "article")
        # a .html link is an article to extract, not a file to download verbatim
        self.assertEqual(
            convert.classify("http://example.com/IncIdeas/BitterLesson.html")[0], "article")
        self.assertEqual(
            convert.classify("https://example.com/book.pdf")[0], "download")
        with self.assertRaises(convert.ConvertError):
            convert.classify("not a url or a file")

    def test_classifies_existing_file(self):
        with tempfile.NamedTemporaryFile(suffix=".epub") as tmp:
            self.assertEqual(convert.classify(tmp.name), ("file", tmp.name))

    def test_gutenberg_url_prefers_the_images_build(self):
        self.assertEqual(
            convert.gutenberg_url("33870"),
            "https://www.gutenberg.org/cache/epub/33870/pg33870-images.epub",
        )

    def test_filenames_are_vfat_safe(self):
        name = convert.safe_filename('Some/Title: "quoted"', "An\\Author")
        self.assertNotRegex(name, r'[\\/:*?"<>|]')
        self.assertTrue(name.endswith(".azw3"))
        self.assertTrue(convert.safe_filename("", "").startswith("Untitled"))


CONTAINER_XML = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="content.opf"
    media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

OPF = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Chess Fundamentals</dc:title>
    <dc:creator>Capablanca, Jose Raul</dc:creator>
  </metadata>
</package>"""


class EpubMetadataTest(unittest.TestCase):
    def _epub(self, container=CONTAINER_XML, opf=OPF):
        fd, path = tempfile.mkstemp(suffix=".epub")
        os.close(fd)
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("META-INF/container.xml", container)
            zf.writestr("content.opf", opf)
        self.addCleanup(os.remove, path)
        return path

    def test_reads_title_and_author(self):
        title, author = convert.epub_metadata(self._epub())
        self.assertEqual(title, "Chess Fundamentals")

    def test_flips_surname_first_authors(self):
        _title, author = convert.epub_metadata(self._epub())
        self.assertEqual(author, "Jose Raul Capablanca")

    def test_strips_wikisource_author_prefix(self):
        opf = OPF.replace("Capablanca, Jose Raul", "Autor:Marco Aurelio")
        _title, author = convert.epub_metadata(self._epub(opf=opf))
        self.assertEqual(author, "Marco Aurelio")

    def test_unreadable_epub_is_not_fatal(self):
        fd, path = tempfile.mkstemp(suffix=".epub")
        os.write(fd, b"not a zip")
        os.close(fd)
        self.addCleanup(os.remove, path)
        self.assertEqual(convert.epub_metadata(path), ("", ""))


MOUNTINFO_SAMPLE = """\
25 30 0:22 / /proc rw,nosuid,relatime shared:5 - proc proc rw
36 30 8:33 / /media/user/Kindle rw,nosuid,nodev,relatime shared:1 - vfat /dev/sdc1 rw,uid=1000
41 30 8:49 / /media/user/My\\040USB\\040Stick rw,relatime shared:2 - vfat /dev/sdd1 rw
44 30 259:2 / / rw,relatime shared:3 - ext4 /dev/nvme0n1p2 rw
"""


class MountinfoTest(unittest.TestCase):
    def test_parses_device_mountpoint_and_source(self):
        entries = list(device.parse_mountinfo(MOUNTINFO_SAMPLE))
        self.assertIn(("8:33", "/media/user/Kindle", "/dev/sdc1"), entries)
        self.assertIn(("259:2", "/", "/dev/nvme0n1p2"), entries)

    def test_unescapes_octal_in_mountpoints(self):
        points = [mp for _mm, mp, _src in device.parse_mountinfo(MOUNTINFO_SAMPLE)]
        self.assertIn("/media/user/My USB Stick", points)

    def test_ignores_lines_without_a_separator(self):
        self.assertEqual(list(device.parse_mountinfo("garbage line\n")), [])

    def test_kindle_dir_override_wins(self):
        tmp = tempfile.mkdtemp(prefix="override-")
        self.addCleanup(shutil.rmtree, tmp, True)
        os.environ["KINDLE_DIR"] = tmp
        self.addCleanup(os.environ.pop, "KINDLE_DIR", None)
        self.assertEqual(device.find_mount(), tmp)
        self.assertIsNone(device.find_device())

    def test_missing_override_directory_reads_as_disconnected(self):
        os.environ["KINDLE_DIR"] = "/nonexistent/kindle"
        self.addCleanup(os.environ.pop, "KINDLE_DIR", None)
        self.assertIsNone(device.find_mount())
        self.assertFalse(device.is_connected())


class UploadTest(unittest.TestCase):
    """POST /api/upload saves the file and queues a job -- no worker, no Calibre."""

    def setUp(self):
        from http.server import ThreadingHTTPServer
        import threading
        from kindlemgr import server
        self.server = server
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def _post(self, name, body):
        import json
        import urllib.error
        import urllib.parse
        import urllib.request
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.httpd.server_port}/api/upload", data=body,
            headers={"X-Filename": urllib.parse.quote(name)}, method="POST")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())

    def test_upload_is_saved_and_queued_under_its_name(self):
        status, job = self._post("Diálogos - Séneca.epub", b"PK fake epub")
        self.assertEqual(status, 200)
        self.addCleanup(shutil.rmtree, job["upload_dir"], True)
        self.assertEqual(job["title"], "Diálogos - Séneca.epub")
        with open(job["url"], "rb") as fh:
            self.assertEqual(fh.read(), b"PK fake epub")
        self.assertEqual(convert.classify(job["url"])[0], "file")

    def test_path_in_filename_is_stripped(self):
        status, job = self._post("../../etc/evil.txt", b"hi")
        self.assertEqual(status, 200)
        self.addCleanup(shutil.rmtree, job["upload_dir"], True)
        self.assertEqual(os.path.dirname(job["url"]), job["upload_dir"])

    def test_named_uploads_are_titled_by_filename(self):
        title = self.server._upload_title
        self.assertEqual(title({"upload_dir": "/x", "title": "Meditaciones.epub"}), "Meditaciones")
        self.assertEqual(title({"upload_dir": "/x", "title": "El arte de vivir.epub"}), "El arte de vivir")
        self.assertIsNone(title({"upload_dir": "/x", "title": "pg2680.epub"}))
        self.assertIsNone(title({"upload_dir": None, "title": "https://x.org/a b"}))

    def test_rejects_unknown_formats(self):
        status, res = self._post("script.sh", b"rm -rf /")
        self.assertEqual(status, 400)
        self.assertIn("error", res)


if __name__ == "__main__":
    unittest.main(verbosity=2)
