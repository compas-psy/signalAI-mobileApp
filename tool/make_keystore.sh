#!/usr/bin/env bash
# Генерация релизного ключа подписи SignalAI.
#
# Запускать ЛОКАЛЬНО (не в CI, не в облаке) — приватный ключ не должен покидать
# вашу машину и не должен попадать в git. Play Protect блокирует приложения,
# подписанные отладочным ключом; этот скрипт создаёт собственный ключ, которым
# подписываются релизные сборки.
#
#   ./tool/make_keystore.sh
#
# Результат:
#   ~/.signalai/upload-keystore.jks   — сам ключ (храните и бэкапьте отдельно!)
#   android/key.properties            — локальный конфиг Gradle (в .gitignore)
#   upload-keystore.jks.base64        — строка для GitHub Secret ANDROID_KEYSTORE_BASE64
#
# ВАЖНО: потеря ключа = невозможность обновить уже установленное приложение
# (и невозможность залить обновление в Play под тем же application id, если вы
# не подключили Play App Signing). Сделайте офлайн-бэкап .jks и паролей.

set -euo pipefail

KEYSTORE_DIR="${HOME}/.signalai"
KEYSTORE_PATH="${KEYSTORE_DIR}/upload-keystore.jks"
ALIAS="signalai-upload"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v keytool >/dev/null 2>&1; then
  echo "keytool не найден. Установите JDK 17 (например, из Android Studio) и повторите." >&2
  exit 1
fi

if [[ -f "${KEYSTORE_PATH}" ]]; then
  echo "Ключ уже существует: ${KEYSTORE_PATH}"
  echo "Если нужен новый — переименуйте старый вручную (перезапись сделает обновления невозможными)."
  exit 1
fi

mkdir -p "${KEYSTORE_DIR}"
chmod 700 "${KEYSTORE_DIR}"

read -r -s -p "Пароль для keystore (запомните/сохраните в менеджере паролей): " STORE_PASS
echo
read -r -s -p "Повторите пароль: " STORE_PASS_CONFIRM
echo
if [[ "${STORE_PASS}" != "${STORE_PASS_CONFIRM}" ]]; then
  echo "Пароли не совпали." >&2
  exit 1
fi
if [[ ${#STORE_PASS} -lt 12 ]]; then
  echo "Пароль короче 12 символов — слишком слабый для ключа подписи." >&2
  exit 1
fi

# RSA 4096 / 10000 дней — требования Google Play к ключу загрузки.
keytool -genkeypair \
  -alias "${ALIAS}" \
  -keyalg RSA \
  -keysize 4096 \
  -validity 10000 \
  -keystore "${KEYSTORE_PATH}" \
  -storetype JKS \
  -storepass "${STORE_PASS}" \
  -keypass "${STORE_PASS}" \
  -dname "CN=SignalAI, OU=Personal, O=SignalAI, L=Moscow, C=RU"

chmod 600 "${KEYSTORE_PATH}"

cat > "${REPO_ROOT}/android/key.properties" <<EOF
storePassword=${STORE_PASS}
keyPassword=${STORE_PASS}
keyAlias=${ALIAS}
storeFile=${KEYSTORE_PATH}
EOF
chmod 600 "${REPO_ROOT}/android/key.properties"

base64 -w0 "${KEYSTORE_PATH}" > "${REPO_ROOT}/upload-keystore.jks.base64" 2>/dev/null \
  || base64 -i "${KEYSTORE_PATH}" | tr -d '\n' > "${REPO_ROOT}/upload-keystore.jks.base64"

echo
echo "Готово."
echo "  keystore:        ${KEYSTORE_PATH}"
echo "  key.properties:  ${REPO_ROOT}/android/key.properties  (в .gitignore)"
echo "  для GitHub:      ${REPO_ROOT}/upload-keystore.jks.base64"
echo
echo "Отпечаток ключа (пригодится для Play Console и FCM):"
keytool -list -v -keystore "${KEYSTORE_PATH}" -alias "${ALIAS}" -storepass "${STORE_PASS}" \
  | grep -E "SHA1:|SHA256:" || true
echo
echo "Дальше — docs/ANDROID_SIGNING.md, раздел «Секреты в GitHub»."
echo "После добавления секретов УДАЛИТЕ upload-keystore.jks.base64:"
echo "  shred -u ${REPO_ROOT}/upload-keystore.jks.base64  # или rm"
