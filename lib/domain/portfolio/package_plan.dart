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
/// Горизонт пакета (ТЗ §7.1). От него зависят ограничения оптимизации.
enum PackageHorizon {
  oneYear('1Y', '1 год', 1),
  fivePlus('5Y_PLUS', '5+ лет', 5);

  const PackageHorizon(this.code, this.label, this.years);

  final String code;
  final String label;

  /// Нижняя граница горизонта в годах.
  final int years;

  static PackageHorizon parse(String? code) => PackageHorizon.values
      .where((h) => h.code == code)
      .firstOrNull ??
      PackageHorizon.fivePlus;
}

/// Профиль риска пакета (ТЗ §7.1).
enum PackageRiskProfile {
  conservative('CONSERVATIVE', 'Консервативный'),
  optimal('OPTIMAL', 'Оптимальный'),
  aggressive('AGGRESSIVE', 'Рискованный');

  const PackageRiskProfile(this.code, this.label);

  final String code;
  final String label;

  static PackageRiskProfile parse(String? code) => PackageRiskProfile.values
      .where((p) => p.code == code)
      .firstOrNull ??
      PackageRiskProfile.optimal;
}

class PackagePlan {
  const PackagePlan({
    required this.id,
    required this.title,
    required this.thesis,
    required this.horizonYears,
    required this.targets,
    required this.invalidation,
    this.horizon = PackageHorizon.fivePlus,
    this.riskProfile = PackageRiskProfile.optimal,
    this.killConditions = const [],
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

  /// Горизонт по ТЗ §7.1: 1 год или 5+ лет.
  final PackageHorizon horizon;

  /// Профиль риска по ТЗ §7.1.
  final PackageRiskProfile riskProfile;

  /// Условия пересмотра отдельных инструментов (ТЗ §7.1, killConditions).
  final List<String> killConditions;

  /// Сумма весов. Должна быть 100 — иначе план не про весь капитал.
  double get totalWeight =>
      targets.fold(0.0, (sum, t) => sum + t.weightPercent);

  /// Потолок доли криптовалюты (ТЗ §7.1).
  static const cryptoCapPercent = 5.0;

  /// Доля криптовалюты в пакете.
  double get cryptoPercent => targets
      .where((t) => t.assetClass == AssetClass.crypto)
      .fold(0.0, (sum, t) => sum + t.weightPercent);

  /// Нарушения ограничений ТЗ §7.1.
  ///
  /// Возвращается список, а не bool: пакет с суммой весов 97% и криптой 10%
  /// сломан двумя разными способами, и чинить их надо по отдельности.
  List<String> violations() {
    final issues = <String>[];
    if ((totalWeight - 100).abs() > 0.01) {
      issues.add('сумма весов ${_pct(totalWeight)} вместо 100%');
    }
    if (cryptoPercent > cryptoCapPercent + 1e-9) {
      issues.add('крипта ${_pct(cryptoPercent)} выше потолка '
          '${_pct(cryptoCapPercent)}');
    }
    final seen = <AssetClass>{};
    for (final target in targets) {
      if (!seen.add(target.assetClass)) {
        issues.add('класс «${target.assetClass.label}» указан дважды');
      }
      if (target.weightPercent <= 0) {
        issues.add('нулевой вес у «${target.assetClass.label}»');
      }
    }
    return issues;
  }

  static String _pct(double v) =>
      '${v.toStringAsFixed(v == v.roundToDouble() ? 0 : 1).replaceAll('.', ',')}%';

  /// Пакеты по умолчанию: три профиля риска на каждый горизонт (ТЗ §7).
  ///
  /// Горизонт — не косметика. На годовом сроке просадку рынка акций
  /// пересидеть нельзя, поэтому доля акций во всех трёх профилях резко ниже,
  /// а несущая часть — короткий долг. На пятилетнем сроке всё наоборот:
  /// консервативный профиль может позволить себе длинные облигации.
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
          horizon: PackageHorizon.oneYear,
          riskProfile: PackageRiskProfile.conservative,
          killConditions: [
            'Ключевая ставка развернулась — доля флоатеров пересматривается',
            'Кредитный рейтинг эмитента в корпоративной части снижен',
            'Инфляция обгоняет доходность портфеля два квартала подряд',
          ],
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
          id: 'conservative_long',
          title: 'Консервативный',
          horizon: PackageHorizon.fivePlus,
          riskProfile: PackageRiskProfile.conservative,
          thesis: 'Тот же приоритет — сохранить, — но горизонт позволяет '
              'держать длинные облигации и небольшую долю акций: пять лет '
              'достаточно, чтобы пересидеть процентный цикл, а год — нет.',
          horizonYears: 5,
          invalidation: 'Инфляция устойчиво выше доходности портфеля два '
              'квартала подряд — состав пересматривается.',
          killConditions: [
            'Кредитный рейтинг эмитента в корпоративной части снижен',
            'Дюрация портфеля выросла выше горизонта владельца',
            'Инфляция обгоняет доходность портфеля два квартала подряд',
          ],
          targets: [
            PackageTarget(assetClass: AssetClass.ofz, weightPercent: 25),
            PackageTarget(assetClass: AssetClass.corpBonds, weightPercent: 20),
            PackageTarget(assetClass: AssetClass.stocks, weightPercent: 15),
            PackageTarget(assetClass: AssetClass.fxBonds, weightPercent: 13),
            PackageTarget(assetClass: AssetClass.gold, weightPercent: 12),
            PackageTarget(assetClass: AssetClass.floaters, weightPercent: 10),
            PackageTarget(assetClass: AssetClass.dividendStocks, weightPercent: 5),
          ],
        ),
        PackagePlan(
          id: 'balanced_short',
          title: 'Оптимальный',
          horizon: PackageHorizon.oneYear,
          riskProfile: PackageRiskProfile.optimal,
          thesis: 'Год — слишком короткий срок, чтобы пересидеть падение '
              'рынка акций. Поэтому доля акций здесь втрое меньше, чем на '
              'горизонте пяти лет, а несущая часть — короткий долг.',
          horizonYears: 1,
          invalidation: 'Деньги понадобились раньше срока — горизонт был '
              'выбран неверно, состав пересматривается под факт.',
          killConditions: [
            'Срок использования денег сдвинулся — горизонт пересматривается',
            'Ключевая ставка развернулась — доля флоатеров пересматривается',
            'Доля акций вышла за полосу — восстанавливается, а не догоняется',
          ],
          targets: [
            PackageTarget(assetClass: AssetClass.moneyMarket, weightPercent: 25),
            PackageTarget(assetClass: AssetClass.floaters, weightPercent: 20),
            PackageTarget(assetClass: AssetClass.corpBonds, weightPercent: 15),
            PackageTarget(assetClass: AssetClass.ofz, weightPercent: 12),
            PackageTarget(assetClass: AssetClass.gold, weightPercent: 10),
            PackageTarget(assetClass: AssetClass.fxBonds, weightPercent: 10),
            PackageTarget(assetClass: AssetClass.stocks, weightPercent: 8),
          ],
        ),
        PackagePlan(
          id: 'aggressive_short',
          title: 'Рискованный',
          horizon: PackageHorizon.oneYear,
          riskProfile: PackageRiskProfile.aggressive,
          thesis: 'Рискованный на годовом горизонте — это не «много акций», '
              'а «много того, что не зависит от одной ставки»: золото, '
              'валютный долг и небольшая крипта. Акции ограничены пятой '
              'частью: из их просадки за год выходят не всегда.',
          horizonYears: 1,
          invalidation: 'Просадка заставила продавать вне плана — профиль '
              'риска выбран неверно, переходим на оптимальный.',
          killConditions: [
            'Просадка заставила продавать вне плана',
            'Крипта превысила потолок 5% — восстанавливается вес, а не потолок',
            'Срок использования денег сдвинулся — горизонт пересматривается',
          ],
          targets: [
            PackageTarget(assetClass: AssetClass.stocks, weightPercent: 20),
            PackageTarget(assetClass: AssetClass.gold, weightPercent: 15),
            PackageTarget(assetClass: AssetClass.corpBonds, weightPercent: 15),
            PackageTarget(assetClass: AssetClass.floaters, weightPercent: 15),
            PackageTarget(assetClass: AssetClass.fxBonds, weightPercent: 12),
            PackageTarget(assetClass: AssetClass.moneyMarket, weightPercent: 10),
            PackageTarget(assetClass: AssetClass.ofz, weightPercent: 8),
            PackageTarget(assetClass: AssetClass.crypto, weightPercent: 5),
          ],
        ),
        PackagePlan(
          id: 'balanced',
          title: 'Оптимальный',
          horizon: PackageHorizon.fivePlus,
          riskProfile: PackageRiskProfile.optimal,
          killConditions: [
            'Доля акций дважды за год вышла за полосу без ребалансировки',
            'Ключевая ставка развернулась — облигационная часть пересматривается',
            'Крипта превысила потолок 5% — восстанавливается вес, а не потолок',
          ],
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
          horizon: PackageHorizon.fivePlus,
          riskProfile: PackageRiskProfile.aggressive,
          killConditions: [
            'Просадка заставила продавать вне плана — профиль выбран неверно',
            'Индекс акций сузился до нескольких эмитентов — состав пересматривается',
            'Крипта превысила потолок 5% — восстанавливается вес, а не потолок',
          ],
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
            PackageTarget(assetClass: AssetClass.dividendStocks, weightPercent: 13),
            // ТЗ §7.1 ставит жёсткий потолок 5% на криптовалюту. Прежние 10%
            // его нарушали; освободившиеся 5% ушли в дивидендные акции —
            // ближайший по роли класс, а не в тот, где красивее история.
            PackageTarget(assetClass: AssetClass.crypto, weightPercent: 5),
            PackageTarget(assetClass: AssetClass.ofz, weightPercent: 10),
            PackageTarget(assetClass: AssetClass.floaters, weightPercent: 5),
          ],
        ),
      ];

  Map<String, dynamic> toJson() => {
        'id': id,
        'title': title,
        'thesis': thesis,
        'horizon': horizonYears,
        'horizon_code': horizon.code,
        'risk_profile': riskProfile.code,
        'invalidation': invalidation,
        'kill_conditions': killConditions,
        'targets': [for (final t in targets) t.toJson()],
      };

  /// Пакеты выбранного горизонта — по одному на профиль риска.
  static List<PackagePlan> forHorizon(PackageHorizon horizon) =>
      [for (final p in defaults()) if (p.horizon == horizon) p];

  static PackagePlan fromJson(Map<String, dynamic> json) => PackagePlan(
        id: json['id'] as String? ?? '',
        title: json['title'] as String? ?? '',
        thesis: json['thesis'] as String? ?? '',
        horizonYears: (json['horizon'] as num?)?.toInt() ?? 5,
        horizon: PackageHorizon.parse(json['horizon_code'] as String?),
        riskProfile: PackageRiskProfile.parse(json['risk_profile'] as String?),
        invalidation: json['invalidation'] as String? ?? '',
        killConditions: [
          for (final c in json['kill_conditions'] as List? ?? const [])
            c as String,
        ],
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
