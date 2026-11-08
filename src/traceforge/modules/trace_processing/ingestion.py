"""Pure ingestion pipeline: raw bytes -> raw records -> normalised + assessed | quarantined.

Persistence of raw bytes happens in the application layer *before* this transformation runs.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from pydantic import ValidationError

from traceforge.contracts.common import sha256_text, utc_now
from traceforge.contracts.datasets import QualityAssessment, QualityReport
from traceforge.contracts.traces import QuarantineRecord, RawRecord, Trajectory
from traceforge.modules.trace_processing.normalization import (
    NormalizationError,
    deterministic_id,
    normalize_record,
)
from traceforge.modules.trace_processing.quality import QualityEngine, build_report

MAX_LINE_BYTES = 2_000_000


@dataclass
class IngestionResult:
    raw: list[RawRecord] = field(default_factory=list)
    trajectories: list[tuple[Trajectory, QualityAssessment, RawRecord]] = field(
        default_factory=list
    )
    quarantined: list[tuple[QuarantineRecord, RawRecord]] = field(default_factory=list)
    report: QualityReport = field(default_factory=QualityReport)


def split_jsonl(data: bytes) -> list[tuple[int, str]]:
    text = data.decode("utf-8", errors="replace")
    return [(i + 1, line) for i, line in enumerate(text.splitlines()) if line.strip()]


class IngestionPipeline:
    def __init__(self, quality: QualityEngine) -> None:
        self.quality = quality

    def run(self, source_uri: str, lines: Iterable[tuple[int | None, str]]) -> IngestionResult:
        result = IngestionResult()
        reasons: list[str] = []
        for line_no, payload in lines:
            raw = RawRecord(
                raw_id=deterministic_id("raw", source_uri, str(line_no), sha256_text(payload)),
                source_uri=source_uri,
                line_number=line_no,
                sha256=sha256_text(payload),
                payload=payload,
                received_at=utc_now(),
            )
            result.raw.append(raw)
            if len(payload.encode("utf-8")) > MAX_LINE_BYTES:
                q = QuarantineRecord(
                    raw_id=raw.raw_id,
                    source_uri=source_uri,
                    line_number=line_no,
                    reasons=["record_too_large"],
                    details=[f">{MAX_LINE_BYTES} bytes"],
                )
                result.quarantined.append((q, raw))
                reasons.append("record_too_large")
                continue
            try:
                trajectory = normalize_record(payload, source_uri, line_no)
            except NormalizationError as exc:
                q = QuarantineRecord(
                    raw_id=raw.raw_id,
                    source_uri=source_uri,
                    line_number=line_no,
                    reasons=[exc.reason],
                    details=exc.details[:20],
                )
                result.quarantined.append((q, raw))
                reasons.append(exc.reason)
                continue
            except ValidationError as exc:
                details = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()][:20]
                q = QuarantineRecord(
                    raw_id=raw.raw_id,
                    source_uri=source_uri,
                    line_number=line_no,
                    reasons=["schema_invalid"],
                    details=details,
                )
                result.quarantined.append((q, raw))
                reasons.append("schema_invalid")
                continue
            result.trajectories.append((trajectory, self.quality.assess(trajectory), raw))
        result.report = build_report(
            len(result.raw), reasons, [(t, a) for t, a, _ in result.trajectories]
        )
        return result
