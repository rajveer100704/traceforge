"""Finite, safe JSON for metric and report documents.

Python's ``json`` writes ``NaN``/``Infinity`` by default, which is not valid JSON and silently
corrupts downstream analysis. ``finite_json`` replaces every non-finite float with ``null`` and
returns the paths it replaced, so the document can record them; ``dumps_finite`` then writes
with ``allow_nan=False`` so a non-finite value can never reach disk unnoticed.
"""

from __future__ import annotations

import json
import math
from typing import Any


def _normalise(obj: Any) -> Any:
    """Plain JSON types; non-JSON objects (datetimes, UUIDs, paths) become strings as before."""
    return json.loads(json.dumps(obj, default=str))


def finite_json(obj: Any) -> tuple[Any, list[str]]:
    """(copy of ``obj`` with non-finite floats replaced by None, sorted list of their paths)."""
    paths: list[str] = []

    def walk(value: Any, path: str) -> Any:
        if isinstance(value, float) and not math.isfinite(value):
            paths.append(path or "$")
            return None
        if isinstance(value, dict):
            return {k: walk(v, f"{path}.{k}" if path else str(k)) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v, f"{path}[{i}]") for i, v in enumerate(value)]
        return value

    return walk(_normalise(obj), ""), sorted(paths)


def dumps_finite(obj: Any, *, record_key: str | None = "non_finite_fields", **kwargs: Any) -> str:
    """Serialise ``obj`` as strict JSON. When ``record_key`` is set and ``obj`` is a dict with
    non-finite values, the replaced paths are recorded under that key."""
    clean, paths = finite_json(obj)
    if paths and record_key and isinstance(clean, dict):
        clean[record_key] = paths
    return json.dumps(clean, allow_nan=False, **kwargs)
