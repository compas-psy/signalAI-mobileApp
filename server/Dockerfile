# Сборка образа движка.
#
# Непривилегированный пользователь и отсутствие компилятора в финальном
# слое — не гигиена ради гигиены: этот контейнер ходит к брокерам.

FROM python:3.11-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /srv

RUN adduser --system --group --home /srv signalai

COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir \
      "fastapi>=0.115,<1.0" "uvicorn[standard]>=0.34,<1.0" \
      "sqlalchemy>=2.0,<3.0" "alembic>=1.14,<2.0" \
      "psycopg[binary]>=3.2,<4.0" "pydantic>=2.10,<3.0" \
      "pydantic-settings>=2.7,<3.0" "pyyaml>=6.0,<7.0" \
      "numpy>=2.0,<3.0" "python-dateutil>=2.9,<3.0" "redis>=5.2,<9.0" \
      "pillow>=11.0,<12.0" "qrcode>=8.2,<9.0"

COPY alembic.ini ./
COPY alembic ./alembic
COPY config ./config
COPY app ./app

USER signalai
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
