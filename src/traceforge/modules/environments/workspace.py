"""Path-safe workspace access for agent tools.

Every agent-supplied path is resolved under a fixed per-run root. Absolute paths, drive letters,
parent traversal, NUL bytes, backslashes, symlinks anywhere in the chain, and non-allow-listed
writes are rejected before touching the filesystem.
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path, PurePosixPath

from traceforge.contracts.common import ErrorCode, TraceForgeError, sha256_text

MAX_READ_BYTES = 64_000
MAX_LIST_ENTRIES = 200


class ToolError(TraceForgeError):
    def __init__(self, code: ErrorCode, message: str, **details: object) -> None:
        super().__init__(message, details=dict(details))
        self.code = code


class Workspace:
    def __init__(
        self,
        root: Path,
        editable: frozenset[str],
        protected_globs: tuple[str, ...],
        writable_extensions: tuple[str, ...] = (".py",),
        max_file_bytes: int = MAX_READ_BYTES,
    ) -> None:
        self.root = root.resolve()
        self.editable = editable
        self.protected_globs = protected_globs
        self.writable_extensions = writable_extensions
        self.max_file_bytes = max_file_bytes

    # ------------------------------------------------------------------ path policy
    def normalize(self, rel: str) -> str:
        if not isinstance(rel, str) or rel == "":
            raise ToolError(ErrorCode.PATH_VIOLATION, "path must be a non-empty string")
        if "\x00" in rel or "\\" in rel:
            raise ToolError(ErrorCode.PATH_VIOLATION, "path contains forbidden characters")
        if rel.startswith("/") or (len(rel) > 1 and rel[1] == ":") or rel.startswith("~"):
            raise ToolError(ErrorCode.PATH_VIOLATION, "absolute paths are not allowed")
        parts = PurePosixPath(rel).parts
        if any(p == ".." for p in parts):
            raise ToolError(ErrorCode.PATH_VIOLATION, "parent traversal is not allowed")
        clean = [p for p in parts if p not in (".", "")]
        return "/".join(clean) if clean else "."

    def resolve(self, rel: str) -> tuple[str, Path]:
        norm = self.normalize(rel)
        candidate = self.root if norm == "." else self.root.joinpath(*norm.split("/"))
        # Reject symlinks anywhere between root and target.
        cursor = self.root
        for part in [] if norm == "." else norm.split("/"):
            cursor = cursor / part
            if cursor.is_symlink():
                raise ToolError(ErrorCode.PATH_VIOLATION, "symlinks are not allowed")
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.root):
            raise ToolError(ErrorCode.PATH_VIOLATION, "path escapes the workspace")
        return norm, resolved

    def is_protected(self, norm: str) -> bool:
        return any(fnmatch.fnmatchcase(norm, g) for g in self.protected_globs)

    # ------------------------------------------------------------------ operations
    def list(self, rel: str = ".") -> list[dict[str, object]]:
        norm, path = self.resolve(rel)
        if not path.exists():
            raise ToolError(ErrorCode.NOT_FOUND, f"no such path: {norm}")
        if path.is_file():
            return [{"path": norm, "type": "file", "size": path.stat().st_size}]
        entries: list[dict[str, object]] = []
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = sorted(
                d for d in dirnames if not d.startswith(".") and d != "__pycache__"
            )
            for name in sorted(filenames):
                full = Path(dirpath) / name
                if full.is_symlink():
                    continue
                rel_path = full.resolve().relative_to(self.root).as_posix()
                entries.append(
                    {
                        "path": rel_path,
                        "type": "file",
                        "size": full.stat().st_size,
                        "editable": rel_path in self.editable and not self.is_protected(rel_path),
                    }
                )
                if len(entries) >= MAX_LIST_ENTRIES:
                    return entries
        return entries

    def read(self, rel: str) -> tuple[str, str]:
        norm, path = self.resolve(rel)
        if not path.is_file():
            raise ToolError(ErrorCode.NOT_FOUND, f"no such file: {norm}")
        if path.stat().st_size > self.max_file_bytes:
            raise ToolError(ErrorCode.SIZE_LIMIT, f"file exceeds {self.max_file_bytes} bytes")
        return norm, path.read_text(encoding="utf-8", errors="replace")

    def check_writable(self, rel: str) -> tuple[str, Path]:
        norm, path = self.resolve(rel)
        if self.is_protected(norm):
            raise ToolError(ErrorCode.PROTECTED_PATH, f"{norm} is protected and cannot be modified")
        if norm not in self.editable:
            raise ToolError(ErrorCode.PROTECTED_PATH, f"{norm} is not an editable file")
        if not norm.endswith(self.writable_extensions):
            raise ToolError(ErrorCode.PROTECTED_PATH, "file type is not writable")
        if not path.is_file():
            raise ToolError(ErrorCode.NOT_FOUND, f"no such file: {norm}")
        return norm, path

    def write(self, rel: str, content: str) -> tuple[str, str, str]:
        norm, path = self.check_writable(rel)
        data = content.encode("utf-8")
        if len(data) > self.max_file_bytes:
            raise ToolError(ErrorCode.SIZE_LIMIT, f"content exceeds {self.max_file_bytes} bytes")
        before = sha256_text(path.read_text(encoding="utf-8"))
        path.write_text(content, encoding="utf-8", newline="\n")
        return norm, before, sha256_text(content)

    def snapshot(self) -> dict[str, str]:
        files: dict[str, str] = {}
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for name in filenames:
                full = Path(dirpath) / name
                if full.is_symlink():
                    continue
                rel = full.relative_to(self.root).as_posix()
                files[rel] = full.read_text(encoding="utf-8", errors="replace")
        return dict(sorted(files.items()))
