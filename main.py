import shutil
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
import json

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field

from src.db import (
    get_task,
    init_db,
    list_tasks,
    rename_task,
    task_to_dict,
    update_task,
)
from src.stars import add_star, list_stars, remove_star
from src.matrix import gap_check_csv, gap_check_xlsx, matrix_csv, matrix_xlsx, safe_filename
from src.topic import fold_topic
from src.orchestrator import analyze_user_paper, run_gap_check, stop_gap_check, start_research, resume_research
from src.settings import apply_env, public_settings, save_settings
from src.tools.arxiv import RECENT_YEARS
from src.tools.sources import source_catalog
from src.reader import find_paper, outline_for_paper, safe_pdf_path
from src.library import (
    delete_upload,
    format_upload_as_paper,
    get_upload,
    library_payload,
    list_uploads,
    outline_for_library_paper,
    resolve_library_paper,
    save_upload,
)
from src.marks import add_mark, get_marks, list_all_notes, save_marks
from src.tutor import ask_tutor_stream, get_memory, paper_key_for
from src.export_notes import notes_pdf_bytes
from src.progress import mark_opened, set_reading_status
from src.cleanup import purge_research, sweep_orphan_pdfs
from src.usage import public_spend, snapshot

load_dotenv()

ROOT = Path(__file__).resolve().parent
static_dir = ROOT / "static"
static_dir.mkdir(exist_ok=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # ── Auto-backup the DB before any migration runs ─────────────────────
    from src.db import DB_PATH
    if DB_PATH.exists():
        bak = DB_PATH.with_suffix(".db.bak")
        try:
            shutil.copy2(DB_PATH, bak)
        except OSError:
            pass  # non-fatal
    # ─────────────────────────────────────────────────────────────────────
    init_db()
    apply_env()
    sweep_orphan_pdfs()
    yield


app = FastAPI(title="Literature Research Agent", lifespan=lifespan)
templates = Jinja2Templates(directory=str(ROOT / "templates"))
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


class ResearchRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")
    idea: str = Field(..., min_length=1, max_length=20000, alias="prompt")
    paper_count: int = Field(default=8, ge=3, le=30)
    task: str = Field(default="", max_length=200)
    domain: str = Field(default="", max_length=200)
    constraint: str = Field(default="", max_length=300)
    provider: str = Field(default="", max_length=20)
    datasets_only: bool = Field(default=False)


class RenameRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str = Field(default="", max_length=400)


class StarRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    paper: dict = Field(default_factory=dict)
    task_id: str = ""
    chat_title: str = ""
    key: str = ""


class MarksRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    marks: list[dict] = Field(default_factory=list)
    research_name: str = Field(default="", max_length=200)
    title: str = Field(default="", max_length=400)
    year: str | int | None = None
    authors: str = Field(default="", max_length=400)


class MarkAddRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str = Field(default="", max_length=2000)
    page: int = Field(default=1, ge=1, le=5000)
    note: str = Field(default="", max_length=4000)


class TutorAskRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    message: str = Field(default="", max_length=4000)
    provider: str = Field(default="", max_length=20)


class StatusRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    paper_key: str = Field(default="", max_length=200)
    status: str = Field(default="reading", max_length=50)


class GapCheckRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    paper_ranks: list[int] = Field(default_factory=list)
    provider: str = Field(default="", max_length=20)



class SettingsRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    openai_api_key: str | None = Field(default=None, max_length=400)
    openai_model: str | None = Field(default=None, max_length=80)
    gemini_api_key: str | None = Field(default=None, max_length=400)
    preferred_provider: str | None = Field(default=None, max_length=20)
    semantic_scholar_api_key: str | None = Field(default=None, max_length=200)
    contact_email: str | None = Field(default=None, max_length=120)


@app.get("/api/settings")
def get_settings_api() -> dict:
    data = public_settings()
    data["spend"] = public_spend()
    return data


@app.post("/api/settings")
def update_settings_api(body: SettingsRequest) -> dict:
    openai_key = None if body.openai_api_key is None else body.openai_api_key.strip()
    gemini_key = None if body.gemini_api_key is None else body.gemini_api_key.strip()
    if openai_key and not openai_key.startswith("sk-"):
        raise HTTPException(status_code=400, detail="That does not look like an OpenAI API key.")
    if gemini_key and gemini_key.startswith("sk-"):
        raise HTTPException(status_code=400, detail="That looks like an OpenAI key. Paste it in the OpenAI box.")
    if gemini_key and not (gemini_key.startswith("AIza") or len(gemini_key) >= 20):
        raise HTTPException(status_code=400, detail="That does not look like a Gemini API key from Google AI Studio.")
    if (
        openai_key is None
        and gemini_key is None
        and body.semantic_scholar_api_key is None
        and body.contact_email is None
        and body.preferred_provider is None
    ):
        raise HTTPException(status_code=400, detail="Nothing to save.")
    return save_settings(
        openai_api_key=openai_key,
        openai_model=body.openai_model,
        gemini_api_key=gemini_key,
        preferred_provider=body.preferred_provider,
        semantic_scholar_api_key=body.semantic_scholar_api_key,
        contact_email=body.contact_email,
    )


@app.get("/api/limits")
def get_limits_api(refresh: bool = False) -> dict:
    if refresh:
        from src.ratelimits import probe_openalex

        probe_openalex()
    from src.ratelimits import public_limits

    return {"limits": public_limits(), "refreshed_at": datetime.now(timezone.utc).isoformat()}


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "settings.html")


@app.get("/read", response_class=HTMLResponse)
def read_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "read.html")


# Legacy JSON routes (keep for older cached pages)
@app.get("/settings.json")
def get_settings_legacy() -> dict:
    return get_settings_api()


@app.post("/settings.json")
def update_settings_legacy(body: SettingsRequest) -> dict:
    return update_settings_api(body)


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html")


@app.post("/research")
def create_research(body: ResearchRequest) -> dict:
    idea = fold_topic(body.idea, body.task, body.domain, body.constraint)
    if len(idea) < 8:
        raise HTTPException(status_code=400, detail="Describe the project idea in a bit more detail.")
    try:
        return start_research(
            idea,
            paper_count=body.paper_count,
            provider=body.provider or None,
            datasets_only=body.datasets_only,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/stars")
def get_stars() -> dict:
    papers = list_stars()
    return {"papers": papers, "count": len(papers)}


@app.post("/api/stars")
def star_paper(body: StarRequest) -> dict:
    try:
        saved = add_star(body.paper, task_id=body.task_id, chat_title=body.chat_title)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"starred": True, "paper": saved, "count": len(list_stars())}


@app.delete("/api/stars")
def unstar_paper(key: str = Query("", max_length=2000)) -> dict:
    paper_id = key.strip()
    if not paper_id:
        raise HTTPException(status_code=400, detail="Missing paper key.")
    found = remove_star(paper_id)
    return {"starred": False, "removed": found, "count": len(list_stars())}


@app.get("/api/notes")
def get_all_notes() -> dict:
    return list_all_notes()


@app.get("/read/library")
def read_library() -> dict:
    return library_payload()


@app.post("/read/you")
async def read_upload_paper(file: UploadFile = File(...)) -> dict:
    name = file.filename or "paper.pdf"
    if not name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Upload a PDF file.")
    data = await file.read()
    try:
        return save_upload(name, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/read/you/{upload_id}")
def read_delete_upload(upload_id: str) -> dict:
    if not delete_upload(upload_id):
        raise HTTPException(status_code=404, detail="Uploaded paper not found.")
    return {"removed": True}


@app.get("/read/you/{upload_id}/outline")
def read_upload_outline(upload_id: str) -> dict:
    paper = resolve_library_paper(kind="you", upload_id=upload_id)
    if not paper:
        raise HTTPException(status_code=404, detail="Uploaded paper not found.")
    data = outline_for_library_paper(paper, research_name="Your papers")
    data["marks"] = get_marks(f"upload:{upload_id}", 1)
    data["tutor"] = get_memory(paper_key_for(kind="you", upload_id=upload_id))
    mark_opened(paper_key_for(kind="you", upload_id=upload_id))
    return data


@app.get("/read/you/{upload_id}/notes.pdf")
def read_upload_notes_pdf(
    upload_id: str,
    pass_index: int = Query(default=0, alias="pass", ge=0, le=3),
    focus: str = Query(default="", max_length=400),
) -> Response:
    if not resolve_library_paper(kind="you", upload_id=upload_id):
        raise HTTPException(status_code=404, detail="Uploaded paper not found.")
    pdf, filename = notes_pdf_bytes(kind="you", upload_id=upload_id, pass_index=pass_index, focus=focus)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@app.get("/read/you/{upload_id}/pdf")
def read_upload_pdf(upload_id: str) -> FileResponse:
    paper = resolve_library_paper(kind="you", upload_id=upload_id)
    path = safe_pdf_path(paper)
    if not path:
        raise HTTPException(status_code=404, detail="No PDF saved for this upload.")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=path.name,
        content_disposition_type="inline",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@app.get("/read/you/{upload_id}/marks")
def read_upload_marks_get(upload_id: str) -> dict:
    if not resolve_library_paper(kind="you", upload_id=upload_id):
        raise HTTPException(status_code=404, detail="Uploaded paper not found.")
    return get_marks(f"upload:{upload_id}", 1)


@app.put("/read/you/{upload_id}/marks")
def read_upload_marks_save(upload_id: str, body: MarksRequest) -> dict:
    if not resolve_library_paper(kind="you", upload_id=upload_id):
        raise HTTPException(status_code=404, detail="Uploaded paper not found.")
    payload = body.model_dump()
    payload["research_name"] = payload.get("research_name") or "Your papers"
    return save_marks(f"upload:{upload_id}", 1, payload)


@app.get("/read/you/{upload_id}/tutor")
def read_upload_tutor_get(upload_id: str) -> dict:
    if not resolve_library_paper(kind="you", upload_id=upload_id):
        raise HTTPException(status_code=404, detail="Uploaded paper not found.")
    return get_memory(paper_key_for(kind="you", upload_id=upload_id))


def _tutor_sse(**kwargs):
    def events():
        try:
            for delta in ask_tutor_stream(**kwargs):
                yield f"data: {json.dumps({'delta': delta}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        except (ValueError, RuntimeError) as exc:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"
        except Exception as exc:
            import traceback
            traceback.print_exc()
            err_msg = str(exc)
            if "quota" in err_msg.lower() or "429" in err_msg or "resource_exhausted" in err_msg.lower():
                user_msg = "Model quota / rate limit reached on free tier. Please switch to OpenAI (gpt-4o) or retry shortly."
            elif "503" in err_msg or "high demand" in err_msg.lower() or "unavailable" in err_msg.lower():
                user_msg = "Model is currently experiencing high demand. Please try OpenAI (gpt-4o) or retry in a moment."
            elif not has_llm_key():
                user_msg = "No API key configured. Add an OpenAI or Gemini API key in Settings."
            else:
                user_msg = f"The tutor could not answer: {err_msg[:160]}"
            yield f"data: {json.dumps({'error': user_msg})}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/read/you/{upload_id}/tutor")
def read_upload_tutor_ask(upload_id: str, body: TutorAskRequest):
    if not resolve_library_paper(kind="you", upload_id=upload_id):
        raise HTTPException(status_code=404, detail="Uploaded paper not found.")
    return _tutor_sse(kind="you", upload_id=upload_id, message=body.message, provider=body.provider)


@app.get("/read/agent/{task_id}/{rank}/notes.pdf")
def read_agent_notes_pdf(
    task_id: str,
    rank: int,
    pass_index: int = Query(default=0, alias="pass", ge=0, le=3),
    focus: str = Query(default="", max_length=400),
) -> Response:
    if not find_paper(task_id, rank):
        raise HTTPException(status_code=404, detail="Paper not found.")
    pdf, filename = notes_pdf_bytes(
        kind="agent",
        task_id=task_id,
        rank=rank,
        pass_index=pass_index,
        focus=focus,
    )
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@app.get("/read/agent/{task_id}/{rank}/tutor")
def read_agent_tutor_get(task_id: str, rank: int) -> dict:
    if not find_paper(task_id, rank):
        raise HTTPException(status_code=404, detail="Paper not found.")
    return get_memory(paper_key_for(kind="agent", task_id=task_id, rank=rank))


@app.post("/read/agent/{task_id}/{rank}/tutor")
def read_agent_tutor_ask(task_id: str, rank: int, body: TutorAskRequest):
    if not find_paper(task_id, rank):
        raise HTTPException(status_code=404, detail="Paper not found.")
    return _tutor_sse(
        kind="agent",
        task_id=task_id,
        rank=rank,
        message=body.message,
        provider=body.provider,
    )


@app.get("/sources")
def sources() -> dict:
    return {"sources": source_catalog(refresh=True), "recent_years": RECENT_YEARS}


@app.get("/chats")
def chats() -> dict:
    return {"chats": list_tasks()}


@app.post("/chats/{task_id}/rename")
def rename_chat(task_id: str, body: RenameRequest) -> dict:
    renamed = rename_task(task_id, body.title or "")
    if not renamed:
        raise HTTPException(status_code=404, detail="Chat not found.")
    return renamed


@app.delete("/chats/{task_id}")
def delete_chat(task_id: str) -> dict:
    if not task_id or task_id.strip() in ("", "undefined", "null"):
        raise HTTPException(status_code=400, detail="Invalid chat ID.")
    if not purge_research(task_id):
        raise HTTPException(status_code=404, detail="Chat not found.")
    return {"deleted": task_id}


@app.get("/api/user_uploads")
def get_user_uploads_api() -> dict:
    uploads = list_uploads()
    return {"uploads": uploads, "count": len(uploads)}


@app.post("/read/status")
def change_reading_status(body: StatusRequest) -> dict:
    return set_reading_status(body.paper_key, body.status)


@app.post("/chats/{task_id}/gap_check")
def start_gap_check(task_id: str, body: GapCheckRequest) -> dict:
    try:
        return run_gap_check(task_id, body.paper_ranks, provider=body.provider or None)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/chats/{task_id}/gap_check")
def get_gap_check(task_id: str) -> dict:
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Chat not found.")
    result = (task_to_dict(task).get("result") or {})
    gc = result.get("gap_check")
    if not gc:
        return {"status": "not_started"}
    return gc


@app.post("/chats/{task_id}/gap_check/stop")
def stop_gap_check_api(task_id: str) -> dict:
    try:
        return stop_gap_check(task_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc




@app.post("/chats/{task_id}/papers")
async def add_paper_to_chat(
    task_id: str,
    file: UploadFile | None = File(None),
    upload_id: str = Query("", max_length=100),
) -> dict:
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Chat not found.")

    upload = None
    if file and file.filename:
        name = file.filename or "paper.pdf"
        if not name.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="Upload a PDF file.")
        data = await file.read()
        try:
            upload = save_upload(name, data)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    elif upload_id:
        upload = get_upload(upload_id)
        if not upload:
            raise HTTPException(status_code=404, detail="Uploaded paper not found.")
    else:
        raise HTTPException(status_code=400, detail="Select an uploaded paper or upload a PDF file.")

    payload = task_to_dict(task)
    result = payload.get("result") or {}
    papers = result.get("papers") or []

    existing_key = f"upload:{upload['id']}"
    for p in papers:
        if p.get("paper_key") == existing_key or (p.get("kind") == "you" and str(p.get("upload_id")) == str(upload["id"])):
            return {"task": payload, "paper": p, "already_exists": True}

    rank = len(papers) + 1
    paper_item = format_upload_as_paper(upload, rank=rank)
    papers.append(paper_item)
    result["papers"] = papers
    update_task(task_id, result=result)

    updated_task = get_task(task_id)
    return {"task": task_to_dict(updated_task) if updated_task else payload, "paper": paper_item, "added": True}


@app.post("/chats/{task_id}/analyze_paper")
def analyze_paper_endpoint(
    task_id: str,
    upload_id: str = Query(..., max_length=100),
    provider: str = Query("", max_length=20),
) -> dict:
    try:
        return analyze_user_paper(task_id, upload_id, provider=provider or None)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc




@app.post("/task/{task_id}/retry")
def retry_research(task_id: str) -> dict:
    try:
        return resume_research(task_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/task/{task_id}")
def task_status(task_id: str, response: Response) -> dict:
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return task_to_dict(task)


def _matrix_payload(task_id: str) -> tuple[dict, list, list]:
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    payload = task_to_dict(task)
    result = payload.get("result") or {}
    papers = result.get("papers") or []
    if payload.get("status") != "done" or not papers:
        raise HTTPException(status_code=409, detail="Run a research query first, then export the matrix.")
    return payload, papers, result.get("datasets") or []


@app.get("/task/{task_id}/paper/{rank}/outline")
def paper_outline(task_id: str, rank: int) -> dict:
    paper = find_paper(task_id, rank)
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found.")
    task = get_task(task_id)
    payload = task_to_dict(task) if task else {}
    research_name = str(payload.get("title") or payload.get("idea") or "")
    data = outline_for_paper(paper, research_name=research_name)
    data["marks"] = get_marks(task_id, rank)
    data["tutor"] = get_memory(paper_key_for(kind="agent", task_id=task_id, rank=rank))
    mark_opened(paper_key_for(kind="agent", task_id=task_id, rank=rank))
    return data


@app.get("/task/{task_id}/paper/{rank}/marks")
def paper_marks_get(task_id: str, rank: int) -> dict:
    if not find_paper(task_id, rank):
        raise HTTPException(status_code=404, detail="Paper not found.")
    return get_marks(task_id, rank)


@app.put("/task/{task_id}/paper/{rank}/marks")
def paper_marks_save(task_id: str, rank: int, body: MarksRequest) -> dict:
    if not find_paper(task_id, rank):
        raise HTTPException(status_code=404, detail="Paper not found.")
    return save_marks(task_id, rank, body.model_dump())


@app.post("/task/{task_id}/paper/{rank}/marks")
def paper_marks_add(task_id: str, rank: int, body: MarkAddRequest) -> dict:
    if not find_paper(task_id, rank):
        raise HTTPException(status_code=404, detail="Paper not found.")
    if not (body.text or "").strip():
        raise HTTPException(status_code=400, detail="Select text in the PDF first, then click Mark.")
    return add_mark(task_id, rank, body.model_dump())


@app.get("/task/{task_id}/paper/{rank}/pdf")
def paper_pdf(task_id: str, rank: int) -> FileResponse:
    paper = find_paper(task_id, rank)
    path = safe_pdf_path(paper)
    if not path:
        raise HTTPException(status_code=404, detail="No PDF saved for this paper.")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=path.name,
        content_disposition_type="inline",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@app.get("/task/{task_id}/matrix.xlsx")
def task_matrix_xlsx(task_id: str) -> Response:
    payload, papers, datasets = _matrix_payload(task_id)
    filename = safe_filename(payload.get("idea") or "", task_id, "xlsx")
    return Response(
        content=matrix_xlsx(papers, datasets),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/task/{task_id}/matrix.csv")
def task_matrix_csv(task_id: str) -> Response:
    payload, papers, datasets = _matrix_payload(task_id)
    filename = safe_filename(payload.get("idea") or "", task_id, "csv")
    return Response(
        content=matrix_csv(papers, datasets).encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/task/{task_id}/gap_check.xlsx")
def task_gap_check_xlsx(task_id: str) -> Response:
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    payload = task_to_dict(task)
    result = payload.get("result") or {}
    gc = result.get("gap_check") or {}
    gc_results = gc.get("results") or []
    if not gc_results:
        raise HTTPException(status_code=409, detail="Run the gap check first, then download.")
    all_gaps = result.get("gaps") or []
    papers = result.get("papers") or []
    filename = safe_filename(f"gap-check-{(payload.get('idea') or '')[:30]}", task_id, "xlsx")
    return Response(
        content=gap_check_xlsx(gc_results, all_gaps, papers),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/task/{task_id}/gap_check.csv")
def task_gap_check_csv(task_id: str) -> Response:
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    payload = task_to_dict(task)
    result = payload.get("result") or {}
    gc = result.get("gap_check") or {}
    gc_results = gc.get("results") or []
    if not gc_results:
        raise HTTPException(status_code=409, detail="Run the gap check first, then download.")
    all_gaps = result.get("gaps") or []
    papers = result.get("papers") or []
    filename = safe_filename(f"gap-check-{(payload.get('idea') or '')[:30]}", task_id, "csv")
    return Response(
        content=gap_check_csv(gc_results, all_gaps, papers).encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )



@app.get("/task/{task_id}/progress")
def task_progress(task_id: str) -> dict:
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    payload = task_to_dict(task)
    return {
        "id": payload["id"],
        "status": payload["status"],
        "progress": payload["progress"],
        "error": payload["error"],
        "usage": payload.get("usage") or {},
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8001, reload=True)
