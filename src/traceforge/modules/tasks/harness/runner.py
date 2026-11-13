"""TraceForge sandbox test runner.

Standard-library only. This file is copied into the sandbox and executed there with
``python -I -B runner.py --root <candidate> --test <module> [--selector S]``.
It is never imported by the TraceForge host process.

Defence-in-depth (NOT a security boundary on its own):
* A Python audit hook denies process creation, networking, native code loading, file writes,
  and file-backed sqlite before candidate code is imported. Denied events are recorded as
  violations and reported to the host verifier.
* The result line is framed with a nonce taken from the environment and removed from
  ``os.environ`` before candidate code is imported.
* Every SQLite connection is hardened as it is created (audit event ``sqlite3.connect/handle``):
  no attached databases (blocks ``ATTACH`` and ``VACUUM INTO``, which otherwise reach files
  without any Python ``open`` event), defensive mode (read-only shadow tables and schema), no
  virtual tables, no extension loading, a PRAGMA allow-list, parser/resource limits, a heap
  limit and a VM-step budget. This is defence in depth: code running in this process can undo
  per-connection settings. The sandbox image's SQLite build (no FTS5/FTS3/R-Tree/session,
  compile-time limits) is the stronger layer; see docs/security/container-vulnerabilities.md.
"""

import importlib.util
import io
import json
import os
import sys
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout

MAX_MESSAGE = 600
BLOCKED_EVENTS = {
    "subprocess.Popen",
    "os.system",
    "os.exec",
    "os.posix_spawn",
    "os.spawn",
    "os.fork",
    "os.forkpty",
    "os.startfile",
    "os.kill",
    "os.remove",
    "os.rename",
    "os.rmdir",
    "os.mkdir",
    "os.chmod",
    "os.symlink",
    "os.link",
    "shutil.rmtree",
    "socket.connect",
    "socket.bind",
    "socket.getaddrinfo",
    "socket.gethostbyname",
    "socket.gethostbyaddr",
    "socket.sendto",
    "socket.__new__",
    "ctypes.dlopen",
    "ctypes.dlsym",
    "ctypes.cdata",
    "sqlite3.enable_load_extension",
    "sqlite3.load_extension",
    "urllib.Request",
    "webbrowser.open",
}
BLOCKED_IMPORTS = {
    "socket",
    "_socket",
    "ssl",
    "subprocess",
    "_posixsubprocess",
    "ctypes",
    "_ctypes",
    "multiprocessing",
    "http.client",
    "urllib.request",
    "ftplib",
    "smtplib",
    "telnetlib",
    "requests",
    "httpx",
}
WRITE_FLAGS = getattr(os, "O_WRONLY", 1) | getattr(os, "O_RDWR", 2) | getattr(os, "O_CREAT", 64)

SQLITE_POLICY_VERSION = "1"
SQLITE_LIMITS = (
    ("SQLITE_LIMIT_ATTACHED", 0),  # no ATTACH / VACUUM INTO: no file-backed databases via SQL
    ("SQLITE_LIMIT_SQL_LENGTH", 100_000),
    ("SQLITE_LIMIT_EXPR_DEPTH", 100),
    ("SQLITE_LIMIT_COMPOUND_SELECT", 50),
    ("SQLITE_LIMIT_LIKE_PATTERN_LENGTH", 1_000),
    ("SQLITE_LIMIT_VARIABLE_NUMBER", 999),
    ("SQLITE_LIMIT_TRIGGER_DEPTH", 10),
    ("SQLITE_LIMIT_LENGTH", 10_000_000),
)
SQLITE_DBCONFIG = (
    ("SQLITE_DBCONFIG_DEFENSIVE", True),
    ("SQLITE_DBCONFIG_TRUSTED_SCHEMA", False),
    ("SQLITE_DBCONFIG_ENABLE_LOAD_EXTENSION", False),
)
SQLITE_HEAP_LIMIT_BYTES = 64 * 1024 * 1024  # process-wide; SQLite only ever lowers this limit
SQLITE_PROGRESS_INTERVAL = 10_000
SQLITE_MAX_VM_STEPS = 50_000_000  # per connection
SQLITE_ALLOWED_PRAGMAS = frozenset(
    {"table_info", "table_xinfo", "table_list", "index_list", "index_info", "index_xinfo"}
)
SQLITE_DENIED_FUNCTIONS = frozenset({"load_extension", "fts3_tokenizer"})


def _harden_sqlite(conn, violations):
    """Apply the SQLite policy to a new connection, or raise (the connection is then refused)."""
    sqlite3 = sys.modules["sqlite3"]
    deny_actions = {
        sqlite3.SQLITE_ATTACH: "attach",
        sqlite3.SQLITE_CREATE_VTABLE: "create_vtable",
        sqlite3.SQLITE_DROP_VTABLE: "drop_vtable",
    }

    def record(detail):
        if len(violations) < 100:
            violations.append(detail)

    def authorizer(action, arg1, arg2, _db, _trigger):
        if action in deny_actions:
            record("sqlite3.denied:" + deny_actions[action])
            return sqlite3.SQLITE_DENY
        # Allow-listed pragmas are read-only introspection; their argument is a table/index name.
        if action == sqlite3.SQLITE_PRAGMA and str(arg1).lower() not in SQLITE_ALLOWED_PRAGMAS:
            record("sqlite3.denied:pragma:" + str(arg1)[:40])
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION and str(arg2).lower() in SQLITE_DENIED_FUNCTIONS:
            record("sqlite3.denied:function:" + str(arg2))
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    steps = {"n": 0}

    def progress():
        steps["n"] += SQLITE_PROGRESS_INTERVAL
        if steps["n"] > SQLITE_MAX_VM_STEPS:
            record("sqlite3.vm_step_limit")
            return 1  # abort the statement
        return 0

    for name, enabled in SQLITE_DBCONFIG:
        conn.setconfig(getattr(sqlite3, name), enabled)
    for name, value in SQLITE_LIMITS:
        conn.setlimit(getattr(sqlite3, name), value)
    conn.execute(f"PRAGMA hard_heap_limit={SQLITE_HEAP_LIMIT_BYTES:d}")
    conn.set_progress_handler(progress, SQLITE_PROGRESS_INTERVAL)
    conn.set_authorizer(authorizer)


def _install_sqlite_policy(violations):
    """Route every ``connect`` through a connection class that hardens itself after init.

    The ``sqlite3.connect/handle`` audit event fires before a connection is initialised, so the
    hook cannot configure it; instead the hook refuses any connection whose exact type is not
    this class (direct ``Connection(...)`` construction, other factories, subclasses).
    """
    import _sqlite3
    import sqlite3
    import sqlite3.dbapi2 as dbapi2

    original_connect = _sqlite3.connect

    class HardenedConnection(sqlite3.Connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            try:
                _harden_sqlite(self, violations)
            except Exception as exc:
                if len(violations) < 100:
                    violations.append("sqlite3.hardening_failed")
                self.close()
                raise PermissionError("sandbox policy denied: sqlite3 hardening failed") from exc

    def connect(database, *args, **kwargs):
        factory = kwargs.pop("factory", HardenedConnection)
        if factory is not HardenedConnection:
            if len(violations) < 100:
                violations.append("sqlite3.denied:factory")
            raise PermissionError("sandbox policy denied: sqlite3 connection factory")
        return original_connect(database, *args, factory=HardenedConnection, **kwargs)

    for module in (sqlite3, dbapi2, _sqlite3):
        module.connect = connect
    return HardenedConnection


def _install_guard(violations):
    armed = {"on": False}
    hardened_cls = _install_sqlite_policy(violations)

    def hook(event, args):
        if event == "sqlite3.connect/handle":
            # Always enforced (armed or not): no connection is ever handed out unhardened.
            if type(args[0]) is not hardened_cls:
                if len(violations) < 100:
                    violations.append("sqlite3.unhardened_connection")
                raise PermissionError("sandbox policy denied: unhardened sqlite3 connection")
            return
        if not armed["on"]:
            return
        blocked = False
        if event in BLOCKED_EVENTS or (event == "import" and args and args[0] in BLOCKED_IMPORTS):
            blocked = True
        elif event == "open" and len(args) >= 3:
            mode, flags = args[1], args[2]
            if (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                isinstance(flags, int) and flags & WRITE_FLAGS
            ):
                blocked = True
        elif event == "sqlite3.connect" and args and str(args[0]) != ":memory:":
            blocked = True
        if blocked:
            detail = event if event != "import" else "import:" + str(args[0])
            if len(violations) < 100:
                violations.append(detail)
            raise PermissionError("sandbox policy denied: " + detail)

    sys.addaudithook(hook)
    return armed


def _load(path):
    spec = importlib.util.spec_from_file_location("tf_test_module", path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load test module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv):
    args = dict(zip(argv[1::2], argv[2::2], strict=False))
    root = args["--root"]
    test_path = args["--test"]
    selector = args.get("--selector", "")
    nonce = os.environ.pop("TF_NONCE", "")
    sys.dont_write_bytecode = True
    violations = []
    armed = _install_guard(violations)
    sys.path.insert(0, root)
    cases = []
    started = time.perf_counter()
    harness_ok = True
    harness_error = ""
    sink = io.StringIO()
    armed["on"] = True
    try:
        with redirect_stdout(sink), redirect_stderr(sink):
            module = _load(test_path)
        names = sorted(
            n for n in dir(module) if n.startswith("test_") and callable(getattr(module, n))
        )
        if selector:
            names = [n for n in names if selector in n]
        for name in names:
            try:
                with redirect_stdout(sink), redirect_stderr(sink):
                    getattr(module, name)()
                cases.append({"name": name, "status": "passed", "message": ""})
            except AssertionError as exc:
                cases.append({"name": name, "status": "failed", "message": str(exc)[:MAX_MESSAGE]})
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    cases.append({"name": name, "status": "error", "message": "exit attempted"})
                    continue
                msg = type(exc).__name__ + ": " + str(exc)
                cases.append({"name": name, "status": "error", "message": msg[:MAX_MESSAGE]})
    except BaseException as exc:
        harness_ok = False
        harness_error = (type(exc).__name__ + ": " + str(exc))[:MAX_MESSAGE]
        tb = traceback.format_exc(limit=3)
        harness_error += "\n" + tb[-MAX_MESSAGE:]
    armed["on"] = False
    result = {
        "harness_ok": harness_ok,
        "harness_error": harness_error,
        "cases": cases,
        "violations": violations,
        "duration": time.perf_counter() - started,
        "captured_output_tail": sink.getvalue()[-2000:],
    }
    sys.__stdout__.write("<<TF-RESULT-" + nonce + ">>" + json.dumps(result) + "\n")
    sys.__stdout__.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
