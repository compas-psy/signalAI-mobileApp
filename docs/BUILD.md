# Как получить приложение

Три способа. Первый работает всегда и ни от чего не зависит — начинайте с него.

| Способ | Работает без CI | Что даёт |
|---|---|---|
| `bash tool/build_apk.sh` | да | APK на телефон за пару минут |
| «Run workflow» в Actions | нет | проверенный APK с точным ref/SHA и постоянной подписью |
| `flutter build web` + браузер | да | посмотреть экраны без телефона |

---

## 1. Собрать APK у себя

```bash
bash tool/build_apk.sh
```

Скрипт сам проверит, чего не хватает, прогонит анализатор и тесты и соберёт
релизный APK. Сборка не выдаётся, если анализатор или тесты красные: узнавать
о поломке на телефоне — самый дорогой способ.

По умолчанию скрипт собирает `thin`: идеи, графики, paper lifecycle и
фоновое сопровождение живут на сервере, локальный скринер не запускается.
Демо включается явно:

```bash
SIGNALAI_MODE=demo bash tool/build_apk.sh
```

Режим `local` считается legacy и отвергается скриптом. Адрес другого сервера
можно задать через `SIGNALAI_API_BASE_URL`.

**Что нужно поставить один раз:**

- Flutter SDK — https://docs.flutter.dev/get-started/install
- Android SDK: проще всего Android Studio, можно только command line tools.
  Дальше задать путь:
  ```bash
  export ANDROID_HOME=$HOME/Android/Sdk
  ```
- Проверить, что всё на месте: `flutter doctor -v`

**Куда попадёт файл:** `build/app/outputs/flutter-apk/app-release.apk`

**Как поставить на телефон:**

```bash
adb install -r build/app/outputs/flutter-apk/app-release.apk
```

Либо перекинуть файл на телефон любым способом и открыть в файловом менеджере.

### Про подпись

Если рядом нет `android/key.properties`, сборка подписывается отладочным
ключом. Такую можно поставить себе — Play Protect предупредит, это ожидаемо.
Раздавать другим и выкладывать в Play нельзя.

Постоянный ключ делается один раз:

```bash
bash tool/make_keystore.sh
```

После этого тот же `build_apk.sh` подхватит его сам. Подробности —
`docs/ANDROID_SIGNING.md`.

---

## 2. Собрать APK в GitHub Actions

Actions → **Android sideload APK** → **Run workflow**. В обязательном поле
`source_ref` укажите принятую ветку, тег или полный commit SHA. Верхний выбор
ветки в форме Actions определяет версию workflow, но не источник APK — поэтому
`source_ref` запрашивается отдельно и затем разрешается в точный SHA.
Для персональной сборки оставьте `mode=thin`; выбора `local` в форме нет.

Перед сборкой автоматически проходит полный `Quality gate`: server pytest на
PostgreSQL, миграции и импорты, `flutter analyze`, `flutter test` и проверка
секретов. Нужен постоянный signing key из GitHub Secrets; при его отсутствии
workflow падает и не генерирует временный ключ.

Готовый файл появляется в pre-release `sideload`, ссылка не меняется:
https://github.com/compas-psy/signalAI-mobileApp/releases/download/sideload/signalai-sideload.apk

В том же pre-release лежат APK с `source_ref` и коротким SHA в имени, а также
`signalai-sideload.json` с полным SHA коммита, SHA-256 APK, отпечатком подписи и
ссылкой на run. Bootstrap secret в APK не компилируется: после установки владелец
вводит `SIGNALAI_DEVICE_TOKEN`, а сервер обменивает его на отдельный token
устройства. В Android Keystore сохраняется только выданный token; bootstrap
secret не авторизует обычные API.

Второй способ запустить — тег:

```bash
git tag sideload-$(date +%Y%m%d-%H%M) <проверенный-commit>
git push origin --tags
```

На обычные коммиты сборка **не запускается**: APK от каждой промежуточной
правки не нужен никому, а прогон занимает раннер на семь-восемь минут.

### Если сборка падает за несколько секунд

Признак: задача завершается за 2–8 секунд, список шагов пустой, в API у неё
`runner_id: 0`. Это значит, что раннер не выдан — код до него не доехал, и
чинить в репозитории нечего.

Смотреть надо в **Settings → Billing and licensing → Actions**: included
minutes и spending limit. Проверить, что дело именно в этом, можно любым
workflow из одной строки `echo` — он упадёт так же.

---

## 3. Посмотреть экраны без телефона

Android SDK для этого не нужен:

```bash
flutter build web --release --no-web-resources-cdn --dart-define=SIGNALAI_MODE=demo
cd build/web && python3 -m http.server 8099
```

Открыть http://127.0.0.1:8099 — приложение работает целиком, на данных макета.

`--no-web-resources-cdn` кладёт движок отрисовки рядом со сборкой: иначе он
тянется с `gstatic.com`, и без доступа туда экран остаётся белым.

Живой `thin` в браузере не поднимется: первичный device token должен храниться
в Android Keystore, которого в web нет. Браузерная сборка предназначена только для
проверки интерфейса на demo-фикстурах.
