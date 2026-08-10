class EquityRadarCard {
  const EquityRadarCard({
    required this.instrumentId,
    required this.symbol,
    required this.title,
    required this.score,
    required this.label,
    required this.researchScore,
    required this.technicalScore,
    required this.liquidityScore,
    this.latestPrice,
    this.return20d,
    this.return60d,
    this.medianDailyNotionalRub,
    this.hasResearchHypothesis = false,
    this.thesis = '',
    this.reasons = const [],
    this.warnings = const [],
  });

  final String instrumentId;
  final String symbol;
  final String title;
  final double score;
  final String label;
  final double researchScore;
  final double technicalScore;
  final double liquidityScore;
  final double? latestPrice;
  final double? return20d;
  final double? return60d;
  final double? medianDailyNotionalRub;
  final bool hasResearchHypothesis;
  final String thesis;
  final List<String> reasons;
  final List<String> warnings;

  factory EquityRadarCard.fromJson(Map<String, dynamic> j) => EquityRadarCard(
        instrumentId: '${j['instrument_id'] ?? ''}',
        symbol: '${j['symbol'] ?? ''}',
        title: '${j['title'] ?? ''}',
        score: _n(j['score']),
        label: '${j['label'] ?? ''}',
        researchScore: _n(j['research_score']),
        technicalScore: _n(j['technical_score']),
        liquidityScore: _n(j['liquidity_score']),
        latestPrice: _nullable(j['latest_price']),
        return20d: _nullable(j['return_20d_percent']),
        return60d: _nullable(j['return_60d_percent']),
        medianDailyNotionalRub: _nullable(j['median_daily_notional_rub']),
        hasResearchHypothesis: j['has_research_hypothesis'] == true,
        thesis: '${j['thesis'] ?? ''}',
        reasons: [for (final x in j['reasons'] as List<dynamic>? ?? const []) '$x'],
        warnings: [for (final x in j['warnings'] as List<dynamic>? ?? const []) '$x'],
      );

  static double _n(Object? value) => switch (value) {
        num n => n.toDouble(),
        String s => double.tryParse(s) ?? 0,
        _ => 0,
      };

  static double? _nullable(Object? value) => value == null ? null : _n(value);
}

class EquityRadarState {
  const EquityRadarState({
    required this.cards,
    this.generatedAt,
    this.asOf,
    this.cadence = '',
    this.method = '',
  });

  final List<EquityRadarCard> cards;
  final DateTime? generatedAt;
  final DateTime? asOf;
  final String cadence;
  final String method;

  factory EquityRadarState.fromJson(Map<String, dynamic> j) {
    final cards = [
      for (final item in j['cards'] as List<dynamic>? ?? const [])
        if (item is Map<String, dynamic>) EquityRadarCard.fromJson(item),
    ]..sort((a, b) => b.score.compareTo(a.score));
    return EquityRadarState(
      cards: cards,
      generatedAt: DateTime.tryParse('${j['generated_at'] ?? ''}')?.toLocal(),
      asOf: DateTime.tryParse('${j['as_of'] ?? ''}')?.toLocal(),
      cadence: '${j['cadence'] ?? ''}',
      method: '${j['method'] ?? ''}',
    );
  }
}
