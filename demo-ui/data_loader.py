"""Read-only data layer for the Northstar evaluation demo.

The UI intentionally reads the two saved runtime artifacts instead of running
the benchmark again.  This module does not load ``golden_dataset.json`` and it
does not expose ``expected_answer`` or gold contexts to the live RAG path.
That keeps the presentation layer useful without creating another leakage
surface.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence


ArtifactStatus = Literal["ok", "missing", "invalid", "partial"]


@dataclass(frozen=True)
class ArtifactState:
    """Small, display-safe status record for one artifact file."""

    name: str
    path: Path
    status: ArtifactStatus
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass(frozen=True)
class ArtifactRead:
    """Result of attempting to read one JSON artifact."""

    payload: dict[str, Any] | None
    state: ArtifactState


@dataclass(frozen=True)
class RetrievedContext:
    """A retriever trace item, ordered exactly as returned by the retriever."""

    rank: int
    source_doc: str
    chunk_id: str | None
    text: str
    score: float | None = None


@dataclass(frozen=True)
class BenchmarkCase:
    """Joined benchmark result plus the saved retriever trace.

    Notice that this model contains the actual answer and evaluation scores,
    but deliberately does not contain the golden expected answer or gold
    contexts.
    """

    id: str
    difficulty: str
    question: str
    actual_answer: str
    faithfulness: float | None
    relevance: float | None
    completeness: float | None
    context_recall: float | None
    context_precision: float | None
    overall: float | None
    passed: bool
    failure_type: str | None
    failure_reason: str
    retrieved_contexts: tuple[RetrievedContext, ...] = ()
    actual_error: str | None = None


@dataclass(frozen=True)
class BenchmarkSnapshot:
    """All read-only data needed by the four UI areas."""

    cases: tuple[BenchmarkCase, ...]
    summary: dict[str, Any]
    failure_analysis: dict[str, Any]
    corpus_id: str | None
    model: str | None
    top_k: int | None
    prompt_version: str | None
    generated_at: str | None
    benchmark_status: ArtifactState
    actual_status: ArtifactState
    warnings: tuple[str, ...] = ()


_METRIC_FIELDS = (
    "faithfulness",
    "relevance",
    "completeness",
    "context_recall",
    "context_precision",
)

_FAILURE_REASONS = {
    "hallucination": (
        "Một phần claim trong answer chưa được grounded tốt vào context đã truy hồi."
    ),
    "irrelevant": "Answer có thông tin hữu ích nhưng chưa bám đủ vào intent của câu hỏi.",
    "incomplete": "Answer bỏ sót một hoặc nhiều điều kiện, ngoại lệ hoặc bước quan trọng.",
    "off_topic": "Answer bị lệch trọng tâm so với câu hỏi hoặc mở rộng sang policy khác.",
    "refusal": "Assistant từ chối trong một tình huống vẫn có thể trả lời an toàn.",
}


def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def read_json_artifact(path: str | Path, name: str) -> ArtifactRead:
    """Read a JSON object and return a non-throwing status for the UI.

    Missing and malformed artifacts are expected presentation states during a
    local demo, so callers receive a status instead of an uncaught exception.
    """

    resolved = _resolved(path)
    if not resolved.exists():
        return ArtifactRead(
            None,
            ArtifactState(name, resolved, "missing", f"{name} not found: {resolved}"),
        )
    if not resolved.is_file():
        return ArtifactRead(
            None,
            ArtifactState(name, resolved, "invalid", f"{name} is not a file: {resolved}"),
        )

    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        return ArtifactRead(
            None,
            ArtifactState(name, resolved, "invalid", f"{name} cannot be read: {exc}"),
        )
    except json.JSONDecodeError as exc:
        return ArtifactRead(
            None,
            ArtifactState(
                name,
                resolved,
                "invalid",
                f"{name} contains invalid JSON at line {exc.lineno}, column {exc.colno}.",
            ),
        )

    if not isinstance(payload, dict):
        return ArtifactRead(
            None,
            ArtifactState(name, resolved, "invalid", f"{name} root must be a JSON object."),
        )
    return ArtifactRead(payload, ArtifactState(name, resolved, "ok"))


def _non_empty_text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _score(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    # Scores in the saved benchmark are normalized to [0, 1].  Refuse invalid
    # values instead of displaying a misleading percentage.
    if number < 0.0 or number > 1.0:
        return None
    return number


def _retriever_score(value: Any) -> float | None:
    """Validate a BM25 trace score; unlike evaluation scores it may exceed 1."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0.0 else None


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _failure_reason(failure_type: str | None) -> str:
    if not failure_type:
        return "Không có failure được ghi nhận trong snapshot."
    return _FAILURE_REASONS.get(
        failure_type,
        f"Snapshot ghi nhận failure type `{failure_type}`; cần xem lại answer và trace.",
    )


def _parse_contexts(
    record: Mapping[str, Any] | None,
    case_id: str,
    warnings: list[str],
) -> tuple[RetrievedContext, ...]:
    if record is None:
        return ()
    raw_contexts = record.get("retrieved_contexts")
    if raw_contexts is None:
        return ()
    if not isinstance(raw_contexts, list):
        warnings.append(f"{case_id}: retrieved_contexts is not a list; trace hidden.")
        return ()

    contexts: list[RetrievedContext] = []
    for index, raw_context in enumerate(raw_contexts, start=1):
        if not isinstance(raw_context, Mapping):
            warnings.append(f"{case_id}: retrieved_contexts[{index}] is not an object.")
            continue
        text = _non_empty_text(raw_context.get("text"))
        if text is None:
            warnings.append(f"{case_id}: retrieved_contexts[{index}] has no text.")
            continue
        source_doc = _non_empty_text(raw_context.get("source_doc")) or "Unknown source"
        chunk_id = _non_empty_text(raw_context.get("chunk_id"))
        contexts.append(
            RetrievedContext(
                rank=index,
                source_doc=source_doc,
                chunk_id=chunk_id,
                text=text,
                score=_retriever_score(raw_context.get("score")),
            )
        )
    return tuple(contexts)


def _join_internal(
    benchmark: Mapping[str, Any],
    actual: Mapping[str, Any] | None,
) -> tuple[list[BenchmarkCase], list[str], list[str]]:
    """Join data and return cases plus benchmark/actual warnings."""

    benchmark_warnings: list[str] = []
    actual_warnings: list[str] = []
    raw_results = benchmark.get("results")
    if not isinstance(raw_results, list):
        raise ValueError("Benchmark artifact results must be a list")

    actual_by_id: dict[str, Mapping[str, Any]] = {}
    if actual is not None:
        raw_answers = actual.get("answers")
        if not isinstance(raw_answers, list):
            actual_warnings.append("Actual answers artifact has no answers list.")
        else:
            for index, raw_answer in enumerate(raw_answers):
                if not isinstance(raw_answer, Mapping):
                    actual_warnings.append(f"Actual answers[{index}] is not an object.")
                    continue
                record_id = _non_empty_text(raw_answer.get("id"))
                if record_id is None:
                    actual_warnings.append(f"Actual answers[{index}] has no id.")
                    continue
                if record_id in actual_by_id:
                    actual_warnings.append(f"Duplicate actual answer id: {record_id}.")
                    continue
                actual_by_id[record_id] = raw_answer

    cases: list[BenchmarkCase] = []
    seen_ids: set[str] = set()
    for index, raw_result in enumerate(raw_results):
        if not isinstance(raw_result, Mapping):
            benchmark_warnings.append(f"Benchmark results[{index}] is not an object.")
            continue
        case_id = _non_empty_text(raw_result.get("id"))
        if case_id is None:
            benchmark_warnings.append(f"Benchmark results[{index}] has no id.")
            continue
        if case_id in seen_ids:
            benchmark_warnings.append(f"Duplicate benchmark result id: {case_id}.")
            continue
        seen_ids.add(case_id)

        actual_record = actual_by_id.get(case_id)
        question = _non_empty_text(raw_result.get("question"))
        if question is None and actual_record is not None:
            question = _non_empty_text(actual_record.get("question"))
        answer = _non_empty_text(raw_result.get("actual_answer"))
        if answer is None and actual_record is not None:
            answer = _non_empty_text(actual_record.get("actual_answer"))
        if question is None or answer is None:
            benchmark_warnings.append(f"{case_id}: question or actual_answer is missing.")
            continue

        difficulty = (_non_empty_text(raw_result.get("difficulty")) or "unknown").lower()
        failure_type = _non_empty_text(raw_result.get("failure_type"))
        raw_reason = _non_empty_text(raw_result.get("failure_reason"))
        actual_error = None
        if actual_record is not None and actual_record.get("error") is not None:
            actual_error = _non_empty_text(str(actual_record.get("error")))
            actual_warnings.append(f"{case_id}: actual answer contains an inference error.")

        contexts = _parse_contexts(actual_record, case_id, actual_warnings)
        # A future artifact may persist traces alongside benchmark results.  It
        # is safe to accept those traces because they are retriever output, not
        # golden evidence.
        if not contexts and isinstance(raw_result.get("retrieved_contexts"), list):
            contexts = _parse_contexts(raw_result, case_id, benchmark_warnings)

        scores = {field: _score(raw_result.get(field)) for field in _METRIC_FIELDS}
        overall = _score(raw_result.get("overall"))
        if overall is None:
            answer_scores = [scores[field] for field in _METRIC_FIELDS[:3]]
            usable = [value for value in answer_scores if value is not None]
            overall = sum(usable) / len(usable) if usable else None

        passed_value = raw_result.get("passed")
        passed = bool(passed_value) if isinstance(passed_value, bool) else False
        cases.append(
            BenchmarkCase(
                id=case_id,
                difficulty=difficulty,
                question=question,
                actual_answer=answer,
                faithfulness=scores["faithfulness"],
                relevance=scores["relevance"],
                completeness=scores["completeness"],
                context_recall=scores["context_recall"],
                context_precision=scores["context_precision"],
                overall=overall,
                passed=passed,
                failure_type=failure_type,
                failure_reason=raw_reason or _failure_reason(failure_type),
                retrieved_contexts=contexts,
                actual_error=actual_error,
            )
        )

    if actual is not None:
        unknown_ids = sorted(set(actual_by_id) - seen_ids)
        if unknown_ids:
            actual_warnings.append(
                "Actual answers contain ids absent from benchmark: " + ", ".join(unknown_ids)
            )
    return cases, benchmark_warnings, actual_warnings


def join_benchmark_artifacts(
    benchmark: Mapping[str, Any],
    actual: Mapping[str, Any] | None = None,
) -> list[BenchmarkCase]:
    """Join benchmark results with actual-answer retriever traces by case id."""

    cases, _, _ = _join_internal(benchmark, actual)
    return cases


def _mean(cases: Sequence[BenchmarkCase], field: str) -> float | None:
    values = [getattr(case, field) for case in cases]
    usable = [value for value in values if isinstance(value, (int, float))]
    return sum(usable) / len(usable) if usable else None


def _build_summary(
    raw_summary: Any,
    cases: Sequence[BenchmarkCase],
) -> dict[str, Any]:
    passed = sum(1 for case in cases if case.passed)
    total = len(cases)
    failure_types: dict[str, int] = {}
    for case in cases:
        if case.failure_type:
            failure_types[case.failure_type] = failure_types.get(case.failure_type, 0) + 1

    summary: dict[str, Any] = {
        "total": total,
        "passed": passed,
        "pass_rate": passed / total if total else 0.0,
        "failure_types": failure_types,
    }
    for field in _METRIC_FIELDS:
        summary[f"avg_{field}"] = _mean(cases, field)
    # Keep non-score metadata from the artifact available to callers without
    # allowing a stale summary to disagree with the rows displayed in the UI.
    if isinstance(raw_summary, Mapping):
        for key in ("snapshot", "notes"):
            if key in raw_summary:
                summary[key] = raw_summary[key]
    return summary


def _normalise_failure_analysis(raw: Any, summary: Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        analysis = dict(raw)
    else:
        analysis = {}
    counts = analysis.get("counts")
    if not isinstance(counts, Mapping):
        counts = summary.get("failure_types", {})
    analysis["counts"] = dict(counts) if isinstance(counts, Mapping) else {}
    suggestions = analysis.get("suggestions")
    analysis["suggestions"] = (
        [item.strip() for item in suggestions if isinstance(item, str) and item.strip()]
        if isinstance(suggestions, list)
        else []
    )
    improvement_log = analysis.get("improvement_log")
    analysis["improvement_log"] = improvement_log if isinstance(improvement_log, str) else ""
    return analysis


def _snapshot_from_reads(benchmark_read: ArtifactRead, actual_read: ArtifactRead) -> BenchmarkSnapshot:
    warnings: list[str] = []
    benchmark_data = benchmark_read.payload
    actual_data = actual_read.payload

    cases: list[BenchmarkCase] = []
    benchmark_warnings: list[str] = []
    actual_warnings: list[str] = []
    if benchmark_data is not None:
        try:
            cases, benchmark_warnings, actual_warnings = _join_internal(
                benchmark_data, actual_data
            )
        except (TypeError, ValueError) as exc:
            benchmark_warnings.append(str(exc))
    else:
        warnings.append(benchmark_read.state.message)

    if actual_data is None:
        warnings.append(actual_read.state.message)
    warnings.extend(benchmark_warnings)
    warnings.extend(actual_warnings)

    benchmark_state = benchmark_read.state
    actual_state = actual_read.state
    if benchmark_state.status == "ok" and benchmark_warnings:
        benchmark_state = replace(
            benchmark_state,
            status="partial",
            message="Benchmark artifact loaded with row-level warnings.",
        )
    if actual_state.status == "ok" and actual_warnings:
        actual_state = replace(
            actual_state,
            status="partial",
            message="Actual answers loaded with trace-level warnings.",
        )

    summary = _build_summary(
        benchmark_data.get("summary") if benchmark_data else None,
        cases,
    )
    raw_failure_analysis = benchmark_data.get("failure_analysis") if benchmark_data else None
    failure_analysis = _normalise_failure_analysis(raw_failure_analysis, summary)

    agent = actual_data.get("agent") if isinstance(actual_data, Mapping) else None
    if not isinstance(agent, Mapping):
        agent = {}
    top_k = _positive_int(agent.get("top_k"))
    model = _non_empty_text(agent.get("model"))
    prompt_version = _non_empty_text(agent.get("prompt_version"))
    generated_at = (
        _non_empty_text(actual_data.get("generated_at"))
        if isinstance(actual_data, Mapping)
        else None
    )
    corpus_id = None
    if isinstance(benchmark_data, Mapping):
        corpus_id = _non_empty_text(benchmark_data.get("corpus_id"))
    if corpus_id is None and isinstance(actual_data, Mapping):
        corpus_id = _non_empty_text(actual_data.get("corpus_id"))
    if (
        isinstance(benchmark_data, Mapping)
        and isinstance(actual_data, Mapping)
        and benchmark_data.get("corpus_id")
        and actual_data.get("corpus_id")
        and benchmark_data.get("corpus_id") != actual_data.get("corpus_id")
    ):
        warnings.append("Benchmark and actual artifacts use different corpus_id values.")
        actual_state = replace(
            actual_state,
            status="partial" if actual_state.status == "ok" else actual_state.status,
            message="Corpus id differs from benchmark artifact.",
        )

    return BenchmarkSnapshot(
        cases=tuple(cases),
        summary=summary,
        failure_analysis=failure_analysis,
        corpus_id=corpus_id,
        model=model,
        top_k=top_k,
        prompt_version=prompt_version,
        generated_at=generated_at,
        benchmark_status=benchmark_state,
        actual_status=actual_state,
        warnings=tuple(warnings),
    )


def load_snapshot_from_paths(
    benchmark_path: str | Path,
    actual_path: str | Path,
) -> BenchmarkSnapshot:
    """Load both artifacts once and return a presentation-ready snapshot."""

    return _snapshot_from_reads(
        read_json_artifact(benchmark_path, "Benchmark results"),
        read_json_artifact(actual_path, "Actual answers"),
    )


def load_snapshot(project_or_artifacts_dir: str | Path) -> BenchmarkSnapshot:
    """Load ``artifacts/benchmark_results.json`` and ``actual_answers.json``.

    Passing either the project root or the artifacts directory is supported to
    make the helper convenient in tests and local demos.
    """

    root = _resolved(project_or_artifacts_dir)
    artifacts_dir = root if root.name.lower() == "artifacts" else root / "artifacts"
    return load_snapshot_from_paths(
        artifacts_dir / "benchmark_results.json",
        artifacts_dir / "actual_answers.json",
    )


def normalize_question(question: str) -> str:
    """Normalise only whitespace/case for exact benchmark-question matching."""

    return " ".join(question.strip().lower().split())


def find_case_by_question(
    cases: Sequence[BenchmarkCase],
    question: str,
) -> BenchmarkCase | None:
    """Find a saved case without fuzzy matching or use of gold data."""

    key = normalize_question(question)
    if not key:
        return None
    return next(
        (case for case in cases if normalize_question(case.question) == key),
        None,
    )


def find_case_by_id(cases: Sequence[BenchmarkCase], case_id: str) -> BenchmarkCase | None:
    """Return a benchmark case by its stable artifact id."""

    return next((case for case in cases if case.id == case_id), None)
