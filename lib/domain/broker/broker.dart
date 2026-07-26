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
  });

  final String symbol;
  final bool long;

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
  });

  final String symbol;
  final bool long;
  final double quantity;
  final double entryPrice;
  final double unrealizedPnl;
}

/// Торговый доступ к бирже.
abstract class Broker {
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
