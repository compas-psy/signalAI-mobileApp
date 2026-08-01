"""Подробная проба одного адреса: где именно всё останавливается.

Обычная проба отвечает «дошло/не дошло». Этого хватило, чтобы снять
подозрение с брандмауэра, и не хватает, чтобы понять молчание файлового
сервера ФНС: read timeout одинаково выглядит и при фильтрации маршрута, и
при кривом обращении с `Range`, и при проблеме MTU внутри docker-сети.

Поэтому здесь фиксируется каждая фаза отдельно: разрешение имени,
соединение, рукопожатие, первый байт ответа, тело. Плюс то, что позволяет
сравнивать прогоны между собой — какой адрес выбран резолвером, какой
протокол, сколько шло до первого байта, какая цепочка перенаправлений.

Про HTTP/2. Его здесь нет и быть не может: `http.client` говорит только
по HTTP/1.1. Это не ограничение диагностики, а ответ на один из вопросов —
версия протокола причиной быть не может, потому что вторая версия никогда
и не использовалась.

Про `Range`. Первая проба намеренно идёт без него. Файловые серверы
ведомств обрабатывают частичный запрос по-разному, и «сервер молчит» из-за
неподдержанного заголовка неотличимо от «сервер молчит» вообще. Диапазон
проверяется отдельной пробой — как дополнение, а не как основной способ.
"""

from __future__ import annotations

import http.client
import socket
import ssl
from contextlib import contextmanager
from dataclasses import dataclass, field
from time import perf_counter
from urllib.parse import urlsplit

from .reach import USER_AGENT

#: Сколько тела читать в потоковой пробе. Смысл — убедиться, что байты
#: пошли, а не забрать выгрузку: несколько гигабайт в диагностике не нужны
#: никому, а первые килобайты отвечают на весь вопрос.
FIRST_CHUNK = 65_536

#: Перенаправлений. Цепочка важнее предела: адрес, уводящий на пять
#: хостов, — это уже ответ.
MAX_REDIRECTS = 5


@dataclass
class Detail:
    """Что произошло на каждой фазе."""

    url: str
    method: str = "GET"
    forced_ipv4: bool = False
    used_range: bool = False
    http_version: str = "HTTP/1.1"
    resolved_ip: str = ""
    status: int | None = None
    content_type: str = ""
    content_length: str = ""
    #: Миллисекунды до первого байта ответа. Долгий TTFB при быстром
    #: соединении означает, что сервер думает, а не что сеть закрыта.
    ttfb_ms: int | None = None
    connect_ms: int | None = None
    total_ms: int | None = None
    bytes_read: int = 0
    redirects: list[str] = field(default_factory=list)
    #: На какой фазе всё остановилось: dns, connect, tls, ttfb, body.
    phase: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status is not None and not self.error


@contextmanager
def _only_ipv4():
    """Заставить резолвер отдавать только IPv4.

    Не косметика: если у хоста есть AAAA, а маршрут по IPv6 у провайдера
    сломан, соединение уходит именно туда и виснет — а выглядит это как
    молчание сервера.
    """
    original = socket.getaddrinfo

    def only(host, port, family=0, *args, **kwargs):
        return original(host, port, socket.AF_INET, *args, **kwargs)

    socket.getaddrinfo = only
    try:
        yield
    finally:
        socket.getaddrinfo = original


def probe(
    url: str,
    *,
    ipv4: bool = False,
    use_range: bool = False,
    read_body: bool = True,
    timeout: float = 30.0,
) -> Detail:
    """Один адрес, все фазы поимённо."""
    if ipv4:
        with _only_ipv4():
            return _probe(url, ipv4, use_range, read_body, timeout)
    return _probe(url, ipv4, use_range, read_body, timeout)


def _probe(
    url: str, ipv4: bool, use_range: bool, read_body: bool, timeout: float
) -> Detail:
    detail = Detail(url=url, forced_ipv4=ipv4, used_range=use_range)
    started = perf_counter()
    current = url

    for _ in range(MAX_REDIRECTS + 1):
        parts = urlsplit(current)
        host = parts.hostname or ""
        port = parts.port or (443 if parts.scheme == "https" else 80)
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"

        detail.phase = "dns"
        try:
            info = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
            detail.resolved_ip = info[0][4][0]
        except OSError as error:
            detail.error = f"dns: {error}"
            return _finish(detail, started)

        detail.phase = "connect"
        connection = _connection(parts.scheme, host, port, timeout)
        try:
            mark = perf_counter()
            connection.connect()
            detail.connect_ms = int((perf_counter() - mark) * 1000)
            detail.resolved_ip = connection.sock.getpeername()[0]
        except ssl.SSLError as error:
            detail.phase = "tls"
            detail.error = f"tls: {error}"
            return _finish(detail, started)
        except OSError as error:
            detail.error = f"connect: {error}"
            return _finish(detail, started)

        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Connection": "close",
        }
        if use_range:
            headers["Range"] = "bytes=0-0"

        detail.phase = "ttfb"
        try:
            mark = perf_counter()
            connection.request("GET", path, headers=headers)
            response = connection.getresponse()
            detail.ttfb_ms = int((perf_counter() - mark) * 1000)
        except (TimeoutError, socket.timeout):
            detail.error = "ttfb: сервер не ответил за отведённое время"
            connection.close()
            return _finish(detail, started)
        except OSError as error:
            detail.error = f"ttfb: {error}"
            connection.close()
            return _finish(detail, started)

        detail.status = response.status
        detail.content_type = response.getheader("Content-Type", "") or ""
        detail.content_length = response.getheader("Content-Length", "") or ""

        location = response.getheader("Location")
        if response.status in (301, 302, 303, 307, 308) and location:
            from urllib.parse import urljoin

            current = urljoin(current, location)
            detail.redirects.append(current)
            connection.close()
            continue

        if read_body:
            detail.phase = "body"
            try:
                chunk = response.read(FIRST_CHUNK)
                detail.bytes_read = len(chunk)
            except (TimeoutError, socket.timeout):
                detail.error = "body: заголовки пришли, тело — нет"
                connection.close()
                return _finish(detail, started)
            except OSError as error:
                detail.error = f"body: {error}"
                connection.close()
                return _finish(detail, started)

        connection.close()
        detail.phase = "done"
        return _finish(detail, started)

    detail.error = f"перенаправлений больше {MAX_REDIRECTS}"
    return _finish(detail, started)


def _connection(scheme: str, host: str, port: int, timeout: float):
    if scheme == "https":
        return http.client.HTTPSConnection(
            host, port, timeout=timeout, context=ssl.create_default_context()
        )
    return http.client.HTTPConnection(host, port, timeout=timeout)


def _finish(detail: Detail, started: float) -> Detail:
    detail.total_ms = int((perf_counter() - started) * 1000)
    return detail


__all__ = ["Detail", "FIRST_CHUNK", "MAX_REDIRECTS", "probe"]
