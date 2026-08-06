import 'package:flutter/services.dart';
import 'package:flutter/widgets.dart';

import '../data/api/api_client.dart';
import '../data/api/engine_client.dart';
import '../data/api/engine_runtime.dart';
import '../data/local_store.dart';
import '../data/native_bridge.dart';
import 'server_background_cycle.dart';

/// Канал foreground-service. Общие методы обслуживает `NativeChannel`, а
/// завершение короткого прогона — `MonitorService`.
const _channel = MethodChannel('ru.signalai.app/native');

/// Один короткий server poll при каждом Android-пробуждении.
///
/// В этом entrypoint намеренно нет `LocalAnalysisRepository`, анализаторов,
/// market/broker клиентов и локального paper-ledger. Сервер уже считает идеи
/// и сопровождает сделки; телефон только сравнивает два bounded snapshot и
/// показывает значимые переходы.
@pragma('vm:entry-point')
Future<void> signalaiMonitorMain() async {
  WidgetsFlutterBinding.ensureInitialized();

  final store = LocalStore();
  const bridge = NativeBridge();
  await _wake();
  try {
    final state = await loadServerMonitorState(store);
    final credentials = await restoreEngineRuntime(store, bridge);
    ServerCycleReport report;

    if (!credentials.ready) {
      state
        ..lastAttemptAt = DateTime.now()
        ..lastError = credentials.issue;
      report = ServerCycleReport(
        notices: const [],
        withdrawn: const [],
        summary: credentials.issue ?? 'Устройство не привязано.',
        complete: false,
        error: credentials.issue,
      );
    } else {
      // Явные runtime-значения не позволяют background-изоляту случайно
      // откатиться к build-time конфигурации.
      final engine = EngineClient(
        client: ApiClient(
          baseUrl: credentials.baseUrl,
          deviceToken: credentials.deviceToken,
        ),
      );
      report = await ServerBackgroundCycle.engine(engine).run(
        state: state,
        now: DateTime.now(),
      );
    }

    // Сначала фиксируем snapshot/дедуп. Если Android убьёт процесс сразу после
    // показа, повтор придёт с тем же стабильным notification id и не создаст
    // второй ряд в шторке.
    await saveServerMonitorState(store, state);
    for (final notice in report.notices) {
      await bridge.notify(
        id: NativeBridge.noticeId(notice.key),
        title: notice.title,
        body: notice.body,
        payload: notice.payload,
      );
    }
    for (final key in report.withdrawn) {
      await bridge.cancelNotice(key);
    }

    await _report(report.summary);
  } on Object catch (error) {
    // Даже повреждённый локальный snapshot или отказ Keystore не оставляет
    // foreground-service висеть до системного таймаута.
    await _report('Фоновая синхронизация не удалась: $error');
  } finally {
    await _finished();
  }
}

Future<ServerMonitorState> loadServerMonitorState(LocalStore store) async {
  final json = await store.read('server_monitor');
  return json == null
      ? ServerMonitorState()
      : ServerMonitorState.fromJson(json);
}

Future<void> saveServerMonitorState(
  LocalStore store,
  ServerMonitorState state,
) =>
    store.write('server_monitor', state.toJson());

Future<void> _wake() async {
  try {
    await _channel.invokeMethod<bool>('monitorWake');
  } on Object {
    // Сервис уже держит короткий wake lock; канал — дополнительная страховка.
  }
}

Future<void> _report(String summary) async {
  try {
    await _channel.invokeMethod<bool>('monitorReport', {'summary': summary});
  } on Object {
    // Служебная строка не должна превращать успешный poll в отказ.
  }
}

Future<void> _finished() async {
  try {
    await _channel.invokeMethod<bool>('monitorFinished');
  } on Object {
    // Сервис мог быть снят системой сразу после сохранения результата.
  }
}
