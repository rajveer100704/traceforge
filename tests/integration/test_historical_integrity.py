"""Protected historical artifacts must stay byte-for-byte identical to the v0.2.0 release.

The manifest (research/HISTORICAL_SHA256SUMS) was generated from the git objects at tag v0.2.0 by
scripts/historical_manifest.py. These tests hash the working tree independently of that script.
New results belong in new directories; historical directories may not gain, lose or change files.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess  # nosec B404
from pathlib import Path

import pytest

from traceforge.application.reproducibility import verify_bundle

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "research" / "HISTORICAL_SHA256SUMS"
RESULTS = ROOT / "research" / "results"
HISTORICAL_EXPERIMENTS = (
    "experiment_A",
    "experiment_B",
    "experiment_C",
    "experiment_D",
    "experiment_E",
    "experiment_F",
    "experiment_G",
    "experiment_H",
    "experiment_H_verifier_1.1.0",
)
ALWAYS_PROTECTED = (
    "research/TRACEFORGE_TECHNICAL_REPORT.md",
    "src/traceforge/modules/tasks/families/legacy_1_0_0.py",
)


def _manifest() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            digest, name = line.split(maxsplit=1)
            out[name.strip()] = digest
    return out


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _problems(entries: dict[str, str], root: Path) -> list[str]:
    problems = []
    for name, digest in sorted(entries.items()):
        f = root / name
        if not f.is_file():
            problems.append(f"missing {name}")
        elif _sha256(f) != digest:
            problems.append(f"modified {name}")
    return problems


def test_manifest_covers_every_protected_artifact() -> None:
    entries = _manifest()
    for name in ALWAYS_PROTECTED:
        assert name in entries
    for exp in HISTORICAL_EXPERIMENTS:
        assert f"research/results/{exp}/bundle/bundle.json" in entries
        assert f"research/results/{exp}/{exp.split('_verifier')[0]}.json" in entries
    assert len(entries) == 55


def test_protected_files_are_byte_identical_to_v0_2_0() -> None:
    assert _problems(_manifest(), ROOT) == []


def test_historical_directories_gain_no_files() -> None:
    """Adding files to a historical result directory is also a modification."""
    entries = set(_manifest())
    for exp in HISTORICAL_EXPERIMENTS:
        on_disk = {
            p.relative_to(ROOT).as_posix() for p in (RESULTS / exp).rglob("*") if p.is_file()
        }
        assert on_disk - entries == set(), f"unexpected files in {exp}"


@pytest.mark.parametrize("exp", HISTORICAL_EXPERIMENTS)
def test_historical_bundle_verifies(exp: str) -> None:
    bundle = RESULTS / exp / "bundle"
    assert verify_bundle(bundle) == []


def test_bundle_hashes_agree_with_manifest() -> None:
    """Two independent records of the same bytes: bundle.json and the tag-derived manifest."""
    import json

    entries = _manifest()
    for exp in HISTORICAL_EXPERIMENTS:
        bundle = RESULTS / exp / "bundle"
        meta = json.loads((bundle / "bundle.json").read_text(encoding="utf-8"))
        for rel, digest in meta["files_sha256"].items():
            assert entries[(bundle / rel).relative_to(ROOT).as_posix()] == digest


def test_tampering_is_detected(tmp_path: Path) -> None:
    name = "research/TRACEFORGE_TECHNICAL_REPORT.md"
    copy = tmp_path / name
    copy.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / name, copy)
    entries = {name: _manifest()[name]}
    assert _problems(entries, tmp_path) == []
    copy.write_bytes(copy.read_bytes() + b"\n")
    assert _problems(entries, tmp_path) == [f"modified {name}"]
    copy.unlink()
    assert _problems(entries, tmp_path) == [f"missing {name}"]


def _tag_available() -> bool:
    if shutil.which("git") is None:
        return False
    r = subprocess.run(  # nosec B603 B607
        ["git", "rev-parse", "-q", "--verify", "v0.2.0^{commit}"],
        cwd=ROOT,
        capture_output=True,
    )
    return r.returncode == 0


@pytest.mark.skipif(not _tag_available(), reason="git tag v0.2.0 not fetched (shallow clone)")
def test_manifest_matches_release_tag() -> None:
    """The manifest itself must equal the bytes committed at the v0.2.0 tag."""
    for name, digest in _manifest().items():
        blob = subprocess.run(  # nosec B603 B607
            ["git", "cat-file", "blob", f"v0.2.0:{name}"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        ).stdout
        assert hashlib.sha256(blob).hexdigest() == digest, name
