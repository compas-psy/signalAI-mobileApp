import 'idea.dart';
import 'idea_state.dart';

/// Запись о пропущенной идее (ТЗ §12).
///
/// ТЗ требует сохранять пропуск с причиной из справочника и свободным
/// комментарием. Без этого журнал отвечает только на вопрос «что я сделал» и
/// молчит о том, чего не сделал, — а именно там живут самые дорогие ошибки:
/// «не увидел вовремя» и «не поверил» лечатся по-разному.
class SkipRecord {
  const SkipRecord({
    required this.ideaId,
    required this.instrumentId,
    required this.direction,
    required this.score,
    required this.reason,
    required this.comment,
    required this.at,
    this.riskPercent,
    this.rrToSecondTarget,
    this.strategy,
  });

  final String ideaId;
  final String instrumentId;

  /// «LONG» / «SHORT» — снимок, а не ссылка: идея уходит, запись остаётся.
  final String direction;

  final int score;
  final SkipReason reason;
  final String comment;
  final DateTime at;

  final double? riskPercent;
  final double? rrToSecondTarget;
  final String? strategy;

  /// Снимок идеи в момент отказа.
  factory SkipRecord.of(
    Idea idea, {
    required SkipReason reason,
    required String comment,
    required DateTime at,
  }) =>
      SkipRecord(
        ideaId: idea.id,
        instrumentId: idea.instrumentId,
        direction: idea.direction.label,
        score: idea.score.value,
        reason: reason,
        comment: comment,
        at: at,
        riskPercent: idea.plan?.riskPercent,
        rrToSecondTarget: idea.plan?.rrToSecondTarget,
        strategy: idea.strategy.label,
      );

  Map<String, dynamic> toJson() => {
        'idea_id': ideaId,
        'instrument': instrumentId,
        'direction': direction,
        'score': score,
        'reason': reason.code,
        'comment': comment,
        'at': at.toIso8601String(),
        if (riskPercent != null) 'risk_percent': riskPercent,
        if (rrToSecondTarget != null) 'rr': rrToSecondTarget,
        if (strategy != null) 'strategy': strategy,
      };

  static SkipRecord fromJson(Map<String, dynamic> j) => SkipRecord(
        ideaId: j['idea_id'] as String? ?? '',
        instrumentId: j['instrument'] as String? ?? '',
        direction: j['direction'] as String? ?? '',
        score: (j['score'] as num?)?.toInt() ?? 0,
        reason: SkipReason.parse(j['reason'] as String?),
        comment: j['comment'] as String? ?? '',
        at: DateTime.tryParse(j['at'] as String? ?? '') ?? DateTime(2000),
        riskPercent: (j['risk_percent'] as num?)?.toDouble(),
        rrToSecondTarget: (j['rr'] as num?)?.toDouble(),
        strategy: j['strategy'] as String?,
      );
}

/// Разрез пропусков по причинам — то, ради чего справочник закрыт.
class SkipBreakdown {
  const SkipBreakdown(this.counts, this.total);

  final Map<SkipReason, int> counts;
  final int total;

  factory SkipBreakdown.of(List<SkipRecord> records) {
    final counts = <SkipReason, int>{};
    for (final record in records) {
      counts.update(record.reason, (v) => v + 1, ifAbsent: () => 1);
    }
    return SkipBreakdown(counts, records.length);
  }

  /// Причины от частых к редким. Ноль не показывается: строка «Лимит риска —
  /// 0» ничего не сообщает и раздувает список до семи пунктов.
  List<MapEntry<SkipReason, int>> get ranked {
    final list = counts.entries.where((e) => e.value > 0).toList()
      ..sort((a, b) => b.value.compareTo(a.value));
    return list;
  }

  double share(SkipReason reason) =>
      total == 0 ? 0 : (counts[reason] ?? 0) / total;
}
