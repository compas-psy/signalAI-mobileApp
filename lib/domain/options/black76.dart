import 'dart:math' as math;

/// Тип опциона.
enum OptionKind {
  call('колл', 'C'),
  put('пут', 'P');

  const OptionKind(this.label, this.code);

  final String label;

  /// Буква в коде контракта MOEX и в выдаче биржи.
  final String code;

  static OptionKind parse(String? raw) =>
      (raw ?? '').toUpperCase().startsWith('P') ? OptionKind.put : OptionKind.call;
}

/// Греки опциона — чувствительность цены к рынку.
class Greeks {
  const Greeks({
    required this.delta,
    required this.gamma,
    required this.theta,
    required this.vega,
    required this.rho,
  });

  /// Сколько пунктов даст опцион на пункт движения базового актива.
  final double delta;

  /// Как быстро меняется дельта. Короткая гамма — то, из-за чего продавцы
  /// опционов теряют больше, чем заработали за месяц, за одну сессию.
  final double gamma;

  /// Сколько стоит день в пунктах. Отрицательная — время против нас.
  final double theta;

  /// Изменение цены на один процентный пункт волатильности.
  final double vega;

  /// Изменение цены на один процентный пункт ставки.
  final double rho;

  static const zero = Greeks(delta: 0, gamma: 0, theta: 0, vega: 0, rho: 0);

  Greeks operator +(Greeks other) => Greeks(
        delta: delta + other.delta,
        gamma: gamma + other.gamma,
        theta: theta + other.theta,
        vega: vega + other.vega,
        rho: rho + other.rho,
      );

  Greeks scale(double factor) => Greeks(
        delta: delta * factor,
        gamma: gamma * factor,
        theta: theta * factor,
        vega: vega * factor,
        rho: rho * factor,
      );
}

/// Модель Блэка-76: опцион на фьючерс.
///
/// Почему именно она, а не Блэк-Шоулз. На FORTS базовый актив опциона —
/// фьючерс, а не акция: дивидендов у него нет, а стоимость удержания уже
/// сидит в цене самого фьючерса. Блэк-Шоулз для такого опциона даёт
/// систематически смещённую цену, и греки по нему врут — особенно на
/// дальних сериях.
///
/// Маржируемость (на MOEX премия не платится, идёт вариационная маржа) на
/// саму формулу не влияет: она меняет денежные потоки, а не справедливую
/// стоимость. Это учтено в расчёте позиции, а не здесь.
abstract final class Black76 {
  /// Теоретическая цена опциона.
  ///
  /// [futures] — цена базового фьючерса, [strike] — страйк,
  /// [years] — время до экспирации в годах, [volatility] — годовая
  /// волатильность долей единицы (0,32 = 32%), [rate] — безрисковая ставка.
  static double price({
    required double futures,
    required double strike,
    required double years,
    required double volatility,
    required OptionKind kind,
    double rate = 0,
  }) {
    final discount = math.exp(-rate * years);
    // Экспирация или нулевая волатильность: цена — это внутренняя стоимость,
    // и никакая модель тут уже ничего не добавит.
    if (years <= 0 || volatility <= 0) {
      final intrinsic = kind == OptionKind.call
          ? math.max(0.0, futures - strike)
          : math.max(0.0, strike - futures);
      return intrinsic * discount;
    }

    final d = _d(futures, strike, years, volatility);
    return kind == OptionKind.call
        ? discount * (futures * _cdf(d.d1) - strike * _cdf(d.d2))
        : discount * (strike * _cdf(-d.d2) - futures * _cdf(-d.d1));
  }

  /// Греки опциона. Тета — на один календарный день, вега и ро — на один
  /// процентный пункт: так их читает человек, а не учебник.
  static Greeks greeks({
    required double futures,
    required double strike,
    required double years,
    required double volatility,
    required OptionKind kind,
    double rate = 0,
  }) {
    if (years <= 0 || volatility <= 0 || futures <= 0 || strike <= 0) {
      return Greeks.zero;
    }
    final discount = math.exp(-rate * years);
    final d = _d(futures, strike, years, volatility);
    final pdf = _pdf(d.d1);
    final sqrtT = math.sqrt(years);

    final delta = kind == OptionKind.call
        ? discount * _cdf(d.d1)
        : -discount * _cdf(-d.d1);
    final gamma = discount * pdf / (futures * volatility * sqrtT);
    final vega = discount * futures * pdf * sqrtT;

    // Тета Блэка-76: распад стоимости плюс дисконтирование обеих ног.
    final common = -discount * futures * pdf * volatility / (2 * sqrtT);
    final theta = kind == OptionKind.call
        ? common +
            rate * discount * (futures * _cdf(d.d1) - strike * _cdf(d.d2))
        : common +
            rate * discount * (strike * _cdf(-d.d2) - futures * _cdf(-d.d1));

    final rho = -years *
        price(
          futures: futures,
          strike: strike,
          years: years,
          volatility: volatility,
          kind: kind,
          rate: rate,
        );

    return Greeks(
      delta: delta,
      gamma: gamma,
      // В год 365 календарных дней: опцион дешевеет и в выходные.
      theta: theta / 365,
      vega: vega / 100,
      rho: rho / 100,
    );
  }

  /// Подразумеваемая волатильность из рыночной цены.
  ///
  /// Сначала биссекция — она сходится всегда, потому что цена монотонна по
  /// волатильности. Ньютон добавлен для точности на последних шагах, но с
  /// откатом к биссекции: у глубоко «вне денег» опционов вега почти нулевая,
  /// и чистый Ньютон там улетает в бесконечность.
  ///
  /// null — цена вне диапазона модели: ниже внутренней стоимости или выше
  /// цены базового актива. Это не ошибка расчёта, а неверная цена, и
  /// придумывать по ней волатильность нельзя.
  static double? impliedVolatility({
    required double marketPrice,
    required double futures,
    required double strike,
    required double years,
    required OptionKind kind,
    double rate = 0,
    double tolerance = 1e-6,
    int maxIterations = 100,
  }) {
    if (marketPrice <= 0 || years <= 0 || futures <= 0 || strike <= 0) return null;

    final intrinsic = price(
      futures: futures,
      strike: strike,
      years: years,
      volatility: 1e-9,
      kind: kind,
      rate: rate,
    );
    if (marketPrice < intrinsic - tolerance) return null;

    var low = 1e-4;
    var high = 5.0; // 500% годовых — предел, за которым модель бессмысленна
    var priceHigh = price(
      futures: futures,
      strike: strike,
      years: years,
      volatility: high,
      kind: kind,
      rate: rate,
    );
    if (marketPrice > priceHigh) return null;

    var guess = 0.3;
    for (var i = 0; i < maxIterations; i++) {
      final theoretical = price(
        futures: futures,
        strike: strike,
        years: years,
        volatility: guess,
        kind: kind,
        rate: rate,
      );
      final diff = theoretical - marketPrice;
      if (diff.abs() < tolerance) return guess;

      if (diff > 0) {
        high = guess;
      } else {
        low = guess;
      }

      final vega = greeks(
        futures: futures,
        strike: strike,
        years: years,
        volatility: guess,
        kind: kind,
        rate: rate,
      ).vega *
          100;
      final step = vega.abs() < 1e-8 ? double.nan : guess - diff / vega;
      // Шаг Ньютона принимается только внутри вилки: иначе он уводит за
      // границы, где цена уже посчитана и заведомо не подходит.
      guess = (step.isNaN || step <= low || step >= high) ? (low + high) / 2 : step;
    }
    return guess;
  }

  /// Вероятность, что опцион окажется «в деньгах» к экспирации.
  ///
  /// Это N(d2) — вероятность в риск-нейтральном мире, а не предсказание
  /// рынка. Полезна как ориентир «насколько далеко страйк», и подписывать её
  /// нужно именно так.
  static double probabilityInTheMoney({
    required double futures,
    required double strike,
    required double years,
    required double volatility,
    required OptionKind kind,
  }) {
    if (years <= 0 || volatility <= 0) {
      final itm = kind == OptionKind.call ? futures > strike : futures < strike;
      return itm ? 1 : 0;
    }
    final d = _d(futures, strike, years, volatility);
    return kind == OptionKind.call ? _cdf(d.d2) : _cdf(-d.d2);
  }

  static ({double d1, double d2}) _d(
    double futures,
    double strike,
    double years,
    double volatility,
  ) {
    final sqrtT = math.sqrt(years);
    final d1 = (math.log(futures / strike) + volatility * volatility / 2 * years) /
        (volatility * sqrtT);
    return (d1: d1, d2: d1 - volatility * sqrtT);
  }

  static double _pdf(double x) => math.exp(-x * x / 2) / math.sqrt(2 * math.pi);

  /// Функция стандартного нормального распределения.
  ///
  /// Приближение Абрамовица–Стигана: ошибка меньше 7,5e-8 — на порядки
  /// точнее, чем шаг цены любого контракта на бирже.
  static double _cdf(double x) {
    const a1 = 0.254829592;
    const a2 = -0.284496736;
    const a3 = 1.421413741;
    const a4 = -1.453152027;
    const a5 = 1.061405429;
    const p = 0.3275911;

    final sign = x < 0 ? -1 : 1;
    final z = x.abs() / math.sqrt2;
    final t = 1 / (1 + p * z);
    final y = 1 -
        (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-z * z);
    return 0.5 * (1 + sign * y);
  }
}
