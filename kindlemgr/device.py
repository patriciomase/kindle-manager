"""Locate the Kindle.

The device node is not stable across sessions (it was /dev/sdc once, it will not
be next time), so everything here resolves by the partition LABEL instead.

Resolution reads /dev/disk/by-label and /proc/self/mountinfo directly rather
than shelling out to lsblk: this runs on every status check and on every
watcher tick, and two file reads cost nothing where a subprocess does not.
lsblk stays as a fallback for setups without udev's by-label symlinks.

Set KINDLE_DIR to point the whole toolkit at a directory instead of real
hardware -- that is how the tests run without a Kindle plugged in.
"""

import os
import re
import subprocess

LABEL = "Kindle"
BY_LABEL = "/dev/disk/by-label"
MOUNTINFO = "/proc/self/mountinfo"

_OCTAL = re.compile(r"\\([0-7]{3})")


def _unescape(path):
    """mountinfo escapes space, tab, newline and backslash in octal."""
    return _OCTAL.sub(lambda m: chr(int(m.group(1), 8)), path)


def parse_mountinfo(text):
    """Yield (major_minor, mountpoint, source) for every current mount."""
    for line in text.splitlines():
        left, _, right = line.partition(" - ")
        if not right:
            continue
        fields = left.split(" ")
        if len(fields) < 5:
            continue
        rest = right.split(" ")
        source = rest[1] if len(rest) > 1 else ""
        yield fields[2], _unescape(fields[4]), source


def _label_device():
    """The device node udev published for our label, or None."""
    link = os.path.join(BY_LABEL, LABEL)
    try:
        return os.path.realpath(link) if os.path.exists(link) else None
    except OSError:
        return None


def _lsblk(fields):
    """Fallback for boxes without /dev/disk/by-label."""
    try:
        out = subprocess.run(
            ["lsblk", "-o", fields, "-nr"],
            capture_output=True, text=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return
    for line in out.splitlines():
        yield [f.replace("\\x20", " ") for f in line.split(" ")]


def find_device():
    """Return the Kindle's device node (/dev/sdX), or None."""
    if os.environ.get("KINDLE_DIR"):
        return None
    dev = _label_device()
    if dev:
        return dev
    for row in _lsblk("LABEL,NAME"):
        if len(row) >= 2 and row[0] == LABEL:
            return "/dev/" + row[1]
    return None


def find_mount():
    """Return the Kindle's mount point, or None if it is not mounted."""
    override = os.environ.get("KINDLE_DIR")
    if override:
        return override if os.path.isdir(override) else None

    dev = _label_device()
    if dev:
        try:
            rdev = os.stat(dev).st_rdev
            wanted = f"{os.major(rdev)}:{os.minor(rdev)}"
        except OSError:
            wanted = None
        try:
            with open(MOUNTINFO, encoding="utf-8") as fh:
                for major_minor, mountpoint, source in parse_mountinfo(fh.read()):
                    if major_minor == wanted or source == dev:
                        return mountpoint
        except OSError:
            pass
        return None      # labelled device exists but is not mounted

    for row in _lsblk("LABEL,MOUNTPOINT"):
        if len(row) >= 2 and row[0] == LABEL and row[1]:
            return row[1]
    return None


def documents_dir():
    """Return the Kindle's documents/ directory, or None if not connected."""
    mount = find_mount()
    if not mount:
        return None
    docs = os.path.join(mount, "documents")
    return docs if os.path.isdir(docs) else None


def is_connected():
    return documents_dir() is not None


def require_documents():
    """Return documents/, or raise a friendly error explaining what to do."""
    docs = documents_dir()
    if docs:
        return docs
    raise DeviceError(
        "No Kindle found.\n"
        "  - plug it in over USB\n"
        "  - unlock the screen: it mounts as a disk only when awake\n"
        "  - if it only charges, try the other cable (many are charge-only)\n"
        "  - expected a partition labelled 'Kindle' under /media/$USER/"
    )


class DeviceError(Exception):
    pass
