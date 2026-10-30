from __future__ import annotations

import secrets
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import db
import leta_display
from printer import print_ticket
from settings import (
    APP_NAME,
    BROWSER_AUTO_PRINT,
    HOST,
    MEDIA_DIR,
    ORGANIZATION_NAME,
    PORT,
    ensure_directories,
)


BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
ensure_directories()
app = FastAPI(title=APP_NAME)
app.mount("/static", StaticFiles(directory=BUNDLE_DIR / "static"), name="static")
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")
app.mount("/sounds", StaticFiles(directory=BUNDLE_DIR / "sounds"), name="sounds")
templates = Jinja2Templates(directory=BUNDLE_DIR / "templates")
sessions: dict[str, dict[str, object]] = {}


@app.on_event("startup")
def startup() -> None:
    db.init_db()


def _send_ticket_to_window(ticket: dict[str, object], window_name: str) -> None:
    """Запускает отправку в фоне: сеть табло не задерживает ответ АРМ."""
    ticket_code = str(ticket.get("ticket_code", "")).strip()
    if not ticket_code:
        return
    try:
        boards = db.list_display_boards_for_window(window_name)
        leta_display.send_to_boards_async(boards, ticket_code)
    except Exception:
        # Ошибка физического табло не должна влиять на работу электронной очереди.
        import logging
        logging.exception("Не удалось запустить отправку на табло для окна %s", window_name)


def context(request: Request, **extra: object) -> dict[str, object]:
    settings = db.get_settings()
    return {
        "request": request,
        "app_name": APP_NAME,
        "organization_name": settings.get("organization_name", ORGANIZATION_NAME),
        "settings": settings,
        "browser_auto_print": BROWSER_AUTO_PRINT,
        "current_user": current_user(request),
        **extra,
    }


def current_user(request: Request) -> dict[str, object] | None:
    return sessions.get(request.cookies.get("session_token", ""))


def has_role(request: Request, *roles: str) -> bool:
    user = current_user(request)
    return bool(user and (user.get("role") in roles))


def redirect_login() -> RedirectResponse:
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return RedirectResponse("/kiosk", status_code=303)


@app.get("/kiosk", response_class=HTMLResponse)
def kiosk(request: Request):
    return templates.TemplateResponse(
        "kiosk.html", context(request, services=db.list_services(enabled_only=True))
    )


@app.post("/api/tickets")
def create_ticket(
    service_id: int,
    request: Request,
    document_type: str = "",
    document_number: str = "",
    phone: str = "",
    full_name: str = "",
):
    try:
        ticket = db.add_ticket(service_id, document_type, document_number, phone, full_name)
        db.audit_event("Выдача талона", "ticket", ticket["id"], ticket["ticket_code"])
        printed, print_message = print_ticket(ticket, db.get_settings())
        return {"ok": True, "ticket": ticket, "printed": printed, "print_message": print_message}
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@app.get("/ticket/{ticket_code}", response_class=HTMLResponse)
def ticket_page(request: Request, ticket_code: str):
    ticket = db.get_ticket(ticket_code=ticket_code)
    if not ticket:
        return templates.TemplateResponse("not_found.html", context(request), status_code=404)
    return templates.TemplateResponse("ticket.html", context(request, ticket=ticket))


@app.get("/operator", response_class=HTMLResponse)
def operator(request: Request):
    if not has_role(request, "operator"):
        return redirect_login()
    user = current_user(request)
    return templates.TemplateResponse("operator.html", context(request, operator_window=user["window_name"], services=db.list_services(enabled_only=True)))


@app.get("/api/queue")
def queue_api():
    return db.queue_snapshot()


@app.get("/api/operator/queue")
def operator_queue_api(request: Request):
    if not has_role(request, "operator"):
        return JSONResponse({"ok": False, "error": "Нет доступа"}, status_code=403)
    return db.operator_queue_snapshot()


@app.post("/api/operator/next")
def operator_next(request: Request):
    if not has_role(request, "operator"):
        return JSONResponse({"ok": False, "error": "Нет доступа"}, status_code=403)
    user = current_user(request)
    ticket = db.call_next(str(user["window_name"]))
    if ticket:
        db.audit_event("Вызов талона", "ticket", ticket["id"], str(user["window_name"]), int(user["id"]))
        _send_ticket_to_window(ticket, str(user["window_name"]))
    return {"ok": True, "ticket": ticket}


@app.post("/api/operator/call")
def operator_call(request: Request, ticket_id: int):
    if not has_role(request, "operator"):
        return JSONResponse({"ok": False, "error": "Нет доступа"}, status_code=403)
    user = current_user(request)
    try:
        ticket = db.call_ticket(ticket_id, str(user["window_name"]))
        db.audit_event("Вызов выбранного талона", "ticket", ticket["id"], str(user["window_name"]), int(user["id"]))
        _send_ticket_to_window(ticket, str(user["window_name"]))
        return {"ok": True, "ticket": ticket}
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@app.post("/api/operator/repeat")
def operator_repeat(request: Request, ticket_id: int):
    if not has_role(request, "operator"):
        return JSONResponse({"ok": False, "error": "Нет доступа"}, status_code=403)
    user = current_user(request)
    try:
        announcement = db.repeat_announcement(ticket_id, str(user["window_name"]))
        db.audit_event("Повторный вызов талона", "ticket", ticket_id, str(user["window_name"]), int(user["id"]))
        _send_ticket_to_window({"ticket_code": announcement["ticket_code"]}, str(user["window_name"]))
        return {"ok": True, "announcement": announcement}
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@app.post("/api/operator/status")
def operator_status(request: Request, ticket_id: int, status: str):
    if not has_role(request, "operator"):
        return JSONResponse({"ok": False, "error": "Нет доступа"}, status_code=403)
    user = current_user(request)
    try:
        ticket = db.update_ticket_status(ticket_id, status, str(user["window_name"]))
        db.audit_event("Изменение статуса: " + status, "ticket", ticket_id, str(user["window_name"]), int(user["id"]))
        # При завершении приёма или отметке «Не явился» гасим табло окна оператора.
        if status in {"completed", "no_show"}:
            try:
                boards = db.list_display_boards_for_window(str(user["window_name"]))
                leta_display.turn_off_boards_async(boards)
            except Exception:
                import logging
                logging.exception("Не удалось запустить выключение табло для окна %s (статус %s)", user["window_name"], status)
        return {"ok": True, "ticket": ticket}
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@app.post("/api/operator/defer")
def operator_defer(request: Request, ticket_id: int, minutes: int = 5):
    if not has_role(request, "operator"):
        return JSONResponse({"ok": False, "error": "Нет доступа"}, status_code=403)
    user = current_user(request)
    try:
        ticket = db.defer_ticket(ticket_id, str(user["window_name"]), minutes)
        db.audit_event("Отложен приём", "ticket", ticket_id, f"{minutes} мин", int(user["id"]))
        return {"ok": True, "ticket": ticket}
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@app.post("/api/operator/redirect")
def operator_redirect(request: Request, ticket_id: int, service_id: int):
    if not has_role(request, "operator"):
        return JSONResponse({"ok": False, "error": "Нет доступа"}, status_code=403)
    user = current_user(request)
    try:
        ticket = db.redirect_ticket(ticket_id, service_id, str(user["window_name"]))
        db.audit_event("Переадресован талон", "ticket", ticket_id, f"услуга {service_id}", int(user["id"]))
        return {"ok": True, "ticket": ticket}
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@app.get("/display", response_class=HTMLResponse)
def display(request: Request):
    return templates.TemplateResponse("display.html", context(request))


@app.get("/api/ads")
def ads_api():
    return db.list_ads(enabled_only=True)


@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request):
    if not has_role(request, "admin"):
        return redirect_login()
    return templates.TemplateResponse(
        "admin.html",
        context(
            request,
            services=db.list_services(),
            ads=db.list_ads(),
            users=db.list_users(),
            audit=db.list_audit(),
            boards=db.list_display_boards(),
            windows=db.list_board_windows(),
        ),
    )


@app.get("/login", response_class=HTMLResponse)
def login(request: Request):
    return templates.TemplateResponse("login.html", context(request))


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login(request: Request):
    return RedirectResponse("/login", status_code=303)


@app.post("/login")
def login_post(username: Annotated[str, Form()], password: Annotated[str, Form()]):
    user = db.authenticate_user(username, password)
    if user:
        token = secrets.token_urlsafe(24)
        sessions[token] = user
        target = {"admin": "/admin", "operator": "/operator", "reports": "/reports"}[user["role"]]
        response = RedirectResponse(target, status_code=303)
        response.set_cookie("session_token", token, httponly=True, samesite="lax")
        return response
    return RedirectResponse("/login?error=1", status_code=303)


@app.get("/logout")
def logout(request: Request):
    sessions.pop(request.cookies.get("session_token", ""), None)
    response = RedirectResponse("/kiosk", status_code=303)
    response.delete_cookie("session_token")
    return response


@app.post("/admin/services")
def service_create(
    request: Request,
    name: Annotated[str, Form()],
    short_name: Annotated[str, Form()],
    code: Annotated[str, Form()],
    prefix: Annotated[str, Form()] = "А",
    priority: Annotated[int, Form()] = 1,
    service_time_minutes: Annotated[int, Form()] = 10,
    description: Annotated[str, Form()] = "",
    color: Annotated[str, Form()] = "#2563eb",
):
    if not has_role(request, "admin"):
        return redirect_login()
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO services(name, short_name, code, prefix, priority, service_time_minutes, description, color, sort_order, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, (SELECT COALESCE(MAX(sort_order),0)+1 FROM services), ?)",
            (name, short_name, code.upper(), prefix.strip().upper()[:4], max(0, min(2, priority)), max(1, service_time_minutes), description, color, db.now()),
        )
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/services/{service_id}/toggle")
def service_toggle(request: Request, service_id: int):
    if not has_role(request, "admin"):
        return redirect_login()
    with db.connection() as conn:
        conn.execute("UPDATE services SET enabled = CASE enabled WHEN 1 THEN 0 ELSE 1 END WHERE id=?", (service_id,))
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/ads")
async def ad_create(
    request: Request,
    title: Annotated[str, Form()],
    duration_seconds: Annotated[int, Form()] = 10,
    media_url: Annotated[str, Form()] = "",
    media_file: UploadFile | None = File(None),
):
    if not has_role(request, "admin"):
        return redirect_login()
    final_url = media_url.strip()
    media_type = "video" if final_url.lower().endswith((".mp4", ".webm", ".mov")) else "image"
    if media_file and media_file.filename:
        safe_name = Path(media_file.filename).name.replace(" ", "_")
        target = MEDIA_DIR / safe_name
        target.write_bytes(await media_file.read())
        final_url = f"/media/{safe_name}"
        media_type = "video" if target.suffix.lower() in {".mp4", ".webm", ".mov"} else "image"
    if final_url:
        db.add_ad(title, final_url, media_type, duration_seconds)
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/ads/{ad_id}/delete")
def ad_delete(request: Request, ad_id: int):
    if not has_role(request, "admin"):
        return redirect_login()
    db.delete_ad(ad_id)
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/boards")
def board_create(
    request: Request,
    name: Annotated[str, Form()],
    ip_address: Annotated[str, Form()],
    window_name: Annotated[str, Form()],
):
    if not has_role(request, "admin"):
        return redirect_login()
    try:
        board_id = db.add_display_board(name, ip_address, window_name)
        db.audit_event("Добавлено физическое табло", "display_board", board_id, f"{name}: {ip_address}, {window_name}")
    except ValueError as exc:
        return RedirectResponse("/admin?board_error=" + str(exc).replace(" ", "%20"), status_code=303)
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/boards/{board_id}/update")
def board_update(
    request: Request,
    board_id: int,
    name: Annotated[str, Form()],
    ip_address: Annotated[str, Form()],
    window_name: Annotated[str, Form()],
):
    if not has_role(request, "admin"):
        return redirect_login()
    try:
        db.update_display_board(board_id, name, ip_address, window_name)
        db.audit_event("Изменено физическое табло", "display_board", board_id, f"{name}: {ip_address}, {window_name}")
    except ValueError as exc:
        return RedirectResponse("/admin?board_error=" + str(exc).replace(" ", "%20"), status_code=303)
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/boards/{board_id}/toggle")
def board_toggle(request: Request, board_id: int):
    if not has_role(request, "admin"):
        return redirect_login()
    board = db.get_display_board(board_id)
    if not board:
        return RedirectResponse("/admin?board_error=Табло%20не%20найдено", status_code=303)
    db.toggle_display_board(board_id, not bool(board["enabled"]))
    db.audit_event("Изменён статус физического табло", "display_board", board_id, str(board["ip_address"]))
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/boards/{board_id}/delete")
def board_delete(request: Request, board_id: int):
    if not has_role(request, "admin"):
        return redirect_login()
    try:
        db.delete_display_board(board_id)
        db.audit_event("Удалено физическое табло", "display_board", board_id, "")
    except ValueError as exc:
        return RedirectResponse("/admin?board_error=" + str(exc).replace(" ", "%20"), status_code=303)
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/boards/{board_id}/test")
def board_test(request: Request, board_id: int):
    if not has_role(request, "admin"):
        return redirect_login()
    board = db.get_display_board(board_id)
    if not board:
        return RedirectResponse("/admin?board_error=Табло%20не%20найдено", status_code=303)
    # Тестовая команда также содержит только цифры — без служебного текста.
    result = leta_display.send_number(str(board["ip_address"]), "8888")
    details = "Успешно" if result.get("ok") else str(result.get("error", "Нет подтверждения"))
    db.audit_event("Тест физического табло", "display_board", board_id, details)
    return RedirectResponse("/admin?board_test=" + ("ok" if result.get("ok") else "error"), status_code=303)


@app.get("/feedback/{ticket_code}", response_class=HTMLResponse)
def feedback_page(request: Request, ticket_code: str):
    return templates.TemplateResponse("feedback.html", context(request, ticket=db.get_ticket(ticket_code=ticket_code)))


@app.post("/feedback/{ticket_code}")
def feedback_submit(request: Request, ticket_code: str, rating: int, comment: str = ""):
    try:
        db.add_feedback(ticket_code, rating, comment)
        return RedirectResponse(f"/feedback/{ticket_code}?sent=1", status_code=303)
    except ValueError as exc:
        return RedirectResponse(f"/feedback/{ticket_code}?error={str(exc)}", status_code=303)


@app.get("/reports", response_class=HTMLResponse)
def reports(request: Request, date_from: str | None = None, date_to: str | None = None):
    if not has_role(request, "admin", "reports"):
        return redirect_login()
    end = date_to or date.today().isoformat()
    start = date_from or (date.today() - timedelta(days=6)).isoformat()
    return templates.TemplateResponse(
        "reports.html",
        context(request, report=db.report_summary(start, end), details=db.report_details(start, end), date_from=start, date_to=end),
    )


@app.post("/admin/settings")
async def settings_update(
    request: Request,
    organization_name: Annotated[str, Form()],
    ticket_footer: Annotated[str, Form()],
    display_title: Annotated[str, Form()],
    logo_url: Annotated[str, Form()] = "",
    voice_gender: Annotated[str, Form()] = "female",
    display_ticker: Annotated[str, Form()] = "",
    logo_file: UploadFile | None = File(None),
):
    if not has_role(request, "admin"):
        return redirect_login()
    final_logo_url = logo_url.strip()
    if logo_file and logo_file.filename:
        allowed_extensions = {".png", ".jpg", ".jpeg", ".webp", ".svg"}
        extension = Path(logo_file.filename).suffix.lower()
        if extension in allowed_extensions:
            target = MEDIA_DIR / f"organization-logo{extension}"
            target.write_bytes(await logo_file.read())
            final_logo_url = f"/media/{target.name}"
    db.set_setting("organization_name", organization_name)
    db.set_setting("ticket_footer", ticket_footer)
    db.set_setting("display_title", display_title)
    db.set_setting("logo_url", final_logo_url)
    db.set_setting("voice_gender", voice_gender if voice_gender in {"male", "female"} else "female")
    db.set_setting("display_ticker", display_ticker.strip())
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/users")
def user_create(
    request: Request,
    username: Annotated[str, Form()],
    password: Annotated[str, Form()],
    display_name: Annotated[str, Form()],
    role: Annotated[str, Form()],
    window_name: Annotated[str, Form()] = "",
):
    if not has_role(request, "admin"):
        return redirect_login()
    try:
        db.add_user(username, password, display_name, role, window_name)
    except ValueError as exc:
        return RedirectResponse("/admin?user_error=" + str(exc), status_code=303)
    except Exception:
        return RedirectResponse("/admin?user_error=Пользователь%20с%20таким%20логином%20уже%20есть", status_code=303)
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/users/{user_id}/toggle")
def user_toggle(request: Request, user_id: int):
    if not has_role(request, "admin"):
        return redirect_login()
    db.toggle_user(user_id)
    return RedirectResponse("/admin", status_code=303)


if __name__ == "__main__":
    import uvicorn

    # Передаём объект приложения напрямую: это совместимо и с обычным Python,
    # и с однофайловой сборкой PyInstaller, где модуль app находится внутри EXE.
    uvicorn.run(app, host=HOST, port=PORT, reload=False)
