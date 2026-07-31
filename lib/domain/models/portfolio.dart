import '../enums.dart';

/// Открытая позиция с прогрессом до ближайшего тейка.
class ActivePosition {
  const ActivePosition({
    required this.symbol,
    required this.direction,
    required this.entryLabel,
    required this.currentLabel,
    required this.pnlLabel,
    required this.pnlPositive,
    required this.progressPercent,
    required this.stage,
    this.signalId = '',
  });

  final String symbol;
  final Direction direction;
  final String entryLabel;
  final String currentLabel;

  /// P&L готовой подписью («+14 080 ₽»).
  final String pnlLabel;
  final bool pnlPositive;

  /// Идея, из которой позиция родилась. Пустая — открывать нечего.
  final String signalId;

  /// Прогресс до ближайшего тейка, 0–100.
  final int progressPercent;

  /// Состояние сопровождения: «TP1 ✓ 50% зафиксировано · SL в безубыток».
  final String stage;

  factory ActivePosition.fromJson(Map<String, dynamic> j) => ActivePosition(
        symbol: j['symbol'] as String,
        direction: Direction.parse(j['direction'] as String),
        entryLabel: j['entry_label'] as String,
        currentLabel: j['current_label'] as String,
        pnlLabel: j['pnl_label'] as String,
        pnlPositive: j['pnl_positive'] as bool? ?? true,
        progressPercent: (j['progress_percent'] as num).toInt(),
        stage: j['stage'] as String? ?? '',
      );
}

/// Строка журнала сделок (ТЗ §9: дата, инструмент, направление, статус, R).
class JournalEntry {
  const JournalEntry({
    required this.date,
    required this.symbol,
    required this.direction,
    required this.outcome,
    required this.rMultiple,
    this.signalId = '',
  });

  final String date;
  final String symbol;
  final Direction direction;

  /// Чем закрылась сделка: TP1/TP2/TP3/SL/эксп.
  final String outcome;

  /// Результат в единицах риска: +2.1, −1.0.
  final double rMultiple;

  /// Идея, из которой сделка родилась. Пустая — открывать нечего.
  final String signalId;

  /// «L» / «S» — колонка направления в журнале.
  String get directionLetter => direction.isLong ? 'L' : 'S';

  factory JournalEntry.fromJson(Map<String, dynamic> j) => JournalEntry(
        date: j['date'] as String,
        symbol: j['symbol'] as String,
        direction: Direction.parse(j['direction'] as String),
        outcome: j['outcome'] as String,
        rMultiple: (j['r_multiple'] as num).toDouble(),
      );
}

/// Плитка статистики: значение + подпись + тон.
class StatTile {
  const StatTile({required this.value, required this.label, this.tone = Tone.neutral});

  final String value;
  final String label;
  final Tone tone;

  factory StatTile.fromJson(Map<String, dynamic> j) => StatTile(
        value: j['value'] as String,
        label: j['label'] as String,
        tone: switch (j['tone'] as String? ?? 'neutral') {
          'positive' => Tone.positive,
          'negative' => Tone.negative,
          'accent' => Tone.accent,
          _ => Tone.neutral,
        },
      );

  Map<String, dynamic> toJson() => {'value': value, 'label': label, 'tone': tone.name};
}

/// Строка блока «На бирже»: то, что видит биржа, а не наш журнал.
class ExchangeRow {
  const ExchangeRow({
    required this.symbol,
    required this.venue,
    required this.detail,
    required this.position,
    this.tone = Tone.neutral,
  });

  final String symbol;

  /// «Bybit · testnet», «Т-Инвестиции · песочница».
  final String venue;

  /// Готовая подпись: объём, цена, состояние заявки.
  final String detail;

  /// true — позиция, false — активная заявка.
  final bool position;

  final Tone tone;
}

/// Прогресс допуска к живым деньгам.
class GateView {
  const GateView({
    required this.allowed,
    required this.reason,
    required this.progress,
  });

  final bool allowed;
  final String reason;

  /// Доля пути, 0–1.
  final double progress;
}

/// Отбракованный кандидат и то, чего отказ стоил.
class RejectionRow {
  const RejectionRow({
    required this.symbol,
    required this.reason,
    required this.moveLabel,
    required this.movePositive,
  });

  final String symbol;
  final String reason;

  /// Ход цены за сутки после отбраковки: «+1,8%» либо «—», если рано.
  final String moveLabel;
  final bool movePositive;
}

/// Сводка экрана «Сделки».
class TradesSummary {
  const TradesSummary({
    required this.equityTitle,
    required this.equityChange,
    required this.equityCurve,
    required this.stats,
    required this.positions,
    required this.journal,
    this.exchange = const [],
    this.gate,
    this.rejections = const [],
  });

  /// Позиции и заявки на биржах. Пусто — торговый доступ не подключён.
  final List<ExchangeRow> exchange;

  /// Допуск к живым деньгам. null — режим без исполнения сделок.
  final GateView? gate;

  /// Что не прошло фильтры и риск-лимиты.
  final List<RejectionRow> rejections;

  final String equityTitle;

  /// Доходность за период готовой подписью («+8,4%»).
  final String equityChange;

  /// Точки кривой эквити (в процентах от старта).
  final List<double> equityCurve;

  final List<StatTile> stats;
  final List<ActivePosition> positions;
  final List<JournalEntry> journal;

  factory TradesSummary.fromJson(Map<String, dynamic> j) => TradesSummary(
        equityTitle: j['equity_title'] as String? ?? 'Эквити · 30 дней',
        equityChange: j['equity_change'] as String? ?? '',
        equityCurve: (j['equity_curve'] as List<dynamic>? ?? const [])
            .map((e) => (e as num).toDouble())
            .toList(growable: false),
        stats: (j['stats'] as List<dynamic>? ?? const [])
            .map((e) => StatTile.fromJson(e as Map<String, dynamic>))
            .toList(growable: false),
        positions: (j['positions'] as List<dynamic>? ?? const [])
            .map((e) => ActivePosition.fromJson(e as Map<String, dynamic>))
            .toList(growable: false),
        journal: (j['journal'] as List<dynamic>? ?? const [])
            .map((e) => JournalEntry.fromJson(e as Map<String, dynamic>))
            .toList(growable: false),
      );
}
