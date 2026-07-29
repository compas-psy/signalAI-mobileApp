#!/usr/bin/env bash
#
# Развёртывание SignalAI на чистой Ubuntu 22.04/24.04.
#
#   git clone https://github.com/compas-psy/signalAI-mobileApp.git
#   cd signalAI-mobileApp/server
#   sudo bash deploy/bootstrap.sh
#
# Что делает: пакеты, Docker, firewall, TLS, база, миграции, автозапуск,
# ежедневный бэкап с проверкой восстановления.
#
# Чего НЕ делает намеренно:
#   * не включает боевую торговлю — paper_only остаётся true;
#   * не спрашивает ключи брокеров при первом запуске: сервер сначала должен
#     научиться считать идеи, и только потом получать доступ к деньгам;
#   * не пишет ни одного секрета в репозиторий и не печатает их на экран.
#
# Скрипт идемпотентен: повторный запуск не ломает уже настроенное.

set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_DIR=/etc/signalai
ENV_FILE="${ENV_DIR}/.env"
BACKUP_DIR=/var/backups/signalai
SERVICE=signalai

say()  { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "нужен root: sudo bash deploy/bootstrap.sh"

# ── Что спросить у владельца ─────────────────────────────────────────────
#
# Домен нужен для TLS. Без него сервер поднимется на localhost, но телефон
# к нему не подключится: приложение ходит только по HTTPS.

DOMAIN="${SIGNALAI_DOMAIN:-}"
ADMIN_IP="${SIGNALAI_ADMIN_IP:-}"
EMAIL="${SIGNALAI_ACME_EMAIL:-}"

if [ -z "$DOMAIN" ]; then
  read -rp "Домен сервера (например signalai.example.com), пусто — без TLS: " DOMAIN
fi
if [ -z "$ADMIN_IP" ]; then
  CURRENT_SSH_IP="$(who am i 2>/dev/null | awk -F'[()]' '{print $2}' || true)"
  read -rp "IP, с которого вы ходите по SSH [${CURRENT_SSH_IP:-не определён}]: " ADMIN_IP
  ADMIN_IP="${ADMIN_IP:-$CURRENT_SSH_IP}"
fi
[ -n "$ADMIN_IP" ] || die "без вашего IP закрывать SSH нельзя — вы потеряете доступ к серверу"

# ── Пакеты ────────────────────────────────────────────────────────────────

say "Системные пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
  ca-certificates curl gnupg ufw fail2ban unattended-upgrades \
  postgresql-client jq >/dev/null
ok "базовые пакеты"

if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin \
    docker-compose-plugin >/dev/null
fi
systemctl enable --now docker >/dev/null 2>&1 || true
ok "docker $(docker --version | awk '{print $3}' | tr -d ,)"

# ── Секреты ───────────────────────────────────────────────────────────────
#
# Пароль базы генерируется здесь и никогда не показывается: его не нужно
# знать человеку, он нужен только контейнерам.

say "Секреты"
install -d -m 0700 "$ENV_DIR"
if [ -f "$ENV_FILE" ]; then
  ok "$ENV_FILE уже существует — оставляю как есть"
else
  POSTGRES_PASSWORD="$(head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 40)"
  DEVICE_TOKEN="$(head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 43)"
  cat > "$ENV_FILE" <<EOF
# Создан bootstrap $(date -u +%Y-%m-%dT%H:%M:%SZ). Права 0600, владелец root.
# В git этот файл не попадает никогда.
POSTGRES_USER=signalai
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
POSTGRES_DB=signalai

# Токен устройства: его нужно передать в сборку приложения через
#   --dart-define=SIGNALAI_DEVICE_TOKEN=...
SIGNALAI_DEVICE_TOKEN=${DEVICE_TOKEN}

# Ключи брокеров добавляются ПОЗЖЕ, когда сервер докажет, что считает верно.
# Право на вывод средств не выдаётся никогда (§19.3).
BYBIT_READ_KEY=
BYBIT_READ_SECRET=
BYBIT_EXEC_KEY=
BYBIT_EXEC_SECRET=
TINVEST_INVEST_READ_TOKEN=
TINVEST_TRADE_READ_TOKEN=
TINVEST_TRADE_EXEC_TOKEN=
TINVEST_SANDBOX_TOKEN=
EOF
  chmod 0600 "$ENV_FILE"
  ok "создан $ENV_FILE (0600, root)"
  warn "токен устройства лежит в $ENV_FILE — он понадобится при сборке приложения"
fi

# ── Firewall ──────────────────────────────────────────────────────────────
#
# Default-deny. SSH только с адреса владельца, наружу — 80/443 для TLS.
# Postgres и Redis не публикуются вовсе: к ним можно только из docker-сети.

say "Firewall"
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow from "$ADMIN_IP" to any port 22 proto tcp comment "SSH владельца" >/dev/null
if [ -n "$DOMAIN" ]; then
  ufw allow 80/tcp comment "ACME" >/dev/null
  ufw allow 443/tcp comment "HTTPS" >/dev/null
fi
ufw --force enable >/dev/null
ok "открыты: 22 только с ${ADMIN_IP}$([ -n "$DOMAIN" ] && echo ', 80/443')"

say "Защита доступа"
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
if sshd -t 2>/dev/null; then
  systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
  ok "вход только по ключу, root-логин запрещён"
else
  warn "конфигурация sshd не прошла проверку — оставлена прежней"
fi
systemctl enable --now fail2ban >/dev/null 2>&1 || true
dpkg-reconfigure -f noninteractive unattended-upgrades >/dev/null 2>&1 || true
ok "fail2ban и автообновления безопасности включены"

# ── Приложение ────────────────────────────────────────────────────────────

say "Сборка и запуск"
cd "$APP_DIR"
ln -sf "$ENV_FILE" "$APP_DIR/.env"
docker compose --env-file "$ENV_FILE" up -d --build postgres redis
ok "база и кэш подняты"

# Миграции — отдельным шагом до старта API: сервер не должен стартовать на
# схеме, которой ещё нет.
docker compose --env-file "$ENV_FILE" run --rm api alembic upgrade head
ok "миграции применены"

docker compose --env-file "$ENV_FILE" up -d --build api
ok "API поднят на 127.0.0.1:8000"

# ── TLS ───────────────────────────────────────────────────────────────────

if [ -n "$DOMAIN" ]; then
  say "TLS для $DOMAIN"
  apt-get install -y -qq --no-install-recommends nginx certbot python3-certbot-nginx >/dev/null
  cat > /etc/nginx/sites-available/signalai <<NGINX
server {
    listen 80;
    server_name ${DOMAIN};

    # Приложение ходит только по HTTPS; здесь остаётся лишь ACME и редирект.
    location /.well-known/acme-challenge/ { root /var/www/html; }
    location / { return 301 https://\$host\$request_uri; }
}
server {
    listen 443 ssl;
    server_name ${DOMAIN};

    client_max_body_size 2m;
    proxy_read_timeout 30s;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-Proto https;
    }
}
NGINX
  ln -sf /etc/nginx/sites-available/signalai /etc/nginx/sites-enabled/signalai
  rm -f /etc/nginx/sites-enabled/default
  if [ -n "$EMAIL" ]; then
    certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --redirect \
      || warn "certbot не выдал сертификат — проверьте, что домен указывает на этот сервер"
  else
    warn "почта для ACME не задана; выпустите сертификат вручную: certbot --nginx -d $DOMAIN"
  fi
  nginx -t && systemctl reload nginx
  ok "nginx настроен"
else
  warn "домен не задан: API доступен только на 127.0.0.1:8000, телефон не подключится"
fi

# ── Автозапуск и бэкапы ───────────────────────────────────────────────────

say "Автозапуск"
cat > /etc/systemd/system/${SERVICE}.service <<UNIT
[Unit]
Description=SignalAI engine
Requires=docker.service
After=docker.service network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${APP_DIR}
ExecStart=/usr/bin/docker compose --env-file ${ENV_FILE} up -d
ExecStop=/usr/bin/docker compose --env-file ${ENV_FILE} down

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable ${SERVICE} >/dev/null
ok "сервис поднимается после перезагрузки"

say "Резервное копирование"
install -d -m 0700 "$BACKUP_DIR"
cat > /usr/local/bin/signalai-backup <<'BACKUP'
#!/usr/bin/env bash
# Журнал по §24 хранится бессрочно, поэтому бэкап проверяется восстановлением:
# копия, которую ни разу не разворачивали, — это надежда, а не бэкап.
set -euo pipefail
ENV_FILE=/etc/signalai/.env
BACKUP_DIR=/var/backups/signalai
. "$ENV_FILE"
STAMP=$(date -u +%Y%m%d-%H%M%S)
FILE="${BACKUP_DIR}/signalai-${STAMP}.sql.gz"

docker exec "$(docker ps -qf name=postgres | head -1)" \
  pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" | gzip > "$FILE"
chmod 0600 "$FILE"

# Проверка восстановлением во временную базу.
TMPDB="restore_check_${STAMP}"
CID="$(docker ps -qf name=postgres | head -1)"
docker exec "$CID" createdb -U "$POSTGRES_USER" "$TMPDB"
if gzip -dc "$FILE" | docker exec -i "$CID" psql -q -U "$POSTGRES_USER" -d "$TMPDB" >/dev/null 2>&1; then
  TABLES=$(docker exec "$CID" psql -tAqU "$POSTGRES_USER" -d "$TMPDB" \
    -c "SELECT count(*) FROM pg_tables WHERE schemaname='public'")
  logger -t signalai-backup "бэкап ${FILE} восстановлен, таблиц: ${TABLES}"
else
  logger -t signalai-backup "ВНИМАНИЕ: бэкап ${FILE} не восстанавливается"
fi
docker exec "$CID" dropdb -U "$POSTGRES_USER" "$TMPDB" || true

find "$BACKUP_DIR" -name 'signalai-*.sql.gz' -mtime +30 -delete
BACKUP
chmod 0700 /usr/local/bin/signalai-backup

cat > /etc/systemd/system/signalai-backup.service <<UNIT
[Unit]
Description=SignalAI database backup with restore check
[Service]
Type=oneshot
ExecStart=/usr/local/bin/signalai-backup
UNIT
cat > /etc/systemd/system/signalai-backup.timer <<UNIT
[Unit]
Description=Daily SignalAI backup
[Timer]
OnCalendar=*-*-* 02:30:00
Persistent=true
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now signalai-backup.timer >/dev/null
ok "ежедневный бэкап в 02:30 UTC с проверкой восстановления"

# ── Проверка ──────────────────────────────────────────────────────────────

say "Проверка"
sleep 5
HEALTH="$(curl -fsS http://127.0.0.1:8000/health 2>/dev/null || true)"
if [ -z "$HEALTH" ]; then
  die "сервер не отвечает на /health. Логи: docker compose logs api"
fi
echo "$HEALTH" | jq -r '
  "  движок:        \(.engine_version)",
  "  конфигурация:  \(.config_hash[0:16])…",
  "  режим:         \(.execution_mode)",
  "  paper_only:    \(.paper_only)",
  "  база:          \(.database)",
  "  состояние:     \(.status)"' 2>/dev/null || echo "$HEALTH"

cat <<FINAL

────────────────────────────────────────────────────────────────────────
Готово. Сервер работает в режиме PAPER: боевые заявки закрыты.

Что дальше, по порядку:

  1. Проверьте снаружи:   curl https://${DOMAIN:-<домен не задан>}/health
  2. Соберите приложение с адресом сервера и токеном устройства:
       flutter build apk --release \\
         --dart-define=SIGNALAI_API_BASE_URL=https://${DOMAIN:-ВАШ_ДОМЕН} \\
         --dart-define=SIGNALAI_DEVICE_TOKEN=<из ${ENV_FILE}>
  3. Ключи бирж добавляйте ТОЛЬКО после того, как сервер начнёт показывать
     осмысленные идеи. Редактировать: sudo nano ${ENV_FILE}
     Bybit — без права вывода средств. Т-Инвестиции — read и exec раздельно.
  4. Живая торговля не включится, пока не пройден гейт §19:
     ≥100 бумажных сделок или 60 дней, OOS profit factor ≥ 1,20.

Полезное:
  журналы:    docker compose --env-file ${ENV_FILE} logs -f api
  перезапуск: systemctl restart ${SERVICE}
  бэкап:      /usr/local/bin/signalai-backup
────────────────────────────────────────────────────────────────────────
FINAL
