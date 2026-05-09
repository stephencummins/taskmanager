"""Task Manager — Starlette 1.0 application."""
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, TypedDict

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

import aiosqlite
import db as database
import schedules as sched

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ── Helpers ───────────────────────────────────────────────────────────────────

def flash(request: Request, message: str, category: str = "success") -> None:
    request.session.setdefault("flashes", []).append({"message": message, "category": category})


def get_flashes(request: Request) -> list:
    return request.session.pop("flashes", [])


def _context(request: Request, **kwargs):
    return {"request": request, "flashes": get_flashes(request), **kwargs}


async def _db(request: Request) -> aiosqlite.Connection:
    return request.state["db"]


# ── Lifespan ──────────────────────────────────────────────────────────────────

class AppState(TypedDict):
    db: aiosqlite.Connection


@asynccontextmanager
async def lifespan(app: Starlette) -> AsyncIterator[AppState]:
    await database.init_db()
    conn = await database.get_db()
    try:
        yield {"db": conn}
    finally:
        await conn.close()


# ── Projects ──────────────────────────────────────────────────────────────────

async def projects_list(request: Request):
    db = await _db(request)
    projects = await database.list_projects(db)
    labels = await database.list_labels(db)
    return templates.TemplateResponse(
        request, "projects.html",
        _context(request, projects=projects, labels=labels),
    )


async def project_create(request: Request):
    form = await request.form()
    name = str(form.get("name", "")).strip()
    if not name:
        flash(request, "Project name is required.", "error")
        return RedirectResponse("/projects", status_code=303)
    db = await _db(request)
    pid = await database.create_project(
        db, name,
        str(form.get("description", "")),
        str(form.get("color", "#6366f1")),
    )
    flash(request, f"Project '{name}' created.")
    return RedirectResponse(f"/projects/{pid}", status_code=303)


async def project_detail(request: Request):
    pid = int(request.path_params["id"])
    db = await _db(request)
    project = await database.get_project(db, pid)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    tasks = await database.list_tasks(db, pid)
    labels = await database.list_labels(db)
    todo        = [t for t in tasks if t["status"] == "todo"]
    in_progress = [t for t in tasks if t["status"] == "in_progress"]
    done        = [t for t in tasks if t["status"] == "done"]
    return templates.TemplateResponse(
        request, "project.html",
        _context(request,
                 project=project, labels=labels,
                 todo=todo, in_progress=in_progress, done=done),
    )


async def project_update(request: Request):
    pid = int(request.path_params["id"])
    form = await request.form()
    action = str(form.get("_action", ""))
    db = await _db(request)
    project = await database.get_project(db, pid)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if action == "delete":
        await database.delete_project(db, pid)
        flash(request, f"Project '{project['name']}' deleted.")
        return RedirectResponse("/projects", status_code=303)
    name = str(form.get("name", "")).strip() or project["name"]
    await database.update_project(db, pid, name,
                                  str(form.get("description", "")),
                                  str(form.get("color", "#6366f1")))
    flash(request, "Project updated.")
    return RedirectResponse(f"/projects/{pid}", status_code=303)


# ── Tasks ─────────────────────────────────────────────────────────────────────

async def task_create(request: Request):
    form = await request.form()
    pid = int(form.get("project_id", 0))
    title = str(form.get("title", "")).strip()
    if not title:
        flash(request, "Task title is required.", "error")
        return RedirectResponse(f"/projects/{pid}", status_code=303)
    db = await _db(request)
    tid = await database.create_task(
        db, pid, title,
        str(form.get("description", "")),
        str(form.get("status", "todo")),
        str(form.get("priority", "medium")),
        str(form.get("due_date", "")) or None,
    )
    flash(request, f"Task '{title}' created.")
    return RedirectResponse(f"/tasks/{tid}", status_code=303)


async def task_detail(request: Request):
    tid = int(request.path_params["id"])
    db = await _db(request)
    task = await database.get_task(db, tid)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    project = await database.get_project(db, task["project_id"])
    comments = await database.list_comments(db, tid)
    all_labels = await database.list_labels(db)
    task_label_ids = {l["id"] for l in task["labels"]}
    available_labels = [l for l in all_labels if dict(l)["id"] not in task_label_ids]
    return templates.TemplateResponse(
        request, "task.html",
        _context(request, task=task, project=project,
                 comments=comments, available_labels=available_labels),
    )


async def task_update(request: Request):
    tid = int(request.path_params["id"])
    form = await request.form()
    action = str(form.get("_action", ""))
    db = await _db(request)
    task = await database.get_task(db, tid)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if action == "delete":
        pid = task["project_id"]
        await database.delete_task(db, tid)
        flash(request, f"Task '{task['title']}' deleted.")
        return RedirectResponse(f"/projects/{pid}", status_code=303)

    if action == "status":
        await database.update_task_status(db, tid, str(form.get("status", task["status"])))
        return RedirectResponse(f"/tasks/{tid}", status_code=303)

    title = str(form.get("title", "")).strip() or task["title"]
    await database.update_task(
        db, tid, title,
        str(form.get("description", "")),
        str(form.get("status", "todo")),
        str(form.get("priority", "medium")),
        str(form.get("due_date", "")) or None,
    )
    flash(request, "Task updated.")
    return RedirectResponse(f"/tasks/{tid}", status_code=303)


# ── Labels ────────────────────────────────────────────────────────────────────

async def labels_list(request: Request):
    db = await _db(request)
    labels = await database.list_labels(db)
    return templates.TemplateResponse(
        request, "labels.html",
        _context(request, labels=labels),
    )


async def label_create(request: Request):
    form = await request.form()
    name = str(form.get("name", "")).strip()
    if not name:
        flash(request, "Label name is required.", "error")
        return RedirectResponse("/labels", status_code=303)
    db = await _db(request)
    await database.create_label(db, name, str(form.get("color", "#64748b")))
    flash(request, f"Label '{name}' created.")
    return RedirectResponse("/labels", status_code=303)


async def label_delete(request: Request):
    lid = int(request.path_params["id"])
    db = await _db(request)
    await database.delete_label(db, lid)
    flash(request, "Label deleted.")
    return RedirectResponse("/labels", status_code=303)


async def task_label_add(request: Request):
    tid = int(request.path_params["id"])
    form = await request.form()
    lid = int(form.get("label_id", 0))
    db = await _db(request)
    await database.add_label_to_task(db, tid, lid)
    return RedirectResponse(f"/tasks/{tid}", status_code=303)


async def task_label_remove(request: Request):
    tid  = int(request.path_params["id"])
    lid  = int(request.path_params["label_id"])
    db = await _db(request)
    await database.remove_label_from_task(db, tid, lid)
    return RedirectResponse(f"/tasks/{tid}", status_code=303)


# ── Comments ──────────────────────────────────────────────────────────────────

async def comment_create(request: Request):
    tid = int(request.path_params["id"])
    form = await request.form()
    body = str(form.get("body", "")).strip()
    if not body:
        flash(request, "Comment cannot be empty.", "error")
        return RedirectResponse(f"/tasks/{tid}", status_code=303)
    db = await _db(request)
    await database.create_comment(
        db, tid,
        str(form.get("author", "Anonymous")).strip() or "Anonymous",
        body,
    )
    return RedirectResponse(f"/tasks/{tid}#comments", status_code=303)


async def comment_delete(request: Request):
    cid = int(request.path_params["id"])
    form = await request.form()
    tid  = int(form.get("task_id", 0))
    db = await _db(request)
    await database.delete_comment(db, cid)
    return RedirectResponse(f"/tasks/{tid}#comments", status_code=303)


# ── Schedules ─────────────────────────────────────────────────────────────────

async def schedules_list(request: Request):
    pm2_jobs = await sched.list_pm2_jobs()
    n8n_workflows, n8n_error = await sched.list_n8n_workflows()
    return templates.TemplateResponse(
        request, "schedules.html",
        _context(request, pm2_jobs=pm2_jobs, n8n_workflows=n8n_workflows, n8n_error=n8n_error),
    )


async def schedule_action(request: Request):
    name = request.path_params["name"]
    form = await request.form()
    action = str(form.get("_action", ""))

    if action == "trigger":
        ok, err = await sched.pm2_trigger(name)
        flash(request, f"Triggered '{name}'." if ok else f"Failed: {err}", "success" if ok else "error")
    elif action == "enable":
        ok, err = await sched.pm2_start(name)
        flash(request, f"Started '{name}'." if ok else f"Failed: {err}", "success" if ok else "error")
    elif action == "disable":
        ok, err = await sched.pm2_stop(name)
        flash(request, f"Stopped '{name}'." if ok else f"Failed: {err}", "success" if ok else "error")
    elif action == "delete":
        ok, err = await sched.pm2_delete(name)
        flash(request, f"Deleted '{name}'." if ok else f"Failed: {err}", "success" if ok else "error")

    return RedirectResponse("/schedules", status_code=303)


async def n8n_action(request: Request):
    wf_id = request.path_params["id"]
    form = await request.form()
    activate = str(form.get("_action", "")) == "activate"
    ok, err = await sched.n8n_toggle(wf_id, activate)
    label = "Activated" if activate else "Deactivated"
    flash(request, f"{label} workflow." if ok else f"n8n error: {err}", "success" if ok else "error")
    return RedirectResponse("/schedules", status_code=303)


async def schedule_create(request: Request):
    form = await request.form()
    name       = str(form.get("name", "")).strip()
    script     = str(form.get("script", "")).strip()
    cron_expr  = str(form.get("cron", "")).strip()
    cwd        = str(form.get("cwd", "")).strip()
    interpreter = str(form.get("interpreter", "bash")).strip()

    if not all([name, script, cron_expr]):
        flash(request, "Name, command and cron expression are required.", "error")
        return RedirectResponse("/schedules", status_code=303)

    ok, err = await sched.pm2_add(name, script, cron_expr, cwd, interpreter)
    flash(request, f"Job '{name}' created." if ok else f"Failed: {err}", "success" if ok else "error")
    return RedirectResponse("/schedules", status_code=303)


# ── Error handlers ────────────────────────────────────────────────────────────

async def not_found(request: Request, exc: HTTPException):
    return templates.TemplateResponse(
        request, "error.html",
        _context(request, status=404, detail=exc.detail),
        status_code=404,
    )


async def server_error(request: Request, exc: Exception):
    return templates.TemplateResponse(
        request, "error.html",
        _context(request, status=500, detail="Something went wrong."),
        status_code=500,
    )


# ── App ───────────────────────────────────────────────────────────────────────

routes = [
    Route("/", lambda r: RedirectResponse("/schedules", status_code=302)),
    Route("/projects",           projects_list,   methods=["GET"]),
    Route("/projects/new",       project_create,  methods=["POST"]),
    Route("/projects/{id:int}",  project_detail,  methods=["GET"]),
    Route("/projects/{id:int}",  project_update,  methods=["POST"]),
    Route("/tasks/new",          task_create,     methods=["POST"]),
    Route("/tasks/{id:int}",     task_detail,     methods=["GET"]),
    Route("/tasks/{id:int}",     task_update,     methods=["POST"]),
    Route("/tasks/{id:int}/labels",                     task_label_add,    methods=["POST"]),
    Route("/tasks/{id:int}/labels/{label_id:int}/remove", task_label_remove, methods=["POST"]),
    Route("/tasks/{id:int}/comments", comment_create,  methods=["POST"]),
    Route("/comments/{id:int}/delete", comment_delete, methods=["POST"]),
    Route("/labels",             labels_list,     methods=["GET"]),
    Route("/labels/new",         label_create,    methods=["POST"]),
    Route("/labels/{id:int}/delete", label_delete, methods=["POST"]),
    Route("/schedules",              schedules_list,   methods=["GET"]),
    Route("/schedules/new",          schedule_create,  methods=["POST"]),
    Route("/schedules/{name}/action", schedule_action, methods=["POST"]),
    Route("/schedules/n8n/{id}/action", n8n_action,   methods=["POST"]),
    Mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static"),
]

app = Starlette(
    debug=True,
    routes=routes,
    lifespan=lifespan,
    middleware=[
        Middleware(SessionMiddleware, secret_key="dev-secret-change-in-prod"),
    ],
    exception_handlers={
        404: not_found,
        500: server_error,
    },
)
