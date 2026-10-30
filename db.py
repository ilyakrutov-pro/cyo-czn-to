from __future__ import annotations

import sqlite3
import base64
import hashlib
import secrets
import hmac
import ipaddress
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from settings import ADMIN_PASSWORD, DATABASE_PATH, TICKET_PREFIX, TICKET_WAITING_DIGITS


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    short_name TEXT NOT NULL,
    code TEXT NOT NULL UNIQUE,
    prefix TEXT NOT NULL DEFAULT 'A',
    priority INTEGER NOT NULL DEFAULT 1,
    service_time_minutes INTEGER NOT NULL DEFAULT 10,
    description TEXT DEFAULT '',
    color TEXT DEFAULT '#2563eb',
    enabled INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    number INTEGER NOT NULL,
    ticket_code TEXT NOT NULL,
    service_id INTEGER NOT NULL REFERENCES services(id),
    status TEXT NOT NULL DEFAULT 'waiting',
    window_name TEXT DEFAULT '',
    issued_at TEXT NOT NULL,
    called_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL,
    notes TEXT DEFAULT '',
    document_type TEXT DEFAULT '',
    document_number TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    full_name TEXT DEFAULT ''
    ,priority INTEGER NOT NULL DEFAULT 1
    ,deferred_until TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'operator',
    window_name TEXT DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id),
    rating INTEGER NOT NULL,
    comment TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id INTEGER,
    details TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS advertisements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    media_url TEXT NOT NULL,
    media_type TEXT NOT NULL DEFAULT 'image',
    duration_seconds INTEGER NOT NULL DEFAULT 10,
    enabled INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS display_announcements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id),
    ticket_code TEXT NOT NULL,
    window_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS display_boards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    ip_address TEXT NOT NULL UNIQUE,
    window_name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tickets_queue ON tickets(service_id, status, issued_at);
CREATE INDEX IF NOT EXISTS idx_tickets_date ON tickets(issued_at);
CREATE INDEX IF NOT EXISTS idx_display_announcements_created ON display_announcements(created_at);
CREATE INDEX IF NOT EXISTS idx_display_boards_window ON display_boards(window_name, enabled);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    iterations = 240_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_b64, digest_b64 = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(Path(DATABASE_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    from settings import ensure_directories

    ensure_directories()
    with connection() as conn:
        conn.executescript(SCHEMA)
        service_columns = {
            item["name"] for item in conn.execute("PRAGMA table_info(services)").fetchall()
        }
        prefix_added = "prefix" not in service_columns
        if prefix_added:
            conn.execute("ALTER TABLE services ADD COLUMN prefix TEXT NOT NULL DEFAULT 'A'")
            conn.execute("UPDATE services SET prefix='Р' WHERE code='REG'")
            conn.execute("UPDATE services SET prefix='К' WHERE code='CONS'")
            conn.execute("UPDATE services SET prefix='П' WHERE code='BEN'")
        ticket_columns = {
            item["name"] for item in conn.execute("PRAGMA table_info(tickets)").fetchall()
        }
        for column in ("document_type", "document_number", "phone", "full_name"):
            if column not in ticket_columns:
                conn.execute(f"ALTER TABLE tickets ADD COLUMN {column} TEXT DEFAULT ''")
        if "priority" not in ticket_columns:
            conn.execute("ALTER TABLE tickets ADD COLUMN priority INTEGER NOT NULL DEFAULT 1")
        if "deferred_until" not in ticket_columns:
            conn.execute("ALTER TABLE tickets ADD COLUMN deferred_until TEXT")
        service_columns = {
            item["name"] for item in conn.execute("PRAGMA table_info(services)").fetchall()
        }
        if "priority" not in service_columns:
            conn.execute("ALTER TABLE services ADD COLUMN priority INTEGER NOT NULL DEFAULT 1")
        if "service_time_minutes" not in service_columns:
            conn.execute("ALTER TABLE services ADD COLUMN service_time_minutes INTEGER NOT NULL DEFAULT 10")
        if conn.execute("SELECT COUNT(*) FROM services").fetchone()[0] == 0:
            timestamp = now()
            conn.executemany(
                """INSERT INTO services
                (name, short_name, code, prefix, priority, service_time_minutes, description, color, sort_order, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        "Постановка на регистрационный учет",
                        "Регистрация",
                        "REG",
                        "Р",
                        1,
                        10,
                        "Регистрация граждан в целях поиска подходящей работы",
                        "#2563eb",
                        1,
                        timestamp,
                    ),
                    (
                        "Консультация по вопросам занятости",
                        "Консультация",
                        "CONS",
                        "К",
                        1,
                        10,
                        "Консультация по услугам службы занятости",
                        "#0f766e",
                        2,
                        timestamp,
                    ),
                    (
                        "Пособие по безработице",
                        "Пособие",
                        "BEN",
                        "П",
                        2,
                        15,
                        "Назначение и перечисление пособия по безработице",
                        "#9333ea",
                        3,
                        timestamp,
                    ),
                ],
            )
        defaults = {
            "ticket_prefix": TICKET_PREFIX,
            "waiting_digits": str(TICKET_WAITING_DIGITS),
            "organization_name": "Центр занятости населения Тульской области",
            "ticket_footer": "Пожалуйста, ожидайте вызова на табло",
            "display_title": "Электронная очередь",
            "logo_url": "",
            "voice_gender": "female",
            "display_ticker": "Пожалуйста, ожидайте вызова на табло. Сохраняйте талон до завершения обслуживания.",
        }
        for key, value in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (key, value)
            )
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            conn.execute(
                "INSERT INTO users(username, password_hash, display_name, role, window_name, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("admin", hash_password(ADMIN_PASSWORD), "Главный администратор", "admin", "", now()),
            )


def rows(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with connection() as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def row(query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    with connection() as conn:
        result = conn.execute(query, params).fetchone()
        return dict(result) if result else None


def get_settings() -> dict[str, str]:
    return {item["key"]: item["value"] for item in rows("SELECT key, value FROM settings")}


def set_setting(key: str, value: str) -> None:
    with connection() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def list_services(enabled_only: bool = False) -> list[dict[str, Any]]:
    condition = "WHERE enabled = 1" if enabled_only else ""
    return rows(f"SELECT * FROM services {condition} ORDER BY sort_order, id")


def add_ticket(
    service_id: int,
    document_type: str = "",
    document_number: str = "",
    phone: str = "",
    full_name: str = "",
) -> dict[str, Any]:
    document_type = document_type.strip().lower()
    document_number = document_number.strip()
    phone = phone.strip()
    full_name = " ".join(full_name.split())
    if document_type not in {"passport", "inn", "snils"}:
        raise ValueError("Выберите тип документа")
    if not full_name:
        raise ValueError("Укажите ФИО")
    if not document_number or not phone:
        raise ValueError("Укажите номер документа и телефон")
    timestamp = now()
    with connection() as conn:
        service = conn.execute(
            "SELECT * FROM services WHERE id = ? AND enabled = 1", (service_id,)
        ).fetchone()
        if not service:
            raise ValueError("Услуга недоступна")
        number = conn.execute(
            "SELECT COALESCE(MAX(number), 0) + 1 FROM tickets "
            "WHERE service_id = ? AND date(issued_at) = date('now', 'localtime')",
            (service_id,),
        ).fetchone()[0]
        prefix = str(service["prefix"] or get_settings().get("ticket_prefix", TICKET_PREFIX)).strip().upper()
        ticket_code = f"{prefix}{int(number):0{TICKET_WAITING_DIGITS}d}"
        cursor = conn.execute(
            """INSERT INTO tickets
            (number, ticket_code, service_id, issued_at, updated_at, document_type, document_number, phone, full_name, priority)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (number, ticket_code, service_id, timestamp, timestamp, document_type, document_number, phone, full_name, service["priority"]),
        )
        ticket_id = cursor.lastrowid
    return get_ticket(ticket_id)  # type: ignore[arg-type]


def get_ticket(ticket_id: int | None = None, ticket_code: str | None = None) -> dict[str, Any] | None:
    if ticket_id is not None:
        return row(
            "SELECT t.*, s.name AS service_name, s.short_name, s.color AS service_color, s.service_time_minutes "
            "FROM tickets t JOIN services s ON s.id = t.service_id WHERE t.id = ?",
            (ticket_id,),
        )
    return row(
        "SELECT t.*, s.name AS service_name, s.short_name, s.color AS service_color, s.service_time_minutes "
        "FROM tickets t JOIN services s ON s.id = t.service_id WHERE t.ticket_code = ? "
        "ORDER BY t.id DESC LIMIT 1",
        (ticket_code,),
    )


def authenticate_user(username: str, password: str) -> dict[str, Any] | None:
    user = row("SELECT * FROM users WHERE username=? AND enabled=1", (username.strip(),))
    if user and verify_password(password, user["password_hash"]):
        return user
    return None


def audit_event(action: str, entity_type: str, entity_id: int | None = None, details: str = "", user_id: int | None = None) -> None:
    with connection() as conn:
        conn.execute(
            "INSERT INTO audit_log(user_id, action, entity_type, entity_id, details, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, action, entity_type, entity_id, details, now()),
        )


def list_audit(limit: int = 50) -> list[dict[str, Any]]:
    return rows(
        "SELECT a.*, u.username, u.display_name FROM audit_log a LEFT JOIN users u ON u.id=a.user_id "
        "ORDER BY a.id DESC LIMIT ?",
        (limit,),
    )


def list_users() -> list[dict[str, Any]]:
    return rows("SELECT id, username, display_name, role, window_name, enabled, created_at FROM users ORDER BY id")


def add_user(username: str, password: str, display_name: str, role: str, window_name: str = "") -> int:
    if not username.strip() or len(password) < 6 or not display_name.strip():
        raise ValueError("Заполните логин, имя и пароль не короче 6 символов")
    if role not in {"admin", "operator", "reports"}:
        raise ValueError("Недопустимая роль")
    if role == "operator" and not window_name.strip():
        raise ValueError("Для оператора нужно указать окно")
    with connection() as conn:
        if role == "operator" and conn.execute(
            "SELECT COUNT(*) FROM users WHERE role='operator' AND window_name=? AND enabled=1",
            (window_name.strip(),),
        ).fetchone()[0]:
            raise ValueError("Это окно уже закреплено за другим оператором")
        cursor = conn.execute(
            "INSERT INTO users(username, password_hash, display_name, role, window_name, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (username.strip(), hash_password(password), display_name.strip(), role, window_name.strip(), now()),
        )
        return int(cursor.lastrowid)


def toggle_user(user_id: int) -> None:
    with connection() as conn:
        conn.execute("UPDATE users SET enabled=CASE enabled WHEN 1 THEN 0 ELSE 1 END WHERE id=?", (user_id,))


def call_next(window_name: str) -> dict[str, Any] | None:
    timestamp = now()
    with connection() as conn:
        current = conn.execute(
            "SELECT id FROM tickets WHERE status = 'called' AND window_name = ? "
            "ORDER BY called_at DESC LIMIT 1",
            (window_name,),
        ).fetchone()
        if current:
            conn.execute(
                "UPDATE tickets SET status='completed', completed_at=?, updated_at=? WHERE id=?",
                (timestamp, timestamp, current[0]),
            )
        next_ticket = conn.execute(
            "SELECT id FROM tickets WHERE (status = 'waiting' OR (status='deferred' AND deferred_until <= ?)) "
            "ORDER BY priority DESC, issued_at, id LIMIT 1",
            (timestamp,),
        ).fetchone()
        if not next_ticket:
            return None
        conn.execute(
            "UPDATE tickets SET status='called', window_name=?, called_at=?, updated_at=?, deferred_until=NULL WHERE id=?",
            (window_name, timestamp, timestamp, next_ticket[0]),
        )
        ticket_id = next_ticket[0]
    return get_ticket(ticket_id)


def call_ticket(ticket_id: int, window_name: str) -> dict[str, Any]:
    """Вызвать выбранный талон в закреплённое окно."""
    timestamp = now()
    with connection() as conn:
        selected = conn.execute(
            "SELECT id FROM tickets WHERE id=? AND "
            "(status='waiting' OR (status='deferred' AND deferred_until <= ?))",
            (ticket_id, timestamp),
        ).fetchone()
        if not selected:
            raise ValueError("Талон уже вызван или недоступен")
        current = conn.execute(
            "SELECT id FROM tickets WHERE status='called' AND window_name=? "
            "ORDER BY called_at DESC LIMIT 1",
            (window_name,),
        ).fetchone()
        if current:
            conn.execute(
                "UPDATE tickets SET status='completed', completed_at=?, updated_at=? WHERE id=?",
                (timestamp, timestamp, current[0]),
            )
        conn.execute(
            "UPDATE tickets SET status='called', window_name=?, called_at=?, updated_at=?, deferred_until=NULL WHERE id=?",
            (window_name, timestamp, timestamp, ticket_id),
        )
    return get_ticket(ticket_id)  # type: ignore[return-value]


def repeat_announcement(ticket_id: int, window_name: str) -> dict[str, Any]:
    with connection() as conn:
        ticket = conn.execute(
            "SELECT id, ticket_code FROM tickets WHERE id=? AND status='called' AND window_name=?",
            (ticket_id, window_name),
        ).fetchone()
        if not ticket:
            raise ValueError("Талон не закреплён за вашим окном")
        cursor = conn.execute(
            "INSERT INTO display_announcements(ticket_id, ticket_code, window_name, created_at) VALUES (?, ?, ?, ?)",
            (ticket["id"], ticket["ticket_code"], window_name, now()),
        )
        announcement_id = int(cursor.lastrowid)
    return {
        "id": announcement_id,
        "ticket_id": ticket["id"],
        "ticket_code": ticket["ticket_code"],
        "window_name": window_name,
    }


def update_ticket_status(ticket_id: int, status: str, window_name: str = "") -> dict[str, Any] | None:
    if status not in {"waiting", "called", "completed", "no_show"}:
        raise ValueError("Недопустимый статус")
    timestamp = now()
    with connection() as conn:
        owned_ticket = conn.execute(
            "SELECT id FROM tickets WHERE id=? AND status='called' AND window_name=?",
            (ticket_id, window_name),
        ).fetchone()
        if not owned_ticket:
            raise ValueError("Талон не закреплён за вашим окном")
        conn.execute(
            "UPDATE tickets SET status=?, window_name=?, updated_at=?, "
            "completed_at=CASE WHEN ? IN ('completed', 'no_show') THEN ? ELSE completed_at END "
            "WHERE id=?",
            (status, window_name, timestamp, status, timestamp, ticket_id),
        )
    return get_ticket(ticket_id)


def defer_ticket(ticket_id: int, window_name: str, minutes: int = 5) -> dict[str, Any] | None:
    timestamp = now()
    with connection() as conn:
        owned_ticket = conn.execute(
            "SELECT id FROM tickets WHERE id=? AND status='called' AND window_name=?",
            (ticket_id, window_name),
        ).fetchone()
        if not owned_ticket:
            raise ValueError("Талон не закреплён за вашим окном")
        deferred_until = (datetime.now() + timedelta(minutes=max(1, minutes))).isoformat(timespec="seconds")
        conn.execute(
            "UPDATE tickets SET status='deferred', window_name='', updated_at=?, deferred_until=? WHERE id=?",
            (timestamp, deferred_until, ticket_id),
        )
    return get_ticket(ticket_id)


def redirect_ticket(ticket_id: int, service_id: int, window_name: str) -> dict[str, Any] | None:
    timestamp = now()
    with connection() as conn:
        owned_ticket = conn.execute(
            "SELECT id FROM tickets WHERE id=? AND status='called' AND window_name=?",
            (ticket_id, window_name),
        ).fetchone()
        service = conn.execute("SELECT id, priority FROM services WHERE id=? AND enabled=1", (service_id,)).fetchone()
        if not owned_ticket:
            raise ValueError("Талон не закреплён за вашим окном")
        if not service:
            raise ValueError("Услуга недоступна")
        conn.execute(
            "UPDATE tickets SET service_id=?, priority=?, status='waiting', window_name='', updated_at=?, called_at=NULL, deferred_until=NULL WHERE id=?",
            (service_id, service["priority"], timestamp, ticket_id),
        )
    return get_ticket(ticket_id)


def add_feedback(ticket_code: str, rating: int, comment: str = "") -> None:
    ticket = get_ticket(ticket_code=ticket_code)
    if not ticket:
        raise ValueError("Талон не найден")
    if ticket["status"] != "completed":
        raise ValueError("Оценить можно только завершённое обслуживание")
    if rating not in range(1, 6):
        raise ValueError("Оценка должна быть от 1 до 5")
    with connection() as conn:
        existing = conn.execute("SELECT id FROM feedback WHERE ticket_id=?", (ticket["id"],)).fetchone()
        if existing:
            raise ValueError("Оценка уже оставлена")
        conn.execute(
            "INSERT INTO feedback(ticket_id, rating, comment, created_at) VALUES (?, ?, ?, ?)",
            (ticket["id"], rating, comment.strip(), now()),
        )


def queue_snapshot() -> dict[str, Any]:
    current = rows(
        "SELECT t.*, s.short_name, s.color AS service_color FROM tickets t "
        "JOIN services s ON s.id=t.service_id WHERE t.status='called' "
        "ORDER BY t.called_at DESC LIMIT 8"
    )
    waiting_queue = rows(
        "SELECT t.id, t.ticket_code, t.issued_at, t.priority, s.short_name, "
        "s.color AS service_color FROM tickets t "
        "JOIN services s ON s.id=t.service_id WHERE t.status='waiting' "
        "ORDER BY t.priority DESC, t.issued_at, t.id LIMIT 12"
    )
    recent = rows(
        "SELECT t.*, s.short_name FROM tickets t JOIN services s ON s.id=t.service_id "
        "WHERE t.status IN ('completed', 'no_show') AND date(t.updated_at)=date('now','localtime') "
        "ORDER BY t.updated_at DESC LIMIT 8"
    )
    waiting = row("SELECT COUNT(*) AS count FROM tickets WHERE status='waiting'") or {"count": 0}
    announcement = row(
        "SELECT id, ticket_id, ticket_code, window_name, created_at "
        "FROM display_announcements ORDER BY id DESC LIMIT 1"
    )
    return {
        "current": current,
        "recent": recent,
        "waiting": waiting["count"],
        "waiting_queue": waiting_queue,
        "announcement": announcement,
    }


def operator_queue_snapshot() -> dict[str, Any]:
    snapshot = queue_snapshot()
    snapshot["waiting_queue"] = rows(
        "SELECT t.id, t.ticket_code, t.full_name, t.issued_at, t.priority, "
        "s.short_name, s.color AS service_color FROM tickets t "
        "JOIN services s ON s.id=t.service_id WHERE t.status='waiting' "
        "ORDER BY t.priority DESC, t.issued_at, t.id LIMIT 30"
    )
    return snapshot


def report_summary(date_from: str, date_to: str) -> dict[str, Any]:
    params = (date_from, date_to)
    total = row(
        "SELECT COUNT(*) AS count FROM tickets WHERE date(issued_at) BETWEEN date(?) AND date(?)",
        params,
    )
    completed = row(
        "SELECT COUNT(*) AS count FROM tickets WHERE status='completed' AND date(issued_at) BETWEEN date(?) AND date(?)",
        params,
    )
    no_show = row(
        "SELECT COUNT(*) AS count FROM tickets WHERE status='no_show' AND date(issued_at) BETWEEN date(?) AND date(?)",
        params,
    )
    average = row(
        "SELECT AVG((julianday(completed_at)-julianday(called_at))*86400) AS seconds "
        "FROM tickets WHERE status='completed' AND called_at IS NOT NULL AND completed_at IS NOT NULL "
        "AND date(issued_at) BETWEEN date(?) AND date(?)",
        params,
    )
    by_service = rows(
        "SELECT s.name, s.short_name, COUNT(t.id) AS count, "
        "SUM(CASE WHEN t.status='completed' THEN 1 ELSE 0 END) AS completed, "
        "AVG(CASE WHEN t.status='completed' AND t.called_at IS NOT NULL AND t.completed_at IS NOT NULL "
        "THEN (julianday(t.completed_at)-julianday(t.called_at))*86400 END) AS avg_seconds "
        "FROM services s LEFT JOIN tickets t ON t.service_id=s.id "
        "AND date(t.issued_at) BETWEEN date(?) AND date(?) "
        "GROUP BY s.id ORDER BY count DESC, s.sort_order",
        params,
    )
    for item in by_service:
        item["avg_seconds"] = round(item["avg_seconds"] or 0)
    return {
        "total": total["count"] if total else 0,
        "completed": completed["count"] if completed else 0,
        "no_show": no_show["count"] if no_show else 0,
        "avg_seconds": round(average["seconds"] or 0) if average else 0,
        "by_service": by_service,
    }


def report_details(date_from: str, date_to: str) -> list[dict[str, Any]]:
    details = rows(
        "SELECT t.ticket_code, s.short_name, t.window_name, t.status, t.called_at, t.completed_at, "
        "CASE WHEN t.called_at IS NOT NULL AND t.completed_at IS NOT NULL "
        "THEN ROUND((julianday(t.completed_at)-julianday(t.called_at))*86400) ELSE NULL END AS duration_seconds "
        "FROM tickets t JOIN services s ON s.id=t.service_id "
        "WHERE date(t.issued_at) BETWEEN date(?) AND date(?) "
        "ORDER BY t.issued_at DESC, t.id DESC",
        (date_from, date_to),
    )
    return details


def list_ads(enabled_only: bool = False) -> list[dict[str, Any]]:
    condition = "WHERE enabled = 1" if enabled_only else ""
    return rows(f"SELECT * FROM advertisements {condition} ORDER BY sort_order, id")


def add_ad(title: str, media_url: str, media_type: str, duration_seconds: int) -> int:
    with connection() as conn:
        cursor = conn.execute(
            """INSERT INTO advertisements
            (title, media_url, media_type, duration_seconds, created_at)
            VALUES (?, ?, ?, ?, ?)""",
            (title, media_url, media_type, max(3, duration_seconds), now()),
        )
        return int(cursor.lastrowid)


def delete_ad(ad_id: int) -> None:
    with connection() as conn:
        conn.execute("DELETE FROM advertisements WHERE id=?", (ad_id,))


def toggle_ad(ad_id: int, enabled: bool) -> None:
    with connection() as conn:
        conn.execute("UPDATE advertisements SET enabled=? WHERE id=?", (int(enabled), ad_id))


# ---------------------------------------------------------------------------
# Физические табло LETA LT-3264
# ---------------------------------------------------------------------------

def _validate_board_data(name: str, ip_address: str, window_name: str) -> tuple[str, str, str]:
    """Проверяет и нормализует данные физического табло."""
    name = " ".join(str(name or "").split())
    ip_address = str(ip_address or "").strip()
    window_name = " ".join(str(window_name or "").split())

    if not name:
        raise ValueError("Укажите название табло")
    if not window_name:
        raise ValueError("Выберите окно для табло")
    try:
        parsed_ip = ipaddress.ip_address(ip_address)
    except ValueError as exc:
        raise ValueError("Укажите корректный IP-адрес табло") from exc
    if parsed_ip.version != 4:
        raise ValueError("Для LETA укажите IPv4-адрес")

    return name, str(parsed_ip), window_name


def list_board_windows() -> list[str]:
    """Возвращает окна, которые уже используются операторами или талонами."""
    result = rows(
        """SELECT window_name FROM users
           WHERE TRIM(COALESCE(window_name, '')) <> ''
           UNION
           SELECT window_name FROM tickets
           WHERE TRIM(COALESCE(window_name, '')) <> ''
           ORDER BY window_name"""
    )
    return [str(item["window_name"]) for item in result]


def list_display_boards(enabled_only: bool = False) -> list[dict[str, Any]]:
    """Список физических табло для админки или отправки сообщений."""
    condition = "WHERE enabled = 1" if enabled_only else ""
    return rows(
        f"SELECT * FROM display_boards {condition} ORDER BY window_name, name, id"
    )


def get_display_board(board_id: int) -> dict[str, Any] | None:
    return row("SELECT * FROM display_boards WHERE id = ?", (board_id,))


def add_display_board(name: str, ip_address: str, window_name: str) -> int:
    name, ip_address, window_name = _validate_board_data(name, ip_address, window_name)
    timestamp = now()
    try:
        with connection() as conn:
            cursor = conn.execute(
                """INSERT INTO display_boards
                   (name, ip_address, window_name, enabled, created_at, updated_at)
                   VALUES (?, ?, ?, 1, ?, ?)""",
                (name, ip_address, window_name, timestamp, timestamp),
            )
            return int(cursor.lastrowid)
    except sqlite3.IntegrityError as exc:
        if "ip_address" in str(exc).lower() or "unique" in str(exc).lower():
            raise ValueError("Табло с таким IP-адресом уже добавлено") from exc
        raise


def update_display_board(board_id: int, name: str, ip_address: str, window_name: str) -> None:
    name, ip_address, window_name = _validate_board_data(name, ip_address, window_name)
    timestamp = now()
    try:
        with connection() as conn:
            cursor = conn.execute(
                """UPDATE display_boards
                   SET name = ?, ip_address = ?, window_name = ?, updated_at = ?
                   WHERE id = ?""",
                (name, ip_address, window_name, timestamp, board_id),
            )
            if cursor.rowcount == 0:
                raise ValueError("Табло не найдено")
    except sqlite3.IntegrityError as exc:
        if "ip_address" in str(exc).lower() or "unique" in str(exc).lower():
            raise ValueError("Другое табло уже использует этот IP-адрес") from exc
        raise


def delete_display_board(board_id: int) -> None:
    with connection() as conn:
        cursor = conn.execute("DELETE FROM display_boards WHERE id = ?", (board_id,))
        if cursor.rowcount == 0:
            raise ValueError("Табло не найдено")


def toggle_display_board(board_id: int, enabled: bool) -> None:
    with connection() as conn:
        cursor = conn.execute(
            "UPDATE display_boards SET enabled = ?, updated_at = ? WHERE id = ?",
            (int(bool(enabled)), now(), board_id),
        )
        if cursor.rowcount == 0:
            raise ValueError("Табло не найдено")


def list_display_boards_for_window(window_name: str) -> list[dict[str, Any]]:
    """Активные табло конкретного окна; используется отправителем UDP."""
    return rows(
        """SELECT * FROM display_boards
           WHERE enabled = 1 AND window_name = ?
           ORDER BY id""",
        (window_name,),
    )
