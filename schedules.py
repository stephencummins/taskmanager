"""pm2 and n8n schedule management."""
import asyncio
import json
import os
import re
import shutil
from typing import Optional

PM2 = "/opt/homebrew/bin/pm2"
N8N_BASE = os.environ.get("N8N_BASE_URL", "http://localhost:5678")
N8N_KEY  = os.environ.get("N8N_API_KEY", "")


# ── pm2 helpers ────────────────────────────────────────────────────────────────

async def _pm2(*args) -> tuple[str, str, int]:
    env = {
        "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
        "HOME": os.environ.get("HOME", "/Users/stephencummins"),
    }
    proc = await asyncio.create_subprocess_exec(
        PM2, *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await proc.communicate()
    return stdout.decode(), stderr.decode(), proc.returncode


async def list_pm2_jobs() -> list[dict]:
    stdout, _, rc = await _pm2("jlist")
    if rc != 0:
        return []
    try:
        procs = json.loads(stdout)
    except json.JSONDecodeError:
        return []
    result = []
    for p in procs:
        env = p.get("pm2_env", {})
        cron = env.get("cron_restart")
        if not cron:
            continue
        result.append({
            "source": "pm2",
            "name":     p["name"],
            "cron":     cron,
            "status":   env.get("status", "unknown"),
            "restarts": env.get("restart_time", 0),
            "script":   env.get("pm_exec_path", ""),
            "pm_id":    p.get("pm_id", 0),
        })
    return result


async def pm2_trigger(name: str) -> tuple[bool, str]:
    _, stderr, rc = await _pm2("restart", name)
    return rc == 0, stderr


async def pm2_start(name: str) -> tuple[bool, str]:
    _, stderr, rc = await _pm2("start", name)
    return rc == 0, stderr


async def pm2_stop(name: str) -> tuple[bool, str]:
    _, stderr, rc = await _pm2("stop", name)
    return rc == 0, stderr


async def pm2_delete(name: str) -> tuple[bool, str]:
    await _pm2("stop", name)
    _, stderr, rc = await _pm2("delete", name)
    return rc == 0, stderr


async def pm2_add(name: str, script: str, cron: str, cwd: str = "", interpreter: str = "bash") -> tuple[bool, str]:
    if not re.match(r'^[a-zA-Z0-9_-]+$', name):
        return False, "Name must be alphanumeric (hyphens/underscores allowed)"
    args = ["start", script, "--name", name, "--cron", cron]
    if interpreter:
        args += ["--interpreter", interpreter]
    if cwd:
        args += ["--cwd", cwd]
    _, stderr, rc = await _pm2(*args)
    if rc == 0:
        await _pm2("save")
    return rc == 0, stderr


# ── n8n helpers ────────────────────────────────────────────────────────────────

async def list_n8n_workflows() -> tuple[list[dict], str | None]:
    """Return (workflows, error). workflows is [] on error."""
    if not N8N_KEY:
        return [], "N8N_API_KEY not configured"
    try:
        import urllib.request
        req = urllib.request.Request(
            f"{N8N_BASE}/api/v1/workflows?limit=50",
            headers={"X-N8N-API-KEY": N8N_KEY, "Accept": "application/json"},
        )
        loop = asyncio.get_event_loop()
        def fetch():
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read())
        data = await loop.run_in_executor(None, fetch)
        workflows = []
        for w in data.get("data", []):
            # Check for schedule triggers
            has_schedule = any(
                "schedule" in n.get("type", "").lower()
                for n in w.get("nodes", [])
            )
            if has_schedule:
                workflows.append({
                    "source":  "n8n",
                    "id":      w["id"],
                    "name":    w["name"],
                    "active":  w.get("active", False),
                    "url":     f"{N8N_BASE}/workflow/{w['id']}",
                })
        return workflows, None
    except Exception as e:
        return [], str(e)


async def n8n_toggle(workflow_id: str, active: bool) -> tuple[bool, str]:
    if not N8N_KEY:
        return False, "N8N_API_KEY not configured"
    import urllib.request
    method = "POST" if active else "POST"
    endpoint = "activate" if active else "deactivate"
    try:
        req = urllib.request.Request(
            f"{N8N_BASE}/api/v1/workflows/{workflow_id}/{endpoint}",
            data=b"",
            headers={"X-N8N-API-KEY": N8N_KEY, "Content-Type": "application/json"},
            method="POST",
        )
        loop = asyncio.get_event_loop()
        def do_request():
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        status = await loop.run_in_executor(None, do_request)
        return status < 300, ""
    except Exception as e:
        return False, str(e)
