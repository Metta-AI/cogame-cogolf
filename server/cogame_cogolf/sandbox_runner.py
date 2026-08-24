"""The sandbox CHILD: runs one submitted implementation, out of process.

Never imported by the server. It is spawned by ``sandbox.py`` as

    python -I -S <this file>

with a scrubbed environment and a fresh empty working directory, reads one
job JSON from stdin and writes one NDJSON line per call to stdout, flushing
each line before the next call starts — so a batch killed at the wall cap
still yields every result it had produced.

    job     {"source": "<impl>", "calls": [{"id": 3, "args": [...]}],
             "cpu_seconds": 1.0}
    line    {"id": 3, "ok": true,  "value": <canon>}
            {"id": 3, "ok": false, "kind": "error|timeout|bad_value",
             "text": "..."}
            {"id": -1, "ok": false, "kind": "broken", "text": "..."}

Before the submitted source is so much as compiled the child drops what it
can: address-space / file-size / process / descriptor rlimits, an audit hook
that denies the network, subprocess, ctypes and file-write event families,
and setuid to an unprivileged uid when it started as root. This is defence
in depth, not a claim of a hardened jail — the only code that reaches it
comes from the two policy containers of one ephemeral episode pod.
"""

from __future__ import annotations

import json
import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cogame_cogolf.values import BadValue, canon  # noqa: E402

ADDRESS_SPACE_BYTES = 256 * 1024 * 1024
MAX_OPEN_FILES = 16
MAX_TEXT_CHARS = 300

# Audit-event families the hook refuses outright.
DENIED_PREFIXES = ("socket.", "subprocess.", "os.exec", "ctypes.", "shutil.",
                   "urllib.", "webbrowser.", "ftplib.", "http.client.",
                   "smtplib.", "sqlite3.", "os.spawn", "os.fork", "pty.")
DENIED_EVENTS = frozenset({"os.system", "os.posix_spawn", "os.putenv",
                           "os.remove", "os.rename", "os.rmdir", "os.mkdir",
                           "os.chmod", "os.chdir", "os.startfile"})
DENIED_IMPORTS = frozenset({"socket", "subprocess", "ctypes",
                            "multiprocessing", "threading", "_thread",
                            "asyncio", "ssl", "urllib", "http", "shutil",
                            "pty", "resource", "signal"})
WRITE_MODES = frozenset("wxa+")


class Denied(RuntimeError):
    """The audit hook refused an operation."""


def _drop_privileges() -> None:
    try:
        import resource
    except ImportError:  # pragma: no cover - POSIX only
        return
    for name, limit in (("RLIMIT_AS", (ADDRESS_SPACE_BYTES,
                                       ADDRESS_SPACE_BYTES)),
                        ("RLIMIT_FSIZE", (0, 0)),
                        ("RLIMIT_NPROC", (0, 0)),
                        ("RLIMIT_NOFILE", (MAX_OPEN_FILES, MAX_OPEN_FILES)),
                        ("RLIMIT_CORE", (0, 0))):
        which = getattr(resource, name, None)
        if which is None:
            continue
        try:
            soft, hard = resource.getrlimit(which)
            wanted = (min(limit[0], hard) if hard >= 0 else limit[0],
                      min(limit[1], hard) if hard >= 0 else limit[1])
            resource.setrlimit(which, wanted)
        except (ValueError, OSError):
            pass  # best effort: an unsettable limit must not kill the batch
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        uid = int(os.environ.get("COGOLF_SANDBOX_UID", "65534"))
        try:
            os.setgroups([])
        except (OSError, AttributeError):
            pass
        try:
            os.setgid(uid)
        except OSError:
            pass
        try:
            os.setuid(uid)
        except OSError:
            pass


def _audit(event: str, args) -> None:
    if event.startswith(DENIED_PREFIXES) or event in DENIED_EVENTS:
        raise Denied(f"blocked operation: {event}")
    if event == "import":
        module = args[0] if args else ""
        root = str(module).split(".")[0]
        if root in DENIED_IMPORTS:
            raise Denied(f"blocked import: {module}")
    elif event == "open":
        mode = args[1] if len(args) > 1 else "r"
        if isinstance(mode, str) and set(mode) & WRITE_MODES:
            raise Denied("blocked file write")
        if isinstance(mode, int) and mode & (os.O_WRONLY | os.O_RDWR):
            raise Denied("blocked file write")


class _Timeout(Exception):
    """The per-call CPU budget expired."""


def _on_alarm(signum, frame):  # pragma: no cover - signal path
    raise _Timeout("call exceeded its CPU budget")


def _clip(text: str) -> str:
    text = str(text).replace("\n", " ").strip()
    return text if len(text) <= MAX_TEXT_CHARS else text[:MAX_TEXT_CHARS - 1] + "\u2026"


def _emit(line: dict) -> None:
    sys.stdout.write(json.dumps(line, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    raw = sys.stdin.read()
    try:
        job = json.loads(raw)
        source = job["source"]
        calls = job["calls"]
        cpu_seconds = float(job.get("cpu_seconds", 1.0))
    except Exception as exc:  # noqa: BLE001
        _emit({"id": -1, "ok": False, "kind": "broken",
               "text": _clip(f"bad job: {exc!r}")})
        return 0

    _drop_privileges()
    signal.signal(signal.SIGVTALRM, _on_alarm)
    sys.addaudithook(_audit)

    namespace: dict = {"__name__": "submission"}
    try:
        exec(compile(source, "<submission>", "exec"), namespace)  # noqa: S102
        solve = namespace.get("solve")
        if not callable(solve):
            raise ValueError("no callable solve(...) defined")
    except BaseException as exc:  # noqa: BLE001 - any failure is 'broken'
        _emit({"id": -1, "ok": False, "kind": "broken",
               "text": _clip(f"{type(exc).__name__}: {exc}")})
        return 0

    for call in calls:
        call_id = call.get("id")
        args = call.get("args") or []
        signal.setitimer(signal.ITIMER_VIRTUAL, cpu_seconds)
        try:
            value = solve(*args)
            signal.setitimer(signal.ITIMER_VIRTUAL, 0)
            _emit({"id": call_id, "ok": True, "value": canon(value)})
        except _Timeout:
            signal.setitimer(signal.ITIMER_VIRTUAL, 0)
            _emit({"id": call_id, "ok": False, "kind": "timeout",
                   "text": f"call exceeded {cpu_seconds:g}s of CPU"})
        except BadValue as exc:
            signal.setitimer(signal.ITIMER_VIRTUAL, 0)
            _emit({"id": call_id, "ok": False, "kind": "bad_value",
                   "text": _clip(str(exc))})
        except BaseException as exc:  # noqa: BLE001
            signal.setitimer(signal.ITIMER_VIRTUAL, 0)
            _emit({"id": call_id, "ok": False, "kind": "error",
                   "text": _clip(f"{type(exc).__name__}: {exc}")})
    return 0


if __name__ == "__main__":
    sys.exit(main())
