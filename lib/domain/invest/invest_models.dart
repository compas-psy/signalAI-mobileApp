import '../models/signal.dart';

/// Фундаментальный паспорт акции — текущий срез из Invest API.
///
/// Истории показателей API не отдаёт, поэтому паспорт по построению не может
/// участвовать ни в ранжире, ни в бэктесте: сегодняшний P/E не был известен
/// год назад, и «проверить» его на прошлом нельзя без заглядывания в будущее.
/// Он показывается человеку для решения — и только.
class FundamentalsPassport {
  const FundamentalsPassport({
    required this.at,
    this.marketCap,
    this.peTtm,
    this.psTtm,
    this.pbTtm,
    this.evToEbitda,
    this.roe,
    this.netMargin,
    this.debtToEbitda,
    this.dividendYieldTtm,
    this.forwardDividendYield,
    this.revenueGrowthOneYear,
    this.revenueGrowthFiveYears,
    this.freeFloat,
    this.consensus,
    this.targetPrice,
    this.buyCount,
    this.holdCount,
    this.sellCount,
    this.nextReportDate,
  });

  /// Когда срез получен — паспорт без даты был бы неотличим от вечного.
  final DateTime at;

  final double? marketCap;
  final double? peTtm;
  final double? psTtm;
  final double? pbTtm;
  final double? evToEbitda;
  final double? roe;
  final double? netMargin;
  final double? debtToEbitda;
  final double? dividendYieldTtm;
  final double? forwardDividendYield;
  final double? revenueGrowthOneYear;
  final double? revenueGrowthFiveYears;
  final double? freeFloat;

  /// Консенсус аналитиков: BUY / HOLD / SELL словами API.
  final String? consensus;
  final double? targetPrice;
  final int? buyCount;
  final int? holdCount;
  final int? sellCount;

  /// Дата ближайшей отчётности — риск гэпа, который стоит знать до входа.
  final DateTime? nextReportDate;

  Map<String, dynamic> toJson() => {
        'at': at.toIso8601String(),
        'market_cap': marketCap,
        'pe': peTtm,
        'ps': psTtm,
        'pb': pbTtm,
        'ev_ebitda': evToEbitda,
        'roe': roe,
        'net_margin': netMargin,
        'debt_ebitda': debtToEbitda,
        'div_yield': dividendYieldTtm,
        'div_yield_fwd': forwardDividendYield,
        'rev_growth_1y': revenueGrowthOneYear,
        'rev_growth_5y': revenueGrowthFiveYears,
        'free_float': freeFloat,
        'consensus': consensus,
        'target_price': targetPrice,
        'buy': buyCount,
        'hold': holdCount,
        'sell': sellCount,
        'next_report': nextReportDate?.toIso8601String(),
      };

  static FundamentalsPassport? fromJson(Map<String, dynamic>? j) {
    if (j == null) return null;
    final at = DateTime.tryParse(j['at'] as String? ?? '');
    if (at == null) return null;
    double? d(String key) => (j[key] as num?)?.toDouble();
    int? i(String key) => (j[key] as num?)?.toInt();
    return FundamentalsPassport(
      at: at,
      marketCap: d('market_cap'),
      peTtm: d('pe'),
      psTtm: d('ps'),
      pbTtm: d('pb'),
      evToEbitda: d('ev_ebitda'),
      roe: d('roe'),
      netMargin: d('net_margin'),
      debtToEbitda: d('debt_ebitda'),
      dividendYieldTtm: d('div_yield'),
      forwardDividendYield: d('div_yield_fwd'),
      revenueGrowthOneYear: d('rev_growth_1y'),
      revenueGrowthFiveYears: d('rev_growth_5y'),
      freeFloat: d('free_float'),
      consensus: j['consensus'] as String?,
      targetPrice: d('target_price'),
      buyCount: i('buy'),
      holdCount: i('hold'),
      sellCount: i('sell'),
      nextReportDate: DateTime.tryParse(j['next_report'] as String? ?? ''),
    );
  }
}

/// Одна инвест-идея: технический сигнал по дневкам плюс паспорт для решения.
class InvestIdea {
  const InvestIdea({required this.signal, this.passport});

  final TradingSignal signal;

  /// null — паспорт недоступен (нет токена или API не ответил); идея от этого
  /// не исчезает: техника считается по публичным данным ISS.
  final FundamentalsPassport? passport;

  Map<String, dynamic> toJson() => {
        'signal': signal.toJson(),
        'passport': passport?.toJson(),
      };

  factory InvestIdea.fromJson(Map<String, dynamic> j) => InvestIdea(
        signal: TradingSignal.fromJson(j['signal'] as Map<String, dynamic>),
        passport:
            FundamentalsPassport.fromJson(j['passport'] as Map<String, dynamic>?),
      );
}

/// Выдача раздела «Инвест» за один ночной пересчёт.
class InvestDigest {
  const InvestDigest({
    required this.at,
    required this.universeSize,
    required this.liquidSize,
    required this.ideas,
    required this.rejections,
    this.passportNote = '',
  });

  /// Когда пересчитано — честность раздела начинается с этой строки.
  final DateTime at;

  /// Сколько бумаг на доске всего и сколько прошло фильтр ликвидности.
  final int universeSize;
  final int liquidSize;

  final List<InvestIdea> ideas;

  /// Причины отбраковки — видно, что скринер работал, а не выдал заготовку.
  final List<String> rejections;

  /// Причина отсутствия паспортов, если их нет («нет токена Т-Инвестиций»).
  final String passportNote;

  Map<String, dynamic> toJson() => {
        'at': at.toIso8601String(),
        'universe': universeSize,
        'liquid': liquidSize,
        'ideas': [for (final i in ideas) i.toJson()],
        'rejections': rejections,
        'passport_note': passportNote,
      };

  factory InvestDigest.fromJson(Map<String, dynamic> j) => InvestDigest(
        at: DateTime.tryParse(j['at'] as String? ?? '') ?? DateTime(2000),
        universeSize: (j['universe'] as num?)?.toInt() ?? 0,
        liquidSize: (j['liquid'] as num?)?.toInt() ?? 0,
        ideas: [
          for (final i in j['ideas'] as List<dynamic>? ?? const [])
            InvestIdea.fromJson(i as Map<String, dynamic>),
        ],
        rejections: [
          for (final r in j['rejections'] as List<dynamic>? ?? const []) r as String,
        ],
        passportNote: j['passport_note'] as String? ?? '',
      );
}
