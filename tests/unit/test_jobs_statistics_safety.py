from __future__ import annotations

import pytest

from traceforge.contracts.jobs import ALLOWED_TRANSITIONS, TERMINAL_STATUSES, JobStatus
from traceforge.domain.jobs import IllegalTransition, assert_transition, can_transition
from traceforge.infrastructure.observability import redact
from traceforge.modules.evaluation.statistics import (
    bootstrap_mean_interval,
    proportion,
    wilson_interval,
)
from traceforge.modules.verification.safety import scan_source

# ----------------------------------------------------------------------------- job state machine


def test_happy_path_transitions() -> None:
    for a, b in [
        (JobStatus.CREATED, JobStatus.QUEUED),
        (JobStatus.QUEUED, JobStatus.RUNNING),
        (JobStatus.RUNNING, JobStatus.SUCCEEDED),
    ]:
        assert_transition(a, b)


@pytest.mark.parametrize("terminal", sorted(TERMINAL_STATUSES))
def test_terminal_states_are_final(terminal: JobStatus) -> None:
    for target in JobStatus:
        assert not can_transition(terminal, target)
    with pytest.raises(IllegalTransition):
        assert_transition(terminal, JobStatus.RUNNING)


def test_no_skip_from_created_to_running() -> None:
    assert not can_transition(JobStatus.CREATED, JobStatus.RUNNING)
    assert set(ALLOWED_TRANSITIONS) == set(JobStatus)


# ----------------------------------------------------------------------------- statistics


def test_wilson_reference_values() -> None:
    lo, hi = wilson_interval(8, 10, 0.95)
    assert lo == pytest.approx(0.4902, abs=1e-3)
    assert hi == pytest.approx(0.9433, abs=1e-3)
    assert wilson_interval(0, 0) == (0.0, 1.0)
    lo, hi = wilson_interval(10, 10)
    assert hi == 1.0 and 0.69 < lo < 0.73
    with pytest.raises(ValueError):
        wilson_interval(5, 3)


def test_proportion_and_bootstrap_deterministic() -> None:
    p = proportion(3, 4)
    assert p["rate"] == 0.75 and p["n"] == 4 and p["ci_low"] < 0.75 < p["ci_high"]
    vals = [0.0, 1.0, 1.0, 0.5, 0.2]
    a = bootstrap_mean_interval(vals, samples=500, seed=7)
    assert a == bootstrap_mean_interval(vals, samples=500, seed=7)
    assert a[0] <= sum(vals) / len(vals) <= a[1]


# ----------------------------------------------------------------------------- static safety scan

ALLOWED = ("sqlite3", "ipaddress", "urllib.parse", "dataclasses")


@pytest.mark.parametrize(
    "src,code",
    [
        ("import socket", "forbidden_import"),
        ("import os", "forbidden_import"),
        ("from urllib.request import urlopen", "forbidden_import"),
        ("from . import x", "relative_import"),
        ("eval('1')", "forbidden_call"),
        ("open('f')", "forbidden_call"),
        ("__import__('os')", "forbidden_call"),
        ("x = ().__class__.__bases__", "dunder_access"),
        ("getattr(x, name)", "dynamic_getattr"),
        ("getattr(x, '_secret')", "private_getattr"),
        ("import sys\nsys._getframe()", "forbidden_import"),
        ("def f():\n    global y", "global_statement"),
        ("print(__builtins__)", "forbidden_name"),
    ],
)
def test_scan_rejects(src: str, code: str) -> None:
    result = scan_source("app/x.py", src, ALLOWED)
    assert code in {f.code for f in result.findings}


def test_scan_accepts_legitimate_fix() -> None:
    src = (
        "import ipaddress\nfrom urllib.parse import urlsplit\n\n"
        "def f(url):\n    a = ipaddress.ip_address('1.1.1.1')\n    return getattr(a, 'ipv4_mapped', None)\n"
    )
    assert scan_source("app/x.py", src, ALLOWED).safe
    assert scan_source("app/x.py", "def broken(:", ALLOWED).syntax_error


# ----------------------------------------------------------------------------- redaction


def test_secret_redaction() -> None:
    text = 'api_key=abcd1234efgh token: "zzzzzzzz" sk-ABCDEFGHIJKLMNOPQRST ghp_aaaaaaaaaaaaaaaaaaaaaaaa Bearer xyz.abc.def'  # gitleaks:allow - synthetic redaction fixtures, not secrets
    out = redact(text)
    for secret in ("abcd1234efgh", "sk-ABCDEFGHIJKLMNOPQRST", "ghp_aaaa", "xyz.abc.def"):
        assert secret not in out
    assert "[REDACTED]" in out
