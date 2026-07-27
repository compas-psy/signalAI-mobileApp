import 'dart:math' as math;

import 'black76.dart';

/// Контракт из цепочки биржи.
class OptionContract {
  const OptionContract({
    required this.secId,
    required this.assetCode,
    required this.strike,
    required this.kind,
    required this.expiration,
    this.last,
    this.theoretical,
    this.impliedVolatility,
    this.openInterest = 0,
    this.trades = 0,
    this.bid,
    this.ask,
    this.priceDecimals = 0,
    this.priceStep = 1,
  });

  /// Полный код контракта: его и вводят в терминале брокера.
  final String secId;

  /// Код базового актива: Si, RI, BR.
  final String assetCode;

  final double strike;
  final OptionKind kind;
  final DateTime expiration;

  /// Цена последней сделки. null — сделок не было.
  final double? last;

  /// Теоретическая цена биржи.
  final double? theoretical;

  /// Подразумеваемая волатильность биржи, в процентах.
  final double? impliedVolatility;

  /// Открытый интерес — главный признак того, что из позиции можно выйти.
  final int openInterest;

  /// Число сделок за день.
  final int trades;

  final double? bid;
  final double? ask;

  final int priceDecimals;
  final double priceStep;

  /// Цена для расчётов: сделка, теоретическая, середина спреда — в порядке
  /// убывания доверия. null — цены нет вовсе, и выдумывать её нельзя.
  double? get price {
    if (last != null && last! > 0) return last;
    if (theoretical != null && theoretical! > 0) return theoretical;
    if (bid != null && ask != null && bid! > 0 && ask! > 0) {
      return (bid! + ask!) / 2;
    }
    return null;
  }

  /// Дней до экспирации.
  int daysTo(DateTime now) => expiration.difference(now).inDays;

  /// Лет до экспирации — для модели.
  double yearsTo(DateTime now) =>
      math.max(0, expiration.difference(now).inSeconds / (365 * 24 * 3600));

  /// Достаточно ли ликвиден, чтобы входить.
  ///
  /// Порог намеренно низкий: он отсекает мёртвые страйки, а не выбирает
  /// хорошие. Решение о входе принимает человек, но входить в контракт, где
  /// нет ни открытого интереса, ни сделок, — значит войти и не выйти.
  bool get liquid => openInterest >= 100 || trades >= 10;
}

/// Направление ноги конструкции.
enum LegSide {
  buy('Покупка', 'BUY'),
  sell('Продажа', 'SELL');

  const LegSide(this.label, this.code);

  final String label;
  final String code;

  bool get isBuy => this == LegSide.buy;
}

/// Нога конструкции.
class OptionLeg {
  const OptionLeg({
    required this.contract,
    required this.side,
    required this.quantity,
    required this.price,
  });

  final OptionContract contract;
  final LegSide side;
  final int quantity;

  /// Цена, по которой считаем конструкцию.
  final double price;

  /// Знак денежного потока: покупка платит, продажа получает.
  double get cashFlow => (side.isBuy ? -1 : 1) * price * quantity;

  /// Выплата ноги при цене базового актива [spot] на экспирации.
  double payoffAt(double spot) {
    final intrinsic = contract.kind == OptionKind.call
        ? math.max(0.0, spot - contract.strike)
        : math.max(0.0, contract.strike - spot);
    return (side.isBuy ? 1 : -1) * intrinsic * quantity;
  }

  Greeks greeksAt(double futures, double volatility, DateTime now) => Black76.greeks(
        futures: futures,
        strike: contract.strike,
        years: contract.yearsTo(now),
        volatility: volatility,
        kind: contract.kind,
      ).scale((side.isBuy ? 1 : -1) * quantity.toDouble());
}

/// Тип конструкции.
enum StructureKind {
  bullCallSpread('Бычий колл-спред', 'умеренный рост'),
  bearPutSpread('Медвежий пут-спред', 'умеренное падение'),
  protectivePut('Защитный пут', 'страховка позиции'),
  coveredCall('Покрытый колл', 'боковик, продажа дорогой волатильности'),
  ironCondor('Железный кондор', 'низкая волатильность'),
  collar('Collar', 'защита позиции за счёт продажи роста');

  const StructureKind(this.label, this.thesis);

  final String label;

  /// Когда конструкция уместна — одной строкой.
  final String thesis;
}

/// Опционная конструкция с ограниченным риском.
class OptionStructure {
  const OptionStructure({
    required this.kind,
    required this.legs,
    required this.futuresPrice,
    required this.at,
    this.underlyingLots = 0,
  });

  final StructureKind kind;
  final List<OptionLeg> legs;

  /// Цена базового фьючерса на момент расчёта.
  final double futuresPrice;

  final DateTime at;

  /// Позиция в базовом активе, против которой строится конструкция:
  /// положительная для покрытого колла и защитного пута.
  final int underlyingLots;

  /// Чистый денежный поток: минус — дебет (платим), плюс — кредит (получаем).
  double get netCashFlow => legs.fold(0.0, (sum, leg) => sum + leg.cashFlow);

  bool get isDebit => netCashFlow < 0;

  /// Дней до ближайшей экспирации.
  int get daysToExpiry =>
      legs.map((l) => l.contract.daysTo(at)).reduce(math.min);

  /// Выплата конструкции при цене [spot] на экспирации, включая премию и
  /// базовый актив.
  double payoffAt(double spot) {
    var total = netCashFlow;
    for (final leg in legs) {
      total += leg.payoffAt(spot);
    }
    // Базовый актив входит своим результатом: без него покрытый колл выглядит
    // безубыточной раздачей денег, а это неправда.
    total += underlyingLots * (spot - futuresPrice);
    return total;
  }

  /// Максимальная прибыль. null — не ограничена.
  double? get maxProfit => _extreme(max: true);

  /// Максимальный убыток. null — не ограничен: такие конструкции приложение
  /// не предлагает.
  double? get maxLoss => _extreme(max: false);

  /// Не ограничен ли убыток конструкции целиком.
  ///
  /// Считается вместе с базовой позицией и с учётом того, что цена не может
  /// уйти ниже нуля. Поэтому покрытый колл проходит: его худший исход —
  /// обнуление фьючерса, который у владельца уже есть, минус полученная
  /// премия. А голая продажа колла не проходит: там убыток растёт вместе с
  /// ценой и упереться ему не во что.
  bool get addsUnboundedRisk => maxLoss == null;

  /// Точки безубытка — где кривая выплат пересекает ноль.
  List<double> get breakEvens {
    final points = _scanRange();
    final result = <double>[];
    for (var i = 1; i < points.length; i++) {
      final a = payoffAt(points[i - 1]);
      final b = payoffAt(points[i]);
      if (a == 0) result.add(points[i - 1]);
      if ((a < 0 && b > 0) || (a > 0 && b < 0)) {
        // Линейная интерполяция: между узлами выплата опциона линейна.
        final t = a.abs() / (a.abs() + b.abs());
        result.add(points[i - 1] + (points[i] - points[i - 1]) * t);
      }
    }
    return result;
  }

  /// Суммарные греки конструкции.
  Greeks greeks(double volatility) {
    var total = Greeks.zero;
    for (final leg in legs) {
      total = total + leg.greeksAt(futuresPrice, volatility, at);
    }
    // Базовый актив — чистая дельта единица на лот.
    return underlyingLots == 0
        ? total
        : total + Greeks(delta: underlyingLots.toDouble(), gamma: 0, theta: 0, vega: 0, rho: 0);
  }

  /// Зарабатывает ли конструкция временем.
  bool earnsTime(double volatility) => greeks(volatility).theta > 0;

  /// Вероятность выйти в прибыль — по вероятности оказаться за точкой
  /// безубытка. Ориентир риск-нейтральной модели, а не предсказание рынка.
  double? probabilityOfProfit(double volatility) {
    final points = breakEvens;
    if (points.isEmpty || volatility <= 0) return null;
    final years = legs.first.contract.yearsTo(at);
    if (years <= 0) return null;

    // Прибыль справа от единственной точки — считаем вероятность туда попасть.
    if (points.length == 1) {
      final profitAbove = payoffAt(points.first * 1.05) > 0;
      final probability = Black76.probabilityInTheMoney(
        futures: futuresPrice,
        strike: points.first,
        years: years,
        volatility: volatility,
        kind: profitAbove ? OptionKind.call : OptionKind.put,
      );
      return probability;
    }
    // Две точки: прибыль между ними (кондор) или вне их.
    final low = points.reduce(math.min);
    final high = points.reduce(math.max);
    final insideProfitable = payoffAt((low + high) / 2) > 0;
    final aboveLow = Black76.probabilityInTheMoney(
      futures: futuresPrice,
      strike: low,
      years: years,
      volatility: volatility,
      kind: OptionKind.call,
    );
    final aboveHigh = Black76.probabilityInTheMoney(
      futures: futuresPrice,
      strike: high,
      years: years,
      volatility: volatility,
      kind: OptionKind.call,
    );
    final between = aboveLow - aboveHigh;
    return insideProfitable ? between : 1 - between;
  }

  /// Есть ли неликвидные ноги — и какие.
  List<String> get illiquidLegs => [
        for (final leg in legs)
          if (!leg.contract.liquid) leg.contract.secId,
      ];

  double? _extreme({required bool max}) {
    final points = _scanRange();
    var best = payoffAt(points.first);
    for (final point in points) {
      final value = payoffAt(point);
      best = max ? math.max(best, value) : math.min(best, value);
    }

    // Вниз цена ограничена нулём: обнуление фьючерса — это худшее, что с ним
    // может случиться, и убыток там конечен. Вверх предела нет, поэтому
    // проверяем далёкую точку: если выплата продолжает уходить, риск (или
    // прибыль) не ограничены, и выдавать такую конструкцию за спред нельзя.
    final far = _scanRange(factor: 6).last;
    final value = payoffAt(far);
    final unbounded =
        max ? value > best + 1e-6 : value < best - 1e-6;
    return unbounded ? null : best;
  }

  /// Узлы для обхода: ноль, страйки с окрестностями и поле сверху.
  ///
  /// Ноль обязателен: без него падение базового актива выглядит бесконечным,
  /// и покрытый колл ошибочно попадает в «неограниченный риск».
  List<double> _scanRange({double factor = 1}) {
    final strikes = [for (final leg in legs) leg.contract.strike]..sort();
    final high = strikes.last * (1 + 0.3 * factor);
    final points = <double>{0, high, futuresPrice};
    for (final strike in strikes) {
      points
        ..add(strike)
        ..add(strike * 0.999)
        ..add(strike * 1.001);
    }
    final list = points.toList()..sort();
    return list;
  }
}
