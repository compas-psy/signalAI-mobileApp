import '../ledger/ledger_event.dart';
import '../ledger/money.dart';

/// Класс актива в пакете.
///
/// Инструменты-прокси заданы явно: пакет должен быть исполним, а не описан
/// словами. «30% в облигациях» без указания, чем именно, — это не план, а
/// пожелание.
enum AssetClass {
  bonds('Облигации', 'RGBITR', ['OFZ', 'корпоративные']),
  stocks('Акции', 'IMOEX', ['SBER', 'LKOH', 'GAZP']),
  moneyMarket('Денежный рынок', 'LQDT', ['LQDT']),
  futures('Фьючерсы', 'IMOEX', ['SiZ5', 'RIZ5']),
  crypto('Крипта', 'BTCUSDT', ['BTC', 'ETH']);

  const AssetClass(this.label, this.benchmark, this.examples);

  final String label;

  /// Серия, по которой считается историческая доходность класса.
  final String benchmark;

  final List<String> examples;

  /// Контур, к которому класс относится по умолчанию.
  Contour get contour => switch (this) {
        AssetClass.bonds || AssetClass.stocks || AssetClass.moneyMarket => Contour.core,
        AssetClass.futures => Contour.tactical,
        AssetClass.crypto => Contour.risk,
      };
}

/// Доля пакета: целевой вес и полоса, внутри которой ничего не трогаем.
///
/// Полоса — не мелочь. Ребалансировка по любому отклонению съедает результат
/// комиссиями и налогом с прибыли; ребалансировка по календарю, наоборот,
/// пропускает моменты, когда вес уехал вдвое. Полоса решает обе задачи:
/// торгуем, только когда действительно разъехалось.
class PackageTarget {
  const PackageTarget({
    required this.assetClass,
    required this.weightPercent,
    this.bandPercent = 5,
  });

  final AssetClass assetClass;

  /// Целевой вес в процентах капитала пакета.
  final double weightPercent;

  /// Допустимое отклонение в процентных пунктах.
  final double bandPercent;

  double get lowerBound => weightPercent - bandPercent;
  double get upperBound => weightPercent + bandPercent;

  Map<String, dynamic> toJson() => {
        'class': assetClass.name,
        'weight': weightPercent,
        'band': bandPercent,
      };

  static PackageTarget fromJson(Map<String, dynamic> json) => PackageTarget(
        assetClass: AssetClass.values.firstWhere(
          (c) => c.name == json['class'],
          orElse: () => AssetClass.stocks,
        ),
        weightPercent: (json['weight'] as num?)?.toDouble() ?? 0,
        bandPercent: (json['band'] as num?)?.toDouble() ?? 5,
      );
}

/// Замысел пакета: состав, горизонт и правило, по которому он считается
/// сломанным.
class PackagePlan {
  const PackagePlan({
    required this.id,
    required this.title,
    required this.thesis,
    required this.horizonYears,
    required this.targets,
    required this.invalidation,
  });

  final String id;
  final String title;

  /// Зачем этот пакет существует.
  final String thesis;

  final int horizonYears;
  final List<PackageTarget> targets;

  /// При каком условии замысел считается сломанным. Без него пакет живёт
  /// вечно и превращается в кладбище решений.
  final String invalidation;

  /// Сумма весов. Должна быть 100 — иначе план не про весь капитал.
  double get totalWeight =>
      targets.fold(0.0, (sum, t) => sum + t.weightPercent);

  /// Три пакета по умолчанию.
  ///
  /// Веса — классические ориентиры для трёх профилей риска, а не подобранные
  /// под красивую доходность: подгонять состав под историю значит получить
  /// пакет, который отлично работал ровно в прошлом.
  static List<PackagePlan> defaults() => const [
        PackagePlan(
          id: 'conservative',
          title: 'Консервативный',
          thesis: 'Сохранить капитал и обогнать вклад. Просадка важнее '
              'доходности: из −10% выходят за год, из −40% — за пять лет.',
          horizonYears: 2,
          invalidation: 'Инфляция устойчиво выше доходности портфеля два '
              'квартала подряд — состав пересматривается.',
          targets: [
            PackageTarget(assetClass: AssetClass.bonds, weightPercent: 55, bandPercent: 7),
            PackageTarget(assetClass: AssetClass.moneyMarket, weightPercent: 30, bandPercent: 7),
            PackageTarget(assetClass: AssetClass.stocks, weightPercent: 15, bandPercent: 5),
          ],
        ),
        PackagePlan(
          id: 'balanced',
          title: 'Оптимальный',
          thesis: 'Расти вместе с рынком акций, но переживать его падения без '
              'вынужденных продаж. Облигации здесь — не доходность, а запас '
              'прочности, из которого докупают на просадке.',
          horizonYears: 5,
          invalidation: 'Доля акций дважды за год уходит за полосу без '
              'ребалансировки — дисциплина сломана, а не рынок.',
          targets: [
            PackageTarget(assetClass: AssetClass.stocks, weightPercent: 50, bandPercent: 7),
            PackageTarget(assetClass: AssetClass.bonds, weightPercent: 30, bandPercent: 7),
            PackageTarget(assetClass: AssetClass.moneyMarket, weightPercent: 15, bandPercent: 5),
            PackageTarget(assetClass: AssetClass.crypto, weightPercent: 5, bandPercent: 3),
          ],
        ),
        PackagePlan(
          id: 'aggressive',
          title: 'Рискованный',
          thesis: 'Максимум роста на горизонте десяти лет. Цена — просадки, '
              'которые придётся пересидеть не продавая; если продадите на дне, '
              'пакет не подходит.',
          horizonYears: 10,
          invalidation: 'Просадка заставила продавать вне плана — профиль '
              'риска выбран неверно, переходим на оптимальный.',
          targets: [
            PackageTarget(assetClass: AssetClass.stocks, weightPercent: 65, bandPercent: 8),
            PackageTarget(assetClass: AssetClass.crypto, weightPercent: 15, bandPercent: 5),
            PackageTarget(assetClass: AssetClass.futures, weightPercent: 10, bandPercent: 5),
            PackageTarget(assetClass: AssetClass.bonds, weightPercent: 10, bandPercent: 5),
          ],
        ),
      ];

  Map<String, dynamic> toJson() => {
        'id': id,
        'title': title,
        'thesis': thesis,
        'horizon': horizonYears,
        'invalidation': invalidation,
        'targets': [for (final t in targets) t.toJson()],
      };

  static PackagePlan fromJson(Map<String, dynamic> json) => PackagePlan(
        id: json['id'] as String? ?? '',
        title: json['title'] as String? ?? '',
        thesis: json['thesis'] as String? ?? '',
        horizonYears: (json['horizon'] as num?)?.toInt() ?? 5,
        invalidation: json['invalidation'] as String? ?? '',
        targets: [
          for (final t in json['targets'] as List? ?? const [])
            PackageTarget.fromJson(t as Map<String, dynamic>),
        ],
      );
}

/// Фактическое состояние класса активов внутри пакета.
class ClassPosition {
  const ClassPosition({
    required this.assetClass,
    required this.value,
    required this.actualPercent,
    required this.target,
  });

  final AssetClass assetClass;
  final Money value;
  final double actualPercent;
  final PackageTarget target;

  /// Отклонение от цели в процентных пунктах.
  double get drift => actualPercent - target.weightPercent;

  /// Вышли ли за полосу — только тогда есть повод торговать.
  bool get outOfBand =>
      actualPercent < target.lowerBound || actualPercent > target.upperBound;
}
