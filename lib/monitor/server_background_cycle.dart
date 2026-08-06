import 'dart:convert';

import '../data/api/engine_client.dart';
import '../domain/idea/idea.dart';
import '../domain/idea/paper_position.dart';

typedef MonitorNotice = ({
  String title,
  String body,
  String payload,
  String key,
});

typedef IdeasLoader = Future<EngineIdeas> Function();
typedef PaperTradesLoader = Future<List<PaperPosition>?> Function();

/// Сохранённый серверный снимок фонового thin-клиента.
///
/// Здесь нет свечей, локального журнала или состояния брокера. Для
/// уведомлений достаточно ответить на два вопроса: какие идеи прямо сейчас
/// actionable и как изменились paper-сделки, которые ведёт сервер.
class ServerMonitorSnapshot {
  const ServerMonitorSnapshot({required this.ideas, required this.trades});

  final Map<String, ServerIdeaSnapshot> ideas;
  final Map<String, ServerPaperSnapshot> trades;

  factory ServerMonitorSnapshot.capture(
    Iterable<Idea> ideas,
    Iterable<PaperPosition> trades,
  ) {
    final ideaRows = <String, ServerIdeaSnapshot>{};
    for (final idea in ideas.toList()..sort((a, b) => a.id.compareTo(b.id))) {
      // WAITING не является сделкой и не попадает даже в actionable-снимок.
      if (!idea.readiness.canAct || !idea.actionable) continue;
      ideaRows[idea.id] = ServerIdeaSnapshot.fromIdea(idea);
    }
    final tradeRows = <String, ServerPaperSnapshot>{};
    for (final trade in trades.toList()..sort((a, b) => a.id.compareTo(b.id))) {
      tradeRows[trade.id] = ServerPaperSnapshot.fromPosition(trade);
    }
    return ServerMonitorSnapshot(ideas: ideaRows, trades: tradeRows);
  }

  Map<String, dynamic> toJson() => {
        'ideas': {for (final item in ideas.entries) item.key: item.value.toJson()},
        'trades': {
          for (final item in trades.entries) item.key: item.value.toJson(),
        },
      };

  factory ServerMonitorSnapshot.fromJson(Map<String, dynamic> json) =>
      ServerMonitorSnapshot(
        ideas: {
          for (final item
              in (json['ideas'] as Map<String, dynamic>? ?? const {}).entries)
            if (item.value is Map<String, dynamic>)
              item.key: ServerIdeaSnapshot.fromJson(
                item.value as Map<String, dynamic>,
              ),
        },
        trades: {
          for (final item
              in (json['trades'] as Map<String, dynamic>? ?? const {}).entries)
            if (item.value is Map<String, dynamic>)
              item.key: ServerPaperSnapshot.fromJson(
                item.value as Map<String, dynamic>,
              ),
        },
      );

  /// Короткий детерминированный отпечаток канонического JSON.
  String get fingerprint {
    var hash = 0x811c9dc5;
    for (final unit in utf8.encode(jsonEncode(toJson()))) {
      hash ^= unit;
      hash = (hash * 0x01000193) & 0xffffffff;
    }
    return hash.toRadixString(16).padLeft(8, '0');
  }
}

class ServerIdeaSnapshot {
  const ServerIdeaSnapshot({
    required this.symbol,
    required this.direction,
    required this.score,
  });

  final String symbol;
  final String direction;
  final int score;

  factory ServerIdeaSnapshot.fromIdea(Idea idea) => ServerIdeaSnapshot(
        symbol: idea.symbolOrId,
        direction: idea.direction.label,
        score: idea.score.value,
      );

  Map<String, dynamic> toJson() => {
        'symbol': symbol,
        'direction': direction,
        'score': score,
      };

  factory ServerIdeaSnapshot.fromJson(Map<String, dynamic> json) =>
      ServerIdeaSnapshot(
        symbol: '${json['symbol'] ?? ''}',
        direction: '${json['direction'] ?? ''}',
        score: (json['score'] as num?)?.toInt() ?? 0,
      );
}

class ServerPaperSnapshot {
  const ServerPaperSnapshot({
    required this.ideaId,
    required this.symbol,
    required this.status,
    required this.entry,
    required this.currentStop,
    required this.atBreakeven,
    required this.tpsTaken,
    required this.tpsTotal,
    required this.resultR,
    required this.outcome,
    required this.closeReason,
  });

  final String ideaId;
  final String symbol;
  final PaperPositionStatus status;
  final double entry;
  final double currentStop;
  final bool atBreakeven;
  final int tpsTaken;
  final int tpsTotal;
  final double? resultR;
  final String outcome;
  final String closeReason;

  factory ServerPaperSnapshot.fromPosition(PaperPosition trade) =>
      ServerPaperSnapshot(
        ideaId: trade.ideaId,
        symbol: trade.symbol,
        status: trade.status,
        entry: trade.entry,
        currentStop: trade.currentStop,
        atBreakeven: trade.atBreakeven,
        tpsTaken: trade.tpsTaken,
        tpsTotal: trade.tpsTotal,
        resultR: trade.resultR,
        outcome: trade.outcome,
        closeReason: trade.closeReason,
      );

  Map<String, dynamic> toJson() => {
        'idea_id': ideaId,
        'symbol': symbol,
        'status': status.name,
        'entry': entry,
        'current_stop': currentStop,
        'at_breakeven': atBreakeven,
        'tps_taken': tpsTaken,
        'tps_total': tpsTotal,
        'result_r': resultR,
        'outcome': outcome,
        'close_reason': closeReason,
      };

  factory ServerPaperSnapshot.fromJson(Map<String, dynamic> json) =>
      ServerPaperSnapshot(
        ideaId: '${json['idea_id'] ?? ''}',
        symbol: '${json['symbol'] ?? ''}',
        status: PaperPositionStatus.parse('${json['status'] ?? ''}'),
        entry: _number(json['entry']),
        currentStop: _number(json['current_stop']),
        atBreakeven: json['at_breakeven'] == true,
        tpsTaken: (json['tps_taken'] as num?)?.toInt() ?? 0,
        tpsTotal: (json['tps_total'] as num?)?.toInt() ?? 0,
        resultR: _nullableNumber(json['result_r']),
        outcome: '${json['outcome'] ?? ''}',
        closeReason: '${json['close_reason'] ?? ''}',
      );

  static double _number(Object? raw) => _nullableNumber(raw) ?? 0;

  static double? _nullableNumber(Object? raw) => switch (raw) {
        num value => value.toDouble(),
        String value => double.tryParse(value),
        _ => null,
      };
}

/// Состояние между Android-пробуждениями.
///
/// Snapshot и множества событий сохраняются на диск до публикации пушей.
/// Повтор после убийства процесса использует те же стабильные ключи и не
/// раскладывает одинаковые уведомления рядом.
class ServerMonitorState {
  ServerMonitorState({
    this.lastAttemptAt,
    this.lastSuccessAt,
    this.lastFingerprint = '',
    this.lastError,
    this.snapshot,
    Set<String>? notifiedIdeas,
    Set<String>? notifiedEvents,
  })  : notifiedIdeas = notifiedIdeas ?? <String>{},
        notifiedEvents = notifiedEvents ?? <String>{};

  DateTime? lastAttemptAt;
  DateTime? lastSuccessAt;
  String lastFingerprint;
  String? lastError;
  ServerMonitorSnapshot? snapshot;
  final Set<String> notifiedIdeas;
  final Set<String> notifiedEvents;

  static const _maxRemembered = 500;

  void trim() {
    _trim(notifiedIdeas);
    _trim(notifiedEvents);
  }

  static void _trim(Set<String> values) {
    if (values.length <= _maxRemembered) return;
    values.removeAll(
      values.take(values.length - _maxRemembered).toList(growable: false),
    );
  }

  Map<String, dynamic> toJson() => {
        'last_attempt_at': lastAttemptAt?.toIso8601String(),
        'last_success_at': lastSuccessAt?.toIso8601String(),
        'last_fingerprint': lastFingerprint,
        'last_error': lastError,
        'snapshot': snapshot?.toJson(),
        'notified_ideas': notifiedIdeas.toList(),
        'notified_events': notifiedEvents.toList(),
      };

  factory ServerMonitorState.fromJson(Map<String, dynamic> json) =>
      ServerMonitorState(
        lastAttemptAt: DateTime.tryParse('${json['last_attempt_at'] ?? ''}'),
        lastSuccessAt: DateTime.tryParse('${json['last_success_at'] ?? ''}'),
        lastFingerprint: '${json['last_fingerprint'] ?? ''}',
        lastError: json['last_error'] as String?,
        snapshot: json['snapshot'] is Map<String, dynamic>
            ? ServerMonitorSnapshot.fromJson(
                json['snapshot'] as Map<String, dynamic>,
              )
            : null,
        notifiedIdeas: _strings(json['notified_ideas']),
        notifiedEvents: _strings(json['notified_events']),
      );

  static Set<String> _strings(Object? raw) => {
        for (final item in raw as List<dynamic>? ?? const []) '$item',
      };
}

class ServerCycleReport {
  const ServerCycleReport({
    required this.notices,
    required this.withdrawn,
    required this.summary,
    required this.complete,
    this.error,
  });

  final List<MonitorNotice> notices;
  final List<String> withdrawn;
  final String summary;
  final bool complete;
  final String? error;
}

/// Один bounded poll серверного источника истины.
class ServerBackgroundCycle {
  const ServerBackgroundCycle({
    required this.loadIdeas,
    required this.loadPaperTrades,
  });

  factory ServerBackgroundCycle.engine(EngineClient engine) =>
      ServerBackgroundCycle(
        loadIdeas: engine.today,
        // live_only=false обязателен: иначе CLOSED исчезает, и клиент может
        // лишь гадать, закрылась сделка или временно пропал ответ.
        loadPaperTrades: () => engine.paperTrades(liveOnly: false),
      );

  final IdeasLoader loadIdeas;
  final PaperTradesLoader loadPaperTrades;

  Future<ServerCycleReport> run({
    required ServerMonitorState state,
    required DateTime now,
  }) async {
    state.lastAttemptAt = now;

    try {
      // Оба запроса стартуют до первого await: poll остаётся коротким, но
      // считается полным только при двух успешных ответах.
      final ideasFuture = loadIdeas();
      final tradesFuture = loadPaperTrades();
      // Future.wait обязательно наблюдает оба отказа. Последовательный await
      // оставлял ошибку второго запроса необработанной, если первый падал.
      final fetched = await Future.wait<Object?>([ideasFuture, tradesFuture]);
      final ideas = fetched[0] as EngineIdeas;
      final trades = fetched[1] as List<PaperPosition>?;

      final failure = ideas.unavailableReason ??
          (trades == null ? 'сервер не отдал paper-сделки' : null);
      if (failure != null) {
        state.lastError = _short(failure);
        return ServerCycleReport(
          notices: const [],
          withdrawn: const [],
          summary: 'Фоновая синхронизация не удалась: ${state.lastError}',
          complete: false,
          error: state.lastError,
        );
      }

      final snapshot = ServerMonitorSnapshot.capture(ideas.ideas, trades!);
      final previous = state.snapshot;
      final notices = <MonitorNotice>[];
      final withdrawn = <String>[];

      for (final idea in ideas.ideas) {
        if (!idea.readiness.canAct || !idea.actionable) continue;
        if (!state.notifiedIdeas.add(idea.id)) continue;
        notices.add(_ideaNotice(idea));
      }

      if (previous != null) {
        for (final item in snapshot.trades.entries) {
          final before = previous.trades[item.key];
          final notice = before == null
              ? _paperAppeared(item.key, item.value, state)
              : _paperTransition(item.key, before, item.value, state);
          if (notice != null) notices.add(notice);
          if (before != null && before.status != item.value.status) {
            withdrawn.add('paper:${item.key}:${before.status.name}');
          }
          if (before != null &&
              before.status.live &&
              !item.value.status.live) {
            withdrawn.addAll([
              'paper:${item.key}:pending',
              'paper:${item.key}:open',
              'paper:${item.key}:tp',
              'paper:${item.key}:stop',
            ]);
          }
        }
        for (final id in previous.ideas.keys) {
          if (!snapshot.ideas.containsKey(id)) withdrawn.add('server-idea:$id');
        }
      }

      state
        ..snapshot = snapshot
        ..lastFingerprint = snapshot.fingerprint
        ..lastSuccessAt = now
        ..lastError = null
        ..trim();

      return ServerCycleReport(
        notices: notices,
        withdrawn: withdrawn,
        summary: 'Сервер проверен · actionable ${snapshot.ideas.length} · '
            'paper ${snapshot.trades.values.where((t) => t.status.live).length}',
        complete: true,
      );
    } on Object catch (error) {
      state.lastError = _short(error);
      return ServerCycleReport(
        notices: const [],
        withdrawn: const [],
        summary: 'Фоновая синхронизация не удалась: ${state.lastError}',
        complete: false,
        error: state.lastError,
      );
    }
  }

  static MonitorNotice _ideaNotice(Idea idea) {
    final plan = idea.plan;
    final body = plan == null
        ? 'Триггер подтверждён сервером · оценка ${idea.score.value}/100. '
            'Откройте карточку и проверьте paper-план.'
        : 'Вход ${_price(plan.entryLow)}–${_price(plan.entryHigh)} · '
            'стоп ${_price(plan.stop)} · paper после подтверждения.';
    return (
      title: '${idea.symbolOrId} · ${idea.direction.label} · можно действовать',
      body: body,
      payload: 'idea:${idea.id}',
      key: 'server-idea:${idea.id}',
    );
  }

  static MonitorNotice? _paperTransition(
    String id,
    ServerPaperSnapshot before,
    ServerPaperSnapshot after,
    ServerMonitorState state,
  ) {
    if (before.status.live && !after.status.live) {
      final event = 'paper:$id:${after.status.name}';
      if (!state.notifiedEvents.add(event)) return null;
      final result = after.resultR;
      final resultText = result == null
          ? ''
          : ' · ${result >= 0 ? '+' : '−'}'
              '${result.abs().toStringAsFixed(2).replaceAll('.', ',')}R';
      final reason = [after.outcome, after.closeReason]
          .where((value) => value.isNotEmpty)
          .join(' · ');
      return (
        title: after.status == PaperPositionStatus.cancelled
            ? '${after.symbol}: paper-заявка отменена'
            : '${after.symbol}: paper-сделка закрыта',
        body: '${reason.isEmpty ? 'Сервер завершил сопровождение' : reason}'
            '$resultText',
        payload: after.ideaId.isEmpty ? '' : 'idea:${after.ideaId}',
        key: 'paper:$id:closed',
      );
    }

    if (before.status == PaperPositionStatus.pending &&
        after.status == PaperPositionStatus.open) {
      final event = 'paper:$id:open';
      if (!state.notifiedEvents.add(event)) return null;
      return (
        title: '${after.symbol}: paper-позиция открыта',
        body: 'Вход ${_price(after.entry)} · стоп '
            '${_price(after.currentStop)} · сопровождение на сервере.',
        payload: after.ideaId.isEmpty ? '' : 'idea:${after.ideaId}',
        key: event,
      );
    }

    if (after.tpsTaken > before.tpsTaken) {
      final event = 'paper:$id:tp:${after.tpsTaken}';
      if (!state.notifiedEvents.add(event)) return null;
      final protection = after.atBreakeven && !before.atBreakeven
          ? ' Стоп перенесён в безубыток.'
          : (after.currentStop != before.currentStop
              ? ' Новый стоп ${_price(after.currentStop)}.'
              : '');
      return (
        title: '${after.symbol}: взят тейк ${after.tpsTaken} '
            'из ${after.tpsTotal}',
        body: 'Зафиксировано ${_r(after.resultR)}.$protection',
        payload: after.ideaId.isEmpty ? '' : 'idea:${after.ideaId}',
        key: 'paper:$id:tp',
      );
    }

    if (after.atBreakeven != before.atBreakeven ||
        after.currentStop != before.currentStop) {
      final event = 'paper:$id:stop:${after.currentStop}:'
          '${after.atBreakeven}';
      if (!state.notifiedEvents.add(event)) return null;
      return (
        title: after.atBreakeven
            ? '${after.symbol}: стоп в безубытке'
            : '${after.symbol}: защита paper-сделки изменена',
        body: 'Текущий стоп ${_price(after.currentStop)}. '
            'Сопровождение выполняет сервер.',
        payload: after.ideaId.isEmpty ? '' : 'idea:${after.ideaId}',
        key: 'paper:$id:stop',
      );
    }
    return null;
  }

  static MonitorNotice? _paperAppeared(
    String id,
    ServerPaperSnapshot trade,
    ServerMonitorState state,
  ) {
    final event = 'paper:$id:${trade.status.name}';
    if (!state.notifiedEvents.add(event)) return null;
    return switch (trade.status) {
      PaperPositionStatus.pending => (
          title: '${trade.symbol}: paper-заявка принята',
          body: 'Ждём вход ${_price(trade.entry)} · сопровождение на сервере.',
          payload: trade.ideaId.isEmpty ? '' : 'idea:${trade.ideaId}',
          key: event,
        ),
      PaperPositionStatus.open => (
          title: '${trade.symbol}: paper-позиция открыта',
          body: 'Вход ${_price(trade.entry)} · стоп '
              '${_price(trade.currentStop)} · сопровождение на сервере.',
          payload: trade.ideaId.isEmpty ? '' : 'idea:${trade.ideaId}',
          key: event,
        ),
      PaperPositionStatus.closed || PaperPositionStatus.cancelled => (
          title: trade.status == PaperPositionStatus.cancelled
              ? '${trade.symbol}: paper-заявка отменена'
              : '${trade.symbol}: paper-сделка закрыта',
          body: [trade.outcome, trade.closeReason]
              .where((value) => value.isNotEmpty)
              .join(' · '),
          payload: trade.ideaId.isEmpty ? '' : 'idea:${trade.ideaId}',
          key: 'paper:$id:closed',
        ),
    };
  }

  static String _r(double? value) {
    if (value == null) return '—';
    return '${value >= 0 ? '+' : '−'}'
        '${value.abs().toStringAsFixed(2).replaceAll('.', ',')}R';
  }

  static String _price(double value) {
    final text = value.toStringAsFixed(4);
    return text.replaceFirst(RegExp(r'\.?0+$'), '').replaceAll('.', ',');
  }

  static String _short(Object error) {
    final text = error.toString();
    return text.length > 220 ? '${text.substring(0, 220)}…' : text;
  }
}
