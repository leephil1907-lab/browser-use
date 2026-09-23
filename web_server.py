from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from browser_use import Agent, Browser, ChatBrowserUse
from browser_use.llm.models import get_llm_by_name

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
DATA_DIR = Path(os.getenv("BROWSER_USE_WORKBENCH_DATA_DIR", "~/.browser-use/workbench")).expanduser()
TASK_FILE = DATA_DIR / "tasks.jsonl"
RADAR_URL = os.getenv("DECISION_RADAR_URL", "").rstrip("/")
DEFAULT_MODEL = os.getenv("BROWSER_USE_MODEL", "").strip()
app = FastAPI(title="Browser Use Decision Workbench", version="1.0.0")
app.mount("/workbench", StaticFiles(directory=WEB_DIR, html=True), name="workbench")


class RunRequest(BaseModel):
    task: str = Field(min_length=1)
    browser: str = Field(default="local")
    model: str | None = None
    startingUrl: str | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_tasks() -> list[dict[str, Any]]:
    if not TASK_FILE.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in TASK_FILE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def append_task(record: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with TASK_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def configured_llm(model: str | None):
    selected = (model or DEFAULT_MODEL).strip()
    if selected:
        return get_llm_by_name(selected)
    if not os.getenv("BROWSER_USE_API_KEY"):
        raise HTTPException(status_code=503, detail="No Browser Use model is configured.")
    return ChatBrowserUse()


def history_payload(history: Any) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    urls: list[str] = []
    for item in getattr(history, "history", []):
        state = getattr(item, "state", None)
        url = getattr(state, "url", None)
        if url:
            urls.append(url)
        output = getattr(item, "model_output", None)
        for action in getattr(output, "action", []) if output else []:
            actions.append(action.model_dump(exclude_none=True, mode="json"))
    return {
        "browser": {
            "url": urls[-1] if urls else "",
            "actions": actions,
        },
        "decision": {
            "source": "browser-use-agent",
            "action": actions[-1] if actions else None,
            "candidates": [],
        },
        "result": history.final_result(),
        "successful": history.is_successful(),
        "durationSeconds": history.total_duration_seconds(),
        "screenshot": screenshot,
    }


@app.get("/")
async def root() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "browserUse": True,
        "radarConfigured": bool(RADAR_URL),
        "workbenchVersion": "1.1.0",
    }


@app.post("/api/agent/run")
async def run_agent(request: RunRequest) -> dict[str, Any]:
    task = request.task.strip()
    if not task:
        raise HTTPException(status_code=422, detail="Task is required.")

    task_id = f"task-{int(time.time() * 1000)}"
    started = utc_now()
    start_clock = time.monotonic()
    browser = None
    record: dict[str, Any] = {
        "id": task_id,
        "task": task,
        "status": "running",
        "createdAt": started,
        "browser": request.browser,
    }
    append_task(record)

    try:
        llm = configured_llm(request.model)
        browser = Browser(use_cloud=request.browser == "cloud")
        objective = task
        if request.startingUrl:
            objective = f"Start at this URL: {request.startingUrl}\n\n{task}"
        agent = Agent(task=objective, llm=llm, browser=browser)
        history = await agent.run()
        payload = history_payload(history)
        record.update(
            {
                "status": "completed" if history.is_successful() is not False else "failed",
                "finishedAt": utc_now(),
                "durationSeconds": round(time.monotonic() - start_clock, 3),
                "result": history.final_result(),
                "urls": payload["browser"]["url"],
            }
        )
        # Rewrite the append-only record with a terminal record. Consumers use the latest id.
        append_task(record)
        return {"id": task_id, **payload}
    except HTTPException as exc:
        record.update({"status": "failed", "finishedAt": utc_now(), "error": exc.detail})
        append_task(record)
        raise
    except Exception as exc:
        record.update({"status": "failed", "finishedAt": utc_now(), "error": str(exc)})
        append_task(record)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass


@app.get("/api/tasks")
async def tasks(q: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    records = load_tasks()
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        latest[record["id"]] = record
    result = list(latest.values())
    if q:
        needle = q.lower()
        result = [r for r in result if needle in str(r.get("task", "")).lower()]
    if status:
        result = [r for r in result if r.get("status") == status]
    return list(reversed(result))


async def radar_request(path: str = "/projects", params: dict[str, str] | None = None) -> Any:
    if not RADAR_URL:
        raise HTTPException(status_code=503, detail="Decision Radar source is not configured.")
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(f"{RADAR_URL}{path}", params=params)
        response.raise_for_status()
        return response.json()


@app.get("/api/decision/projects")
async def decision_projects(
    q: str | None = None,
    category: str | None = None,
    sort: str | None = None,
    saved: bool = False,
) -> Any:
    params = {k: v for k, v in {"q": q, "category": category, "sort": sort, "saved": str(saved).lower()}.items() if v}
    return await radar_request("/projects", params)


@app.post("/api/decision/saved")
async def save_decision(payload: dict[str, Any]) -> Any:
    if not RADAR_URL:
        raise HTTPException(status_code=503, detail="Decision Radar source is not configured.")
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(f"{RADAR_URL}/saved", json=payload)
        response.raise_for_status()
        return response.json()
