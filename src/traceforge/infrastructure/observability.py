"""Structured logging with secret redaction, Prometheus metrics, optional OpenTelemetry."""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

correlation_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)

SECRET_PATTERNS = [
    re.compile(
        r"(?i)(api[_-]?key|secret|token|password|passwd|authorization)(\s*[:=]\s*|\"\s*:\s*\")([^\s\",]{4,})"
    ),
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}"),
]


def redact(text: str) -> str:
    for pattern in SECRET_PATTERNS:
        if pattern.groups >= 3:
            text = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
        else:
            text = pattern.sub("[REDACTED]", text)
    return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        cid = correlation_id_var.get()
        if cid:
            payload["correlation_id"] = cid
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_traceforge", False):
            root.removeHandler(h)
    handler = logging.StreamHandler(sys.stderr)
    handler._traceforge = True  # type: ignore[attr-defined]
    handler.setFormatter(
        JsonFormatter() if json_output else logging.Formatter("%(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(level.upper())


class Metrics:
    """Low-cardinality metrics (no task/run UUID labels)."""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()
        r = self.registry
        self.trace_ingestion = Counter(
            "traceforge_trace_ingestion_total", "Ingested records", ["status"], registry=r
        )
        self.validation_failures = Counter(
            "traceforge_trace_validation_failures_total",
            "Validation failures",
            ["reason"],
            registry=r,
        )
        self.dataset_build = Histogram(
            "traceforge_dataset_build_duration_seconds", "Dataset build time", registry=r
        )
        self.sandbox_create = Histogram(
            "traceforge_sandbox_create_duration_seconds",
            "Sandbox creation",
            ["provider"],
            registry=r,
        )
        self.sandbox_timeouts = Counter(
            "traceforge_sandbox_timeout_total", "Sandbox timeouts", ["provider"], registry=r
        )
        self.sandbox_cleanup_failures = Counter(
            "traceforge_sandbox_cleanup_failures_total",
            "Cleanup failures",
            ["provider"],
            registry=r,
        )
        self.tool_calls = Counter(
            "traceforge_agent_tool_calls_total", "Agent tool calls", ["tool", "status"], registry=r
        )
        self.rollout_duration = Histogram(
            "traceforge_agent_rollout_duration_seconds",
            "Rollout duration",
            ["provider_kind"],
            registry=r,
        )
        self.verifier_results = Counter(
            "traceforge_verifier_results_total",
            "Verifier verdicts",
            ["profile", "verdict"],
            registry=r,
        )
        self.reward_hacking = Counter(
            "traceforge_reward_hacking_cases_total",
            "Adversarial cases",
            ["design", "rewarded"],
            registry=r,
        )
        self.training_steps = Counter(
            "traceforge_training_steps_total", "Training steps", ["mode"], registry=r
        )
        self.eval_success = Gauge(
            "traceforge_evaluation_success_rate",
            "Latest evaluation success rate",
            ["split"],
            registry=r,
        )


METRICS = Metrics()


def setup_tracing(service_name: str, console: bool = False) -> Any:
    """Configure an OpenTelemetry tracer provider (console exporter optional)."""
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if console:
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    return trace.get_tracer("traceforge")


def tracer() -> Any:
    from opentelemetry import trace

    return trace.get_tracer("traceforge")
