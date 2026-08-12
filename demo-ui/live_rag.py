"""Live RAG adapter and transparent artifact fallback for the demo UI."""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from data_loader import BenchmarkCase, BenchmarkSnapshot, RetrievedContext, find_case_by_question


AssistantFactory = Callable[[Path, Any, int], Any]


@dataclass(frozen=True)
class LiveRagResult:
    """Safe result object for one explicit live/fallback request."""

    status: str
    question: str
    answer: str | None
    retrieved_contexts: tuple[RetrievedContext, ...]
    source_label: str
    message: str
    fallback_case_id: str | None = None
    error: str | None = None

    @property
    def is_live(self) -> bool:
        return self.status == "live"


def load_project_environment(project_root: str | Path) -> None:
    """Load the local ``.env`` without printing or returning its contents."""

    try:
        from dotenv import load_dotenv

        env_path = Path(project_root).expanduser().resolve() / ".env"
        if env_path.is_file():
            load_dotenv(env_path, override=False)
    except ImportError:
        # The dependency is declared by the project, but keeping this helper
        # optional makes data-only tests usable in a minimal Python runtime.
        return


def _usable_setting(value: Any, *, placeholder_prefixes: Sequence[str] = ()) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    cleaned = value.strip()
    return not any(cleaned.lower().startswith(prefix.lower()) for prefix in placeholder_prefixes)


def is_openrouter_configured(env: Mapping[str, str] | None = None) -> bool:
    """Return whether enough OpenRouter configuration exists for a live call.

    The API key itself is never returned by this function or included in a UI
    status message.  A copied ``.env.example`` placeholder is treated as
    missing configuration.
    """

    values: Mapping[str, str] = env if env is not None else os.environ
    key = values.get("OPENROUTER_API_KEY", "")
    model = values.get("OPENROUTER_MODEL", "")
    base_url = values.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    return (
        _usable_setting(key, placeholder_prefixes=("your_", "replace_", "<"))
        and _usable_setting(model)
        and _usable_setting(base_url)
    )


def _retriever_score(value: Any) -> float | None:
    """Validate a BM25 score; BM25 is not normalized to [0, 1]."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else None


def _chunk_value(chunk: Any, field: str, default: Any = None) -> Any:
    if isinstance(chunk, Mapping):
        return chunk.get(field, default)
    return getattr(chunk, field, default)


def _contexts_from_chunks(chunks: Any) -> tuple[RetrievedContext, ...]:
    if chunks is None:
        return ()
    if not isinstance(chunks, (list, tuple)):
        chunks = tuple(chunks)
    contexts: list[RetrievedContext] = []
    for rank, chunk in enumerate(chunks, start=1):
        text = _chunk_value(chunk, "text", "")
        if not isinstance(text, str) or not text.strip():
            continue
        source_doc = _chunk_value(chunk, "source_doc", "Unknown source")
        chunk_id = _chunk_value(chunk, "chunk_id")
        contexts.append(
            RetrievedContext(
                rank=rank,
                source_doc=source_doc.strip() if isinstance(source_doc, str) else "Unknown source",
                chunk_id=chunk_id.strip() if isinstance(chunk_id, str) and chunk_id.strip() else None,
                text=text.strip(),
                score=_retriever_score(_chunk_value(chunk, "score")),
            )
        )
    return tuple(contexts)


def run_live_rag(
    question: str,
    corpus_dir: str | Path,
    *,
    top_k: int = 5,
    generator: Any = None,
    assistant_factory: AssistantFactory | None = None,
) -> LiveRagResult:
    """Call ``DomainAssistant.answer_with_trace()`` for one question.

    ``assistant_factory`` is a test seam and a Streamlit cache seam.  It must
    receive ``(corpus_dir, generator, top_k)`` and return an object exposing
    the same ``answer_with_trace`` method as ``DomainAssistant``.
    """

    cleaned_question = question.strip() if isinstance(question, str) else ""
    if not cleaned_question:
        raise ValueError("Question must be a non-empty string")
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
        raise ValueError("top_k must be a positive integer")

    resolved_corpus = Path(corpus_dir).expanduser().resolve()
    if assistant_factory is not None:
        assistant = assistant_factory(resolved_corpus, generator, top_k)
    else:
        # Import lazily so the dashboard and artifact fallback remain usable
        # when a minimal environment has no OpenAI package configured.
        from domain_assistant import DomainAssistant

        assistant = DomainAssistant.from_corpus(
            resolved_corpus,
            generator=generator,
            top_k=top_k,
        )

    response = assistant.answer_with_trace(cleaned_question)
    answer = getattr(response, "actual_answer", None)
    if not isinstance(answer, str) or not answer.strip():
        raise RuntimeError("DomainAssistant returned an empty answer")
    contexts = _contexts_from_chunks(getattr(response, "retrieved_chunks", ()))
    return LiveRagResult(
        status="live",
        question=cleaned_question,
        answer=answer.strip(),
        retrieved_contexts=contexts,
        source_label="Live RAG · DomainAssistant",
        message="Câu trả lời được sinh trực tiếp qua DomainAssistant.answer_with_trace().",
    )


def _redact_error(error: BaseException) -> str:
    """Keep an error useful while ensuring an accidental key is not shown."""

    message = str(error).strip() or "No additional details."
    for secret_name in ("OPENROUTER_API_KEY",):
        secret = os.getenv(secret_name, "").strip()
        if secret:
            message = message.replace(secret, "[redacted]")
    # Also cover common OpenRouter/OpenAI key-shaped fragments if a client
    # includes one in an exception string.
    message = re.sub(r"(?:sk-or-v1-|sk-)[A-Za-z0-9_-]{12,}", "[redacted]", message)
    return message[:500]


def answer_with_fallback(
    question: str,
    snapshot: BenchmarkSnapshot,
    corpus_dir: str | Path,
    *,
    top_k: int | None = None,
    live_enabled: bool | None = None,
    generator: Any = None,
    assistant_factory: AssistantFactory | None = None,
) -> LiveRagResult:
    """Prefer live RAG, then use an exact benchmark case if live is unavailable.

    Fallback is intentionally exact-question only.  An arbitrary question with
    no API configuration receives a friendly error instead of a misleading
    answer copied from a nearby benchmark case.
    """

    cleaned_question = question.strip() if isinstance(question, str) else ""
    if not cleaned_question:
        return LiveRagResult(
            status="error",
            question="",
            answer=None,
            retrieved_contexts=(),
            source_label="",
            message="Hãy nhập một câu hỏi trước khi gửi.",
            error="Question must be a non-empty string",
        )

    effective_top_k = top_k or snapshot.top_k or 5
    enabled = is_openrouter_configured() if live_enabled is None else live_enabled
    matched_case = find_case_by_question(snapshot.cases, cleaned_question)

    if enabled:
        try:
            return run_live_rag(
                cleaned_question,
                corpus_dir,
                top_k=effective_top_k,
                generator=generator,
                assistant_factory=assistant_factory,
            )
        except Exception as exc:  # live/network/client errors become UI state
            safe_error = _redact_error(exc)
            if matched_case is not None:
                return LiveRagResult(
                    status="fallback",
                    question=cleaned_question,
                    answer=matched_case.actual_answer,
                    retrieved_contexts=matched_case.retrieved_contexts,
                    source_label=f"Artifact fallback · {matched_case.id}",
                    message=(
                        "Live RAG không khả dụng; đang hiển thị câu trả lời đã lưu "
                        f"của benchmark case {matched_case.id}."
                    ),
                    fallback_case_id=matched_case.id,
                    error=safe_error,
                )
            return LiveRagResult(
                status="error",
                question=cleaned_question,
                answer=None,
                retrieved_contexts=(),
                source_label="Live RAG",
                message="Live RAG không thể hoàn tất câu hỏi này.",
                error=safe_error,
            )

    if matched_case is not None:
        return LiveRagResult(
            status="fallback",
            question=cleaned_question,
            answer=matched_case.actual_answer,
            retrieved_contexts=matched_case.retrieved_contexts,
            source_label=f"Artifact fallback · {matched_case.id}",
            message=(
                "Chưa có cấu hình OpenRouter; đang hiển thị câu trả lời và retriever "
                f"trace đã lưu của benchmark case {matched_case.id}."
            ),
            fallback_case_id=matched_case.id,
        )

    return LiveRagResult(
        status="unavailable",
        question=cleaned_question,
        answer=None,
        retrieved_contexts=(),
        source_label="",
        message=(
            "Câu hỏi này chưa có trong snapshot benchmark và OpenRouter chưa được "
            "cấu hình. Hãy thêm OPENROUTER_API_KEY vào .env để hỏi live."
        ),
    )

