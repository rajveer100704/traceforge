"""Protected historical artifacts must stay byte-for-byte identical to the v0.2.0 release.

The manifest (research/HISTORICAL_SHA256SUMS) was generated from the git objects at tag v0.2.0 by
scripts/historical_manifest.py. These tests hash the working tree independently of that script,
reject symlinks, and anchor the manifest itself to the tag (mandatory on GitHub Actions, where CI
fetches the tag; skipped only for local clones without it). New results belong in new
directories; historical directories may not gain, lose or change entries.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess  # nosec B404
from pathlib import Path
from types import ModuleType

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
ON_CI = os.environ.get("GITHUB_ACTIONS") == "true"


def _manifest() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            digest, name = line.split(maxsplit=1)
            out[name.strip()] = digest
    return out


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _entries_under(root: Path, rel_dir: str) -> tuple[set[str], set[str]]:
    """(regular files, symlinks) below ``rel_dir`` without following symlinks."""
    files: set[str] = set()
    links: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root / rel_dir, followlinks=False):
        for name in [*dirnames, *filenames]:
            p = Path(dirpath) / name
            rel = p.relative_to(root).as_posix()
            if p.is_symlink():
                links.add(rel)
            elif p.is_file():
                files.add(rel)
    return files, links


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "historical_manifest", ROOT / "scripts" / "historical_manifest.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tag_available() -> bool:
    if shutil.which("git") is None:
        return False
    r = subprocess.run(  # nosec B603 B607
        ["git", "rev-parse", "-q", "--verify", "v0.2.0^{commit}"], cwd=ROOT, capture_output=True
    )
    return r.returncode == 0


def test_manifest_covers_every_protected_artifact() -> None:
    entries = _manifest()
    for name in ALWAYS_PROTECTED:
        assert name in entries
    for exp in HISTORICAL_EXPERIMENTS:
        assert f"research/results/{exp}/bundle/bundle.json" in entries
        assert f"research/results/{exp}/{exp.split('_verifier')[0]}.json" in entries
    assert len(entries) == 55


def test_protected_files_are_regular_and_byte_identical() -> None:
    problems = []
    for name, digest in sorted(_manifest().items()):
        f = ROOT / name
        if f.is_symlink():
            problems.append(f"symlink {name}")
        elif not f.is_file():
            problems.append(f"missing {name}")
        elif _sha256(f) != digest:
            problems.append(f"modified {name}")
    assert problems == []


def test_historical_directories_gain_no_entries_and_contain_no_symlinks() -> None:
    entries = set(_manifest())
    for exp in HISTORICAL_EXPERIMENTS:
        files, links = _entries_under(ROOT, f"research/results/{exp}")
        assert links == set(), f"symlinks in {exp}"
        assert files - entries == set(), f"unexpected files in {exp}"


@pytest.mark.parametrize("exp", HISTORICAL_EXPERIMENTS)
def test_historical_bundle_verifies(exp: str) -> None:
    assert verify_bundle(RESULTS / exp / "bundle") == []


def test_bundle_hashes_agree_with_manifest() -> None:
    """Two independent records of the same bytes: bundle.json and the tag-derived manifest."""
    entries = _manifest()
    for exp in HISTORICAL_EXPERIMENTS:
        bundle = RESULTS / exp / "bundle"
        meta = json.loads((bundle / "bundle.json").read_text(encoding="utf-8"))
        for rel, digest in meta["files_sha256"].items():
            assert entries[(bundle / rel).relative_to(ROOT).as_posix()] == digest


def test_manifest_matches_release_tag() -> None:
    """Anchors the manifest to v0.2.0, so editing a file and the manifest together still fails.

    CI fetches the tag explicitly, so on GitHub Actions a missing tag is a failure, not a skip.
    """
    if not _tag_available():
        if ON_CI:
            pytest.fail("tag v0.2.0 is not available in CI; the workflow must fetch it")
        pytest.skip("git tag v0.2.0 not fetched (local clone without tags)")
    entries = _manifest()
    tag_names = subprocess.run(  # nosec B603 B607
        ["git", "ls-tree", "-r", "--name-only", "v0.2.0", "--", *ALWAYS_PROTECTED]
        + [f"research/results/{e}" for e in HISTORICAL_EXPERIMENTS],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode()
    assert set(tag_names.split()) == set(entries)
    for name, digest in entries.items():
        blob = subprocess.run(  # nosec B603 B607
            ["git", "cat-file", "blob", f"v0.2.0:{name}"], cwd=ROOT, capture_output=True, check=True
        ).stdout
        assert hashlib.sha256(blob).hexdigest() == digest, name


def test_checker_script_reports_intact_tree() -> None:
    assert _script().check() == []


# ---------------------------------------------------------------- negative tests (copies only)


def _protected_copy(tmp_path: Path) -> Path:
    for name in _manifest():
        dst = tmp_path / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, dst)
    return tmp_path


def test_checker_detects_modified_missing_and_unlisted(tmp_path: Path) -> None:
    root = _protected_copy(tmp_path)
    check = _script().check
    assert check(MANIFEST, root) == []
    report = root / "research/TRACEFORGE_TECHNICAL_REPORT.md"
    report.write_bytes(report.read_bytes() + b"\n")
    (root / "research/results/experiment_B/experiment_B.md").unlink()
    (root / "research/results/experiment_H/extra.json").write_text("{}", encoding="utf-8")
    assert sorted(check(MANIFEST, root)) == [
        "missing research/results/experiment_B/experiment_B.md",
        "modified research/TRACEFORGE_TECHNICAL_REPORT.md",
        "unlisted research/results/experiment_H/extra.json",
    ]


def test_checker_detects_truncated_manifest(tmp_path: Path) -> None:
    root = _protected_copy(tmp_path)
    lines = MANIFEST.read_text(encoding="utf-8").splitlines(keepends=True)
    truncated = tmp_path / "TRUNCATED"
    truncated.write_text("".join(lines[:-5]), encoding="utf-8")
    problems = _script().check(truncated, root)
    assert len(problems) == 5 and all(p.startswith("unlisted ") for p in problems)


def _can_symlink(tmp_path: Path) -> bool:
    try:
        (tmp_path / "probe-link").symlink_to(tmp_path)
    except OSError:
        return False
    (tmp_path / "probe-link").unlink()
    return True


def test_checker_rejects_symlinked_files_and_directories(tmp_path: Path) -> None:
    if not _can_symlink(tmp_path):
        pytest.skip("symlink creation not permitted on this platform")
    root = _protected_copy(tmp_path / "tree")
    report = root / "research/TRACEFORGE_TECHNICAL_REPORT.md"
    same_bytes = tmp_path / "elsewhere.md"
    shutil.copyfile(report, same_bytes)
    report.unlink()
    report.symlink_to(same_bytes)  # identical bytes, but no longer the committed regular file
    extra_dir = tmp_path / "extra"
    extra_dir.mkdir()
    (extra_dir / "added.json").write_text("{}", encoding="utf-8")
    (root / "research/results/experiment_A/linked").symlink_to(extra_dir, target_is_directory=True)
    problems = _script().check(MANIFEST, root)
    assert "symlink research/TRACEFORGE_TECHNICAL_REPORT.md" in problems
    assert "symlink research/results/experiment_A/linked" in problems
    _files, links = _entries_under(root, "research/results/experiment_A")
    assert "research/results/experiment_A/linked" in links
