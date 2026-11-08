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


def _install_guard(violations):
    armed = {"on": False}

    def hook(event, args):
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
