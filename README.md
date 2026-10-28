# kindle-manager

Sideload books — and any web article — onto a USB-mounted Kindle Paperwhite,
from Linux, without remembering the incantations.

```
$ kindle-add https://www.gutenberg.org/ebooks/33870
→ downloading https://www.gutenberg.org/cache/epub/33870/pg33870-images.epub
→ converting with local Calibre
✓ "Chess Fundamentals" → Kindle (1.4 MB)
  it appears on the home screen once you unplug (the device indexes on disconnect)

$ kindle-add https://some.blog/post          # a plain article becomes a book
✓ "The Bitter Lesson" queued (61.2 KB)
  1 waiting — delivered next time the Kindle is plugged in
```

## Why it works this way

The Paperwhite 4 mounts as **plain USB mass storage** (not MTP), so books are
just files under `documents/`. Two rules drive the whole design:

- **AZW3 is the delivery format.** EPUB does *not* appear when copied over USB
  — only via Send-to-Kindle. PDF is fixed-layout and unreadable on a 6" screen.
  So everything reflowable gets converted to AZW3 first.
- **The device is usually unplugged.** Anything you add is converted immediately
  and parked in a queue, and you deliver the queue when you next connect the
  Kindle. That is what makes "paste a link now, read it tonight" work.

## Commands

| | |
|---|---|
| `kindle-add <target>` | file, Project Gutenberg id/URL, direct ebook link, or **any article URL** |
| `kindle-ls` | list real books (handles both `.azw3` and `.kfx`-inside-`.sdr`) |
| `kindle-rm <pattern>` | remove a book *and* its `.sdr` sidecar |
| `kindle-clippings [dir]` | export `My Clippings.txt` to one markdown file per book |
| `kindle-flush` | deliver everything queued |
| `kindle-eject` | sync and unmount safely |
| `kindle-inboxd` | run the paste-a-URL web inbox |
| `kindle status` | device + queue at a glance |

Every command is also a subcommand of `kindle` (`kindle add`, `kindle ls`, …).

## The inbox

`kindle-inboxd` serves a small page on the LAN (default port **8137**):

- paste a URL into the box, or
- pick or drop a file on the page (EPUB, MOBI, TXT, PDF…) -- a file you named
  ("Meditaciones.epub") is titled by its name, since exports like Wikisource's
  often carry a chapter title ("Índice") in their metadata, or
- drag the **bookmarklet** it offers into your bookmarks bar and click it on any
  article — including from a phone browser on the same network.

The article (or file) is extracted, converted to AZW3 and queued. When you next plug the
Kindle in, the **Deliver** button on the page sends everything waiting — or run
`kindle-flush` from a terminal.

Delivery is manual on purpose: polling for a device that gets connected once a
month is thousands of wake-ups for nothing. If you would rather it happen by
itself, set `KINDLE_AUTOFLUSH=1` and the service checks every 30 seconds
(`KINDLE_AUTOFLUSH_SECONDS` to change that). Detecting the Kindle reads
`/dev/disk/by-label` and `/proc/self/mountinfo` — two file reads, no
subprocess — so the check is cheap either way.

## Install

```bash
./install.sh              # symlink the commands into ~/.local/bin
./install.sh --service    # + run the inbox as a systemd user service on boot
```

## Dependencies

Nothing to install by hand — the Python here is standard library only.

- **Calibre** does the conversion. A natively installed `ebook-convert` is used
  when present; otherwise a Calibre Docker image is built on first use.
- **Article extraction** always runs in a small Python/trafilatura Docker image.

Both images are built on demand the first time they are needed.

## Testing without a Kindle

Set `KINDLE_DIR` to a directory that looks like the device and every command
works against it:

```bash
python3 tests/test_kindlemgr.py      # 24 tests, no hardware
KINDLE_DIR=/path/to/fake kindle-ls
```

`KINDLE_STATE` likewise relocates the queue (default
`~/.local/share/kindle-manager`).

## Layout

```
bin/          kindle + kindle-* shims
kindlemgr/    device, library, clippings, queue, convert, server, cli
docker/       Dockerfile.fetch (trafilatura), Dockerfile.calibre
web/          the inbox page
systemd/      user service for the inbox
tests/        run against a fake Kindle tree
```
