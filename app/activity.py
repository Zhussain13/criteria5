"""Who changed what in Criterion 5. Everything is saved in one place, so whatever a criteria member adds is
seen at once by the criteria head, the HoD and the principal; this log shows them who did it and when."""
from __future__ import annotations

from flask import g


def log(db, program_id: int, action: str, detail: str = "", user_id: int | None = None):
    uid = user_id if user_id is not None else (g.user["id"] if g.get("user") else None)
    db.execute("INSERT INTO activity (program_id, user_id, action, detail) VALUES (?, ?, ?, ?)",
               (program_id, uid, action, detail[:300]))


def recent(db, program_id: int, limit: int = 12) -> list:
    return db.execute(
        "SELECT a.*, u.name AS user_name, u.role AS user_role FROM activity a LEFT JOIN users u ON u.id = a.user_id "
        "WHERE a.program_id = ? ORDER BY a.id DESC LIMIT ?", (program_id, limit)).fetchall()
