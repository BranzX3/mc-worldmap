"""Small atomic progress-file helpers shared by long-running pipeline steps."""

import hashlib
import json
import os


def world_session_locked(world_path):
    """Return True when Minecraft or another editor holds session.lock."""
    lock_path = os.path.join(world_path, "session.lock")
    if not os.path.exists(lock_path):
        return False

    fd = None
    acquired = False
    try:
        fd = os.open(lock_path, os.O_RDWR)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            acquired = True
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            acquired = False
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
            fcntl.flock(fd, fcntl.LOCK_UN)
            acquired = False
    except OSError:
        return True
    finally:
        if fd is not None:
            if acquired:
                try:
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(fd)
    return False


def load_progress(path):
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as handle:
        return {line.strip() for line in handle if line.strip()}


def save_progress(path, entries):
    entries = {str(item).strip() for item in entries if str(item).strip()}
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8", newline="\n") as handle:
        if entries:
            handle.write("\n".join(sorted(entries)) + "\n")
    os.replace(temp_path, path)


def content_fingerprint(paths, version=""):
    """Hash input/code contents so an incompatible checkpoint cannot resume."""
    digest = hashlib.sha256()
    digest.update(str(version).encode("utf-8"))
    for path in sorted(os.path.abspath(path) for path in paths):
        digest.update(os.path.basename(path).encode("utf-8"))
        with open(path, "rb") as handle:
            while True:
                block = handle.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
    return digest.hexdigest()


def load_progress_metadata(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def save_progress_metadata(path, metadata):
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(metadata, handle, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
    os.replace(temp_path, path)
