"""Потоковая загрузка больших выгрузок (§10.1, MAX_INLINE_BYTES).

Цифры из живого прогона: 104, 98, 218 и 75 мегабайт. Сервер — восемь
гигабайт, на которых уже живут Postgres, Redis, два питоновских процесса и
модель с пределом в пять гигабайт. Прочитать такой архив в память значит
не «замедлить сбор», а выселить базу.

Поэтому здесь ничего не возвращается байтами. Файл едет на диск кусками,
и дальше с ним работают как с файлом: `zipfile` умеет читать члены архива
по одному, не разворачивая его целиком.

Про докачку. Разрыв на сто двадцатом мегабайте при обычной загрузке
означает начать сначала — и так по кругу, пока не повезёт. Файловый хост
ФНС отвечает 206 на `Range`, это проверено прогоном, поэтому продолжение с
места обрыва здесь есть. Возобновление опирается на `ETag`: без сверки
докачка склеила бы куски двух разных публикаций в один файл, и архив
оказался бы битым не в момент загрузки, а в момент разбора.
"""

from __future__ import annotations

import hashlib
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .policy import Permit
from .reach import USER_AGENT

#: Кусок чтения. Не настройка производительности: при меньшем размере
#: заметно растут накладные расходы, при большем — пик памяти.
CHUNK = 1024 * 1024

#: Потолок на объект. Двести восемнадцать мегабайт — самый большой набор
#: ФНС сегодня; запас держит от сюрприза, а не от роста.
MAX_OBJECT_BYTES = 1024 * 1024 * 1024

#: Загрузка идёт минутами. Таймаут здесь — на бездействие сокета, а не на
#: всю передачу: сто мегабайт по обычному каналу законно едут дольше.
IDLE_TIMEOUT = 60.0


@dataclass
class Downloaded:
    """Что приехало на диск."""

    url: str
    status: int
    path: Path | None
    size: int = 0
    sha256: str = ""
    content_type: str = ""
    etag: str = ""
    resumed_from: int = 0
    requested_at: datetime | None = None
    responded_at: datetime | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.path is not None and not self.error


class TooLarge(RuntimeError):
    """Объект больше потолка.

    Отдельный тип: недокачанный файл, разобранный как полный, даёт тихо
    неполные наблюдения — а это хуже, чем отсутствие наблюдений.
    """


def stream(
    url: str,
    destination: Path,
    permit: Permit,
    *,
    expected_etag: str = "",
    max_bytes: int = MAX_OBJECT_BYTES,
    timeout: float = IDLE_TIMEOUT,
    opener=None,
) -> Downloaded:
    """Забрать объект на диск, продолжив с места обрыва, если можно."""
    if "fetch" not in permit.operations:
        raise PermissionError(f"{permit.source_id}: пропуск не даёт права на загрузку")

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    already = partial.stat().st_size if partial.exists() else 0

    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if already and expected_etag:
        # Докачка только при известной версии. Иначе куски двух разных
        # публикаций склеятся в один файл, и битым он окажется не при
        # загрузке, а при разборе — через сутки и в другом месте.
        headers["Range"] = f"bytes={already}-"
        headers["If-Range"] = expected_etag
    else:
        already = 0

    result = Downloaded(url=url, status=0, path=None, requested_at=datetime.now(UTC))
    request = urllib.request.Request(url, headers=headers, method="GET")
    open_url = opener or urllib.request.urlopen

    try:
        with open_url(request, timeout=timeout) as response:
            result.status = response.status
            result.content_type = response.headers.get("Content-Type", "") or ""
            result.etag = response.headers.get("ETag", "") or ""
            дописываем = response.status == 206 and already > 0
            result.resumed_from = already if дописываем else 0
            if not дописываем:
                already = 0

            digest = hashlib.sha256()
            if дописываем and partial.exists():
                with partial.open("rb") as prior:
                    for кусок in iter(lambda: prior.read(CHUNK), b""):
                        digest.update(кусок)

            written = already
            with partial.open("ab" if дописываем else "wb") as sink:
                while True:
                    кусок = response.read(CHUNK)
                    if not кусок:
                        break
                    written += len(кусок)
                    if written > max_bytes:
                        raise TooLarge(
                            f"{url}: больше {max_bytes} байт — загрузка прервана"
                        )
                    digest.update(кусок)
                    sink.write(кусок)

            result.size = written
            result.sha256 = digest.hexdigest()
    except urllib.error.HTTPError as error:
        result.status = error.code
        result.error = f"источник ответил {error.code}"
        return _stamp(result)
    except TooLarge as error:
        result.error = str(error)
        return _stamp(result)
    except Exception as error:  # noqa: BLE001 — причина уедет в журнал
        # Частичный файл намеренно остаётся: следующая попытка продолжит с
        # него. Удалить его значило бы гарантировать, что стомегабайтная
        # загрузка на плохом канале не завершится никогда.
        result.error = f"{type(error).__name__}: {error}"
        return _stamp(result)

    partial.replace(destination)
    result.path = destination
    return _stamp(result)


def _stamp(result: Downloaded) -> Downloaded:
    result.responded_at = datetime.now(UTC)
    return result


__all__ = ["CHUNK", "Downloaded", "IDLE_TIMEOUT", "MAX_OBJECT_BYTES", "TooLarge", "stream"]
