import 'dart:async';

import 'package:flutter/services.dart';
import 'package:flutter/widgets.dart';

import '../data/local_analysis_repository.dart';
import '../data/local_store.dart';
import '../data/native_bridge.dart';
import '../data/state_lock.dart';
import 'background_cycle.dart';

/// Канал сервиса. Тот же, что у интерфейса: общие методы обслуживает
/// `NativeChannel`, специфичные для контура — сам `MonitorService`.
const _channel = MethodChannel('ru.signalai.app/native');

/// Точка входа фонового изолята.
///
/// Помечена `vm:entry-point`, иначе компилятор её выбросит: из Dart её никто
/// не вызывает, вход происходит со стороны Android.
///
/// Здесь намеренно нет ни одного пути, ведущего к отправке ордера. Фон считает
/// и уведомляет; торговое решение подтверждает человек, а в фоне его нет.
@pragma('vm:entry-point')
Future<void> signalaiMonitorMain() async {
  // Изолят свой, привязки в нём ещё нет — без этого не работает канал.
  WidgetsFlutterBinding.ensureInitialized();

  final store = LocalStore();
  // Пометка нужна журналу пересчётов: владелец должен видеть, какие прогоны
  // сделал фон, пока приложение было закрыто, а какие — экран у него в руках.
  final repository = LocalAnalysisRepository(store: store)..inBackground = true;
  final cycle = BackgroundCycle(target: repository, lock: StateLock(store));
  const bridge = NativeBridge();

  final mode = await _mode();
  var notificationId = 500;

  while (true) {
    final state = await _loadState(store);
    final report = await cycle.run(state: state, now: DateTime.now());
    await _saveState(store, state);

    for (final notice in report.notices) {
      await bridge.notify(
        id: notificationId++,
        title: notice.title,
        body: notice.body,
      );
      // Идентификаторы не должны расти бесконечно: система хранит их
      // пожизненно, а нам нужен только различимый набор.
      if (notificationId > 599) notificationId = 500;
    }
    await _report(report.summary);

    if (mode != 'persistent') break;
    // Спим до следующего часа. Будильник продолжает страховать: если система
    // снимет сервис во сне, поднимется новый.
    await Future<void>.delayed(const Duration(hours: 1));
  }

  await _finished();
}

Future<String> _mode() async {
  try {
    return await _channel.invokeMethod<String>('monitorMode') ?? 'persistent';
  } on Object {
    return 'persistent';
  }
}

Future<void> _report(String summary) async {
  try {
    await _channel.invokeMethod<bool>('monitorReport', {'summary': summary});
  } on Object {
    // Служебная строка — не повод ронять прогон.
  }
}

Future<void> _finished() async {
  try {
    await _channel.invokeMethod<bool>('monitorFinished');
  } on Object {
    // Сервис уже мог быть снят системой.
  }
}

Future<MonitorState> _loadState(LocalStore store) async {
  final json = await store.read('monitor');
  return json == null ? MonitorState() : MonitorState.fromJson(json);
}

Future<void> _saveState(LocalStore store, MonitorState state) =>
    store.write('monitor', state.toJson());
