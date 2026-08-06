#!/usr/bin/env bash
#
# Сборка APK на своей машине — без GitHub Actions.
#
# Зачем: сборка в CI зависит от раннера, а раннер зависит от лимитов
# аккаунта. Когда его нет, приложение всё равно должно быть чем-то собрано.
# Этот скрипт делает то же, что делает workflow сайдлоада, только локально
# и за пару минут.
#
#   bash tool/build_apk.sh
#
# Подпись берётся автоматически: если рядом лежит android/key.properties —
# ваш постоянный ключ (tool/make_keystore.sh), если нет — отладочный только для
# локальной разработки. GitHub sideload временную подпись не допускает.

set -euo pipefail

cd "$(dirname "$0")/.."

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\n\033[31mНе получилось: %s\033[0m\n' "$*" >&2; exit 1; }

# ── Чего может не хватать ────────────────────────────────────────────────

command -v flutter >/dev/null 2>&1 || fail \
  "не найден flutter. Поставьте SDK: https://docs.flutter.dev/get-started/install
   и убедитесь, что flutter есть в PATH."

SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
if [ -z "$SDK" ] || [ ! -d "$SDK" ]; then
  fail "не найден Android SDK.
   Поставьте Android Studio либо command line tools и задайте переменную:
     export ANDROID_HOME=\$HOME/Android/Sdk
   Проверить, чего не хватает, можно так: flutter doctor -v"
fi

say "Flutter и Android SDK на месте"
flutter --version | head -1
echo "Android SDK: $SDK"

# ── Проверки перед сборкой ───────────────────────────────────────────────
#
# Красный анализатор или упавший тест — повод не выдавать сборку, а не
# повод узнать об этом на телефоне.

say "Зависимости"
flutter pub get --enforce-lockfile

say "Анализатор"
flutter analyze || fail "анализатор нашёл проблемы — они попадут в сборку"

say "Тесты"
flutter test || fail "тесты красные — собирать нечего"

# ── Сборка ───────────────────────────────────────────────────────────────

# Персональная поставка имеет только два режима. `thin` получает идеи,
# paper-сделки и фоновое сопровождение от сервера; старый локальный скринер
# не должен запускаться ни случайно, ни как неявный default.
BUILD_MODE="${SIGNALAI_MODE:-thin}"
API_BASE_URL="${SIGNALAI_API_BASE_URL:-https://api.ilyamartynov.ru}"
case "$BUILD_MODE" in
  thin|demo) ;;
  *) fail "режим '$BUILD_MODE' не поддерживается: допустимы только thin и demo" ;;
esac

if [ -f android/key.properties ]; then
  say "Сборка с постоянным ключом подписи"
else
  say "Сборка с отладочной подписью"
  echo "android/key.properties нет. Такую сборку можно поставить себе"
  echo "(Play Protect предупредит), но не раздать и не выложить в Play."
  echo "Постоянный ключ: bash tool/make_keystore.sh"
fi

flutter build apk --release \
  --dart-define=SIGNALAI_MODE="$BUILD_MODE" \
  --dart-define=SIGNALAI_API_BASE_URL="$API_BASE_URL"

APK=build/app/outputs/flutter-apk/app-release.apk
[ -f "$APK" ] || fail "gradle отработал, но файла нет: $APK"

say "Готово"
ls -lh "$APK" | awk '{print "  файл:   " $9 "\n  размер: " $5}'

# Чем подписан — это стоит увидеть до установки, а не после.
APKSIGNER=$(find "$SDK/build-tools" -name apksigner -type f 2>/dev/null | sort | tail -1 || true)
if [ -n "$APKSIGNER" ]; then
  echo "  подпись:"
  "$APKSIGNER" verify --print-certs "$APK" 2>/dev/null \
    | grep -i "Signer #1 certificate DN" | sed 's/^/    /' || echo "    определить не удалось"
fi

cat <<'HINT'

Поставить на телефон:
  1. подключить телефон кабелем, включить отладку по USB;
  2. adb install -r build/app/outputs/flutter-apk/app-release.apk

Либо просто перекинуть файл на телефон и открыть его в файловом менеджере.
HINT
