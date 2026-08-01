"""Потоковая загрузка больших выгрузок (§10.1).

Живой прогон дал размеры наборов ФНС: 104, 98, 218 и 75 мегабайт. Сервер —
восемь гигабайт, на которых уже живут база, кэш, два питоновских процесса
и модель с пределом в пять гигабайт. Прочитать такой архив в память значит
не замедлить сбор, а выселить базу.

Отсюда всё, что здесь проверяется: файл едет на диск кусками, докачка не
склеивает разные публикации, а превышение потолка обрывает загрузку — не
дав разобрать неполный архив как полный.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime

import pytest

from app.research import download
from app.research.policy import Permit

NOW = datetime(2026, 8, 1, tzinfo=UTC)


def permit(*operations: str) -> Permit:
    return Permit(
        source_id="fns_open_data",
        operations=frozenset(operations or ("fetch",)),
        issued_at=NOW,
        expires_at=NOW,
    )


class Ответ:
    def __init__(self, body: bytes, status: int = 200, headers: dict | None = None):
        self._stream = io.BytesIO(body)
        self.status = status
        self.headers = headers or {"Content-Type": "application/octet-stream"}

    def read(self, size: int) -> bytes:
        return self._stream.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def отдаёт(response, запомнить: dict | None = None):
    def opener(request, **_):
        if запомнить is not None:
            запомнить["headers"] = dict(request.headers)
        return response

    return opener


def test_файл_едет_на_диск_а_не_в_память(tmp_path):
    цель = tmp_path / "data.zip"
    result = download.stream(
        "https://file.nalog.ru/opendata/x/data.zip",
        цель,
        permit(),
        opener=отдаёт(Ответ(b"PK" + b"x" * 5000)),
    )
    assert result.ok
    assert цель.exists()
    assert цель.stat().st_size == 5002
    assert result.size == 5002
    assert result.sha256


def test_без_пропуска_загрузки_нет(tmp_path):
    """Это не соглашение, а сигнатура."""
    with pytest.raises(PermissionError):
        download.stream(
            "https://x.test/a.zip", tmp_path / "a.zip", permit("transform")
        )


def test_превышение_потолка_обрывает_загрузку(tmp_path):
    """Недокачанный архив, разобранный как полный, даёт тихо неполные
    наблюдения — это хуже, чем отсутствие наблюдений."""
    result = download.stream(
        "https://x.test/big.zip",
        tmp_path / "big.zip",
        permit(),
        max_bytes=1000,
        opener=отдаёт(Ответ(b"y" * 5000)),
    )
    assert not result.ok
    assert "больше 1000" in result.error
    assert not (tmp_path / "big.zip").exists()


def test_докачка_только_при_известной_версии(tmp_path):
    """Без сверки куски двух публикаций склеились бы в один файл.

    И битым он оказался бы не при загрузке, а при разборе — через сутки и
    в другом месте.
    """
    цель = tmp_path / "data.zip"
    частичный = цель.with_suffix(".zip.part")
    частичный.write_bytes("начало".encode())

    запомнено: dict = {}
    download.stream(
        "https://x.test/data.zip", цель, permit(),
        opener=отдаёт(Ответ("хвост".encode()), запомнено),
    )
    # Версия неизвестна — Range не отправляется, файл берётся заново.
    assert "Range" not in запомнено["headers"]

    частичный.write_bytes("начало".encode())
    запомнено.clear()
    download.stream(
        "https://x.test/data.zip", цель, permit(),
        expected_etag='"abc"',
        opener=отдаёт(Ответ("хвост".encode(), status=206), запомнено),
    )
    заголовки = {k.lower(): v for k, v in запомнено["headers"].items()}
    # Шесть кириллических букв — двенадцать байт: докачка считает байты,
    # а не символы, и это ровно тот случай, где спутать их дорого.
    assert заголовки["range"] == "bytes=12-"
    assert заголовки["if-range"] == '"abc"'


def test_ответ_200_на_докачку_начинает_заново(tmp_path):
    """Сервер вправе прислать файл целиком вместо продолжения.

    Дописать это в хвост частичного файла — получить мусор.
    """
    цель = tmp_path / "data.zip"
    цель.with_suffix(".zip.part").write_bytes("старое".encode())

    result = download.stream(
        "https://x.test/data.zip", цель, permit(),
        expected_etag='"abc"',
        opener=отдаёт(Ответ("новое-целиком".encode())),
    )
    assert result.resumed_from == 0
    assert цель.read_bytes() == "новое-целиком".encode()


def test_частичный_файл_переживает_обрыв(tmp_path):
    """Удалять его значило бы гарантировать, что стомегабайтная загрузка
    на плохом канале не завершится никогда."""

    class Рвётся(Ответ):
        def read(self, size):
            кусок = super().read(size)
            if not кусок:
                raise TimeoutError("обрыв")
            return кусок

    цель = tmp_path / "data.zip"
    result = download.stream(
        "https://x.test/data.zip", цель, permit(),
        opener=отдаёт(Рвётся(b"z" * 100)),
    )
    assert not result.ok
    assert цель.with_suffix(".zip.part").exists()
    assert not цель.exists()


def test_ошибка_источника_не_создаёт_файла(tmp_path):
    import urllib.error

    def падает(_request, **_):
        raise urllib.error.HTTPError("u", 404, "Not Found", {}, None)

    result = download.stream(
        "https://x.test/нет.zip", tmp_path / "нет.zip", permit(), opener=падает
    )
    assert result.status == 404
    assert not result.ok


def test_потолок_выше_самого_большого_набора():
    """218 мегабайт — самый большой набор ФНС на сегодня."""
    assert download.MAX_OBJECT_BYTES > 218_402_129
