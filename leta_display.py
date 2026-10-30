from __future__ import annotations

import logging
import socket
import struct
import threading
import time
from typing import Any

UDP_PORT = 27923
REPEAT_DELAY_SECONDS = 1.0
RECEIVE_TIMEOUT_SECONDS = 1.2

logger = logging.getLogger(__name__)


def crc16_modbus(data: bytes) -> int:
    """CRC16 Modbus, младший байт передаётся первым."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def frame(msg_num: int, payload: bytes) -> bytes:
    body = struct.pack("<H", msg_num) + payload
    core = struct.pack("<H", 2 + 2 + len(payload) + 2) + body
    return b"\x02\x02" + core + struct.pack("<H", crc16_modbus(core)) + b"\x03"


def text_payload(text: str, flags: int = 0, ha: int = 2, va: int = 2) -> bytes:
    # LETA LT-3264 использует однобайтовую кириллическую кодировку; для номера
    # талона фактически передаются только ASCII-цифры и буквы префикса.
    return bytes((0x74, flags, ha, va)) + text.encode("cp1251", errors="replace")


def send_number(ip_address: str, number: str, *, repeat: bool = True) -> dict[str, Any]:
    """Отправляет номер талона на одно табло и проверяет ответ устройства."""
    number = str(number).strip()
    if not number:
        return {"ok": False, "error": "Пустой номер талона"}
    packet = frame(1, text_payload(number))
    replies: list[bytes] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(RECEIVE_TIMEOUT_SECONDS)
            for attempt in range(2 if repeat else 1):
                sock.sendto(packet, (ip_address, UDP_PORT))
                try:
                    response, _ = sock.recvfrom(2048)
                    replies.append(response)
                except socket.timeout:
                    pass
                if repeat and attempt == 0:
                    time.sleep(REPEAT_DELAY_SECONDS)
        if replies:
            return {"ok": True, "ip_address": ip_address, "replies": len(replies)}
        return {"ok": False, "ip_address": ip_address, "error": "Табло не подтвердило получение команды"}
    except OSError as exc:
        logger.warning("Ошибка UDP-отправки на LETA %s: %s", ip_address, exc)
        return {"ok": False, "ip_address": ip_address, "error": str(exc)}


# LETA AT console: отдельный UDP-порт, команда передаётся обычным ASCII-текстом.
AT_UDP_PORT = 22224
AT_OFF_COMMAND = b"at+im=0,0,0\r\n"
BLINK_COUNT = 3
BLINK_START_DELAY_SECONDS = 1.5
BLINK_STEP_DELAY_SECONDS = 0.5
CALL_REPEAT_DELAY_SECONDS = 0.1


def _send_text_packet(sock: socket.socket, ip_address: str, number: str) -> None:
    """Одна отправка номера без дополнительного повтора."""
    packet = frame(1, text_payload(number))
    sock.sendto(packet, (ip_address, UDP_PORT))


def _send_initial_text_burst(sock: socket.socket, ip_address: str, number: str) -> None:
    """Быстро перебивает старый номер двумя пакетами нового талона."""
    packet = frame(1, text_payload(number))
    sock.sendto(packet, (ip_address, UDP_PORT))
    time.sleep(CALL_REPEAT_DELAY_SECONDS)
    sock.sendto(packet, (ip_address, UDP_PORT))


def _send_at_off(sock: socket.socket, ip_address: str) -> None:
    sock.sendto(AT_OFF_COMMAND, (ip_address, AT_UDP_PORT))


def send_call_sequence(ip_address: str, number: str, blink_count: int = BLINK_COUNT) -> dict[str, Any]:
    """Сначала даёт телевизору время запустить звуковое объявление, затем
    показывает номер и выполняет три мигания. В конце номер остаётся на табло.
    """
    number = str(number).strip()
    if not number:
        return {"ok": False, "ip_address": ip_address, "error": "Пустой номер талона"}

    sent_text = 0
    sent_off = 0
    try:
        # Звук запускается телевизионной страницей независимо от этого потока.
        # Небольшая пауза позволяет ТВ начать воспроизведение до мигания табло.
        time.sleep(BLINK_START_DELAY_SECONDS)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as text_sock, \
             socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as at_sock:
            # Сначала дважды быстро отправляем новый номер без гашения:
            # это должно сразу перебить остаточное отображение предыдущего талона.
            _send_initial_text_burst(text_sock, ip_address, number)
            _send_initial_text_burst(text_sock, ip_address, number)
            sent_text += 3
            time.sleep(BLINK_STEP_DELAY_SECONDS)

            # Затем три мигания: погасить -> один раз показать новый номер.
            for _ in range(max(1, int(blink_count))):
                _send_at_off(at_sock, ip_address)
                sent_off += 1
                time.sleep(BLINK_STEP_DELAY_SECONDS)
                _send_text_packet(text_sock, ip_address, number)
                sent_text += 1
                time.sleep(BLINK_STEP_DELAY_SECONDS)

        logger.info(
            "LETA %s: отправлена последовательность вызова %s (текст=%s, AT OFF=%s)",
            ip_address, number, sent_text, sent_off,
        )
        return {"ok": True, "ip_address": ip_address, "text_packets": sent_text, "off_commands": sent_off}
    except OSError as exc:
        logger.warning("Ошибка последовательности вызова LETA %s: %s", ip_address, exc)
        return {"ok": False, "ip_address": ip_address, "error": str(exc)}


def send_off(ip_address: str) -> dict[str, Any]:
    """Гасит одно табло AT-командой at+im=0,0,0."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            _send_at_off(sock, ip_address)
        return {"ok": True, "ip_address": ip_address}
    except OSError as exc:
        logger.warning("Не удалось погасить LETA %s: %s", ip_address, exc)
        return {"ok": False, "ip_address": ip_address, "error": str(exc)}


def _send_call_worker(board: dict[str, Any], number: str) -> None:
    ip_address = str(board["ip_address"])
    result = send_call_sequence(ip_address, number)
    if result.get("ok"):
        logger.info(
            "Вызов %s отправлен на табло %s (%s): %s миганий",
            number, board.get("name"), ip_address, result.get("off_commands"),
        )
    else:
        logger.warning(
            "Не удалось отправить вызов %s на табло %s (%s): %s",
            number, board.get("name"), ip_address, result.get("error"),
        )


def _send_off_worker(board: dict[str, Any]) -> None:
    ip_address = str(board["ip_address"])
    result = send_off(ip_address)
    if result.get("ok"):
        logger.info("Табло %s (%s) выключено", board.get("name"), ip_address)
    else:
        logger.warning("Не удалось выключить табло %s (%s): %s", board.get("name"), ip_address, result.get("error"))


def send_to_boards_async(boards: list[dict[str, Any]], number: str) -> None:
    """Запускает мигание на каждом назначенном табло в отдельном потоке."""
    for board in boards:
        thread = threading.Thread(target=_send_call_worker, args=(dict(board), str(number)), daemon=True)
        thread.start()


def turn_off_boards_async(boards: list[dict[str, Any]]) -> None:
    """Асинхронно отправляет AT OFF на все назначенные табло."""
    for board in boards:
        thread = threading.Thread(target=_send_off_worker, args=(dict(board),), daemon=True)
        thread.start()
