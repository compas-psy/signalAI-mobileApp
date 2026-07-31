/// Пакет капитала: состав, посчитанный движком (§6, UX-ТЗ §7.1).
///
/// Заготовок здесь нет и быть не может. Всё, что показывает экран, приезжает
/// с сервера: доли, роли, тезисы и условия выхода. Клиент не додумывает ни
/// одного числа — доля, посчитанная на телефоне, разошлась бы с той, по
/// которой сервер считает ребаланс.
library;

/// Шаг конвейера сборки. Прогресс — это факт из базы, а не флаг.
class PortfolioStage {
  const PortfolioStage({
    required this.key,
    required this.name,
    required this.done,
    this.detail = '',
  });

  final String key;
  final String name;
  final bool done;
  final String detail;
}

/// Доля класса активов — полоса на экране, а не строка текста.
class ClassSlice {
  const ClassSlice({
    required this.assetClass,
    required this.label,
    required this.weight,
  });

  final String assetClass;
  final String label;
  final double weight;
}

class PackagePosition {
  const PackagePosition({
    required this.instrumentId,
    required this.symbol,
    required this.title,
    required this.assetClass,
    required this.weight,
    required this.role,
    required this.thesis,
    required this.killConditions,
    this.score,
    this.expectedReturn,
  });

  final String instrumentId;
  final String symbol;
  final String title;
  final String assetClass;
  final double weight;
  final String role;
  final String thesis;
  final String killConditions;
  final double? score;
  final double? expectedReturn;
}

/// Размер пакета §6.5.
enum PackageSize {
  simple('SIMPLE', 'Простой'),
  balanced('BALANCED', 'Сбалансированный'),
  maxPotential('MAX_POTENTIAL', 'Максимальный');

  const PackageSize(this.code, this.label);

  final String code;
  final String label;

  static PackageSize? parse(String raw) {
    for (final value in values) {
      if (value.code == raw) return value;
    }
    return null;
  }
}

class EnginePackage {
  const EnginePackage({
    required this.id,
    required this.profile,
    required this.size,
    required this.horizonYears,
    required this.expectedLow,
    required this.expectedHigh,
    required this.volatility,
    required this.drawdown,
    required this.rationale,
    required this.generatedAt,
    required this.validUntil,
    this.cvar95,
    this.meetsTarget = true,
    this.warnings = const [],
    this.stress = const {},
    this.mix = const [],
    this.positions = const [],
  });

  final String id;
  final String profile;
  final PackageSize size;
  final int horizonYears;

  /// Границы ожидаемой доходности. Это не обещание, а разброс по окнам
  /// проверки на истории — поэтому их два числа, а не одно.
  final double expectedLow;
  final double expectedHigh;

  final double volatility;
  final double drawdown;
  final double? cvar95;
  final String rationale;

  /// Состав годен, но рискованнее, чем обещает профиль. Прятать такой нельзя:
  /// на рынке, пережившем 2022 год, «не уложился в целевую просадку» — это
  /// свойство рынка, а не брак состава. Экран показывает отметку рядом с
  /// числами риска, владелец решает сам.
  final bool meetsTarget;
  final List<String> warnings;

  /// Худшее, что уже случалось с этим составом: день, месяц, квартал, год.
  final Map<String, String> stress;

  final List<ClassSlice> mix;
  final List<PackagePosition> positions;
  final DateTime generatedAt;
  final DateTime validUntil;

  bool get isStale => DateTime.now().isAfter(validUntil);
}

/// Ответ движка целиком: пакеты и состояние конвейера.
///
/// Статус едет всегда, даже когда пакетов нет. Пустой список сам по себе
/// ничего не объясняет: он одинаково выглядит и когда данных ещё нет, и
/// когда ни один состав не прошёл проверку на истории.
class PortfolioState {
  const PortfolioState({
    required this.stages,
    required this.packages,
    this.reason = '',
    this.universe = 0,
    this.generatedAt,
    this.unavailableReason,
  });

  const PortfolioState.unavailable(String reason)
      : stages = const [],
        packages = const [],
        reason = '',
        universe = 0,
        generatedAt = null,
        unavailableReason = reason;

  final List<PortfolioStage> stages;
  final List<EnginePackage> packages;
  final String reason;
  final int universe;
  final DateTime? generatedAt;
  final String? unavailableReason;

  bool get isAvailable => unavailableReason == null;

  /// Горизонты, по которым есть хоть один состав.
  List<int> get horizons {
    final seen = <int>{for (final p in packages) p.horizonYears};
    final list = seen.toList()..sort();
    return list;
  }

  List<EnginePackage> forHorizon(int years) =>
      [for (final p in packages) if (p.horizonYears == years) p];
}
