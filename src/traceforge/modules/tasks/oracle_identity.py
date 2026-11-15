"""Authoritative oracle identity of a task: what decides PASS/FAIL, independent of cosmetics.

Generated task instances of one template differ only in names and literal values (seeded
identifiers, hosts, table names). Whether two tasks share a *structural scenario* is a question
about their oracles: the hidden tests, the visible tests and the verifier configuration that
judge an episode. ``oracle_fingerprint`` hashes a normalised form of exactly those inputs.

Normalisation (``ORACLE_NORMALISATION_VERSION``): Python sources are tokenised; comments and
non-logical newlines are dropped; keywords, operators and NEWLINE/INDENT/DEDENT are kept;
every other identifier becomes ``N``, every string literal ``S`` and every number ``0``. The
fingerprint therefore changes when test logic, assertions, operators or structure change, and
does not change when only seeded names or literal values change. Names alone are never used as
evidence of equivalent oracle behaviour: test bodies are part of the fingerprint.
"""

from __future__ import annotations

import io
import keyword
import tokenize
from typing import Any

from traceforge.contracts.common import sha256_json, sha256_text
from traceforge.contracts.tasks import TaskBundle

ORACLE_NORMALISATION_VERSION = "1"
VISIBLE_TEST_PATH = "tests/test_visible.py"

_DROP = frozenset({tokenize.COMMENT, tokenize.NL, tokenize.ENCODING, tokenize.ENDMARKER})
_LAYOUT = frozenset({tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT})


def normalise_source(source: str) -> str:
    """Token stream of ``source`` with identifiers and literals abstracted (see module doc)."""
    out: list[str] = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type in _DROP:
            continue
        if tok.type == tokenize.NAME:
            out.append(tok.string if keyword.iskeyword(tok.string) else "N")
        elif tok.type == tokenize.STRING:
            out.append("S")
        elif tok.type == tokenize.NUMBER:
            out.append("0")
        elif tok.type in _LAYOUT:
            out.append(tokenize.tok_name[tok.type])
        else:
            out.append(tok.string)
    return " ".join(out)


def source_fingerprint(source: str) -> str:
    return sha256_text(normalise_source(source))


def oracle_identity(bundle: TaskBundle, oracle_version: str) -> dict[str, Any]:
    """The explicit, hashable oracle identity of one task bundle."""
    o = bundle.oracle
    return {
        "normalisation_version": ORACLE_NORMALISATION_VERSION,
        "oracle_version": oracle_version,
        "hidden_tests": {
            name: source_fingerprint(text) for name, text in sorted(bundle.hidden_files.items())
        },
        "visible_tests": source_fingerprint(bundle.workspace_files.get(VISIBLE_TEST_PATH, "")),
        "verifier": {
            "verifier_id": o.verifier_id,
            "verifier_version": o.verifier_version,
            "hidden_test_module": o.hidden_test_module,
            "security_test_prefix": o.security_test_prefix,
            "functional_test_prefix": o.functional_test_prefix,
            "editable_globs": sorted(o.editable_globs),
            "protected_globs": sorted(o.protected_globs),
            "success_predicates": sorted(o.success_predicates),
        },
    }


def oracle_fingerprint(bundle: TaskBundle, oracle_version: str) -> str:
    return sha256_json(oracle_identity(bundle, oracle_version))
