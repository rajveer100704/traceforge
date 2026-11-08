"""Container vulnerability gate over Trivy JSON reports.

Policy (docs/security/container-vulnerabilities.md):

* Every CRITICAL/HIGH finding fails the gate unless it is covered by a valid exception in
  ``security/vulnerability-exceptions.yaml``.
* An exception is valid only if it names the vulnerability id, is scoped to the image (and
  optionally packages), has an owner, a reason, a review date and an expiry no more than
  ``MAX_EXCEPTION_DAYS`` after the review date, and has not expired.
* An exception recorded as ``status: no-fix-available`` stops applying as soon as Trivy reports a
  fixed version: a fix makes the finding actionable again.
* Expired or malformed exceptions fail the gate. Exceptions that match nothing are reported.

Usage:
    python scripts/trivy_gate.py --exceptions security/vulnerability-exceptions.yaml \
        --report traceforge:ci=reports/traceforge_ci.json \
        --report traceforge-sandbox:0.1=reports/traceforge-sandbox_0.1.json \
        [--summary out.md] [--result out.json] [--today 2026-11-09]

Exit status: 0 = pass, 1 = gate failed, 2 = usage/input error.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

GATED_SEVERITIES = ("CRITICAL", "HIGH")
MAX_EXCEPTION_DAYS = 90
REQUIRED_FIELDS = ("id", "images", "owner", "reason", "reviewed", "expires", "status")
STATUSES = ("no-fix-available", "not-exploitable", "fix-pending")


@dataclass
class Finding:
    image: str
    vuln_id: str
    package: str
    installed: str
    fixed: str | None
    severity: str
    status: str | None
    title: str
    target: str

    @property
    def fix_available(self) -> bool:
        return bool(self.fixed)


@dataclass
class GateResult:
    passed: bool
    findings: list[dict[str, Any]] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    excepted: list[dict[str, Any]] = field(default_factory=list)
    unused_exceptions: list[str] = field(default_factory=list)
    counts: dict[str, dict[str, int]] = field(default_factory=dict)


def load_findings(image: str, report: dict[str, Any]) -> list[Finding]:
    out = []
    for result in report.get("Results") or []:
        for v in result.get("Vulnerabilities") or []:
            out.append(
                Finding(
                    image=image,
                    vuln_id=str(v.get("VulnerabilityID")),
                    package=str(v.get("PkgName")),
                    installed=str(v.get("InstalledVersion", "")),
                    fixed=v.get("FixedVersion") or None,
                    severity=str(v.get("Severity", "UNKNOWN")),
                    status=v.get("Status"),
                    title=str(v.get("Title") or "")[:160],
                    target=str(result.get("Target", "")),
                )
            )
    return out


def validate_exceptions(doc: dict[str, Any], today: date) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (valid exceptions, problems)."""
    valid, problems = [], []
    for i, e in enumerate(doc.get("exceptions") or []):
        label = f"exception[{i}] {e.get('id', '?')}"
        missing = [f for f in REQUIRED_FIELDS if not e.get(f)]
        if missing:
            problems.append(f"{label}: missing {missing}")
            continue
        if e["status"] not in STATUSES:
            problems.append(f"{label}: status must be one of {STATUSES}")
            continue
        try:
            reviewed = date.fromisoformat(str(e["reviewed"]))
            expires = date.fromisoformat(str(e["expires"]))
        except ValueError:
            problems.append(f"{label}: reviewed/expires must be ISO dates")
            continue
        if (expires - reviewed).days > MAX_EXCEPTION_DAYS:
            problems.append(f"{label}: expiry more than {MAX_EXCEPTION_DAYS} days after review")
            continue
        if expires < today:
            problems.append(f"{label}: EXPIRED on {expires.isoformat()} — re-triage required")
            continue
        valid.append(e)
    return valid, problems


def _matches(e: dict[str, Any], f: Finding) -> bool:
    if e["id"] != f.vuln_id or f.image not in e["images"]:
        return False
    pkgs = e.get("packages")
    if pkgs and f.package not in pkgs:
        return False
    # A recorded "no fix" stops covering the finding once a fix exists.
    return not (e["status"] == "no-fix-available" and f.fix_available)


def evaluate(findings: list[Finding], exceptions_doc: dict[str, Any], today: date) -> GateResult:
    valid, problems = validate_exceptions(exceptions_doc, today)
    result = GateResult(passed=True, failures=list(problems))
    used: set[int] = set()
    for f in findings:
        counts = result.counts.setdefault(f.image, {})
        counts[f.severity] = counts.get(f.severity, 0) + 1
        if f.severity not in GATED_SEVERITIES:
            continue
        row = {
            "image": f.image,
            "id": f.vuln_id,
            "package": f.package,
            "installed": f.installed,
            "fixed": f.fixed,
            "severity": f.severity,
            "status": f.status,
            "title": f.title,
        }
        match = next((i for i, e in enumerate(valid) if _matches(e, f)), None)
        if match is None:
            row["decision"] = "FAIL"
            result.failures.append(
                f"{f.image}: {f.severity} {f.vuln_id} in {f.package} {f.installed}"
                + (f" (fixed in {f.fixed})" if f.fixed else " (no fix; no valid exception)")
            )
        else:
            used.add(match)
            row["decision"] = f"EXCEPTED until {valid[match]['expires']}"
            result.excepted.append(row)
        result.findings.append(row)
    result.unused_exceptions = [
        f"{e['id']} ({', '.join(e['images'])})" for i, e in enumerate(valid) if i not in used
    ]
    result.passed = not result.failures
    return result


def summary_markdown(result: GateResult) -> str:
    lines = [
        "## Container vulnerability gate: " + ("PASS" if result.passed else "FAIL"),
        "",
        "| Image | " + " | ".join(("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")) + " |",
        "|---|---|---|---|---|---|",
    ]
    for image, c in sorted(result.counts.items()):
        lines.append(
            f"| `{image}` | "
            + " | ".join(str(c.get(s, 0)) for s in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"))
            + " |"
        )
    if result.failures:
        lines += ["", "### Failing (actionable)", ""] + [f"- {x}" for x in result.failures]
    if result.excepted:
        lines += [
            "",
            "### Time-bounded exceptions in effect",
            "",
            "| Image | Severity | ID | Package | Installed | Fixed | Decision |",
            "|---|---|---|---|---|---|---|",
        ]
        for r in result.excepted:
            lines.append(
                f"| `{r['image']}` | {r['severity']} | {r['id']} | {r['package']} | "
                f"{r['installed']} | {r['fixed'] or '-'} | {r['decision']} |"
            )
    if result.unused_exceptions:
        lines += ["", "### Exceptions matching no finding (remove them)", ""]
        lines += [f"- {x}" for x in result.unused_exceptions]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--exceptions", type=Path, required=True)
    ap.add_argument("--report", action="append", required=True, help="IMAGE=path/to/report.json")
    ap.add_argument("--summary", type=Path)
    ap.add_argument("--result", type=Path)
    ap.add_argument("--today", type=date.fromisoformat, default=date.today())
    args = ap.parse_args(argv)
    findings: list[Finding] = []
    for spec in args.report:
        image, sep, path = spec.partition("=")
        if not sep:
            print(f"--report must be IMAGE=PATH, got {spec!r}", file=sys.stderr)
            return 2
        findings += load_findings(image, json.loads(Path(path).read_text(encoding="utf-8")))
    exceptions = yaml.safe_load(args.exceptions.read_text(encoding="utf-8")) or {}
    result = evaluate(findings, exceptions, args.today)
    md = summary_markdown(result)
    print(md)
    if args.summary:
        args.summary.write_text(md, encoding="utf-8")
    if args.result:
        args.result.write_text(json.dumps(result.__dict__, indent=2), encoding="utf-8")
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
