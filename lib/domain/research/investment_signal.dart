class InvestmentSignal {
  const InvestmentSignal({
    required this.rank,
    required this.instrumentId,
    required this.symbol,
    required this.title,
    required this.action,
    required this.actionLabel,
    required this.score,
    required this.horizon,
    required this.fundamentalScore,
    required this.technicalScore,
    required this.technicalState,
    required this.catalystAdjustment,
    required this.why,
    required this.risks,
    this.price,
    this.catalyst,
  });

  final int rank;
  final String instrumentId;
  final String symbol;
  final String title;
  final String action;
  final String actionLabel;
  final double score;
  final double? price;
  final String horizon;
  final double fundamentalScore;
  final double technicalScore;
  final String technicalState;
  final double catalystAdjustment;
  final List<String> why;
  final List<String> risks;
  final Map<String, dynamic>? catalyst;

  bool get isActionable => action == 'ACCUMULATE' || action == 'EARLY';

  factory InvestmentSignal.fromJson(Map<String, dynamic> json) => InvestmentSignal(
        rank: (json['rank'] as num?)?.toInt() ?? 0,
        instrumentId: '${json['instrument_id'] ?? ''}',
        symbol: '${json['symbol'] ?? ''}',
        title: '${json['title'] ?? json['symbol'] ?? ''}',
        action: '${json['action'] ?? 'WATCH'}',
        actionLabel: '${json['action_label'] ?? 'НАБЛЮДАТЬ'}',
        score: _number(json['score']) ?? 0,
        price: _number(json['price']),
        horizon: '${json['horizon'] ?? '6–12 мес.'}',
        fundamentalScore: _number(json['fundamental_score']) ?? 0,
        technicalScore: _number(json['technical_score']) ?? 0,
        technicalState: '${json['technical_state'] ?? ''}',
        catalystAdjustment: _number(json['catalyst_adjustment']) ?? 0,
        why: _strings(json['why']),
        risks: _strings(json['risks']),
        catalyst: json['catalyst'] is Map<String, dynamic>
            ? json['catalyst'] as Map<String, dynamic>
            : null,
      );
}

class InvestmentSignalsState {
  const InvestmentSignalsState({
    required this.signals,
    required this.universeCount,
    required this.scoredCount,
    required this.eligibleCount,
    this.marketDay,
    this.generatedAt,
    this.dataAsOf,
    this.reason = '',
    this.error = '',
  });

  const InvestmentSignalsState.failure(String message)
      : signals = const [],
        universeCount = 0,
        scoredCount = 0,
        eligibleCount = 0,
        marketDay = null,
        generatedAt = null,
        dataAsOf = null,
        reason = '',
        error = message;

  final List<InvestmentSignal> signals;
  final int universeCount;
  final int scoredCount;
  final int eligibleCount;
  final DateTime? marketDay;
  final DateTime? generatedAt;
  final DateTime? dataAsOf;
  final String reason;
  final String error;

  bool get failed => error.isNotEmpty;

  factory InvestmentSignalsState.fromJson(Map<String, dynamic> json) =>
      InvestmentSignalsState(
        signals: [
          for (final item in json['signals'] as List<dynamic>? ?? const [])
            if (item is Map<String, dynamic>) InvestmentSignal.fromJson(item),
        ],
        universeCount: (json['universe_count'] as num?)?.toInt() ?? 0,
        scoredCount: (json['scored_count'] as num?)?.toInt() ?? 0,
        eligibleCount: (json['eligible_count'] as num?)?.toInt() ?? 0,
        marketDay: _date(json['market_day']),
        generatedAt: _date(json['generated_at']),
        dataAsOf: _date(json['data_as_of']),
        reason: '${json['reason'] ?? ''}',
      );
}

double? _number(Object? value) => switch (value) {
      num v => v.toDouble(),
      String v => double.tryParse(v.replaceAll(',', '.')),
      _ => null,
    };

DateTime? _date(Object? value) {
  if (value == null) return null;
  final raw = '$value';
  final parsed = DateTime.tryParse(raw);
  if (parsed != null) return parsed;
  return DateTime.tryParse('${raw}T00:00:00');
}

List<String> _strings(Object? value) => [
      for (final item in value as List<dynamic>? ?? const [])
        if ('$item'.trim().isNotEmpty) '$item',
    ];
