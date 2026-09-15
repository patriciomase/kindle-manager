# kindle-manager

Small toolkit to manage the household Kindle from this Linux box without hand-holding —
list / add / remove books, convert to the right format, extract highlights, and eject
safely. Born out of doing all of this manually on 2026-07-21; the goal is to turn that
session's one-off commands into repeatable scripts.

## The device (known facts)

- **Model:** Kindle Paperwhite 4 (10th gen, 2018). The "8th Edition" user guide (ASIN `B07VGYRGH4`) is the PW4's.
- **Firmware:** `5.18.1` (see `system/version.txt` on the device).
- **Connection:** exposes itself as **USB mass storage** (older style), NOT MTP. So it mounts as a normal `vfat` disk — read/write directly, no `gvfs`/`mtp` needed.
- **Mount point:** `/media/$USER/Kindle` (udisks auto-mount). Label = `Kindle`.
- **Device node:** was `/dev/sdc` this session, but that is **not stable** — always resolve dynamically (see below), never hardcode `sdc`.
- **Capacity:** ~6.3 GB, ~6 GB free.

### Resolve the mount reliably
```bash
KDIR=$(lsblk -o LABEL,MOUNTPOINT -nr | awk '$1=="Kindle"{print $2}')
KDEV=$(lsblk -o LABEL,NAME -nr | awk '$1=="Kindle"{print "/dev/"$2}')
# KDIR -> /media/$USER/Kindle , KDEV -> /dev/sdc (whatever it is today)
```

## Format rules (the whole point)

| Format | Reflowable | Works via USB drag-drop | Use it? |
|--------|-----------|--------------------------|---------|
| **AZW3** | yes | yes | **YES — default target** |
| MOBI | yes | yes | works, deprecated — prefer AZW3 |
| TXT | plain | yes | fine for raw text, no styling |
| **EPUB** | yes | **NO** | will NOT appear via USB (only via Send-to-Kindle → KFX) |
| PDF | no (fixed) | yes | avoid — ugly on 6" e-ink, no reflow |
| KFX | yes | (Amazon-delivered) | don't create manually |

**Rule:** always deliver **AZW3**. Convert anything reflowable (EPUB/HTML/TXT) with Calibre.

## Convert recipe (proven)

Calibre is installed via **apt** (`calibre 7.6.0` → provides `ebook-convert`). apt build, not
snap, so `ebook-convert` can freely read scratchpad and write to `/media/$USER/Kindle`.

```bash
ebook-convert INPUT.epub "Title - Author.azw3" \
  --output-profile kindle_pw3 \
  --title  "Book Title" \
  --authors "Author Name"
```
- `--output-profile kindle_pw3` matches the PW4's 300 ppi / 1072×1448 screen.
- Then copy to the device and flush:
```bash
cp "Title - Author.azw3" "$KDIR/documents/" && sync
```
- The Kindle indexes new sideloads **on disconnect**; the book then appears on the home screen.

## Book storage layout (important for list/remove)

Books live under `documents/`. Sideloaded/downloaded ones are in `documents/Downloads/Items01/`.
Watch out — **book content comes in two eras**:
- Old: `*.azw`, `*.pdf`, `*.azw3` sitting at top level (easy to spot).
- Modern: **`*.kfx`**, sometimes stored **inside the `*.sdr` sidecar folder** (`<book>.sdr/assets/attachables/*.kfx`). A naive `find` for `*.azw`/`*.pdf` **misses these** — that bit us on 2026-07-21. Always include `*.kfx` and search inside `.sdr`.

Each book has a matching **`<book>.sdr`** sidecar folder (reading position, annotations). Remove
it alongside the book. `Personal letter_PSNL!…` folders are **Kindle Unlimited receipts**, not books.

- **Highlights/notes:** `documents/My Clippings.txt` (plain text — easy to parse/export).
- **Keep, don't delete:** built-in dictionaries (`documents/dictionaries/`, Oxford Spanish dict), the Kindle User's Guide, `My Clippings.txt`.
- **Leave alone:** `update.bin.tmp.partial` in the device root = a partial firmware download; the Kindle resumes it.

## Safe eject
```bash
udisksctl unmount -b "$KDEV"     # KDEV from the resolver above
```

## Public-domain source

Free "quick reads" come from **Project Gutenberg** (public domain = classics). Prefer the
**epub-with-images** build:
`https://www.gutenberg.org/cache/epub/<ID>/pg<ID>-images.epub`
(e.g. Chess Fundamentals = ID `33870`).

## How it is built (implemented 2026-09-15)

Standard-library Python package + thin bash shims. No pip installs on the host:
the two things that need dependencies run in Docker images built on demand.

```
bin/kindle            single entry point; bin/kindle-{add,ls,rm,clippings,eject,flush,inboxd} are shims
kindlemgr/device.py   resolve the mount by LABEL (never hardcode sdc); KINDLE_DIR overrides for tests
kindlemgr/library.py  listing that understands both storage eras (.azw3 top level AND .kfx inside .sdr)
kindlemgr/clippings.py  My Clippings.txt -> one markdown file per book (EN + ES metadata lines)
kindlemgr/queue.py    the spool in ~/.local/share/kindle-manager -- books wait here while unplugged
kindlemgr/convert.py  source -> AZW3; native ebook-convert if present, else the Calibre image
kindlemgr/server.py   the inbox: paste a URL, background worker, auto-flush watcher
docker/               Dockerfile.fetch (trafilatura) and Dockerfile.calibre
```

**The queue is the core idea.** Adding never requires the device: anything is
converted immediately and parked in the spool, and delivered when the Kindle is
next connected -- the **Deliver** button on the inbox page, or `kindle-flush`.
`kindle-inboxd` serves that as a LAN page on port 8137 with a bookmarklet, so
an article found on the phone is one click from being queued.

Delivery is **manual by default**: this Kindle gets plugged in about once a
month, so a watcher polling for it is thousands of wake-ups for nothing.
`KINDLE_AUTOFLUSH=1` opts into automatic delivery (every 30 s).

Mount detection deliberately avoids `lsblk`: it reads the `/dev/disk/by-label`
symlink and `/proc/self/mountinfo` directly, so a status check is two file
reads rather than a subprocess. `lsblk` remains as a fallback.

- [x] `kindle-add <file|gutenberg-id|url|article-url>`
- [x] `kindle-ls` — handles `.azw`/`.kfx`/`.sdr`, skips dictionaries and KU receipts
- [x] `kindle-rm <pattern>` — book + sidecar, with confirmation and `--dry-run`
- [x] `kindle-clippings` — per-book markdown export
- [x] `kindle-eject` — sync + `udisksctl unmount`
- [x] Friendly "no Kindle connected / it may be charge-only" message
- [x] Web inbox + bookmarklet + one-button delivery (auto-delivery opt-in)

Hub box is **the LAN desktop**: it has Calibre natively, it is always on,
and it runs the inbox as a systemd user service. `tests/test_kindlemgr.py` runs
anywhere against a fake tree via `KINDLE_DIR` — no hardware.

## Owner notes

- User is a Spanish speaker (Argentina); reading tastes lean sci-fi (often in Spanish), chess,
  software engineering, and discipline/self-improvement.
- Sudo commands go through the **bash-manager** workflow (`~/Desktop/Projects/bash-manager/pending.sh`),
  never pasted inline. (That's how Calibre got installed.)
