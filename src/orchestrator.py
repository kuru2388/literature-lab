"""Run the multi-agent pipeline and persist per-step progress."""

from __future__ import annotations

import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from src.agents.downloader import download_papers
from src.agents.extractor import extract_papers
from src.agents.datasets import collect_datasets
from src.agents.gaps import find_gaps
from src.agents.planner import plan_research
from src.agents.ranker import rank_abstracts, rank_final
from src.agents.searcher import search_papers
from src.agents.verifier import verify_papers, verify_tags
from src.agents.writer import write_report
from src.db import create_task, get_task, task_to_dict, update_task
from src.llm import use_provider
from src.settings import active_model_id, apply_env, has_llm_key, resolve_provider
from src.tools.arxiv import RECENT_YEARS
from src.tools.sources import source_catalog
from src.usage import begin_run, commit_run

STEPS = [
    {"id": "plan", "label": "Prepare search tags"},
    {"id": "verify_tags", "label": "Verify keywords"},
    {"id": "search", "label": "Search research papers"},
    {"id": "rank_abstracts", "label": "Rank abstracts"},
    {"id": "download", "label": "Download PDFs"},
    {"id": "extract", "label": "Extract findings"},
    {"id": "gaps", "label": "Find research gaps"},
    {"id": "rank_final", "label": "Rank best papers"},
    {"id": "verify_papers", "label": "Verify fit to your idea"},
    {"id": "datasets", "label": "Collect datasets"},
    {"id": "report", "label": "Write report"},
]

_STEP_IDS = [step["id"] for step in STEPS]
_executor = ThreadPoolExecutor(max_workers=2)


def _fresh_steps() -> list[dict[str, Any]]:
    return [
        {"id": step["id"], "label": step["label"], "status": "pending", "detail": ""}
        for step in STEPS
    ]


def _set_step(
    task_id: str,
    steps: list[dict[str, Any]],
    step_id: str,
    status: str,
    detail: str = "",
    *,
    state: dict[str, Any] | None = None,
) -> None:
    for step in steps:
        if step["id"] == step_id:
            step["status"] = status
            step["detail"] = detail
            break
    update_task(task_id, steps=steps, result=state)


def start_research(idea: str, *, paper_count: int = 8, provider: str | None = None, datasets_only: bool = False) -> dict[str, Any]:
    apply_env()
    if not has_llm_key():
        raise RuntimeError("Add an OpenAI or Gemini API key in Settings, then click Save.")
    chosen = resolve_provider(provider)
    paper_count = max(3, min(30, int(paper_count)))
    task_id = str(uuid.uuid4())
    steps = _fresh_steps()
    create_task(task_id, idea=idea.strip(), steps=steps)
    update_task(task_id, result={"idea": idea.strip(), "paper_count": paper_count, "provider": chosen, "datasets_only": datasets_only})
    _executor.submit(_run_pipeline, task_id, idea.strip(), paper_count, chosen, False, datasets_only)
    task = get_task(task_id)
    return task_to_dict(task) if task else {"id": task_id, "status": "running"}


def resume_research(task_id: str, *, provider: str | None = None) -> dict[str, Any]:
    apply_env()
    if not has_llm_key():
        raise RuntimeError("Add an OpenAI or Gemini API key in Settings, then click Save.")
    task = get_task(task_id)
    if not task:
        raise RuntimeError("Chat not found.")
    payload = task_to_dict(task)
    if payload.get("status") == "running":
        return payload
    if payload.get("status") == "done" and (payload.get("result") or {}).get("report"):
        return payload
    idea = str(payload.get("idea") or "").strip()
    if not idea:
        raise RuntimeError("This chat has no research idea to resume.")
    result = payload.get("result") or {}
    paper_count = max(3, min(30, int(result.get("paper_count") or 8)))
    chosen = resolve_provider(provider or result.get("provider"))
    update_task(task_id, status="running", error="")
    _executor.submit(_run_pipeline, task_id, idea, paper_count, chosen, True)
    task = get_task(task_id)
    return task_to_dict(task) if task else {"id": task_id, "status": "running"}


def _step_by_id(steps: list[dict[str, Any]], step_id: str) -> dict[str, Any] | None:
    return next((step for step in steps if step.get("id") == step_id), None)


def _tags_from_steps(steps: list[dict[str, Any]]) -> list[str]:
    plan = _step_by_id(steps, "plan") or {}
    if plan.get("status") != "done":
        return []
    detail = str(plan.get("detail") or "")
    if not detail or detail.lower() == "no tags":
        return []
    return [part.strip() for part in detail.split(",") if part.strip()]


def _hydrate_state(steps: list[dict[str, Any]], state: dict[str, Any], idea: str) -> dict[str, Any]:
    plan = state.get("plan") if isinstance(state.get("plan"), dict) else {}
    tags = [str(tag).strip() for tag in (plan.get("tags") or state.get("tags") or []) if str(tag).strip()]
    if not tags:
        tags = _tags_from_steps(steps)
    if not tags and idea:
        tags = [idea]
    if tags:
        plan = dict(plan)
        plan.setdefault("tags", tags)
        state["plan"] = plan
        state["tags"] = tags
    return state


def _inputs_ok(step_id: str, state: dict[str, Any]) -> bool:
    plan = state.get("plan") or {}
    tags = plan.get("tags") or state.get("tags") or []
    needs = {
        "plan": True,
        "verify_tags": bool(plan or tags),
        "search": bool(tags or state.get("idea")),
        "rank_abstracts": bool(state.get("candidates")),
        "download": bool(state.get("shortlist")),
        "extract": bool(state.get("downloaded")),
        "gaps": bool(state.get("extracted")),
        "rank_final": bool(state.get("extracted")),
        "verify_papers": bool(state.get("ranked") or state.get("papers")),
        "datasets": bool(state.get("papers") or state.get("ranked")),
        "report": bool(state.get("papers") or state.get("ranked")),
    }
    return bool(needs.get(step_id, False))


def _completed_detail(step: dict[str, Any] | None) -> bool:
    if not step:
        return False
    detail = str(step.get("detail") or "").strip()
    if not detail:
        return False
    checks = {
        "plan": "AI search tags" not in detail and detail.lower() not in {"pending", "waiting"},
        "verify_tags": "approved" in detail,
        "search": "unique papers" in detail,
        "rank_abstracts": detail.lower().startswith("kept top"),
        "download": "pdf" in detail.lower(),
        "extract": "papers" in detail.lower(),
        "gaps": "gap" in detail.lower(),
        "rank_final": "ranked" in detail.lower(),
        "verify_papers": "confirmed" in detail.lower(),
        "datasets": "dataset" in detail.lower(),
        "report": "ready" in detail.lower(),
    }
    return bool(checks.get(str(step.get("id") or ""), False))


def _is_complete(step: dict[str, Any] | None) -> bool:
    if not step:
        return False
    if step.get("status") == "done":
        return True
    if step.get("status") == "error":
        return False
    return _completed_detail(step)


def _restore_completed(steps: list[dict[str, Any]]) -> None:
    """A bad retry may have flipped done steps back to pending. Restore them."""
    search = _step_by_id(steps, "search")
    if _is_complete(search) or _completed_detail(search):
        for step_id in ("plan", "verify_tags"):
            step = _step_by_id(steps, step_id)
            if step and step.get("status") != "error":
                step["status"] = "done"
    for step in steps:
        if step.get("status") in {"pending", "running"} and _completed_detail(step):
            step["status"] = "done"


def _start_index(steps: list[dict[str, Any]], state: dict[str, Any]) -> int:
    _restore_completed(steps)
    first_open = next(
        (index for index, step in enumerate(steps) if not _is_complete(step)),
        len(STEPS),
    )
    if first_open >= len(STEPS):
        return len(STEPS)

    search_i = _STEP_IDS.index("search")
    search_done = _is_complete(_step_by_id(steps, "search"))
    has_later_work = bool(
        state.get("papers")
        or state.get("ranked")
        or state.get("extracted")
        or state.get("downloaded")
        or state.get("shortlist")
        or state.get("datasets")
        or state.get("gaps")
    )

    if has_later_work:
        return first_open

    if search_done:
        if state.get("candidates"):
            return max(first_open, search_i + 1)
        return search_i

    idx = first_open
    while idx > 0 and not _inputs_ok(_STEP_IDS[idx], state):
        idx -= 1
    return idx


def _busy_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        token in text
        for token in ("503", "high demand", "unavailable", "429", "rate limit", "resource_exhausted")
    )


def _public_error(exc: Exception, *, failed_label: str, saved_label: str) -> str:
    if _busy_error(exc):
        if saved_label:
            return (
                f"The model is busy right now. {saved_label} is saved. "
                f"Click Try again to continue from {failed_label}."
            )
        return f"The model is busy right now. Click Try again to continue from {failed_label}."
    first = str(exc).split("\n")[0].strip()[:220]
    if saved_label:
        return f"{failed_label} stopped: {first} Finished work is saved. Click Try again to continue."
    return f"{failed_label} stopped: {first}"


def _run_step(step_id: str, state: dict[str, Any], idea: str, paper_count: int) -> str:
    datasets_only: bool = bool(state.get("datasets_only"))

    if step_id == "plan":
        plan = plan_research(idea, datasets_only=datasets_only)
        tags = plan.get("tags") or plan.get("queries") or []
        state["plan"] = plan
        state["tags"] = tags
        return ", ".join(tags[:6]) or "no tags"

    if step_id == "verify_tags":
        plan = state.get("plan") or {}
        tags = plan.get("tags") or state.get("tags") or []
        audit = verify_tags(idea, tags, datasets_only=datasets_only)
        tags = audit.get("tags") or tags
        plan["tags"] = tags
        plan["tag_audit"] = audit
        state["plan"] = plan
        state["tags"] = tags
        dropped = audit.get("dropped") or []
        return (
            f"{len(tags)} approved"
            + (f", {len(dropped)} dropped" if dropped else "")
            + (f" · {audit['note']}" if audit.get("note") else "")
        )

    if step_id == "search":
        plan = state.get("plan") or {}
        tags = plan.get("tags") or state.get("tags") or [idea]
        found = search_papers(
            tags or [idea],
            limit=max(100, min(150, paper_count * 10)),
            recent_years=RECENT_YEARS,
            min_results=paper_count,
        )
        state["candidates"] = found["papers"]
        state["search_window"] = found["window"]
        state["sources"] = found["counts"]
        state["candidates_count"] = len(found["papers"])
        if not found["papers"]:
            raise RuntimeError(
                "No papers found on any source for the generated tags. "
                f"Tags were: {', '.join(tags) or '(none)'}."
            )
        return (
            f"{len(found['papers'])} unique papers · {found['window']} · "
            + " · ".join(f"{item['name']} {item['kept']}" for item in found["counts"])
        )

    if step_id == "rank_abstracts":
        plan = state.get("plan") or {}
        tags = plan.get("tags") or state.get("tags") or []
        understanding = plan.get("understanding") or {}
        shortlist = rank_abstracts(
            idea,
            state.get("candidates") or [],
            keep=paper_count,
            tags=tags,
            understanding=understanding or None,
            datasets_only=datasets_only,
        )
        state["shortlist"] = shortlist
        return f"Kept top {len(shortlist)} of {len(state.get('candidates') or [])}"

    if step_id == "download":
        downloaded = download_papers(state.get("shortlist") or [])
        state["downloaded"] = downloaded
        ok = sum(1 for paper in downloaded if paper.get("pdf_path"))
        missing = len(downloaded) - ok
        return f"{ok}/{len(downloaded)} PDFs" + (f" · {missing} abstract-only" if missing else "")

    if step_id == "extract":
        extracted = extract_papers(idea, state.get("downloaded") or [])
        state["extracted"] = extracted
        strategies = sorted(
            {paper.get("read_strategy") or "" for paper in extracted if paper.get("read_strategy")}
        )
        return f"{len(extracted)} papers · " + (
            "; ".join(item for item in strategies if item)[:80] or "annotated"
        )

    if step_id == "gaps":
        questions = (state.get("plan") or {}).get("research_questions") or []
        gaps = find_gaps(idea, questions, state.get("extracted") or [])
        state["gaps"] = gaps
        return f"{len(gaps)} gaps"

    if step_id == "rank_final":
        ranked = rank_final(idea, state.get("extracted") or [], state.get("gaps") or [])
        state["ranked"] = ranked
        return f"{len(ranked)} ranked"

    if step_id == "verify_papers":
        ranked = verify_papers(idea, state.get("ranked") or state.get("papers") or [])
        state["ranked"] = ranked
        state["papers"] = ranked
        fits = sum(1 for paper in ranked if paper.get("fits_idea"))
        return f"{fits}/{len(ranked)} confirmed relevant"

    if step_id == "datasets":
        datasets = collect_datasets(idea, state.get("papers") or state.get("ranked") or [])
        state["datasets"] = datasets
        return f"{len(datasets)} datasets" if datasets else "no datasets named"

    if step_id == "report":
        report = write_report(
            idea,
            state.get("plan") or {},
            state.get("papers") or state.get("ranked") or [],
            state.get("gaps") or [],
        )
        state["report"] = report
        if "Reading notes (draft)" in report[:80]:
            return "Draft notes — model was busy"
        return "Report ready"

    raise RuntimeError(f"Unknown step: {step_id}")


def _final_result(state: dict[str, Any], idea: str, paper_count: int, provider: str) -> dict[str, Any]:
    papers = state.get("papers") or state.get("ranked") or []
    return {
        "idea": idea,
        "plan": state.get("plan") or {},
        "paper_count": paper_count,
        "provider": provider,
        "candidates_count": state.get("candidates_count") or len(state.get("candidates") or []),
        "search_window": state.get("search_window") or "",
        "sources": state.get("sources") or [],
        "papers": papers,
        "gaps": state.get("gaps") or [],
        "datasets": state.get("datasets") or [],
        "report": state.get("report") or "",
    }


def _run_pipeline(
    task_id: str,
    idea: str,
    paper_count: int = 8,
    provider: str = "openai",
    resume: bool = False,
    datasets_only: bool = False,
) -> None:
    apply_env()
    use_provider(provider)
    paper_count = max(3, min(30, int(paper_count)))
    begin_run(task_id, active_model_id(provider))
    task = get_task(task_id)
    payload = task_to_dict(task) if task else {}
    steps = payload.get("progress") or _fresh_steps()
    if not resume:
        steps = _fresh_steps()
    state = dict(payload.get("result") or {})
    state["idea"] = idea
    state["paper_count"] = paper_count
    state["provider"] = provider
    state["datasets_only"] = datasets_only or bool(state.get("datasets_only"))
    if resume:
        state = _hydrate_state(steps, state, idea)
    start_at = _start_index(steps, state) if resume else 0
    for index, step in enumerate(steps):
        if index >= start_at:
            step["status"] = "pending"
    error_text: str | None = None
    public_error = ""
    result_payload: dict[str, Any] | None = None
    try:
        update_task(task_id, status="running", steps=steps, result=state, error="")
        for index, spec in enumerate(STEPS):
            if index < start_at:
                continue
            hint = {
                "plan": "AI search tags",
                "verify_tags": "Second agent auditing the keywords",
                "search": "Searching " + ", ".join(item["name"] for item in source_catalog()),
                "rank_abstracts": f"Scoring abstracts, keeping top {paper_count}",
                "download": "Saving open-access PDFs",
                "extract": "Reading papers",
                "gaps": "Comparing literature to your idea",
                "rank_final": "Re-ranking with full notes",
                "verify_papers": "Checking each paper against your idea",
                "datasets": "Listing datasets used in these papers",
                "report": "Writing the memo",
            }.get(spec["id"], spec["label"])
            _set_step(task_id, steps, spec["id"], "running", hint, state=state)
            detail = _run_step(spec["id"], state, idea, paper_count)
            _set_step(task_id, steps, spec["id"], "done", detail, state=state)
        result_payload = _final_result(state, idea, paper_count, provider)
    except Exception as exc:
        result_payload = None
        error_text = f"{exc}\n\n{traceback.format_exc()}"
        failed = next((step for step in steps if step["status"] == "running"), None)
        saved = next((step for step in reversed(steps) if step["status"] == "done"), None)
        public_error = _public_error(
            exc,
            failed_label=(failed or {}).get("label") or "This step",
            saved_label=(saved or {}).get("label") or "",
        )
        if failed:
            failed["status"] = "error"
            failed["detail"] = public_error
        update_task(task_id, steps=steps, result=state)

    usage = commit_run()
    if result_payload is not None:
        result_payload["usage"] = usage
        update_task(
            task_id,
            status="done",
            result=result_payload,
            steps=steps,
            usage=usage,
            error="",
        )
    else:
        update_task(
            task_id,
            status="error",
            steps=steps,
            result=state,
            error=public_error or error_text,
            usage=usage,
        )


def analyze_user_paper(
    task_id: str,
    upload_id: str,
    *,
    provider: str | None = None,
) -> dict[str, Any]:
    from src.library import format_upload_as_paper, get_upload
    from src.agents.extractor import extract_paper

    apply_env()
    if not has_llm_key():
        raise RuntimeError("Add an OpenAI or Gemini API key in Settings, then click Save.")

    task = get_task(task_id)
    if not task:
        raise RuntimeError("Chat thread not found.")

    payload = task_to_dict(task)
    result = dict(payload.get("result") or {})
    idea = str(payload.get("idea") or result.get("idea") or "").strip()
    if not idea:
        idea = "Analyze uploaded research paper and extract key methodology, datasets, and findings."

    upload = get_upload(upload_id)
    if not upload:
        raise RuntimeError("Uploaded paper PDF not found.")

    chosen = resolve_provider(provider or result.get("provider"))
    use_provider(chosen)
    begin_run(task_id, active_model_id(chosen))

    try:
        papers = list(result.get("papers") or [])
        existing_key = f"upload:{upload_id}"
        target_index = None
        for i, p in enumerate(papers):
            if p.get("paper_key") == existing_key or (p.get("kind") == "you" and str(p.get("upload_id") or "") == str(upload_id)):
                target_index = i
                break

        if target_index is not None:
            raw_paper = dict(papers[target_index])
        else:
            rank = len(papers) + 1
            raw_paper = format_upload_as_paper(upload, rank=rank)
            papers.append(raw_paper)
            target_index = len(papers) - 1

        target_rank = target_index + 1
        raw_paper["rank"] = target_rank

        # 1. Extract detailed fields (problem, method, features, metrics, key_findings, datasets, limitations)
        extracted_paper = extract_paper(idea, raw_paper)

        # 2. Verify paper & generate why_read, why_it_matters, verdict, score
        verified_list = verify_papers(idea, [extracted_paper])
        final_paper = dict(verified_list[0]) if verified_list else extracted_paper
        final_paper["rank"] = target_rank
        final_paper["kind"] = "you"
        final_paper["source"] = "You"
        final_paper["source_tag"] = "You"
        final_paper["is_user_upload"] = True
        final_paper["upload_id"] = upload_id
        final_paper["paper_key"] = f"upload:{upload_id}"
        final_paper["pdf_path"] = upload.get("pdf_path") or final_paper.get("pdf_path") or ""
        final_paper["has_pdf"] = True

        papers[target_index] = final_paper
        for idx, p in enumerate(papers, start=1):
            p["rank"] = idx

        result["papers"] = papers

        # 3. Collect datasets for Dataset comparison
        datasets = collect_datasets(idea, papers)
        result["datasets"] = datasets

        # 4. Find research gaps
        plan = result.get("plan") if isinstance(result.get("plan"), dict) else {}
        questions = plan.get("research_questions") or []
        gaps = find_gaps(idea, questions, papers)
        result["gaps"] = gaps

        # 5. Write/update Reading notes (draft) report
        report = write_report(idea, plan, papers, gaps)
        result["report"] = report

        usage = commit_run()
        result["usage"] = usage

        update_task(task_id, status="done", result=result, usage=usage, error="")

        updated_task = get_task(task_id)
        return {
            "status": "done",
            "task": task_to_dict(updated_task) if updated_task else payload,
            "paper": final_paper,
            "provider": chosen,
        }
    except Exception as exc:
        usage = commit_run()
        err_msg = str(exc).split("\n")[0].strip()
        if _busy_error(exc):
            err_msg = "The model is busy or rate limited right now. Click Try again to analyze."
        update_task(task_id, error=err_msg, usage=usage)
        raise RuntimeError(err_msg) from exc


def run_gap_check(
    task_id: str,
    paper_ranks: list[int],
    *,
    provider: str | None = None,
) -> dict[str, Any]:
    """Start a background gap-check run for the selected papers."""
    apply_env()
    if not has_llm_key():
        raise RuntimeError("Add an OpenAI or Gemini API key in Settings, then click Save.")

    task = get_task(task_id)
    if not task:
        raise RuntimeError("Chat not found.")
    payload = task_to_dict(task)
    if payload.get("status") != "done":
        raise RuntimeError("Research must be complete before running a gap check.")

    result = payload.get("result") or {}
    papers = result.get("papers") or []
    gaps = result.get("gaps") or []

    if not gaps:
        raise RuntimeError("No research gaps found in this session. Run the full pipeline first.")

    selected = [p for p in papers if int(p.get("rank") or 0) in set(int(r) for r in paper_ranks)] if paper_ranks else list(papers)
    if not selected:
        raise RuntimeError("No papers matched the selected ranks.")

    chosen = resolve_provider(provider or result.get("provider"))
    result = dict(result)
    result["gap_check"] = {"status": "running", "results": [], "selected_ranks": list(paper_ranks)}
    update_task(task_id, result=result)

    _executor.submit(_run_gap_check_bg, task_id, str(payload.get("idea") or ""), selected, gaps, chosen)

    task = get_task(task_id)
    return task_to_dict(task) if task else {"id": task_id, "status": "running"}


def stop_gap_check(task_id: str) -> dict[str, Any]:
    """Stop an in-progress gap check task."""
    task = get_task(task_id)
    if not task:
        raise RuntimeError("Chat not found.")
    payload = task_to_dict(task)
    result = dict(payload.get("result") or {})
    gc = dict(result.get("gap_check") or {})
    gc["status"] = "stopped"
    gc["error"] = "Gap check stopped by user."
    result["gap_check"] = gc
    update_task(task_id, result=result)
    return gc


def _run_gap_check_bg(
    task_id: str,
    idea: str,
    selected_papers: list[dict],
    all_gaps: list[dict],
    provider: str,
) -> None:
    from src.agents.gap_checker import check_gaps_resolved

    apply_env()
    use_provider(provider)
    begin_run(task_id, active_model_id(provider))

    try:
        def should_stop() -> bool:
            t = get_task(task_id)
            if not t:
                return True
            res = task_to_dict(t).get("result") or {}
            gc = res.get("gap_check") or {}
            return gc.get("status") == "stopped"

        def on_progress(idx: int, total: int, msg: str, logs: list[dict]) -> bool:
            if should_stop():
                return True
            t = get_task(task_id)
            current_res = dict(task_to_dict(t).get("result") or {}) if t else {}
            gc = dict(current_res.get("gap_check") or {})
            if gc.get("status") == "stopped":
                return True
            gc["status"] = "running"
            gc["progress"] = f"[{idx}/{total}] {msg}" if idx > 0 else msg
            gc["logs"] = list(logs)
            current_res["gap_check"] = gc
            update_task(task_id, result=current_res)
            return False

        outcome = check_gaps_resolved(
            idea, selected_papers, all_gaps, on_progress=on_progress, should_stop=should_stop
        )

        usage = commit_run()
        if should_stop() or outcome.get("stopped"):
            t = get_task(task_id)
            current_res = dict(task_to_dict(t).get("result") or {}) if t else {}
            gc = dict(current_res.get("gap_check") or {})
            gc["status"] = "stopped"
            gc["error"] = "Gap check stopped by user."
            gc["results"] = outcome.get("results") or []
            gc["logs"] = outcome.get("logs") or []
            current_res["gap_check"] = gc
            update_task(task_id, result=current_res, usage=usage)
            return

        t = get_task(task_id)
        current_res = dict(task_to_dict(t).get("result") or {}) if t else {}
        current_res["gap_check"] = {
            "status": "done",
            "results": outcome.get("results") or [],
            "logs": outcome.get("logs") or [],
            "selected_ranks": [int(p.get("rank") or 0) for p in selected_papers],
            "usage": usage,
        }
        update_task(task_id, result=current_res)

    except Exception as exc:
        usage = commit_run()
        err = str(exc).split("\n")[0].strip()
        if _busy_error(exc):
            err = "The model is busy or rate limited. Click Try again."
        t = get_task(task_id)
        current_res = dict(task_to_dict(t).get("result") or {}) if t else {}
        current_res["gap_check"] = {"status": "error", "error": err, "results": []}
        update_task(task_id, result=current_res, usage=usage)



