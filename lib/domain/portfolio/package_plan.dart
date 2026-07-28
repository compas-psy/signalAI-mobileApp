import '../ledger/ledger_event.dart';
import '../ledger/money.dart';

/// Класс актива в пакете.
///
/// Инструменты-прокси заданы явно: пакет должен быть исполним, а не описан
/// словами. «30% в облигациях» без указания, чем именно, — это не план, а
/// пожелание.
///
/// Комиссии (TER) взяты из обзоров и **на бирже не проверены**: ISS их не
/// отдаёт. Поэтому они помечены как справочные и не участвуют ни в одном
/// расчёте — только показываются. Тикер проверяется сам собой: если биржа не
/// отдала по нему цену, разбор пакета отказывается считать количество.
enum AssetClass {
  ofz('ОФЗ', 'RGBITR', 'SBGB', ['SBGB', 'SUGB'], 'Гособлигации с фиксированным '
      'купоном: процентный риск, длинная дюрация'),
  corpBonds('Корп. облигации', 'RUCBTRNS', 'SBRB', ['SBRB', 'OBLG'],
      'Кредитный риск эмитентов — другая природа, чем у ОФЗ'),
  floaters('Флоатеры', '', 'TPAY', ['TPAY', 'SBFR'],
      'Переменный купон: почти нулевая дюрация, защита в цикле роста ставки'),
  moneyMarket('Денежный рынок', 'LQDT', 'LQDT', ['LQDT', 'SBMM'],
      'Обратное РЕПО с ЦК: ближайшее к деньгам'),
  stocks('Акции РФ', 'MCFTR', 'EQMX', ['EQMX', 'TMOS', 'SBMX'],
      'Широкий рынок. Индекс концентрирован: заметная доля веса на нескольких '
      'эмитентах и на нефтегазе'),
  dividendStocks('Дивидендные акции', 'IRDIVTR', 'DIVD', ['DIVD', 'TDIV'],
      'Не отдельный класс, а фактор внутри акций: корреляция с индексом высокая'),
  gold('Золото', 'RUGOLD', 'GOLD', ['GOLD', 'AKGD'],
      'Товарная нога. Единственный актив портфеля, не зависящий от рублёвой '
      'ставки и от прибыли российских компаний'),
  fxBonds('Валютные облигации', '', 'SBCB', ['SBCB', 'TLCB'],
      'Замещающие и валютные выпуски: защита от девальвации'),
  crypto('Крипта', 'BTCUSDT', 'BTCUSDT', ['BTCUSDT', 'ETHUSDT'],
      'Котируется в USDT — без курса к рублю в пакет не разбирается');

  const AssetClass(
    this.label,
    this.benchmark,
    this.proxy,
    this.examples,
    this.thesis,
  );

  final String label;

  /// Серия, по которой считается историческая доходность класса.
  /// Пустая строка — индекса нет, симуляция по классу невозможна.
  final String benchmark;

  /// Инструмент, которым класс покупается на самом деле.
  ///
  /// Без него пакет — пожелание, а не план: «30% в облигациях» нельзя
  /// выставить в терминале.
  final String proxy;

  /// Чем ещё можно закрыть класс, если основной инструмент недоступен.
  final List<String> examples;

  /// Зачем класс в пакете. Не украшение: без этого владелец не может решить,
  /// какой класс резать первым.
  final String thesis;

  /// Где инструмент торгуется.
  bool get onCrypto => this == AssetClass.crypto;

  /// Есть ли индекс, по которому класс можно симулировать исторически.
  bool get hasBenchmark => benchmark.isNotEmpty;

  /// Контур, к которому класс относится по умолчанию.
  Contour get contour => switch (this) {
        AssetClass.ofz ||
        AssetClass.corpBonds ||
        AssetClass.floaters ||
        AssetClass.moneyMarket ||
        AssetClass.stocks ||
        AssetClass.gold =>
          Contour.core,
        AssetClass.fxBonds || AssetClass.dividendStocks => Contour.tactical,
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
    double? bandPercent,
  }) : _band = bandPercent;

  final AssetClass assetClass;

  /// Целевой вес в процентах капитала пакета.
  final double weightPercent;

  final double? _band;

  /// Допустимое отклонение в процентных пунктах.
  ///
  /// По умолчанию — четверть веса, но не меньше 3 и не больше 8 п.п.
  /// Константа здесь не работает: при цели 10% полоса ±5 п.п. позволяет классу
  /// уполовиниться, не выйдя из полосы, а при цели 55% те же ±5 п.п. заставляют
  /// торговать крупный класс из-за косметического сдвига. Нижняя отсечка
  /// защищает мелкие доли от торговли на шуме, верхняя не даёт ядру уехать.
  double get bandPercent => _band ?? defaultBand(weightPercent);

  static double defaultBand(double weight) =>
      (0.25 * weight).clamp(3.0, 8.0).toDouble();

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
  /// Веса обоснованы **структурой рисков**, а не исторической доходностью.
  /// Подгонять состав под прошлое значит получить пакет, который отлично
  /// работал ровно в прошлом. Три соображения, по которым состав именно такой:
  ///
  /// 1. Облигации — не один риск, а три разных: процентный (ОФЗ с фиксом),
  ///    кредитный (корпоративные) и почти нулевая дюрация (флоатеры). Один
  ///    инструмент на «облигации» означает, что в цикле роста ставки защитная
  ///    часть портфеля даёт акционерную просадку.
  /// 2. Индекс акций концентрирован: заметная доля веса приходится на
  ///    несколько эмитентов и на нефтегаз. Половина капитала в нём — это не
  ///    «широкий рынок», а ставка на несколько бумаг.
  /// 3. Рублёвый портфель без валютной и товарной ноги полностью открыт
  ///    девальвации и не имеет ни одного актива, не зависящего от рублёвой
  ///    ставки. Золото и валютные облигации закрывают ровно это.
  ///
  /// Фьючерса как класса активов здесь нет. Фьючерс на индекс даёт ту же
  /// экспозицию, что и фонд акций: 65% акций плюс 10% фьючерса — это 75% беты
  /// одного индекса, подписанные как диверсификация. Плечо и хедж остаются
  /// инструментом тактики, а не долей в пакете.
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
            PackageTarget(assetClass: AssetClass.floaters, weightPercent: 20),
            PackageTarget(assetClass: AssetClass.moneyMarket, weightPercent: 20),
            PackageTarget(assetClass: AssetClass.ofz, weightPercent: 18),
            PackageTarget(assetClass: AssetClass.corpBonds, weightPercent: 17),
            PackageTarget(assetClass: AssetClass.gold, weightPercent: 10),
            PackageTarget(assetClass: AssetClass.fxBonds, weightPercent: 8),
            PackageTarget(assetClass: AssetClass.stocks, weightPercent: 7),
          ],
        ),
        PackagePlan(
          id: 'balanced',
          title: 'Оптимальный',
          thesis: 'Расти вместе с рынком акций, но переживать его падения без '
              'вынужденных продаж. Защита здесь — не доходность, а запас '
              'прочности, из которого докупают на просадке. Она стоит на трёх '
              'ногах: ставка, валюта, золото.',
          horizonYears: 5,
          invalidation: 'Доля акций дважды за год уходит за полосу без '
              'ребалансировки — дисциплина сломана, а не рынок.',
          targets: [
            PackageTarget(assetClass: AssetClass.stocks, weightPercent: 35),
            PackageTarget(assetClass: AssetClass.ofz, weightPercent: 15),
            PackageTarget(assetClass: AssetClass.corpBonds, weightPercent: 13),
            PackageTarget(assetClass: AssetClass.gold, weightPercent: 12),
            PackageTarget(assetClass: AssetClass.floaters, weightPercent: 10),
            PackageTarget(assetClass: AssetClass.fxBonds, weightPercent: 10),
            PackageTarget(assetClass: AssetClass.crypto, weightPercent: 5),
          ],
        ),
        PackagePlan(
          id: 'aggressive',
          title: 'Рискованный',
          thesis: 'Максимум роста на горизонте десяти лет. Цена — просадки, '
              'которые придётся пересидеть не продавая; если продадите на дне, '
              'пакет не подходит. Проверка приходит в месяцы, когда индекс на '
              'многолетнем минимуме, и три четверти капитала в нём этот тест '
              'не проходят.',
          horizonYears: 10,
          invalidation: 'Просадка заставила продавать вне плана — профиль '
              'риска выбран неверно, переходим на оптимальный.',
          targets: [
            PackageTarget(assetClass: AssetClass.stocks, weightPercent: 45),
            PackageTarget(assetClass: AssetClass.gold, weightPercent: 12),
            PackageTarget(assetClass: AssetClass.fxBonds, weightPercent: 10),
            PackageTarget(assetClass: AssetClass.dividendStocks, weightPercent: 10),
            PackageTarget(assetClass: AssetClass.crypto, weightPercent: 10),
            PackageTarget(assetClass: AssetClass.ofz, weightPercent: 8),
            PackageTarget(assetClass: AssetClass.floaters, weightPercent: 5),
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
