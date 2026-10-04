"""Irreversible Linux filesystem boundary for submitted Python programs.

Only Python's standard library and system loader libraries are readable.
The engine, private captures, credentials and all writes remain inaccessible.
Unsupported kernels or container syscall policies fail before code admission.
"""

import ctypes
import os
import sysconfig
from pathlib import Path


class Ruleset(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class PathBeneath(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int)]


def restrict_filesystem() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    # Linux x86_64/aarch64 syscall numbers; both canonical image architectures.
    create, add, restrict = 444, 445, 446
    abi = libc.syscall(create, 0, 0, 1)
    if abi < 3:
        raise RuntimeError(f"Cogolf requires Landlock ABI >= 3; observed {abi}")
    # ABI 3 covers every filesystem operation through TRUNCATE. No write grant.
    handled = (1 << 15) - 1
    read_file, read_dir = 1 << 2, 1 << 3
    rules = Ruleset(handled)
    descriptor = libc.syscall(create, ctypes.byref(rules), ctypes.sizeof(rules), 0)
    if descriptor < 0:
        raise OSError(ctypes.get_errno(), "Landlock ruleset creation failed")
    stdlib = Path(sysconfig.get_path("stdlib")).resolve()
    readable = [
        path
        for path in stdlib.iterdir()
        if path.name not in {"site-packages", "dist-packages", "__pycache__"}
    ]
    # Dynamic extension loading needs the system loader and shared libraries.
    readable.extend(
        Path(path)
        for path in ("/lib", "/usr/lib", "/etc/ld.so.cache")
        if Path(path).exists()
    )
    try:
        for path in readable:
            parent = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                entry = PathBeneath(
                    read_file | (read_dir if path.is_dir() else 0), parent
                )
                if libc.syscall(add, descriptor, 1, ctypes.byref(entry), 0) < 0:
                    raise OSError(
                        ctypes.get_errno(), f"Landlock read grant failed: {path}"
                    )
            finally:
                os.close(parent)
        if libc.prctl(38, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "no_new_privs failed")
        if libc.syscall(restrict, descriptor, 0) < 0:
            raise OSError(ctypes.get_errno(), "Landlock restriction failed")
    finally:
        os.close(descriptor)
