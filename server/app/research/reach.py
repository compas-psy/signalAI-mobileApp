"""Проба доступности источника: что именно мешает сбору.

Пустой экран разведки объясняется тремя разными вещами, и лечатся они
по-разному: правовой шлюз не пустил, сеть сервера не выпустила, или сам
источник не ответил. Снаружи все три выглядят одинаково — гипотез нет.

Различить вторую и третью нельзя одним HTTP-запросом. `TimeoutError` при
чтении означает, что TCP и TLS уже установились: сеть выпустила, молчит
источник. Сказать в этом случае «проверьте исходящий HTTPS» — отправить
владельца настраивать брандмауэр, который ни при чём. Поэтому проба идёт
в два шага: сначала соединение, потом запрос.

Шаг с соединением и есть ответ на вопрос «пускает ли сеть». Всё, что
происходит после успешного рукопожатия TLS, — уже свойство источника.
"""

from __future__ import annotations

import socket
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit

# Представляемся честно. У ФНС ссылка на пользователя данных — одно из
# условий использования открытых наборов, и анонимная проба ему противоречит.
USER_AGENT = (
    "SignalAI-Research/1.0 "
    "(+https://github.com/compas-psy/signalAI-mobileApp)"
)

#: Проба не тянет данные, а проверяет проходимость: тайм-аут короткий,
#: тело обрезано первым байтом. Долгая проба на молчащем хосте вешает
#: весь ответ, а ответ нужен целиком — иначе непонятно, что уже проверено.
TIMEOUT = 6.0


@dataclass(frozen=True, slots=True)
class HostProbe:
    """Результат по одному хосту."""

    host: str
    url: str
    #: Выпустила ли сеть сервера: TCP и TLS дошли до конца.
    network_open: bool
    #: Ответил ли источник по HTTP. Любой код — это ответ.
    answered: bool
    status: int | None
    detail: str

    @property
    def blocked_by_network(self) -> bool:
        return not self.network_open


def probe(url: str, *, timeout: float = TIMEOUT) -> HostProbe:
    """Проверить один адрес, не забирая данных."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if not host:
        return HostProbe(host, url, False, False, None, "адрес без хоста")

    resolved = _resolve(host, port)
    if resolved is not None:
        return HostProbe(host, url, False, False, None, resolved)

    connected = _connect(host, port, tls=parts.scheme == "https", timeout=timeout)
    if connected is not None:
        return HostProbe(
            host, url, False, False, None,
            f"сеть не выпустила: {connected}. "
            f"Нужен исходящий доступ с сервера к {host}:{port}",
        )

    return _request(host, url, timeout=timeout)


def _resolve(host: str, port: int) -> str | None:
    """Имя не разрешается — это не брандмауэр, а DNS или опечатка.

    Отдельная причина потому, что чинится в другом месте: правило для
    исходящих здесь не поможет.
    """
    try:
        socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as error:
        return f"имя {host} не разрешается: {error.strerror or error}"
    return None


def _connect(host: str, port: int, *, tls: bool, timeout: float) -> str | None:
    """Довести соединение до конца рукопожатия TLS.

    До этой точки всё, что может помешать, — сеть. После неё — источник.
    Ошибка сертификата тоже считается «сеть пустила»: пакеты дошли, спор
    идёт уже о доверии, и путать это с закрытым портом нельзя.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            if not tls:
                return None
            context = ssl.create_default_context()
            with context.wrap_socket(raw, server_hostname=host):
                return None
    except ssl.SSLError as error:
        # Рукопожатие не сошлось, но дошло: сеть пустила.
        return None if _handshake_reached(error) else f"TLS: {error}"
    except (TimeoutError, socket.timeout):
        return "соединение не установилось за отведённое время"
    except OSError as error:
        return f"{type(error).__name__}: {error.strerror or error}"


def _handshake_reached(error: ssl.SSLError) -> bool:
    """Ответил ли хост хоть чем-то на попытку TLS.

    `SSLCertVerificationError` и ошибки протокола означают полученный от
    хоста ответ — значит, сеть выпустила. Обрыв на середине рукопожатия
    неотличим от фильтрации, и его мы сетевой проблемой считаем.
    """
    return isinstance(error, ssl.SSLCertVerificationError) or "EOF" not in str(error)


def _request(host: str, url: str, *, timeout: float) -> HostProbe:
    """Запрос после установленного соединения.

    GET с `Range`, а не HEAD: часть государственных файловых хостов на HEAD
    не отвечает вовсе, и молчание в ответ на него — свойство метода, а не
    доступности. Один байт вместо всего файла — потому что нужен код
    ответа, а не данные.
    """
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Range": "bytes=0-0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return HostProbe(
                host, url, True, True, response.status, "источник отвечает"
            )
    except urllib.error.HTTPError as error:
        # Код может быть любым: 404 на корне каталога — это ответ, а не
        # недоступность. Вопрос пробы — дошёл ли запрос.
        return HostProbe(
            host, url, True, True, error.code,
            f"сеть пускает, источник ответил {error.code}",
        )
    except Exception as error:  # noqa: BLE001 — причина важнее типа
        return HostProbe(
            host, url, True, False, None,
            f"сеть пускает, источник не ответил ({type(error).__name__}: "
            f"{error}). Брандмауэр здесь ни при чём",
        )
