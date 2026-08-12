"""Streamlit presentation app for the Northstar RAG evaluation snapshot."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st


APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from data_loader import (  # noqa: E402  (app directory is added above)
    BenchmarkCase,
    BenchmarkSnapshot,
    find_case_by_id,
    load_snapshot,
)
from live_rag import (  # noqa: E402
    LiveRagResult,
    answer_with_fallback,
    is_openrouter_configured,
    load_project_environment,
)


CORPUS_DIR = PROJECT_ROOT / "data" / "student_services"
NAV_ITEMS = (
    "Tổng quan",
    "Khám phá benchmark",
    "RAG trực tiếp",
    "Failure analysis & Demo 3 phút",
)


@st.cache_data(show_spinner=False)
def cached_snapshot(project_root: str) -> BenchmarkSnapshot:
    """Cache only the read-only artifact snapshot."""

    return load_snapshot(project_root)


@st.cache_resource(show_spinner=False)
def cached_domain_assistant(corpus_dir: str, top_k: int):
    """Cache the corpus/retriever while preserving DomainAssistant's interface."""

    from domain_assistant import DomainAssistant

    return DomainAssistant.from_corpus(Path(corpus_dir), top_k=top_k)


def _cached_assistant_factory(corpus_dir: Path, _generator, top_k: int):
    # The live UI uses the configured OpenRouter generator created by
    # DomainAssistant.  ``_generator`` remains in the seam for fake-generator
    # tests and for the reusable live_rag adapter.
    return cached_domain_assistant(str(corpus_dir), top_k)


def _inject_styles() -> None:
    st.markdown(
        """
        <style>
        :root { --ns-navy: #10233f; --ns-blue: #2b6cb0; --ns-ink: #172033; }
        .block-container { max-width: 1500px; padding-top: 1.25rem; padding-bottom: 3rem; }
        .hero { background: linear-gradient(120deg, #10233f 0%, #1e4e79 65%, #2b6cb0 100%);
                color: #ffffff; border-radius: 18px; padding: 1.35rem 1.6rem;
                margin-bottom: 1rem; box-shadow: 0 10px 28px rgba(16,35,63,.16); }
        .hero h1 { margin: 0 0 .25rem 0; font-size: 2rem; letter-spacing: -.02em; }
        .hero p { margin: 0; opacity: .86; font-size: 1rem; }
        .eyebrow { text-transform: uppercase; letter-spacing: .12em; font-size: .72rem;
                   font-weight: 700; opacity: .72; margin-bottom: .35rem; }
        .section-kicker { color: #53708e; font-size: .76rem; letter-spacing: .1em;
                          text-transform: uppercase; font-weight: 700; margin-top: .5rem; }
        .status-chip { display: inline-block; border-radius: 999px; padding: .22rem .65rem;
                       font-size: .76rem; font-weight: 700; margin-right: .35rem; }
        .status-ok { color: #116149; background: #dff7ed; }
        .status-partial { color: #835d00; background: #fff3cd; }
        .status-bad { color: #9b2c2c; background: #ffe2e2; }
        .small-note { color: #64748b; font-size: .82rem; }
        .case-id { color: #2b6cb0; font-weight: 800; letter-spacing: .04em; }
        .demo-card { border: 1px solid #dbe6f0; border-radius: 14px; padding: 1rem;
                     min-height: 190px; background: #fbfdff; }
        .demo-time { color: #2b6cb0; font-weight: 800; font-size: .78rem; letter-spacing: .08em; }
        div[data-testid="stMetric"] { background: #fbfdff; border: 1px solid #e1eaf3;
                                        padding: .7rem .8rem; border-radius: 12px; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _format_percent(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _format_score(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def _status_html(label: str, status: str) -> str:
    css = "status-ok" if status == "ok" else "status-partial" if status == "partial" else "status-bad"
    return f'<span class="status-chip {css}">{label}: {status}</span>'


def _render_artifact_status(snapshot: BenchmarkSnapshot) -> None:
    st.markdown(
        _status_html("benchmark_results", snapshot.benchmark_status.status)
        + _status_html("actual_answers", snapshot.actual_status.status),
        unsafe_allow_html=True,
    )
    if snapshot.warnings:
        with st.expander("Chi tiết trạng thái dữ liệu", expanded=False):
            for warning in snapshot.warnings:
                st.write(f"• {warning}")


def _render_source_cards(contexts, *, heading: str = "Retriever evidence") -> None:
    st.markdown(f"**{heading}**")
    if not contexts:
        st.info("Snapshot không có retriever trace cho case này.")
        return
    for context in contexts:
        score = "—" if context.score is None else f"{context.score:.3f}"
        label = f"#{context.rank} · {context.source_doc} · BM25 {score}"
        with st.expander(label, expanded=False):
            if context.chunk_id:
                st.caption(f"chunk_id: {context.chunk_id} · rank: {context.rank}")
            st.write(context.text)


def _render_case_detail(case: BenchmarkCase, *, compact: bool = False) -> None:
    st.markdown(f"**Question / câu hỏi**  \n{case.question}")
    st.markdown(f"**Answer / câu trả lời**  \n{case.actual_answer}")

    score_values = (
        ("Faithfulness", case.faithfulness),
        ("Relevance", case.relevance),
        ("Completeness", case.completeness),
        ("Context Recall", case.context_recall),
        ("Context Precision", case.context_precision),
    )
    score_columns = st.columns(5)
    for column, (label, value) in zip(score_columns, score_values):
        column.metric(label, _format_score(value))

    if not case.passed:
        failure_type = case.failure_type or "unknown"
        st.warning(f"Failure type: `{failure_type}` · {case.failure_reason}")
    else:
        st.success("Pass theo snapshot benchmark.")

    if case.actual_error:
        st.error("Artifact ghi nhận lỗi ở lần sinh answer này; trace có thể không đầy đủ.")
    _render_source_cards(case.retrieved_contexts)


def render_overview(snapshot: BenchmarkSnapshot) -> None:
    st.markdown(
        '<div class="hero"><div class="eyebrow">Northstar University Student Services</div>'
        '<h1>RAG Evaluation Dashboard</h1>'
        '<p>Snapshot benchmark thật · read-only · tập trung vào chất lượng answer và retrieval</p></div>',
        unsafe_allow_html=True,
    )
    st.subheader("Tổng quan")
    summary = snapshot.summary
    total = int(summary.get("total") or 0)
    passed = int(summary.get("passed") or 0)
    pass_rate = summary.get("pass_rate")
    if not isinstance(pass_rate, (int, float)):
        pass_rate = passed / total if total else None

    overview_columns = st.columns([1.25, 1, 1, 1, 1, 1])
    overview_columns[0].metric("Pass rate", _format_percent(pass_rate), f"{passed}/{total} passed")
    overview_columns[1].metric("Total QA", str(total))
    overview_columns[2].metric("Passed", str(passed))
    overview_columns[3].metric("Failures", str(max(total - passed, 0)))
    overview_columns[4].metric("Corpus", snapshot.corpus_id or "—")
    overview_columns[5].metric("Top-k", str(snapshot.top_k or "—"))

    st.markdown('<div class="section-kicker">Năm metric chính</div>', unsafe_allow_html=True)
    st.markdown("#### Answer quality")
    answer_columns = st.columns(3)
    answer_metric_labels = (
        ("Faithfulness", "avg_faithfulness"),
        ("Relevance", "avg_relevance"),
        ("Completeness", "avg_completeness"),
    )
    for column, (label, key) in zip(answer_columns, answer_metric_labels):
        value = summary.get(key)
        column.metric(label, _format_percent(value if isinstance(value, (int, float)) else None))
    st.caption("Answer-quality: answer có grounded, đúng intent và đủ nội dung đến đâu.")

    st.markdown("#### Retrieval quality")
    retrieval_columns = st.columns(2)
    retrieval_metric_labels = (
        ("Context Recall", "avg_context_recall"),
        ("Context Precision", "avg_context_precision"),
    )
    for column, (label, key) in zip(retrieval_columns, retrieval_metric_labels):
        value = summary.get(key)
        column.metric(label, _format_percent(value if isinstance(value, (int, float)) else None))
    st.caption("Retrieval-quality: evidence cần thiết có được lấy lên, và có đứng sớm trong ranking không.")

    info_columns = st.columns(3)
    info_columns[0].markdown(f"**Model**  \n{snapshot.model or '—'}")
    info_columns[1].markdown(f"**Prompt version**  \n{snapshot.prompt_version or '—'}")
    info_columns[2].markdown(f"**Snapshot**  \n{snapshot.generated_at or '—'}")
    _render_artifact_status(snapshot)
    st.caption("Nút làm mới chỉ đọc lại hai artifact JSON; không chạy lại benchmark hoặc gọi API.")


def _filtered_cases(
    cases: tuple[BenchmarkCase, ...],
    difficulties: list[str],
    pass_filter: str,
    failure_types: list[str],
) -> list[BenchmarkCase]:
    difficulty_set = set(difficulties)
    failure_set = set(failure_types)
    filtered: list[BenchmarkCase] = []
    for case in cases:
        if difficulty_set and case.difficulty not in difficulty_set:
            continue
        if pass_filter == "Pass" and not case.passed:
            continue
        if pass_filter == "Fail" and case.passed:
            continue
        if failure_set and (case.failure_type or "none") not in failure_set:
            continue
        filtered.append(case)
    return filtered


def render_benchmark(snapshot: BenchmarkSnapshot) -> None:
    st.title("Khám phá benchmark")
    st.caption("Các câu hỏi, answers và retriever evidence dưới đây lấy từ snapshot thật; không sinh lại.")

    difficulty_values = sorted({case.difficulty for case in snapshot.cases})
    difficulty_labels = {value: value.title() for value in difficulty_values}
    selected_difficulties = st.multiselect(
        "Lọc theo độ khó",
        options=difficulty_values,
        format_func=lambda value: difficulty_labels.get(value, value),
        placeholder="Tất cả độ khó",
    )
    filter_columns = st.columns(2)
    with filter_columns[0]:
        pass_filter = st.selectbox("Trạng thái", ["Tất cả", "Pass", "Fail"])
    failure_values = sorted({case.failure_type for case in snapshot.cases if case.failure_type})
    with filter_columns[1]:
        selected_failures = st.multiselect(
            "Failure type",
            options=failure_values,
            placeholder="Tất cả failure type",
        )

    visible = _filtered_cases(snapshot.cases, selected_difficulties, pass_filter, selected_failures)
    st.caption(f"Hiển thị {len(visible)}/{len(snapshot.cases)} QA")

    focus_id = st.session_state.pop("focus_case_id", None)
    if focus_id:
        focused = find_case_by_id(snapshot.cases, focus_id)
        if focused is not None:
            st.info(f"Demo focus: {focused.id}")
            _render_case_detail(focused)
            st.divider()

    if not visible:
        st.info("Không có case phù hợp với bộ lọc hiện tại.")
        return
    for case in visible:
        status = "PASS" if case.passed else f"FAIL · {case.failure_type or 'unknown'}"
        with st.expander(f"{case.id} · {case.difficulty.title()} · {status} · {case.question}"):
            _render_case_detail(case)


def _live_status_message(result: LiveRagResult) -> None:
    if result.status == "live":
        st.success(f"🟢 {result.source_label} · {result.message}")
    elif result.status == "fallback":
        st.warning(f"🟡 {result.source_label} · {result.message}")
    elif result.status == "unavailable":
        st.info(result.message)
    else:
        st.error(result.message)
    if result.error:
        with st.expander("Chi tiết lỗi kỹ thuật", expanded=False):
            st.code(result.error)


def render_live(snapshot: BenchmarkSnapshot) -> None:
    st.title("RAG trực tiếp")
    st.caption("Live path chỉ nhận question và corpus. Khi live không khả dụng, chỉ benchmark question mới có fallback.")
    load_project_environment(PROJECT_ROOT)
    configured = is_openrouter_configured()
    if configured:
        st.success("OpenRouter đã được cấu hình; lần gửi tiếp theo sẽ ưu tiên live RAG.")
    else:
        st.info("Chưa có cấu hình OpenRouter hợp lệ. Các benchmark question vẫn có thể xem bằng artifact fallback.")

    sample_cases = [
        case for case_id in ("E01", "H03", "H02")
        if (case := find_case_by_id(snapshot.cases, case_id)) is not None
    ]
    sample_options = ["Tự nhập"] + [f"{case.id} · {case.question}" for case in sample_cases]
    if "live_sample" not in st.session_state:
        st.session_state.live_sample = sample_options[1] if len(sample_options) > 1 else sample_options[0]
    selected_sample = st.selectbox("Câu hỏi mẫu", sample_options, key="live_sample")
    previous_sample = st.session_state.get("live_previous_sample")
    if selected_sample != previous_sample:
        st.session_state.live_previous_sample = selected_sample
        if selected_sample != "Tự nhập":
            sample_id = selected_sample.split(" · ", 1)[0]
            sample_case = find_case_by_id(snapshot.cases, sample_id)
            if sample_case is not None:
                st.session_state.live_question = sample_case.question
    if "live_question" not in st.session_state:
        st.session_state.live_question = sample_cases[0].question if sample_cases else ""
    question = st.text_area("Question (English)", key="live_question", height=120)

    if st.button("Gửi câu hỏi", type="primary", use_container_width=False):
        with st.spinner("Đang hỏi DomainAssistant…"):
            result = answer_with_fallback(
                question,
                snapshot,
                CORPUS_DIR,
                top_k=snapshot.top_k,
                live_enabled=configured,
                assistant_factory=_cached_assistant_factory,
            )
        st.session_state.live_result = result

    result = st.session_state.get("live_result")
    if isinstance(result, LiveRagResult):
        st.divider()
        _live_status_message(result)
        if result.answer:
            st.markdown("### Answer")
            st.write(result.answer)
            _render_source_cards(result.retrieved_contexts)


def _demo_button(case_id: str) -> None:
    st.session_state["nav"] = "Khám phá benchmark"
    st.session_state["focus_case_id"] = case_id


def render_failure_analysis(snapshot: BenchmarkSnapshot) -> None:
    st.title("Failure analysis & Demo 3 phút")
    st.caption("Failure types và improvement proposals được đọc từ failure_analysis trong benchmark artifact.")
    analysis = snapshot.failure_analysis
    counts = analysis.get("counts")
    if not isinstance(counts, dict):
        counts = snapshot.summary.get("failure_types", {})
    counts = {str(key): int(value) for key, value in counts.items() if isinstance(value, (int, float))}

    chart_columns = st.columns([1.15, 1])
    with chart_columns[0]:
        st.markdown("#### Failure types")
        if counts:
            st.bar_chart(counts, height=280)
        else:
            st.info("Chưa có failure data trong snapshot.")
    with chart_columns[1]:
        st.markdown("#### Improvement proposals")
        suggestions = analysis.get("suggestions")
        if isinstance(suggestions, list) and suggestions:
            for suggestion in suggestions:
                st.markdown(f"- {suggestion}")
        else:
            st.info("Artifact chưa ghi suggestions.")

    improvement_log = analysis.get("improvement_log")
    if isinstance(improvement_log, str) and improvement_log.strip():
        with st.expander("Improvement log trong artifact", expanded=False):
            st.markdown(improvement_log)

    st.markdown('<div class="section-kicker">Demo script · 3 phút</div>', unsafe_allow_html=True)
    st.markdown("#### Một luồng trình chiếu: năng lực → multi-policy → failure minh bạch")
    demo_columns = st.columns(3)
    demo_specs = (
        ("00:00", "E01", "Answer chuẩn", "Một câu hỏi factual, pass và evidence đứng đầu."),
        ("01:00", "H03", "Multi-policy", "Kết hợp renewal failure, conduct sanction và medical leave."),
        ("02:00", "H02", "Failure minh bạch", "Thấy rõ hallucination signal, context recall thấp và giới hạn answer."),
    )
    for column, (minute, case_id, title, description) in zip(demo_columns, demo_specs):
        case = find_case_by_id(snapshot.cases, case_id)
        with column:
            st.markdown(
                f'<div class="demo-card"><div class="demo-time">{minute} · {case_id}</div>'
                f'<h4>{title}</h4><p>{description}</p></div>',
                unsafe_allow_html=True,
            )
            if case is not None:
                st.caption(f"{case.question[:120]}{'…' if len(case.question) > 120 else ''}")
                st.button(
                    f"Mở {case_id}",
                    key=f"demo_{case_id}",
                    use_container_width=True,
                    on_click=_demo_button,
                    args=(case_id,),
                )
            else:
                st.warning(f"Không tìm thấy {case_id} trong snapshot.")


def main() -> None:
    st.set_page_config(
        page_title="Northstar · RAG Evaluation",
        page_icon="🎓",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _inject_styles()
    load_project_environment(PROJECT_ROOT)

    with st.sidebar:
        st.markdown("## Northstar")
        st.caption("Student Services · AI Evaluation")
        if "nav" not in st.session_state:
            st.session_state.nav = NAV_ITEMS[0]
        st.radio("Khu vực", NAV_ITEMS, key="nav", label_visibility="collapsed")
        st.divider()
        if st.button("Làm mới dữ liệu", use_container_width=True):
            # The only cache invalidated here is the artifact snapshot.  This
            # never invokes the benchmark runner or the live assistant.
            cached_snapshot.clear()
            st.rerun()
        st.caption("Read-only snapshot · không chạy lại 20 QA")

    snapshot = cached_snapshot(str(PROJECT_ROOT))
    if snapshot.benchmark_status.status != "ok":
        st.warning("Benchmark artifact chưa hoàn toàn sẵn sàng; dashboard sẽ hiển thị phần dữ liệu đọc được.")

    section = st.session_state.nav
    if section == "Tổng quan":
        render_overview(snapshot)
    elif section == "Khám phá benchmark":
        render_benchmark(snapshot)
    elif section == "RAG trực tiếp":
        render_live(snapshot)
    else:
        render_failure_analysis(snapshot)


if __name__ == "__main__":
    main()
