from __future__ import annotations

from typing import Any

from settings import AUTO_PRINT, ORGANIZATION_NAME, PRINTER_NAME


def ticket_text(ticket: dict[str, Any], settings: dict[str, str]) -> str:
    footer = settings.get("ticket_footer", "Пожалуйста, ожидайте вызова на табло")
    organization = settings.get("organization_name", ORGANIZATION_NAME)
    return (
        f"{organization}\n"
        f"\n"
        f"ВАШ ТАЛОН\n"
        f"\n"
        f"{ticket['ticket_code']}\n"
        f"\n"
        f"{ticket['service_name']}\n"
        f"\n"
        f"{footer}\n"
        f"\n"
        f"{ticket['issued_at']}\n"
    )


def print_ticket(ticket: dict[str, Any], settings: dict[str, str]) -> tuple[bool, str]:
    """Печать на Windows-принтер через RAW; при недоступном драйвере возвращает понятную причину."""
    if not (AUTO_PRINT or PRINTER_NAME):
        return False, "Автопечать выключена"
    try:
        import win32print  # type: ignore

        printer = PRINTER_NAME or win32print.GetDefaultPrinter()
        handle = win32print.OpenPrinter(printer)
        try:
            job = win32print.StartDocPrinter(handle, 1, ("Талон электронной очереди", None, "RAW"))
            win32print.StartPagePrinter(handle)
            payload = ticket_text(ticket, settings).encode("cp866", errors="replace")
            win32print.WritePrinter(handle, payload)
            win32print.EndPagePrinter(handle)
            win32print.EndDocPrinter(handle)
        finally:
            win32print.ClosePrinter(handle)
        return True, f"Талон отправлен на принтер «{printer}»"
    except ImportError:
        return False, "Для прямой печати установите pywin32"
    except Exception as exc:  # pragma: no cover - зависит от драйвера Windows
        return False, f"Ошибка печати: {exc}"
