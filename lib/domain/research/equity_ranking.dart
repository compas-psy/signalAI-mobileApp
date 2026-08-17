/// Daily company ranking shown in Portfolio → Signals.
///
/// This is not a trading idea: there is no side, entry, stop, quantity or
/// execution action. It is a server-ranked research queue that combines
/// measured fundamentals, D1 context and explicit pre-move diagnostics.
library;

class EquityRankingState {
  const EquityRankingState({
    required this.marketDay,
    required this.generatedAt,
    required this.items,
    this.dataAsOf,
    this.methodology = '',
    this.universeCount = 0,
    this.scoredCount = 0,
    this.reason = '',
    this.unavailableReason,
  });

  const EquityRankingState.unavailable(String reason)
      : marketDay = '',
        generatedAt = null,
        dataAsOf = null,
        methodology = '',
        universeCount = 0,
        scoredCount = 0,
        items = const [],
        reason = '',
        unavailableReason = reason;

  final String marketDay;
  final DateTime? generatedAt;
  final DateTime? dataAsOf;
  final String methodology;
  final int universeCount;
  final int scoredCount;
  final List<EquityRankingItem> items;
  final String reason;
  final String? unavailableReason;

  bool get isAvailable => unavailableReason == null;

  factory EquityRankingState.fromJson(Map<String, dynamic> json) =>
      EquityRankingState(
        marketDay: '${json['market_day'] ?? ''}',
        generatedAt: DateTime.tryParse('${json['generated_at'] ?? ''}'),
        dataAsOf: DateTime.tryParse('${json['data_as_of'] ?? ''}'),
        methodology: '${json['methodology'] ?? ''}',
        universeCount: _int(json['universe_count']),
        scoredCount: _int(json['scored_count']),
        items: [
          for (final item in json['items'] as List<dynamic>? ?? const [])
            if (item is Map<String, dynamic>) EquityRankingItem.fromJson(item),
        ],
        reason: '${json['reason'] ?? ''}',
      );

  static int _int(Object? raw) => switch (raw) {
        num n => n.toInt(),
        String s => int.tryParse(s) ?? 0,
        _ => 0,
      };
}

class EquityRankingItem {
  const EquityRankingItem({
    required this.rank,
    required this.instrumentId,
    required this.symbol,
    required this.title,
    required this.score,
    required this.tier,
    required this.eligible,
    required this.fundamentalScore,
    required this.technicalScore,
    this.rankChange,
    this.earlyScore,
    this.earlyState = '',
    this.earlyEligible = false,
    this.chasePenalty,
    this.whyNow = const [],
    this.confirmation = '',
    this.invalidation = '',
    this.return5d,
    this.return20d,
    this.return3m,
    this.return6m,
    this.breakoutDistance,
    this.turnoverRatio,
    this.accumulationScore,
    this.compressionRatio,
    this.catalystAdjustment = 0,
    this.technicalState = '',
    this.price,
    this.momentum3m,
    this.momentum6m,
    this.drawdown6m,
    this.volatility3m,
    this.fundamentalFacts = const [],
    this.technicalFacts = const [],
    this.warnings = const [],
    this.hypothesis,
  });

  final int rank;
  final int? rankChange;
  final String instrumentId;
  final String symbol;
  final String title;
  final double score;
  final String tier;
  final bool eligible;
  final double fundamentalScore;
  final double technicalScore;
  final double? earlyScore;
  final String earlyState;
  final bool earlyEligible;
  final double? chasePenalty;
  final List<String> whyNow;
  final String confirmation;
  final String invalidation;
  final double? return5d;
  final double? return20d;
  final double? return3m;
  final double? return6m;
  final double? breakoutDistance;
  final double? turnoverRatio;
  final double? accumulationScore;
  final double? compressionRatio;
  final double catalystAdjustment;
  final String technicalState;
  final double? price;
  // Backwards-compatible aliases for the previous client surface.
  final double? momentum3m;
  final double? momentum6m;
  final double? drawdown6m;
  final double? volatility3m;
  final List<String> fundamentalFacts;
  final List<String> technicalFacts;
  final List<String> warnings;
  final RankingHypothesis? hypothesis;

  bool get isLate => earlyState == 'поздно / не догонять';
  bool get isEarly => earlyEligible;

  factory EquityRankingItem.fromJson(Map<String, dynamic> json) =>
      EquityRankingItem(
        rank: _int(json['rank']),
        rankChange: _nullableInt(json['rank_change']),
        instrumentId: '${json['instrument_id'] ?? ''}',
        symbol: '${json['symbol'] ?? ''}',
        title: '${json['title'] ?? ''}',
        score: _num(json['score']),
        tier: '${json['tier'] ?? ''}',
        eligible: json['eligible'] == true,
        fundamentalScore: _num(json['fundamental_score']),
        technicalScore: _num(json['technical_score']),
        earlyScore: _nullable(json['early_score']),
        earlyState: '${json['early_state'] ?? ''}',
        earlyEligible: json['early_eligible'] == true,
        chasePenalty: _nullable(json['chase_penalty']),
        whyNow: _strings(json['why_now']),
        confirmation: '${json['confirmation'] ?? ''}',
        invalidation: '${json['invalidation'] ?? ''}',
        return5d: _nullable(json['return_5d']),
        return20d: _nullable(json['return_20d']),
        return3m: _nullable(json['return_3m']),
        return6m: _nullable(json['return_6m']),
        breakoutDistance: _nullable(json['breakout_distance']),
        turnoverRatio: _nullable(json['turnover_ratio']),
        accumulationScore: _nullable(json['accumulation_score']),
        compressionRatio: _nullable(json['compression_ratio']),
        catalystAdjustment: _num(json['catalyst_adjustment']),
        technicalState: '${json['technical_state'] ?? ''}',
        price: _nullable(json['price']),
        momentum3m: _nullable(json['momentum_3m'] ?? json['return_3m']),
        momentum6m: _nullable(json['momentum_6m'] ?? json['return_6m']),
        drawdown6m: _nullable(json['drawdown_6m']),
        volatility3m: _nullable(json['volatility_3m']),
        fundamentalFacts: _strings(json['fundamental_facts']),
        technicalFacts: _strings(json['technical_facts']),
        warnings: _strings(json['warnings']),
        hypothesis: switch (json['hypothesis']) {
          Map<String, dynamic> value => RankingHypothesis.fromJson(value),
          _ => null,
        },
      );

  static int _int(Object? raw) => switch (raw) {
        num n => n.toInt(),
        String s => int.tryParse(s) ?? 0,
        _ => 0,
      };

  static int? _nullableInt(Object? raw) => switch (raw) {
        null => null,
        num n => n.toInt(),
        String s => int.tryParse(s),
        _ => null,
      };

  static double _num(Object? raw) => switch (raw) {
        num n => n.toDouble(),
        String s => double.tryParse(s) ?? 0,
        _ => 0,
      };

  static double? _nullable(Object? raw) => switch (raw) {
        null => null,
        num n => n.toDouble(),
        String s => double.tryParse(s),
        _ => null,
      };

  static List<String> _strings(Object? raw) => [
        for (final value in raw as List<dynamic>? ?? const [])
          if ('$value'.trim().isNotEmpty) '$value',
      ];
}

class RankingHypothesis {
  const RankingHypothesis({
    required this.title,
    required this.direction,
    required this.state,
    required this.evidenceScore,
    required this.economicScore,
    required this.priority,
    this.asOf,
  });

  final String title;
  final String direction;
  final String state;
  final double evidenceScore;
  final double economicScore;
  final double priority;
  final DateTime? asOf;

  bool get positive => direction == 'positive';
  bool get negative => direction == 'negative';

  factory RankingHypothesis.fromJson(Map<String, dynamic> json) =>
      RankingHypothesis(
        title: '${json['title'] ?? ''}',
        direction: '${json['direction'] ?? ''}',
        state: '${json['state'] ?? ''}',
        evidenceScore: EquityRankingItem._num(json['evidence_score']),
        economicScore: EquityRankingItem._num(json['economic_score']),
        priority: EquityRankingItem._num(json['priority']),
        asOf: DateTime.tryParse('${json['as_of'] ?? ''}'),
      );
}
