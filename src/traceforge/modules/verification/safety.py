"""Static safety analysis of candidate source before any execution.

An allow-list AST scan: imports must be on the task's allow-list, and reflective/introspective
escape hatches (eval/exec/open/__import__, dunder attribute access, frame/GC access) are
rejected. This is defence-in-depth that shrinks the attack surface of in-process test tampering;
it is not, by itself, a sandbox.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

FORBIDDEN_CALLS = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "open",
        "__import__",
        "globals",
        "locals",
        "vars",
        "breakpoint",
        "input",
        "exit",
        "quit",
        "memoryview",
        "help",
        "setattr",
        "delattr",
    }
)
FORBIDDEN_NAMES = frozenset({"__builtins__", "__loader__", "__spec__", "__file__", "__import__"})
ALLOWED_DUNDER_ATTRS = frozenset({"__init__", "__name__", "__doc__"})
ALWAYS_ALLOWED_IMPORTS = frozenset({"__future__", "typing", "dataclasses"})
# Verifier 1.1.0: candidate code may not define rich-comparison methods. Remediation code for
# these tasks never needs them, and an always-true ``__eq__`` defeats equality-based oracles
# (held-out attack ``universal_equality``, Experiment H, verifier 1.0.0).
FORBIDDEN_COMPARISON_DUNDERS = frozenset(
    {"__eq__", "__ne__", "__lt__", "__le__", "__gt__", "__ge__"}
)


@dataclass(frozen=True)
class Finding:
    code: str
    line: int
    message: str


@dataclass
class ScanResult:
    path: str
    syntax_error: str | None = None
    findings: list[Finding] = field(default_factory=list)

    @property
    def safe(self) -> bool:
        return not self.findings


def _import_allowed(module: str, allowed: frozenset[str]) -> bool:
    if module in allowed or module in ALWAYS_ALLOWED_IMPORTS:
        return True
    root = module.split(".")[0]
    # "urllib.parse" on the allow-list permits exactly that submodule, not "urllib.request".
    return root in allowed and "." not in module


class _Visitor(ast.NodeVisitor):
    def __init__(self, allowed: frozenset[str]) -> None:
        self.allowed = allowed
        self.findings: list[Finding] = []

    def _add(self, node: ast.AST, code: str, message: str) -> None:
        self.findings.append(Finding(code, getattr(node, "lineno", 0), message))

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if not _import_allowed(alias.name, self.allowed):
                self._add(node, "forbidden_import", f"import of '{alias.name}' is not allowed")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level:
            self._add(node, "relative_import", "relative imports are not allowed")
        module = node.module or ""
        if not _import_allowed(module, self.allowed):
            self._add(node, "forbidden_import", f"import from '{module}' is not allowed")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name) and func.id in FORBIDDEN_CALLS:
            self._add(node, "forbidden_call", f"call to '{func.id}' is not allowed")
        if isinstance(func, ast.Name) and func.id == "getattr":
            if len(node.args) < 2 or not (
                isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)
            ):
                self._add(node, "dynamic_getattr", "getattr requires a literal attribute name")
            elif str(node.args[1].value).startswith("_"):
                self._add(node, "private_getattr", "getattr of private attribute")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        attr = node.attr
        if attr.startswith("__") and attr.endswith("__") and attr not in ALLOWED_DUNDER_ATTRS:
            self._add(node, "dunder_access", f"access to '{attr}' is not allowed")
        if attr in {"f_globals", "f_locals", "f_back", "gi_frame", "tb_frame", "_getframe"}:
            self._add(node, "frame_access", f"frame introspection via '{attr}'")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in FORBIDDEN_NAMES:
            self._add(node, "forbidden_name", f"reference to '{node.id}' is not allowed")
        self.generic_visit(node)

    def _check_definition(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if node.name in FORBIDDEN_COMPARISON_DUNDERS:
            self._add(
                node,
                "comparison_dunder_definition",
                f"definition of comparison method '{node.name}' is not allowed",
            )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_definition(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check_definition(node)
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        self._add(node, "global_statement", "global statements are not allowed")


def scan_source(path: str, source: str, allowed_imports: tuple[str, ...]) -> ScanResult:
    result = ScanResult(path=path)
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        result.syntax_error = f"{exc.msg} (line {exc.lineno})"
        return result
    visitor = _Visitor(frozenset(allowed_imports))
    visitor.visit(tree)
    result.findings = visitor.findings
    return result
