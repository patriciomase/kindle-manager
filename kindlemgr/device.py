"""Locate the Kindle.

The device node is not stable across sessions (it was /dev/sdc once, it will not
be next time), so everything here resolves by the partition LABEL instead.
Set KINDLE_DIR to point the whole toolkit at a directory instead of real
hardware -- that is how the tests run without a Kindle plugged in.
"""

import os
import subprocess

LABEL = "Kindle"


def _lsblk(fields):
    """Yield rows of `lsblk -o <fields> -nr` as lists of strings."""
    try:
        out = subprocess.run(
            ["lsblk", "-o", fields, "-nr"],
            capture_output=True, text=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return
    for line in out.splitlines():
        # -r escapes spaces in mountpoints as \x20
        yield [f.replace("\\x20", " ") for f in line.split(" ")]


def find_mount():
    """Return the Kindle's mount point, or None if it is not mounted."""
    override = os.environ.get("KINDLE_DIR")
    if override:
        return override if os.path.isdir(override) else None
    for row in _lsblk("LABEL,MOUNTPOINT"):
        if len(row) >= 2 and row[0] == LABEL and row[1]:
            return row[1]
    return None


def find_device():
    """Return the Kindle's device node (/dev/sdX), or None."""
    if os.environ.get("KINDLE_DIR"):
        return None
    for row in _lsblk("LABEL,NAME"):
        if len(row) >= 2 and row[0] == LABEL:
            return "/dev/" + row[1]
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
