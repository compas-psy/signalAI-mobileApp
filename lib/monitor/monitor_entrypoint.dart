import 'dart:async';

import 'package:flutter/services.dart';
import 'package:flutter/widgets.dart';

import '../data/local_analysis_repository.dart';
import '../data/local_store.dart';
import '../data/native_bridge.dart';
import '../data/state_lock.dart';
import 'background_cycle.dart';
import 'monitor_pace.dart';

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

  while (true) {
    final state = await _loadState(store);

    // Шаг наблюдения выбирается до прогона: на разряженном телефоне без
    // единой открытой сделки часовой пересчёт круглосуточно — это не
    // «постоянное наблюдение», а разряженный к вечеру телефон, который не
    // следит уже ни за чем.
    final pace = paceFor(
      watching: _watching(repository),
      power: await _power(),
    );

    if (!pace.skipRun) {
      await _wake();
      final report = await cycle.run(state: state, now: DateTime.now());
      await _saveState(store, state);

      for (final notice in report.notices) {
        // Идентификатор выводится из ключа, а не из счётчика. Счётчик
        // означал две беды сразу: повторное уведомление об одной идее
        // ложилось рядом вместо замены, и снять его потом было нечем —
        // связи между идеей и её пушем не существовало.
        await bridge.notify(
          id: NativeBridge.noticeId(notice.key),
          title: notice.title,
          body: notice.body,
          payload: notice.payload,
        );
      }
      // Отработавшие идеи уходят из шторки сами. Иначе пуш врёт не
      // текстом, а фактом присутствия: он утверждает, что есть повод
      // действовать, когда действовать уже не по чему.
      for (final key in report.withdrawn) {
        await bridge.cancelNotice(key);
      }
      await _report('${report.summary} · ${pace.reason}');
    } else {
      // Пропуск обязан быть виден. «Контур ничего не делал три часа» без
      // причины неотличимо от «контур сломался», и владелец узнаёт об этом
      // в худший момент.
      await _report(pace.reason);
    }

    if (mode != 'persistent') break;
    // Блокировка процессора снимается на время сна, а будильник
    // переставляется под тот же интервал: страховка обязана будить тогда
    // же, когда собирался проснуться сам контур.
    await _sleep(pace.sleep);
    await Future<void>.delayed(pace.sleep);
  }

  await _finished();
}

/// Сколько объектов на сопровождении: открытые и выставленные сделки.
///
/// Ноль значит, что сторожить нечего — новую идею контур всё равно найдёт
/// следующим прогоном, а будить телефон каждый час незачем.
int _watching(LocalAnalysisRepository repository) {
  try {
    return repository.ledger.openOrPending.length;
  } on Object {
    // Журнал ещё не прочитан — считаем, что следить есть за чем: ошибиться
    // в сторону лишнего прогона дешевле, чем проспать открытую позицию.
    return 1;
  }
}

Future<PowerState> _power() async {
  try {
    final raw = await _channel.invokeMethod<Map<Object?, Object?>>('powerState');
    return raw == null ? const PowerState() : PowerState.fromMap(raw);
  } on Object {
    // Состояние питания неизвестно — работаем в обычном темпе, а не
    // экономим на догадке.
    return const PowerState();
  }
}

Future<void> _wake() async {
  try {
    await _channel.invokeMethod<bool>('monitorWake');
  } on Object {
    // Не дали блокировку — прогон всё равно попробуем.
  }
}

Future<void> _sleep(Duration until) async {
  try {
    await _channel.invokeMethod<bool>('monitorSleep', {
      'minutes': until.inMinutes,
    });
  } on Object {
    // Сервис уже мог быть снят системой.
  }
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
