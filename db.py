"""Database setup and queries using aiosqlite."""
import aiosqlite
from pathlib import Path

DB_PATH = Path(__file__).parent / "taskmanager.db"


async def get_db(path: Path = DB_PATH) -> aiosqlite.Connection:
    conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    return conn


async def init_db(path: Path = DB_PATH) -> None:
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS projects (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                color       TEXT NOT NULL DEFAULT '#6366f1',
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS labels (
                id    INTEGER PRIMARY KEY AUTOINCREMENT,
                name  TEXT NOT NULL UNIQUE,
                color TEXT NOT NULL DEFAULT '#64748b'
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                title       TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                status      TEXT NOT NULL DEFAULT 'todo'
                                CHECK (status IN ('todo','in_progress','done')),
                priority    TEXT NOT NULL DEFAULT 'medium'
                                CHECK (priority IN ('low','medium','high')),
                due_date    TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS task_labels (
                task_id  INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                label_id INTEGER NOT NULL REFERENCES labels(id) ON DELETE CASCADE,
                PRIMARY KEY (task_id, label_id)
            );

            CREATE TABLE IF NOT EXISTS comments (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id    INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                author     TEXT NOT NULL DEFAULT 'Anonymous',
                body       TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)
        await db.commit()


# ── Projects ──────────────────────────────────────────────────────────────────

async def list_projects(db: aiosqlite.Connection) -> list:
    async with db.execute("""
        SELECT p.*, COUNT(t.id) AS task_count
        FROM projects p
        LEFT JOIN tasks t ON t.project_id = p.id
        GROUP BY p.id
        ORDER BY p.created_at DESC
    """) as cur:
        return await cur.fetchall()


async def get_project(db: aiosqlite.Connection, project_id: int):
    async with db.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ) as cur:
        return await cur.fetchone()


async def create_project(db: aiosqlite.Connection, name: str, description: str, color: str) -> int:
    async with db.execute(
        "INSERT INTO projects (name, description, color) VALUES (?, ?, ?)",
        (name, description, color),
    ) as cur:
        await db.commit()
        return cur.lastrowid


async def update_project(db: aiosqlite.Connection, project_id: int, name: str, description: str, color: str) -> None:
    await db.execute(
        "UPDATE projects SET name=?, description=?, color=? WHERE id=?",
        (name, description, color, project_id),
    )
    await db.commit()


async def delete_project(db: aiosqlite.Connection, project_id: int) -> None:
    await db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    await db.commit()


# ── Tasks ─────────────────────────────────────────────────────────────────────

async def list_tasks(db: aiosqlite.Connection, project_id: int) -> list:
    async with db.execute("""
        SELECT t.*,
               GROUP_CONCAT(l.name, ',')  AS label_names,
               GROUP_CONCAT(l.color, ',') AS label_colors,
               GROUP_CONCAT(l.id, ',')    AS label_ids
        FROM tasks t
        LEFT JOIN task_labels tl ON tl.task_id = t.id
        LEFT JOIN labels l ON l.id = tl.label_id
        WHERE t.project_id = ?
        GROUP BY t.id
        ORDER BY
            CASE t.priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
            t.created_at DESC
    """, (project_id,)) as cur:
        rows = await cur.fetchall()
    return [_hydrate_task(r) for r in rows]


async def get_task(db: aiosqlite.Connection, task_id: int):
    async with db.execute("""
        SELECT t.*,
               GROUP_CONCAT(l.name, ',')  AS label_names,
               GROUP_CONCAT(l.color, ',') AS label_colors,
               GROUP_CONCAT(l.id, ',')    AS label_ids
        FROM tasks t
        LEFT JOIN task_labels tl ON tl.task_id = t.id
        LEFT JOIN labels l ON l.id = tl.label_id
        WHERE t.id = ?
        GROUP BY t.id
    """, (task_id,)) as cur:
        row = await cur.fetchone()
    return _hydrate_task(row) if row else None


def _hydrate_task(row) -> dict:
    t = dict(row)
    names  = (t.pop("label_names", None) or "").split(",") if t.get("label_names") else []
    colors = (t.pop("label_colors", None) or "").split(",") if t.get("label_colors") else []
    ids    = (t.pop("label_ids", None) or "").split(",") if t.get("label_ids") else []
    t["labels"] = [
        {"id": int(i), "name": n, "color": c}
        for i, n, c in zip(ids, names, colors) if i
    ]
    return t


async def create_task(db: aiosqlite.Connection, project_id: int, title: str,
                      description: str, status: str, priority: str, due_date: str | None) -> int:
    async with db.execute(
        """INSERT INTO tasks (project_id, title, description, status, priority, due_date)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (project_id, title, description, status, priority, due_date or None),
    ) as cur:
        await db.commit()
        return cur.lastrowid


async def update_task(db: aiosqlite.Connection, task_id: int, title: str,
                      description: str, status: str, priority: str, due_date: str | None) -> None:
    await db.execute(
        """UPDATE tasks SET title=?, description=?, status=?, priority=?, due_date=?
           WHERE id=?""",
        (title, description, status, priority, due_date or None, task_id),
    )
    await db.commit()


async def update_task_status(db: aiosqlite.Connection, task_id: int, status: str) -> None:
    await db.execute("UPDATE tasks SET status=? WHERE id=?", (status, task_id))
    await db.commit()


async def delete_task(db: aiosqlite.Connection, task_id: int) -> None:
    await db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    await db.commit()


# ── Labels ────────────────────────────────────────────────────────────────────

async def list_labels(db: aiosqlite.Connection) -> list:
    async with db.execute("SELECT * FROM labels ORDER BY name") as cur:
        return await cur.fetchall()


async def create_label(db: aiosqlite.Connection, name: str, color: str) -> int:
    async with db.execute(
        "INSERT OR IGNORE INTO labels (name, color) VALUES (?, ?)", (name, color)
    ) as cur:
        await db.commit()
        return cur.lastrowid


async def delete_label(db: aiosqlite.Connection, label_id: int) -> None:
    await db.execute("DELETE FROM labels WHERE id = ?", (label_id,))
    await db.commit()


async def add_label_to_task(db: aiosqlite.Connection, task_id: int, label_id: int) -> None:
    await db.execute(
        "INSERT OR IGNORE INTO task_labels (task_id, label_id) VALUES (?, ?)",
        (task_id, label_id),
    )
    await db.commit()


async def remove_label_from_task(db: aiosqlite.Connection, task_id: int, label_id: int) -> None:
    await db.execute(
        "DELETE FROM task_labels WHERE task_id=? AND label_id=?",
        (task_id, label_id),
    )
    await db.commit()


# ── Comments ──────────────────────────────────────────────────────────────────

async def list_comments(db: aiosqlite.Connection, task_id: int) -> list:
    async with db.execute(
        "SELECT * FROM comments WHERE task_id=? ORDER BY created_at ASC",
        (task_id,),
    ) as cur:
        return await cur.fetchall()


async def create_comment(db: aiosqlite.Connection, task_id: int, author: str, body: str) -> int:
    async with db.execute(
        "INSERT INTO comments (task_id, author, body) VALUES (?, ?, ?)",
        (task_id, author, body),
    ) as cur:
        await db.commit()
        return cur.lastrowid


async def delete_comment(db: aiosqlite.Connection, comment_id: int) -> None:
    await db.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
    await db.commit()
