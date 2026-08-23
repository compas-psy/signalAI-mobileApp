# Сборка образа движка.
#
# Непривилегированный пользователь и отсутствие компилятора в финальном
# слое — не гигиена ради гигиены: этот контейнер ходит к брокерам.

FROM python:3.11-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /srv

RUN adduser --system --group --home /srv signalai

# T-Invest API uses the Russian National CA chain.  Keep normal TLS
# verification enabled and extend Debian's system trust store with the two
# official Gosuslugi certificates.  Pin the downloaded bytes so a changed
# upstream file fails the image build instead of silently changing trust.
ARG RUSSIAN_TRUSTED_ROOT_CA_SHA256=936a43fea6e8e525bcc0f81acd9c3d21b4fc4b9b68acea7906d698005afc6504
ARG RUSSIAN_TRUSTED_SUB_CA_SHA256=f0ae589f36774f29ef3648f7984b08d42fcce6f1ffeeb6236d773daeb2744ea6
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl; \
    curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
      https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt \
      --output /tmp/russian_trusted_root_ca_pem.crt; \
    curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
      https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt \
      --output /tmp/russian_trusted_sub_ca_pem.crt; \
    echo "$RUSSIAN_TRUSTED_ROOT_CA_SHA256  /tmp/russian_trusted_root_ca_pem.crt" | sha256sum -c -; \
    echo "$RUSSIAN_TRUSTED_SUB_CA_SHA256  /tmp/russian_trusted_sub_ca_pem.crt" | sha256sum -c -; \
    install -m 0644 /tmp/russian_trusted_root_ca_pem.crt \
      /usr/local/share/ca-certificates/russian_trusted_root_ca.crt; \
    install -m 0644 /tmp/russian_trusted_sub_ca_pem.crt \
      /usr/local/share/ca-certificates/russian_trusted_sub_ca.crt; \
    update-ca-certificates; \
    rm -f /tmp/russian_trusted_root_ca_pem.crt /tmp/russian_trusted_sub_ca_pem.crt; \
    apt-get purge -y --auto-remove curl; \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir \
      "fastapi>=0.115,<1.0" "uvicorn[standard]>=0.34,<1.0" \
      "sqlalchemy>=2.0,<3.0" "alembic>=1.14,<2.0" \
      "psycopg[binary]>=3.2,<4.0" "pydantic>=2.10,<3.0" \
      "pydantic-settings>=2.7,<3.0" "pyyaml>=6.0,<7.0" \
      "numpy>=2.0,<3.0" "python-dateutil>=2.9,<3.0" "redis>=5.2,<9.0" \
      "lighter-sdk==1.1.2" \
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
