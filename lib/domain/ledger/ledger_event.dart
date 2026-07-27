import 'money.dart';

/// Тип операции книги.
///
/// Разделение денежных потоков и результата — не формальность: пополнение
/// увеличивает капитал, но не является прибылью, и если смешать их в одном
/// типе, доходность будет врать ровно на размер пополнений.
enum LedgerEventType {
  /// Стартовый остаток при первом заведении счёта.
  opening('Начальный остаток', LedgerKind.cashflow),

  /// Внешний ввод денег.
  deposit('Пополнение', LedgerKind.cashflow),

  /// Внешний вывод денег.
  withdrawal('Вывод', LedgerKind.cashflow),

  /// Перевод между своими счетами: совокупный капитал не меняется.
  transfer('Перевод', LedgerKind.transfer),

  /// Покупка инструмента.
  buy('Покупка', LedgerKind.trade),

  /// Продажа инструмента.
  sell('Продажа', LedgerKind.trade),

  /// Комиссия биржи или брокера.
  fee('Комиссия', LedgerKind.cost),

  /// Фандинг бессрочного контракта: может быть в обе стороны.
  funding('Фандинг', LedgerKind.cost),

  /// Вариационная маржа по фьючерсу.
  variationMargin('Вариационная маржа', LedgerKind.income),

  /// Дивиденд по акции.
  dividend('Дивиденд', LedgerKind.income),

  /// Купон по облигации.
  coupon('Купон', LedgerKind.income),

  /// Процент на остаток.
  interest('Проценты', LedgerKind.income),

  /// Налог.
  tax('Налог', LedgerKind.cost),

  /// Конвертация валюты.
  exchange('Конвертация', LedgerKind.transfer),

  /// Исправление ошибочной записи. Историю не стирает — компенсирует.
  correction('Корректировка', LedgerKind.correction);

  const LedgerEventType(this.label, this.kind);

  final String label;
  final LedgerKind kind;

  static LedgerEventType parse(String raw) => LedgerEventType.values.firstWhere(
        (t) => t.name == raw,
        orElse: () => LedgerEventType.correction,
      );
}

/// Семейство операции — по нему считаются агрегаты «Сегодня» и «Капитала».
enum LedgerKind {
  /// Внешние деньги: в прибыль не входят.
  cashflow,

  /// Внутреннее перемещение: совокупный капитал не меняется.
  transfer,

  /// Сделка: меняет лоты и реализованный результат.
  trade,

  /// Доход: дивиденды, купоны, проценты, положительная вармаржа.
  income,

  /// Издержка: комиссии, фандинг, налоги.
  cost,

  /// Исправление.
  correction,
}

/// Контур капитала: сквозной атрибут, а не отдельный раздел.
enum Contour {
  core('Ядро'),
  tactical('Тактика'),
  risk('Риск');

  const Contour(this.label);
  final String label;

  static Contour? parse(String? raw) => raw == null
      ? null
      : Contour.values.where((c) => c.name == raw).firstOrNull;
}

/// Кто породил запись.
enum LedgerSource {
  /// Импорт от брокера.
  broker('брокер'),

  /// Внесено руками владельцем.
  manual('вручную'),

  /// Посчитано приложением (бумажный журнал, начисление).
  system('система');

  const LedgerSource(this.label);
  final String label;

  static LedgerSource parse(String raw) => LedgerSource.values.firstWhere(
        (s) => s.name == raw,
        orElse: () => LedgerSource.manual,
      );
}

/// Состояние сверки записи с брокером.
enum ReconcileStatus {
  matched('сверено'),
  pending('ждёт сверки'),
  mismatch('расхождение'),
  stale('данные устарели'),
  manual('ручной счёт');

  const ReconcileStatus(this.label);
  final String label;

  static ReconcileStatus parse(String raw) => ReconcileStatus.values.firstWhere(
        (s) => s.name == raw,
        orElse: () => ReconcileStatus.pending,
      );
}

/// Неизменяемая запись книги.
///
/// Единственный источник истины: остатки, позиции и результат — её проекции.
/// Запись не редактируется и не удаляется никогда; ошибка исправляется
/// событием [LedgerEventType.correction] со ссылкой [correctsId].
class LedgerEvent {
  const LedgerEvent({
    required this.id,
    required this.type,
    required this.effectiveAt,
    required this.accountId,
    required this.cashImpact,
    this.receivedAt,
    this.instrument,
    this.quantity,
    this.price,
    this.fee,
    this.tax,
    this.counterAccountId,
    this.contour,
    this.packageId,
    this.tradeId,
    this.ideaId,
    this.source = LedgerSource.manual,
    this.reconcile = ReconcileStatus.pending,
    this.brokerRef,
    this.correctsId,
    this.note,
    this.fxRate,
  });

  /// Неизменяемый идентификатор. По нему отбрасываются дубли импорта.
  final String id;

  final LedgerEventType type;

  /// Момент, к которому операция относится (UTC).
  final DateTime effectiveAt;

  /// Когда запись попала в книгу (UTC). Отличается от [effectiveAt] у
  /// импорта задним числом — и по этой разнице видно опоздания брокера.
  final DateTime? receivedAt;

  /// Счёт, на котором произошла операция.
  final String accountId;

  /// Изменение денег на счёте. Для покупки отрицательное, для продажи
  /// положительное; уже с учётом комиссии, если брокер её удержал сразу.
  final Money cashImpact;

  /// Тикер инструмента. null — чисто денежная операция.
  final String? instrument;

  /// Количество: положительное всегда, направление задаёт [type].
  final Quantity? quantity;

  /// Цена за единицу.
  final Money? price;

  final Money? fee;
  final Money? tax;

  /// Второй счёт перевода или конвертации.
  final String? counterAccountId;

  final Contour? contour;
  final String? packageId;

  /// Сделка (замысел), к которой относится операция.
  final String? tradeId;

  /// Идея, из которой выросла сделка.
  final String? ideaId;

  final LedgerSource source;
  final ReconcileStatus reconcile;

  /// Идентификатор ордера/исполнения у брокера.
  final String? brokerRef;

  /// Какую запись исправляет эта корректировка.
  final String? correctsId;

  final String? note;

  /// Курс пересчёта в базовую валюту на момент операции. Хранится вместе с
  /// операцией: пересчитывать историю по сегодняшнему курсу — значит менять
  /// прошлое.
  final double? fxRate;

  LedgerKind get kind => type.kind;

  /// Входит ли операция в инвестиционный результат.
  ///
  /// Пополнения, выводы и переводы — нет: это перемещение денег владельца,
  /// а не заработок.
  bool get affectsPnl =>
      kind == LedgerKind.trade || kind == LedgerKind.income || kind == LedgerKind.cost;

  Map<String, dynamic> toJson() => {
        'id': id,
        'type': type.name,
        'at': effectiveAt.toUtc().toIso8601String(),
        if (receivedAt != null) 'received': receivedAt!.toUtc().toIso8601String(),
        'account': accountId,
        'cash': cashImpact.toJson(),
        if (instrument != null) 'instrument': instrument,
        if (quantity != null) 'qty': quantity!.toJson(),
        if (price != null) 'price': price!.toJson(),
        if (fee != null) 'fee': fee!.toJson(),
        if (tax != null) 'tax': tax!.toJson(),
        if (counterAccountId != null) 'counter': counterAccountId,
        if (contour != null) 'contour': contour!.name,
        if (packageId != null) 'package': packageId,
        if (tradeId != null) 'trade': tradeId,
        if (ideaId != null) 'idea': ideaId,
        'source': source.name,
        'reconcile': reconcile.name,
        if (brokerRef != null) 'ref': brokerRef,
        if (correctsId != null) 'corrects': correctsId,
        if (note != null) 'note': note,
        if (fxRate != null) 'fx': fxRate,
      };

  static LedgerEvent fromJson(Map<String, dynamic> json) => LedgerEvent(
        id: json['id'] as String,
        type: LedgerEventType.parse(json['type'] as String),
        effectiveAt: DateTime.parse(json['at'] as String).toUtc(),
        receivedAt: json['received'] == null
            ? null
            : DateTime.parse(json['received'] as String).toUtc(),
        accountId: json['account'] as String,
        cashImpact: Money.fromJson(json['cash'] as Map<String, dynamic>),
        instrument: json['instrument'] as String?,
        quantity: json['qty'] == null
            ? null
            : Quantity.fromJson(json['qty'] as Map<String, dynamic>),
        price: json['price'] == null
            ? null
            : Money.fromJson(json['price'] as Map<String, dynamic>),
        fee: json['fee'] == null ? null : Money.fromJson(json['fee'] as Map<String, dynamic>),
        tax: json['tax'] == null ? null : Money.fromJson(json['tax'] as Map<String, dynamic>),
        counterAccountId: json['counter'] as String?,
        contour: Contour.parse(json['contour'] as String?),
        packageId: json['package'] as String?,
        tradeId: json['trade'] as String?,
        ideaId: json['idea'] as String?,
        source: LedgerSource.parse(json['source'] as String? ?? 'manual'),
        reconcile: ReconcileStatus.parse(json['reconcile'] as String? ?? 'pending'),
        brokerRef: json['ref'] as String?,
        correctsId: json['corrects'] as String?,
        note: json['note'] as String?,
        fxRate: (json['fx'] as num?)?.toDouble(),
      );
}
