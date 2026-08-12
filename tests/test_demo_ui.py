"""Tests for the read-only demo data layer and live/fallback seam."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace


DAY_DIR = Path(__file__).parent.parent
UI_DIR = DAY_DIR / "demo-ui"
if str(UI_DIR) not in sys.path:
    sys.path.insert(0, str(UI_DIR))


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


data_loader = _load_module(UI_DIR / "data_loader.py", "northstar_demo_data_loader")
live_rag = _load_module(UI_DIR / "live_rag.py", "northstar_demo_live_rag")


def _artifacts() -> tuple[dict, dict]:
    benchmark = {
        "corpus_id": "northstar-test",
        "summary": {},
        "results": [
            {
                "id": "E01",
                "difficulty": "easy",
                "question": "When is the deadline?",
                "actual_answer": "The deadline is Friday.",
                "faithfulness": 1.0,
                "relevance": 0.8,
                "completeness": 0.9,
                "context_recall": 1.0,
                "context_precision": 0.75,
                "overall": 0.9,
                "passed": True,
                "failure_type": None,
            }
        ],
        "failure_analysis": {
            "counts": {},
            "suggestions": ["Keep the answer grounded."],
            "improvement_log": "| Status |\n| Open |",
        },
    }
    actual = {
        "corpus_id": "northstar-test",
        "generated_at": "2026-08-12T00:00:00+00:00",
        "agent": {"model": "test/model", "top_k": 5, "prompt_version": "test"},
        "answers": [
            {
                "id": "E01",
                "question": "When is the deadline?",
                "actual_answer": "The deadline is Friday.",
                "retrieved_contexts": [
                    {
                        "source_doc": "01_calendar.md",
                        "chunk_id": "NU-01-P01",
                        "text": "The deadline is Friday.",
                        "score": 12.5,
                    }
                ],
                "error": None,
            }
        ],
    }
    return benchmark, actual


def _write_artifacts(directory: Path, benchmark: dict, actual: dict) -> tuple[Path, Path]:
    benchmark_path = directory / "benchmark_results.json"
    actual_path = directory / "actual_answers.json"
    benchmark_path.write_text(json.dumps(benchmark), encoding="utf-8")
    actual_path.write_text(json.dumps(actual), encoding="utf-8")
    return benchmark_path, actual_path


def test_join_preserves_answer_scores_and_bm25_trace(tmp_path: Path):
    benchmark, actual = _artifacts()
    cases = data_loader.join_benchmark_artifacts(benchmark, actual)

    assert len(cases) == 1
    assert cases[0].id == "E01"
    assert cases[0].actual_answer == "The deadline is Friday."
    assert cases[0].retrieved_contexts[0].score == 12.5
    assert cases[0].retrieved_contexts[0].source_doc == "01_calendar.md"


def test_snapshot_reports_missing_and_corrupt_artifacts(tmp_path: Path):
    missing = data_loader.load_snapshot_from_paths(
        tmp_path / "benchmark_results.json",
        tmp_path / "actual_answers.json",
    )
    assert missing.benchmark_status.status == "missing"
    assert missing.actual_status.status == "missing"
    assert missing.cases == ()

    benchmark_path = tmp_path / "benchmark_results.json"
    actual_path = tmp_path / "actual_answers.json"
    benchmark_path.write_text("{not-json", encoding="utf-8")
    actual_path.write_text(json.dumps(_artifacts()[1]), encoding="utf-8")
    corrupt = data_loader.load_snapshot_from_paths(benchmark_path, actual_path)
    assert corrupt.benchmark_status.status == "invalid"
    assert corrupt.actual_status.status == "ok"
    assert corrupt.cases == ()
    assert corrupt.warnings


def test_benchmark_question_uses_artifact_fallback_without_live_call(tmp_path: Path):
    benchmark, actual = _artifacts()
    benchmark_path, actual_path = _write_artifacts(tmp_path, benchmark, actual)
    snapshot = data_loader.load_snapshot_from_paths(benchmark_path, actual_path)

    def should_not_run(*_args, **_kwargs):
        raise AssertionError("live assistant should not run when disabled")

    result = live_rag.answer_with_fallback(
        "When is the deadline?",
        snapshot,
        tmp_path / "corpus",
        live_enabled=False,
        assistant_factory=should_not_run,
    )
    assert result.status == "fallback"
    assert result.fallback_case_id == "E01"
    assert result.answer == "The deadline is Friday."
    assert result.retrieved_contexts[0].score == 12.5


def test_live_path_uses_answer_with_trace_and_fake_generator(tmp_path: Path):
    calls: list[tuple[Path, object, int]] = []

    class FakeAssistant:
        def answer_with_trace(self, question: str):
            return SimpleNamespace(
                actual_answer=f"Live answer for: {question}",
                retrieved_chunks=(
                    SimpleNamespace(
                        source_doc="02_registration.md",
                        chunk_id="NU-02-P01",
                        text="Live evidence.",
                        score=8.75,
                    ),
                ),
            )

    fake_generator = object()

    def factory(corpus_dir: Path, generator: object, top_k: int):
        calls.append((corpus_dir, generator, top_k))
        return FakeAssistant()

    result = live_rag.run_live_rag(
        "What is required?",
        tmp_path / "corpus",
        top_k=3,
        generator=fake_generator,
        assistant_factory=factory,
    )
    assert result.status == "live"
    assert result.answer == "Live answer for: What is required?"
    assert result.retrieved_contexts[0].score == 8.75
    assert calls == [((tmp_path / "corpus").resolve(), fake_generator, 3)]


def test_live_error_falls_back_to_exact_case(tmp_path: Path):
    benchmark, actual = _artifacts()
    benchmark_path, actual_path = _write_artifacts(tmp_path, benchmark, actual)
    snapshot = data_loader.load_snapshot_from_paths(benchmark_path, actual_path)

    def failing_factory(*_args, **_kwargs):
        raise RuntimeError("network unavailable")

    result = live_rag.answer_with_fallback(
        "When is the deadline?",
        snapshot,
        tmp_path / "corpus",
        live_enabled=True,
        assistant_factory=failing_factory,
    )
    assert result.status == "fallback"
    assert result.fallback_case_id == "E01"
    assert "network unavailable" in (result.error or "")


def test_openrouter_placeholder_is_not_treated_as_configured():
    assert not live_rag.is_openrouter_configured(
        {
            "OPENROUTER_API_KEY": "your_openrouter_api_key_here",
            "OPENROUTER_MODEL": "openai/gpt-4o-mini",
            "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
        }
    )
    assert live_rag.is_openrouter_configured(
        {
            "OPENROUTER_API_KEY": "test-key",
            "OPENROUTER_MODEL": "openai/gpt-4o-mini",
            "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
        }
    )
