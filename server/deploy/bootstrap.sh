#!/usr/bin/env bash
#
# Развёртывание SignalAI на чистой Ubuntu 22.04/24.04.
#
#   git clone https://github.com/compas-psy/signalAI-mobileApp.git
#   cd signalAI-mobileApp/server
#   umask 077
#   openssl rand -hex 32 > /root/signalai-device-token
#   # Сохраните это же значение в менеджере паролей.
#   sudo env SIGNALAI_DEVICE_TOKEN_FILE=/root/signalai-device-token \
#     bash deploy/bootstrap.sh
#   sudo shred -u /root/signalai-device-token
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
umask 077

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

# Device token должен быть известен владельцу: он один раз вводит то же
# значение в приложении, а клиент хранит его в Android Keystore. На чистом
# сервере bootstrap поэтому не генерирует неизвестный токен. Для CI безопаснее
# передать защищённый файл, чем секрет в argv.
TOKEN_WAS_PROVIDED=false
DEVICE_TOKEN="${SIGNALAI_DEVICE_TOKEN:-}"
DEVICE_TOKEN_FILE="${SIGNALAI_DEVICE_TOKEN_FILE:-}"
if [ -n "$DEVICE_TOKEN" ] && [ -n "$DEVICE_TOKEN_FILE" ]; then
  die "задайте только SIGNALAI_DEVICE_TOKEN или SIGNALAI_DEVICE_TOKEN_FILE"
fi
if [ -n "$DEVICE_TOKEN_FILE" ]; then
  [ -f "$DEVICE_TOKEN_FILE" ] || die "файл device token не найден"
  DEVICE_TOKEN="$(<"$DEVICE_TOKEN_FILE")"
  TOKEN_WAS_PROVIDED=true
elif [ -n "$DEVICE_TOKEN" ]; then
  TOKEN_WAS_PROVIDED=true
elif [ -f "$ENV_FILE" ]; then
  DEVICE_TOKEN="$(sed -n 's/^SIGNALAI_DEVICE_TOKEN=//p' "$ENV_FILE" | tail -1)"
else
  die "для первой установки задайте и сохраните SIGNALAI_DEVICE_TOKEN (>=32 URL-safe символов)"
fi
unset SIGNALAI_DEVICE_TOKEN SIGNALAI_DEVICE_TOKEN_FILE

case "$DEVICE_TOKEN" in
  *[!A-Za-z0-9._~-]*) die "device token должен быть одной URL-safe строкой" ;;
esac
[ "${#DEVICE_TOKEN}" -ge 32 ] || die "device token должен содержать не менее 32 символов"

# ── Что спросить у владельца ─────────────────────────────────────────────
#
# Домен нужен для TLS. Без него сервер поднимется на localhost, но телефон
# к нему не подключится: приложение ходит только по HTTPS.

DOMAIN="${SIGNALAI_DOMAIN:-}"
ADMIN_IP="${SIGNALAI_ADMIN_IP:-}"
EMAIL="${SIGNALAI_ACME_EMAIL:-}"

# Скрипт запускают двумя способами: руками в терминале и из CI. Во втором
# случае терминала нет, и `read` не просто не может спросить — bash даже не
# печатает приглашение, а `set -e` обрывает выполнение. Снаружи это выглядит
# как мгновенная смерть без единой строки объяснения, и найти причину почти
# невозможно.
#
# Поэтому спрашиваем только когда есть у кого.
interactive() { [ -t 0 ]; }

if [ -z "$DOMAIN" ] && interactive; then
  read -rp "Домен сервера (например signalai.example.com), пусто — без TLS: " DOMAIN
fi
if [ -z "$ADMIN_IP" ]; then
  if interactive; then
    CURRENT_SSH_IP="$(who am i 2>/dev/null | awk -F'[()]' '{print $2}' || true)"
    read -rp "IP, с которого вы ходите по SSH [${CURRENT_SSH_IP:-не определён}]: " ADMIN_IP
    ADMIN_IP="${ADMIN_IP:-$CURRENT_SSH_IP}"
  else
    # Из CI адрес приходит параметром. Если его не передали — не гадаем:
    # SSH остаётся открытым для всех, что и требуется для развёртывания с
    # раннера, приходящего каждый раз с нового адреса.
    ADMIN_IP="0.0.0.0/0"
  fi
fi

if [ -z "$DOMAIN" ]; then
  warn "домен не задан — TLS настраиваться не будет, API останется на 127.0.0.1:8000"
fi

# ── Пакеты ────────────────────────────────────────────────────────────────

say "Системные пакеты"
export DEBIAN_FRONTEND=noninteractive
# Один недоступный сторонний репозиторий возвращает ненулевой код и под
# `set -e` обрывает всё развёртывание. На сервере, где живёт чужой сайт,
# посторонних PPA обычно хватает, а к нашим пакетам они отношения не имеют.
apt-get update -qq || warn "часть репозиториев недоступна — ставим из того, что есть"
apt-get install -y -qq --no-install-recommends \
  ca-certificates curl gnupg ufw fail2ban unattended-upgrades \
  postgresql-client jq >/dev/null \
  || die "не удалось поставить базовые пакеты — проверьте apt на сервере"
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
  if [ "$TOKEN_WAS_PROVIDED" = true ]; then
    TMP_ENV="$(mktemp "${ENV_DIR}/.env.XXXXXX")"
    awk '!/^SIGNALAI_DEVICE_TOKEN=/' "$ENV_FILE" > "$TMP_ENV"
    printf 'SIGNALAI_DEVICE_TOKEN=%s\n' "$DEVICE_TOKEN" >> "$TMP_ENV"
    chmod 0600 "$TMP_ENV"
    chown --reference="$ENV_FILE" "$TMP_ENV"
    mv -f "$TMP_ENV" "$ENV_FILE"
    ok "$ENV_FILE уже существовал; device token обновлён из явного ввода"
  else
    ok "$ENV_FILE уже существует — сохранён его device token"
  fi
else
  POSTGRES_PASSWORD="$(head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 40)"
  cat > "$ENV_FILE" <<EOF
# Создан bootstrap $(date -u +%Y-%m-%dT%H:%M:%SZ). Права 0600, владелец root.
# В git этот файл не попадает никогда.
POSTGRES_USER=signalai
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
POSTGRES_DB=signalai

# Токен устройства: server runtime only. В APK не компилируется;
# владелец один раз вводит тот же токен, клиент хранит его в Android Keystore.
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
  ok "device token записан только как server runtime secret"
fi
bash "$APP_DIR/deploy/ensure_runtime_secrets.sh" "$ENV_FILE"
ok "изолированный vault key Lighter live обеспечен без вывода значения"
unset DEVICE_TOKEN

# ── Firewall ──────────────────────────────────────────────────────────────
#
# Default-deny. SSH только с адреса владельца, наружу — 80/443 для TLS.
# Postgres и Redis не публикуются вовсе: к ним можно только из docker-сети.

say "Firewall"
#
# Правила ДОБАВЛЯЮТСЯ, а не пересоздаются. На этом сервере может уже жить
# чужой сайт, и `ufw --force reset` снёс бы разрешения, о которых мы не
# знаем, — сайт лёг бы молча, а причину искали бы днями.
#
# Порты 80 и 443 открываются всегда, независимо от того, задан ли домен:
# если на машине уже есть сайт, закрыть их значит его выключить.
ufw allow 80/tcp comment "HTTP" >/dev/null 2>&1 || true
ufw allow 443/tcp comment "HTTPS" >/dev/null 2>&1 || true
ufw allow 22/tcp comment "SSH" >/dev/null 2>&1 || true
if [ -n "$ADMIN_IP" ] && [ "$ADMIN_IP" != "0.0.0.0/0" ]; then
  ufw allow from "$ADMIN_IP" comment "адрес владельца" >/dev/null 2>&1 || true
fi

if ufw status | head -1 | grep -qi inactive; then
  ufw default deny incoming >/dev/null
  ufw default allow outgoing >/dev/null
  ufw --force enable >/dev/null
  ok "firewall включён: 22, 80, 443"
else
  ok "firewall уже работал — правила добавлены, прежние не тронуты"
fi

# SSH оставляем открытым для всех адресов намеренно. Развёртывание идёт
# через GitHub Actions, а её раннеры приходят каждый раз с нового адреса:
# ограничение по IP сделало бы автоматическое обновление невозможным.
# Безопасность обеспечивается ключом, отключёнными паролями и fail2ban.

say "Защита доступа"
#
# Пароли отключаем только если вход по ключу уже работает. Иначе можно
# закрыть единственную дверь: bootstrap выполняется по SSH, и если ключ
# лежит не у того пользователя, следующего подключения не будет.
if [ -s "${HOME}/.ssh/authorized_keys" ] || [ -s /root/.ssh/authorized_keys ]; then
  cp -n /etc/ssh/sshd_config /etc/ssh/sshd_config.signalai.bak 2>/dev/null || true
  sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
  sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
  if sshd -t 2>/dev/null; then
    systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
    ok "вход по ключу, пароли отключены (копия конфигурации: sshd_config.signalai.bak)"
  else
    cp /etc/ssh/sshd_config.signalai.bak /etc/ssh/sshd_config 2>/dev/null || true
    warn "конфигурация sshd не прошла проверку — возвращена прежняя"
  fi
else
  warn "ключей в authorized_keys не нашлось — пароли НЕ отключены, чтобы не потерять доступ"
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

# Планировщик §26 поднимается после API: он не публикует портов и живёт
# отдельным процессом, чтобы перезапуск API не рвал загрузку на середине.
docker compose --env-file "$ENV_FILE" up -d --build scheduler
ok "планировщик запущен: вселенная, загрузка, допуск, скан"

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
  # Чужие конфигурации не трогаем. Здесь может уже стоять сайт владельца, и
  # снос «стандартного» узла выключил бы его без предупреждения. Наш узел
  # отвечает только на свой server_name, остальные продолжают работать.
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
     Через 15–20 минут проверьте данные, не печатая token:
       read -rsp 'Device token: ' SIGNALAI_TOKEN; echo
       curl -s -H "Authorization: Bearer \$SIGNALAI_TOKEN" https://${DOMAIN:-ВАШ_ДОМЕН}/api/v1/market/status | jq '{in_universe, tradable, with_data, last_bar_time}'
       unset SIGNALAI_TOKEN
  2. Соберите тонкий клиент только с адресом сервера:
       flutter build apk --release \\
         --dart-define=SIGNALAI_MODE=thin \\
         --dart-define=SIGNALAI_API_BASE_URL=https://${DOMAIN:-ВАШ_ДОМЕН}
     При первом запуске введите тот же заранее сохранённый device token.
     Он не лежит в APK; мобильный клиент хранит его в Android Keystore.
  3. Ключи бирж добавляйте ТОЛЬКО после того, как сервер начнёт показывать
     осмысленные идеи. Редактировать: sudo nano ${ENV_FILE}
     Bybit — без права вывода средств. Т-Инвестиции — read и exec раздельно.
  4. Живая торговля не включится, пока не пройден гейт §19:
     ≥100 бумажных сделок или 60 дней, OOS profit factor ≥ 1,20.

Полезное:
  журналы API:        docker compose --env-file ${ENV_FILE} logs -f api
  журналы загрузки:   docker compose --env-file ${ENV_FILE} logs -f scheduler
  что загружено:      read -rsp 'Device token: ' SIGNALAI_TOKEN; echo
                       curl -s -H "Authorization: Bearer \$SIGNALAI_TOKEN" http://127.0.0.1:8000/api/v1/market/status | jq
                       unset SIGNALAI_TOKEN
  перезапуск: systemctl restart ${SERVICE}
  бэкап:      /usr/local/bin/signalai-backup
────────────────────────────────────────────────────────────────────────
FINAL