import '../enums.dart';

/// Площадка, через которую идёт исполнение.
enum BrokerId {
  /// Криптовалютные перпетуалы.
  bybit,

  /// Российский рынок: срочная секция MOEX через Т-Инвестиции.
  tinvest;

  static BrokerId parse(String? v) =>
      BrokerId.values.firstWhere((b) => b.name == v, orElse: () => BrokerId.bybit);

  String get title => switch (this) {
        BrokerId.bybit => 'Bybit',
        BrokerId.tinvest => 'Т-Инвестиции',
      };

  /// Какой рынок исполняет эта площадка.
  Market get market => switch (this) {
        BrokerId.bybit => Market.crypto,
        BrokerId.tinvest => Market.forts,
      };

  static BrokerId? forMarket(Market market) => switch (market) {
        Market.crypto => BrokerId.bybit,
        Market.forts => BrokerId.tinvest,
        // Акции пока не исполняются: скринер их не выдаёт.
        Market.moex => null,
      };
}

/// Куда уходит ордер: тестовая площадка или живые деньги.
enum TradingMode {
  /// Testnet биржи. Ключи отдельные, денег нет — здесь проверяется, что
  /// приложение действительно умеет выставлять и сопровождать ордера.
  testnet,

  /// Живой счёт. Открывается только после того, как бумажная статистика
  /// набрала выборку и оказалась прибыльной, см. [LiveTradingGate].
  live;

  static TradingMode parse(String v) =>
      TradingMode.values.firstWhere((m) => m.name == v, orElse: () => TradingMode.testnet);

  String get label => switch (this) {
        TradingMode.testnet => 'testnet — тренировочный счёт',
        TradingMode.live => 'live — реальные деньги',
      };

  /// Название режима на языке площадки: у Bybit это testnet, у брокера —
  /// песочница. Одно и то же по смыслу, но подписывать чужим словом нельзя.
  String labelFor(BrokerId broker) => switch ((this, broker)) {
        (TradingMode.testnet, BrokerId.bybit) => 'testnet — тренировочный счёт',
        (TradingMode.testnet, BrokerId.tinvest) => 'песочница — виртуальный счёт',
        (TradingMode.live, _) => 'live — реальные деньги',
      };
}

/// Чем подтверждается сделка на этом устройстве.
///
/// Отдельный тип вместо «доступно/недоступно»: отказ обязан называть причину,
/// иначе владельцу нечего чинить. Подтверждение — не украшение, а условие
/// отправки ордера: без него торговый контур не готов, что бы ни показывал
/// остальной интерфейс.
enum ConfirmMethod {
  /// Отпечаток или лицо.
  biometrics,

  /// ПИН, пароль или графический ключ устройства.
  credential,

  /// Подтверждать нечем.
  none;

  static ConfirmMethod parse(String? v) => switch (v) {
        'biometrics' => ConfirmMethod.biometrics,
        'credential' => ConfirmMethod.credential,
        _ => ConfirmMethod.none,
      };

  bool get available => this != ConfirmMethod.none;

  String get label => switch (this) {
        ConfirmMethod.biometrics => 'отпечаток или лицо',
        ConfirmMethod.credential => 'ПИН устройства',
        ConfirmMethod.none => 'нечем',
      };

  /// Что делать владельцу, если подтверждать нечем.
  String get hint => this == ConfirmMethod.none
      ? 'Ордер нельзя отправить: подтверждать сделку нечем. Включите '
          'блокировку экрана (ПИН, пароль или отпечаток) в настройках '
          'телефона — приложение спросит подтверждение перед каждой заявкой.'
      : '';
}

/// Итог последней проверки ключей площадки.
///
/// Хранится вместе с состоянием приложения: отказ биржи обязан пережить тост,
/// иначе владелец видит «ключи заданы» и не знает, что ими нельзя торговать.
class BrokerKeyCheck {
  const BrokerKeyCheck({required this.ok, required this.note, required this.at});

  /// Биржа приняла ключ.
  final bool ok;

  /// Её ответ либо причина отказа — как есть, без пересказа.
  final String note;
  final DateTime at;

  Map<String, dynamic> toJson() => {
        'ok': ok,
        'note': note,
        'at': at.toIso8601String(),
      };

  static BrokerKeyCheck? fromJson(Map<String, dynamic> j) {
    final at = DateTime.tryParse(j['at'] as String? ?? '');
    if (at == null) return null;
    return BrokerKeyCheck(
      ok: j['ok'] as bool? ?? false,
      note: j['note'] as String? ?? '',
      at: at,
    );
  }
}

/// Активная заявка на бирже.
class BrokerOrder {
  const BrokerOrder({
    required this.orderId,
    required this.symbol,
    required this.long,
    required this.quantity,
    required this.price,
    required this.status,
  });

  final String orderId;
  final String symbol;
  final bool long;
  final double quantity;
  final double price;

  /// Состояние заявки словами биржи.
  final String status;
}

/// Заявка на открытие позиции.
class OrderRequest {
  const OrderRequest({
    required this.symbol,
    required this.long,
    required this.quantity,
    required this.entry,
    required this.stopLoss,
    required this.takeProfit,
    this.stopEntry = false,
  });

  final String symbol;
  final bool long;

  /// Вход стоп-заявкой по пробою: заявка активируется, когда цена проходит
  /// уровень в сторону сделки, а не ждёт отката к нему.
  final bool stopEntry;

  /// Объём в единицах контракта биржи.
  final double quantity;

  /// Цена лимитной заявки.
  final double entry;

  /// Защитный стоп. Выставляется вместе с ордером, а не «потом руками»:
  /// позиция без стопа — это не сделка по стратегии.
  final double stopLoss;

  /// Первая цель. Остальные ступени ведёт сопровождение.
  final double takeProfit;
}

/// Что ответила биржа.
class OrderResult {
  const OrderResult({required this.accepted, required this.orderId, required this.message});

  const OrderResult.rejected(String reason)
      : accepted = false,
        orderId = '',
        message = reason;

  final bool accepted;
  final String orderId;
  final String message;
}

/// Позиция на счёте — то, что видит биржа, а не то, что мы себе записали.
class BrokerPosition {
  const BrokerPosition({
    required this.symbol,
    required this.long,
    required this.quantity,
    required this.entryPrice,
    required this.unrealizedPnl,
    this.stopLoss = 0,
    bool? protected,
  }) : _protected = protected;

  final String symbol;
  final bool long;
  final double quantity;
  final double entryPrice;
  final double unrealizedPnl;

  /// Уровень защитного стопа на бирже. Ноль — уровень неизвестен.
  ///
  /// У Bybit стоп прикреплён к позиции, и цена приходит вместе с ней. У
  /// брокера стоп живёт отдельной заявкой: там известен сам факт защиты, а
  /// цену пришлось бы искать по заявкам. Поэтому ноль здесь означает «цена
  /// неизвестна», а не «стопа нет», — см. [protectedByStop].
  final double stopLoss;

  /// Защищена ли позиция, когда цену стопа площадка не отдаёт.
  final bool? _protected;

  /// Есть ли на бирже защита по этой позиции.
  bool get protectedByStop => _protected ?? stopLoss > 0;

  /// Позиция без стопа. Ровно то состояние, в котором её нельзя оставлять.
  bool get unprotected => !protectedByStop;
}

/// Торговый доступ к бирже.
abstract class Broker {
  /// Кто исполняет.
  BrokerId get id;

  /// Название площадки для интерфейса.
  String get name;

  TradingMode get mode;

  /// Готов ли брокер принимать ордера: ключи на месте и приняты биржей.
  Future<bool> get isReady;

  /// Проверка ключей: возвращает описание счёта или бросает исключение
  /// с человеческой причиной.
  Future<String> checkAccess();

  Future<OrderResult> placeOrder(OrderRequest request);

  Future<List<BrokerPosition>> positions();

  /// Активные заявки. Пусто, если их нет или площадка их не отдаёт.
  Future<List<BrokerOrder>> orders();

  /// Позиции, у которых на бирже нет защитного стопа.
  ///
  /// Снять стоп может биржа по истечении заявки, брокер или сам владелец из
  /// своего приложения — приложение обязано это замечать, а не считать, что
  /// поставленный один раз стоп стоит вечно.
  Future<List<BrokerPosition>> unprotectedPositions();

  /// Поставить защитный стоп по уже открытой позиции.
  ///
  /// Возвращает false, если брокер отказал. Это не открытие позиции, а
  /// уменьшение риска по существующей — потому и разрешено фоновому контуру,
  /// которому торговать нельзя.
  Future<bool> placeProtectiveStop({
    required String symbol,
    required double stopPrice,
    required bool long,
    required double quantity,
  });

  /// Снятие всех активных заявок. Используется аварийной остановкой.
  Future<int> cancelAllOrders();
}

/// Отказ торгового доступа с человеческой формулировкой.
class BrokerException implements Exception {
  const BrokerException(this.message);

  final String message;

  @override
  String toString() => message;
}

/// Операция счёта, как её отдаёт брокер.
///
/// Сырьё для книги капитала: не «что есть сейчас», а «как оно возникло».
/// Разбор в события книги делает слой данных — здесь только то, что реально
/// пришло от площадки, без интерпретации.
class BrokerOperation {
  const BrokerOperation({
    required this.id,
    required this.type,
    required this.description,
    required this.at,
    required this.payment,
    required this.currency,
    this.price = 0,
    this.quantity = 0,
    this.instrument = '',
  });

  /// Идентификатор у брокера. По нему книга отбрасывает дубли импорта.
  final String id;

  /// Машинный тип: OPERATION_TYPE_BUY, OPERATION_TYPE_BROKER_FEE и так далее.
  final String type;

  /// Человеческое описание от брокера.
  final String description;

  final DateTime at;

  /// Изменение денег на счёте со знаком.
  final double payment;

  final String currency;
  final double price;
  final double quantity;
  final String instrument;
}
