"""Command line entry point. `kindle <subcommand>`, or the kindle-* shims."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

from . import __version__, clippings, convert, device, library, queue

IS_TTY = sys.stdout.isatty()


def _c(code, text):
    return f"\033[{code}m{text}\033[0m" if IS_TTY else text


def info(msg):
    print(f"{_c('36', '→')} {msg}")


def ok(msg):
    print(f"{_c('32', '✓')} {msg}")


def warn(msg):
    print(f"{_c('33', '!')} {msg}", file=sys.stderr)


def die(msg, code=1):
    print(f"{_c('31', '✗')} {msg}", file=sys.stderr)
    sys.exit(code)


# --- add ---------------------------------------------------------------------

def cmd_add(args):
    workdir = tempfile.mkdtemp(prefix="kindle-add-")
    try:
        try:
            path, title, author = convert.prepare(
                args.target, workdir, args.title, args.author, on_event=info
            )
        except convert.ConvertError as exc:
            die(str(exc))

        entry = queue.add(path, title, author, source=args.target)
        size = library.human_size(entry["size"])

        docs = None if args.queue else device.documents_dir()
        if docs:
            delivered, failed = queue.flush(docs)
            if failed:
                die(f"could not copy onto the Kindle: {failed[0][1]}")
            ok(f'"{title}" → Kindle ({size})')
            print("  it appears on the home screen once you unplug (the device indexes on disconnect)")
        else:
            ok(f'"{title}" queued ({size})')
            print(f"  {len(queue.pending())} waiting — delivered next time the Kindle is plugged in")

        if args.json:
            print(json.dumps(entry, ensure_ascii=False))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# --- ls ----------------------------------------------------------------------

def cmd_ls(args):
    try:
        docs = device.require_documents()
    except device.DeviceError as exc:
        die(str(exc))

    books = library.scan(docs)
    if args.json:
        print(json.dumps([b.as_dict() for b in books], indent=2, ensure_ascii=False))
        return
    if not books:
        print("no books on the device")
        return

    width = min(max(len(b.title) for b in books), 70)
    for b in books:
        title = b.title if len(b.title) <= width else b.title[: width - 1] + "…"
        print(f"{title:<{width}}  {b.fmt:<6} {library.human_size(b.size):>9}")
    noun = "book" if len(books) == 1 else "books"
    print(f"\n{len(books)} {noun}, {library.human_size(sum(b.size for b in books))}")


# --- rm ----------------------------------------------------------------------

def cmd_rm(args):
    try:
        docs = device.require_documents()
    except device.DeviceError as exc:
        die(str(exc))

    matches = library.find(docs, args.pattern)
    if not matches:
        die(f"nothing matches '{args.pattern}'")

    print(f"{len(matches)} book(s) match '{args.pattern}':")
    for b in matches:
        print(f"  {b.title}  ({b.fmt}, {library.human_size(b.size)})")
        if b.sdr:
            print(f"    + sidecar {os.path.basename(b.sdr)}")

    if args.dry_run:
        return
    if not args.yes:
        reply = input("remove these, sidecars included? [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            print("cancelled")
            return

    for b in matches:
        for path in b.files:
            try:
                os.remove(path)
            except OSError as exc:
                warn(f"{path}: {exc}")
        if b.sdr and os.path.isdir(b.sdr):
            shutil.rmtree(b.sdr, ignore_errors=True)
        queue.log(f"removed {b.title}")
        ok(f"removed {b.title}")
    os.sync()


# --- clippings ---------------------------------------------------------------

def cmd_clippings(args):
    try:
        docs = device.require_documents()
    except device.DeviceError as exc:
        die(str(exc))

    path = os.path.join(docs, "My Clippings.txt")
    if not os.path.exists(path):
        die("no My Clippings.txt on the device (nothing highlighted yet?)")

    items = clippings.parse_file(path)
    if not items:
        die("My Clippings.txt is empty")

    if args.json:
        print(json.dumps(items, indent=2, ensure_ascii=False))
        return

    written = clippings.export(items, args.outdir)
    for file_path, count in written:
        print(f"  {count:>4}  {os.path.basename(file_path)}")
    ok(f"{len(items)} clippings from {len(written)} books → {args.outdir}")


# --- eject -------------------------------------------------------------------

def cmd_eject(args):
    mount = device.find_mount()
    if not mount:
        die("no Kindle mounted")
    dev = device.find_device()
    os.sync()
    if not dev:
        die(f"mounted at {mount} but no device node found (KINDLE_DIR override?)")

    result = subprocess.run(["udisksctl", "unmount", "-b", dev],
                            capture_output=True, text=True)
    if result.returncode != 0:
        die(result.stderr.strip() or "unmount failed")
    ok(f"ejected {dev} — safe to unplug")


# --- flush / status ----------------------------------------------------------

def cmd_flush(args):
    waiting = queue.pending()
    if not waiting:
        print("queue is empty")
        return
    try:
        docs = device.require_documents()
    except device.DeviceError as exc:
        die(str(exc))

    delivered, failed = queue.flush(docs)
    for entry in delivered:
        ok(f'"{entry["title"]}" → Kindle')
    for entry, err in failed:
        warn(f'{entry["title"]}: {err}')
    if delivered:
        print("unplug to let the Kindle index them")


def cmd_status(args):
    mount = device.find_mount()
    print(f"kindle    {mount or 'not connected'}")
    waiting = queue.pending()
    print(f"queue     {len(waiting)} waiting")
    for entry in waiting:
        print(f"          · {entry['title']}  ({library.human_size(entry['size'])})")
    recent = queue.sent(5)
    if recent:
        print("recent    " + recent[0]["title"])
        for entry in recent[1:]:
            print(f"          {entry['title']}")


def cmd_serve(args):
    from .server import serve
    serve(args.host, args.port)


# --- parser ------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="kindle",
        description="Manage a USB-mounted Kindle: add books and articles, list, remove, "
                    "export highlights, eject.",
    )
    parser.add_argument("--version", action="version", version=f"kindle-manager {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("add", help="add a file, Gutenberg id, ebook URL, or article URL")
    p.add_argument("target")
    p.add_argument("--title")
    p.add_argument("--author")
    p.add_argument("--queue", action="store_true",
                   help="always queue, even if the Kindle is connected")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("ls", help="list books on the device")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_ls)

    p = sub.add_parser("rm", help="remove a book and its .sdr sidecar")
    p.add_argument("pattern")
    p.add_argument("-y", "--yes", action="store_true", help="skip the confirmation")
    p.add_argument("-n", "--dry-run", action="store_true")
    p.set_defaults(func=cmd_rm)

    p = sub.add_parser("clippings", help="export My Clippings.txt per book")
    p.add_argument("outdir", nargs="?", default="clippings")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_clippings)

    p = sub.add_parser("eject", help="sync and unmount")
    p.set_defaults(func=cmd_eject)

    p = sub.add_parser("flush", help="deliver everything queued")
    p.set_defaults(func=cmd_flush)

    p = sub.add_parser("status", help="device and queue at a glance")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("serve", help="run the paste-a-URL inbox on the LAN")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=int(os.environ.get("KINDLE_PORT", 8137)))
    p.set_defaults(func=cmd_serve)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except KeyboardInterrupt:
        print()
        sys.exit(130)
