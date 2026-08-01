import '../data/state_lock.dart';
import '../domain/broker/broker.dart';
import '../domain/idea/idea.dart';
import '../domain/idea/trade_plan.dart';
import '../domain/ledger/signal_ledger.dart';
import '../domain/models/digest.dart';
import '../domain/models/signal.dart';

/// То немногое, что фоновому циклу нужно от приложения.
///
/// Узкий интерфейс вместо прямой зависимости от репозитория: цикл проверяется
/// обычными тестами, без Keystore, сети и нативного канала.
abstract interface class MonitorTarget {
  /// Журнал бумажных сделок.
  SignalLedger get ledger;

  /// Пересчёт идей. Он же сверяет журнал по свежим свечам — отдельной сверки
  /// в цикле нет намеренно, иначе появилось бы два разных способа прожить
  /// сделку вперёд.
  Future<DailyDigest> fetchDigest({bool force});

  /// Позиции, как их видит биржа. Пусто, если торгового доступа нет.
  Future<List<BrokerPosition>> brokerPositions();

  /// Позиции без защитного стопа на бирже.
  Future<List<BrokerPosition>> unprotectedPositions();

  /// Поставить защитный стоп по открытой позиции. false — брокер отказал.
  Future<bool> placeProtectiveStop({
    required String symbol,
    required double stopPrice,
    required bool long,
    required double quantity,
  });

  /// Ночной прогон раздела «Инвест»: пересчёт, если пора, и уведомления о
  /// сильных идеях. Пусто — пересчёт не требовался.
  Future<List<({String title, String body})>> investNightly();
}

/// Одно уведомление: заголовок, текст и адрес открываемого экрана.
///
/// Адрес — часть уведомления, а не необязательное дополнение. Пуш без него
/// приводит владельца на тот экран, где он был вчера, и заставляет искать
/// идею руками, то есть перестаёт работать ровно там, ради чего послан.
/// Уведомление вместе с тем, чем оно опознаётся.
///
/// [key] нужен затем, что пуш надо не только показать, но и снять. Без
/// устойчивого ключа связи между идеей и её уведомлением не существует, и
/// отработавшая идея висит в шторке до тех пор, пока её не смахнут руками.
typedef MonitorNotice = ({String title, String body, String payload, String key});

/// Итог одного прогона.
class CycleReport {
  const CycleReport({
    required this.notices,
    required this.summary,
    this.withdrawn = const [],
    this.skipped = false,
    this.error,
  });

  const CycleReport.skipped()
      : notices = const [],
        withdrawn = const [],
        summary = 'Приложение открыто — считает оно',
        skipped = true,
        error = null;

  final List<MonitorNotice> notices;

  /// Ключи уведомлений, которые пора убрать из шторки.
  ///
  /// Отдельно от [notices] намеренно: снятие — не побочный эффект показа.
  /// Идея, которая отработала и нового повода не создала, обязана исчезать
  /// сама, иначе пуш врёт не текстом, а фактом присутствия.
  final List<String> withdrawn;

  /// Строка для служебного уведомления постоянного режима.
  final String summary;

  /// Передний план держал блокировку — цикл ничего не делал.
  final bool skipped;

  /// Причина отказа, если прогон не удался.
  final String? error;
}

/// Что фон уже отправлял — чтобы не присылать один и тот же стоп каждый час.
class MonitorState {
  MonitorState({
    this.lastRun,
    Set<String>? notifiedTrades,
    Set<String>? notifiedSignals,
    Set<String>? knownPositions,
    this.lastError,
  })  : notifiedTrades = notifiedTrades ?? {},
        notifiedSignals = notifiedSignals ?? {},
        knownPositions = knownPositions ?? {};

  DateTime? lastRun;

  /// Идентификаторы закрытых сделок, о которых уже сообщили.
  final Set<String> notifiedTrades;

  /// Идеи, о которых уже сообщили: символ и цена входа.
  final Set<String> notifiedSignals;

  /// Символы позиций, которые в прошлый раз были на бирже.
  final Set<String> knownPositions;

  /// Сколько раз подряд не удалось восстановить стоп, по символам.
  ///
  /// Один сбой сети — не повод будить ночью, два подряд — повод.
  final Map<String, int> stopFailures = {};

  String? lastError;

  /// Ограничитель: журнал живёт годами, а множества — нет.
  static const _maxRemembered = 300;

  void _trim(Set<String> set) {
    if (set.length <= _maxRemembered) return;
    final excess = set.length - _maxRemembered;
    set.removeAll(set.take(excess).toList());
  }

  Map<String, dynamic> toJson() => {
        'last_run': lastRun?.toIso8601String(),
        'notified_trades': notifiedTrades.toList(),
        'notified_signals': notifiedSignals.toList(),
        'known_positions': knownPositions.toList(),
        'stop_failures': stopFailures,
        'last_error': lastError,
      };

  factory MonitorState.fromJson(Map<String, dynamic> j) => MonitorState(
        lastRun: DateTime.tryParse(j['last_run'] as String? ?? ''),
        notifiedTrades: _strings(j['notified_trades']),
        notifiedSignals: _strings(j['notified_signals']),
        knownPositions: _strings(j['known_positions']),
        lastError: j['last_error'] as String?,
      )..stopFailures.addAll({
          for (final e in (j['stop_failures'] as Map<String, dynamic>? ?? const {}).entries)
            e.key: (e.value as num?)?.toInt() ?? 0,
        });

  static Set<String> _strings(Object? value) => {
        for (final v in value as List<dynamic>? ?? const []) v as String,
      };
}

/// Фоновый прогон: сопровождение сделок и поиск новых идей.
///
/// Что фон делает намеренно **не** делает: не отправляет ордера. Подтвердить
/// сделку биометрией в фоне некому, а отправить без подтверждения — нарушить
/// собственное правило. Поэтому здесь только расчёт и уведомления.
class BackgroundCycle {
  const BackgroundCycle({
    required this.target,
    required this.lock,
    this.minPushScore = 75,
  });

  final MonitorTarget target;
  final StateLock lock;

  /// Порог оценки, ниже которого о новой идее не будят.
  final int minPushScore;

  Future<CycleReport> run({
    required MonitorState state,
    required DateTime now,
  }) async {
    if (await lock.heldByOther(StateLock.monitor, now: now)) {
      return const CycleReport.skipped();
    }

    // Состояние журнала до пересчёта: по разнице видно, что закрылось.
    final before = {
      for (final t in target.ledger.trades) t.id: t.status,
    };

    final notices = <MonitorNotice>[];
    final withdrawn = <String>[];
    String? error;
    try {
      await lock.heartbeat(StateLock.monitor);
      // Пересчёт форсируем, только когда закрылся новый часовой бар: тот же
      // каденс, что у переднего плана и у бэктеста.
      final digest = await target.fetchDigest(force: _barClosed(state.lastRun, now));

      notices.addAll(_closedTrades(before, state));
      notices.addAll(_newSignals(digest, state));
      notices.addAll(await _positions(state));
      notices.addAll(await _protectPositions(state));
      notices.addAll(await _investNightly(state));
      // Снятие идёт после показа: идея, которая только что закрылась,
      // сначала сообщает об этом, и лишь потом уходит её прежний пуш.
      withdrawn.addAll(_stale(digest, state));
    } on Object catch (e) {
      // Фон обязан пережить любой отказ: сети может не быть вовсе, а сервис
      // должен дожить до следующего часа и попробовать снова.
      error = _short(e);
    } finally {
      await lock.release(StateLock.monitor);
    }

    state.lastRun = now;
    state.lastError = error;
    return CycleReport(
      notices: notices,
      withdrawn: withdrawn,
      summary: _summary(error),
      error: error,
    );
  }

  /// Закрылся ли новый часовой бар с прошлого прогона.
  bool _barClosed(DateTime? last, DateTime now) {
    if (last == null) return true;
    return DateTime(now.year, now.month, now.day, now.hour)
        .isAfter(DateTime(last.year, last.month, last.day, last.hour));
  }

  List<MonitorNotice> _closedTrades(
    Map<String, PaperStatus> before,
    MonitorState state,
  ) {
    final notices = <MonitorNotice>[];
    for (final trade in target.ledger.trades) {
      if (trade.status != PaperStatus.closed) continue;
      // Закрылась именно сейчас: раньше была живой, и мы о ней не сообщали.
      final wasLive = before[trade.id] != PaperStatus.closed;
      if (!wasLive || !state.notifiedTrades.add(trade.id)) continue;
      final r = trade.resultR ?? 0;
      notices.add((
        title: '${trade.symbol}: сделка закрыта',
        body: '${trade.outcome ?? '—'} · '
            '${r >= 0 ? '+' : '−'}${r.abs().toStringAsFixed(2).replaceAll('.', ',')}R',
        // Идея за сделкой есть не всегда: журнал ведёт и скринер на
        // устройстве. Нет — уведомление просто открывает приложение.
        payload: trade.signalId.isEmpty ? '' : 'idea:${trade.signalId}',
        key: 'trade:${trade.id}',
      ));
    }
    state._trim(state.notifiedTrades);
    return notices;
  }

  List<MonitorNotice> _newSignals(DailyDigest digest, MonitorState state) {
    final notices = <MonitorNotice>[];
    for (final signal in digest.signals) {
      if (signal.score < minPushScore) continue;
      // Ключ включает вход: та же идея с переставленным уровнем — новая идея.
      final key = 'signal:${signal.symbol}@${signal.entry}';
      if (!state.notifiedSignals.add(key)) continue;
      notices.add(signalNotice(signal));
    }
    state._trim(state.notifiedSignals);
    return notices;
  }

  /// Пуши по идеям, которых больше нет среди живых.
  ///
  /// Идея уходит из выдачи, когда отработала, протухла или перестала
  /// проходить допуск. Её уведомление при этом остаётся в шторке навсегда:
  /// система хранит показанное, пока его не смахнут руками. Владелец
  /// открывает такой пуш и не находит там ничего — то есть пуш врёт не
  /// содержанием, а самим фактом присутствия.
  List<String> _stale(DailyDigest digest, MonitorState state) {
    final live = {
      for (final signal in digest.signals) 'signal:${signal.symbol}@${signal.entry}',
    };
    final stale = state.notifiedSignals
        .where((key) => key.startsWith('signal:') && !live.contains(key))
        .toList();
    // Ключ забывается вместе с уведомлением: если идея вернётся, о ней
    // нужно сообщить заново, а не промолчать из-за старой отметки.
    state.notifiedSignals.removeAll(stale);
    return stale;
  }

  /// Ночной пересчёт «Инвеста» — раз в сутки, дедупликация как у сигналов.
  Future<List<MonitorNotice>> _investNightly(MonitorState state) async {
    final notices = <MonitorNotice>[];
    for (final notice in await target.investNightly()) {
      if (!state.notifiedSignals.add('invest:${notice.title}')) continue;
      notices.add((
        title: notice.title,
        body: notice.body,
        payload: '',
        key: 'invest:${notice.title}',
      ));
    }
    return notices;
  }

  Future<List<MonitorNotice>> _positions(MonitorState state) async {
    final positions = await target.brokerPositions();
    final open = {for (final p in positions) p.symbol};
    final notices = <MonitorNotice>[
      for (final symbol in state.knownPositions)
        if (!open.contains(symbol))
          (
            title: '$symbol: позиции на бирже больше нет',
            body: 'Проверьте историю сделок',
            payload: '',
            key: 'position:$symbol',
          ),
    ];
    state.knownPositions
      ..clear()
      ..addAll(open);
    return notices;
  }

  /// Проверка защиты: позиция без стопа — то состояние, в котором её нельзя
  /// оставлять ни на час.
  ///
  /// Уровень берётся из журнала: его решила стратегия при выдаче идеи.
  /// Выдумать уровень нельзя, поэтому позиции, которую приложение не
  /// открывало, стоп не ставится — только предупреждение.
  Future<List<MonitorNotice>> _protectPositions(MonitorState state) async {
    final naked = await target.unprotectedPositions();
    if (naked.isEmpty) {
      state.stopFailures.clear();
      return const [];
    }

    final notices = <MonitorNotice>[];
    for (final position in naked) {
      final planned = _plannedStop(position.symbol);
      if (planned == null) {
        // Позиция открыта не нами: свой уровень ей навязывать не наше дело.
        if (state.notifiedTrades.add('naked:${position.symbol}')) {
          notices.add((
            title: '${position.symbol}: позиция без стопа',
            body: 'Приложение её не открывало — уровень защиты неизвестен. '
                'Выставьте стоп сами.',
            payload: '',
            key: 'naked:${position.symbol}',
          ));
        }
        continue;
      }

      final placed = await target.placeProtectiveStop(
        symbol: position.symbol,
        stopPrice: planned,
        long: position.long,
        quantity: position.quantity,
      );
      if (placed) {
        state.stopFailures.remove(position.symbol);
        notices.add((
          title: '${position.symbol}: защита восстановлена',
          body: 'Стоп выставлен заново на ${_price(planned)}.',
          payload: '',
          key: 'stop:${position.symbol}',
        ));
        continue;
      }

      final failures = (state.stopFailures[position.symbol] ?? 0) + 1;
      state.stopFailures[position.symbol] = failures;
      if (failures >= 2) {
        notices.add((
          title: '${position.symbol}: позиция без стопа',
          body: 'Выставить защиту не удалось $failures раза подряд. '
              'Закройте позицию или поставьте стоп вручную.',
          payload: '',
          key: 'naked:${position.symbol}',
        ));
      }
    }
    return notices;
  }

  /// Уровень стопа, который решила стратегия по живой записи журнала.
  ///
  /// После первого тейка правило ведения переносит стоп в безубыток — и
  /// восстанавливать защиту надо на этом уровне, а не на исходном: исходный
  /// стоп после TP1 означал бы снова рисковать уже заработанным.
  double? _plannedStop(String symbol) {
    for (final trade in target.ledger.openOrPending) {
      if (trade.symbol != symbol) continue;
      final breakeven = trade.breakevenAfterTp1 && trade.tpsTaken > 0;
      return breakeven ? trade.entry : trade.stopLoss;
    }
    return null;
  }

  static String _price(double value) =>
      value.toStringAsFixed(value.abs() < 100 ? 2 : 0).replaceAll('.', ',');

  String _summary(String? error) {
    if (error != null) return 'Последний прогон не удался: $error';
    final live = target.ledger.openOrPending.length;
    final total = target.ledger.totalR;
    final r = '${total >= 0 ? '+' : '−'}'
        '${total.abs().toStringAsFixed(1).replaceAll('.', ',')}R';
    return live == 0 ? 'Открытых сделок нет · итог $r' : 'В работе: $live · итог $r';
  }

  static String _short(Object error) {
    final text = error.toString();
    return text.length > 120 ? '${text.substring(0, 117)}…' : text;
  }
}

/// Уведомление об идее движка.
///
/// Отдельный текст от сигнального: у идеи есть состояние и срок, и «дошла до
/// решения» читается иначе, чем «появилась». Одинаковый текст на оба повода
/// заставлял бы открывать приложение, чтобы понять, что вообще случилось.
MonitorNotice ideaNotice(Idea idea, {bool armed = false}) {
  final payload = 'idea:${idea.id}';
  final plan = idea.plan;
  final head = armed
      ? '${idea.symbolOrId} · ${idea.direction.label} · можно решать'
      : '${idea.symbolOrId} · ${idea.direction.label} · ${idea.score.value}/100';
  if (plan == null) {
    return (
      title: head,
      body: '${idea.strategy.label}. Плана пока нет — открыть разбор.',
      payload: payload,
      key: 'idea:${idea.id}',
    );
  }
  final decimals = _planDecimals(plan);
  return (
    title: head,
    body: 'Вход ${plan.entryLow.toStringAsFixed(decimals)}–'
        '${plan.entryHigh.toStringAsFixed(decimals)} · '
        'SL ${plan.stop.toStringAsFixed(decimals)} · '
        'R:R ${plan.rrToSecondTarget.toStringAsFixed(1).replaceAll('.', ',')}. '
        '${idea.strategy.label}.',
    payload: payload,
    key: 'idea:${idea.id}',
  );
}

/// Сколько знаков у цен плана. Считается по самим ценам: у фьючерса на
/// доллар знаков нет, у крипты бывает шесть, и округлить чужую цену до
/// целых значит показать не ту цену.
int _planDecimals(TradePlan plan) {
  for (final price in [plan.entryLow, plan.entryHigh, plan.stop]) {
    final text = price.toStringAsFixed(6).replaceAll(RegExp(r'0+$'), '');
    final dot = text.indexOf('.');
    if (dot >= 0 && text.length - dot - 1 > 0) return text.length - dot - 1;
  }
  return 0;
}

/// Уведомление о новой идее. Общее для фона и переднего плана, чтобы текст
/// не разъезжался в зависимости от того, кто его отправил.
MonitorNotice signalNotice(TradingSignal signal) => (
      title: '${signal.symbol} · ${signal.direction.label} · ${signal.score}/100',
      body: 'Вход ${signal.entry.toStringAsFixed(signal.priceDecimals)} · '
          'SL ${signal.stopLoss.toStringAsFixed(signal.priceDecimals)} · '
          'R:R ${signal.riskReward}. Открыть SignalAI, чтобы посмотреть разбор.',
      payload: 'idea:${signal.id}',
      // Ключ включает вход: та же бумага с переставленным уровнем — другая
      // идея, и снимать вместе с ней прежний пуш было бы неверно.
      key: 'signal:${signal.symbol}@${signal.entry}',
    );
