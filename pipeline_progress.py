"""Small atomic progress-file helpers shared by long-running pipeline steps."""

import hashlib
import json
import os
import sys


def use_utf8_stdout():
    """บังคับ stdout/stderr เป็น UTF-8 — เรียกเป็นบรรทัดแรกของ main() ทุกตัว

    Windows ตั้ง encoding ของ stdout ตาม locale (cp874 สำหรับไทย) ซึ่งเข้ารหัส
    อักขระอย่าง `km²` ไม่ได้ พอ redirect output ลงไฟล์หรือ pipe จะเกิด
    UnicodeEncodeError **กลางการพิมพ์รายงาน** ทั้งที่คำนวณเสร็จหมดแล้ว —
    งานที่ใช้เวลาหลายนาทีจึงเสียเปล่าเพราะการพิมพ์บรรทัดเดียว

    เป็นสาเหตุเดียวกับที่ข้อความไทยในล็อกกลายเป็นตัวยึกยือเมื่อรันผ่าน pipe
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


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


def working_set_mb():
    """RAM ที่ process ใช้อยู่จริง (MB) — คืน None เมื่ออ่านไม่ได้

    ctypes บน Windows 64-bit ต้องประกาศ argtypes/restype เอง ไม่งั้น HANDLE
    จาก GetCurrentProcess() ถูกมองเป็น int 32 บิตแล้วถูกตัดครึ่ง เรียกสำเร็จ
    แต่ได้ศูนย์กลับมา ซึ่งเคยทำให้แถบ progress ของ build_terrain โชว์ RAM 0MB
    มาตลอดทั้งที่ดูเหมือนใช้งานได้
    """
    if os.name != "nt":
        return None
    import ctypes

    class _Counters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_uint32),
            ("PageFaultCount", ctypes.c_uint32),
        ] + [
            (name, ctypes.c_size_t)
            for name in (
                "PeakWorkingSetSize", "WorkingSetSize",
                "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                "PagefileUsage", "PeakPagefileUsage",
            )
        ]

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.GetCurrentProcess.argtypes = []
        psapi.GetProcessMemoryInfo.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(_Counters), ctypes.c_uint32
        ]
        psapi.GetProcessMemoryInfo.restype = ctypes.c_int

        counters = _Counters()
        counters.cb = ctypes.sizeof(_Counters)
        if not psapi.GetProcessMemoryInfo(
            kernel32.GetCurrentProcess(), ctypes.byref(counters),
            counters.cb,
        ):
            return None
        return counters.WorkingSetSize / (1024 * 1024)
    except (OSError, AttributeError, ValueError):
        return None


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
