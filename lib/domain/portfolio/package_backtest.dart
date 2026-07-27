import 'dart:math' as math;

import '../analysis/candle.dart';
import 'package_plan.dart';

/// Результат исторической симуляции пакета.
///
/// Это история, а не прогноз. Разница принципиальная: симуляция отвечает на
/// вопрос «как этот состав вёл себя раньше», и ничего не обещает про будущее.
/// Подписывать её словом «ожидаемая доходность» — значит выдавать прошлое за
/// гарантию, чего это приложение не делает.
class PackageBacktest {
  const PackageBacktest({
    required this.from,
    required this.to,
    required this.cagr,
    required this.maxDrawdown,
    required this.volatility,
    required this.worstYear,
    required this.worstYearReturn,
    required this.equity,
    required this.stress,
    required this.missing,
  });

  final DateTime from;
  final DateTime to;

  /// Среднегодовая доходность в процентах.
  final double cagr;

  /// Максимальная просадка в процентах — то, что придётся пересидеть.
  final double maxDrawdown;

  /// Годовая волатильность в процентах.
  final double volatility;

  /// Худший календарный год периода и его результат.
  final int worstYear;
  final double worstYearReturn;

  /// Кривая капитала, нормированная на единицу.
  final List<double> equity;

  /// Стресс-срезы: реальные периоды, а не выдуманные сценарии.
  final Map<String, double> stress;

  /// Классы, по которым не нашлось истории: их вес перераспределён, и об
  /// этом нужно сказать, а не молча посчитать по остальным.
  final List<String> missing;

  int get years => math.max(1, to.difference(from).inDays) ~/ 365;

  bool get isEmpty => equity.length < 2;
}

/// Историческая симуляция пакета по реальным дневным сериям.
///
/// Ребалансировка ежеквартальная: чаще — издержки съедают эффект, реже —
/// веса успевают уехать так, что портфель перестаёт быть тем, что задумали.
abstract final class PackageSimulator {
  /// [series] — дневные свечи по бенчмарку каждого класса.
  static PackageBacktest run({
    required PackagePlan plan,
    required Map<AssetClass, List<Candle>> series,
    int rebalanceEveryDays = 63,
  }) {
    final available = <AssetClass, List<Candle>>{};
    final missing = <String>[];
    for (final target in plan.targets) {
      final candles = series[target.assetClass];
      if (candles == null || candles.length < 30) {
        missing.add(target.assetClass.label);
      } else {
        available[target.assetClass] = candles;
      }
    }
    if (available.isEmpty) {
      return PackageBacktest(
        from: DateTime.now(),
        to: DateTime.now(),
        cagr: 0,
        maxDrawdown: 0,
        volatility: 0,
        worstYear: 0,
        worstYearReturn: 0,
        equity: const [],
        stress: const {},
        missing: missing,
      );
    }

    // Общий календарь: даты, которые есть у всех рядов. Иначе пропуск в одной
    // серии сдвигает остальные и симуляция считает несуществующий портфель.
    final dates = _commonDates(available.values);
    if (dates.length < 30) {
      return PackageBacktest(
        from: dates.isEmpty ? DateTime.now() : dates.first,
        to: dates.isEmpty ? DateTime.now() : dates.last,
        cagr: 0,
        maxDrawdown: 0,
        volatility: 0,
        worstYear: 0,
        worstYearReturn: 0,
        equity: const [],
        stress: const {},
        missing: missing,
      );
    }

    final prices = <AssetClass, Map<DateTime, double>>{
      for (final entry in available.entries)
        entry.key: {for (final c in entry.value) _day(c.time): c.close},
    };

    // Веса нормируем на доступные классы: если истории по крипте нет, её доля
    // не исчезает в никуда, а распределяется по остальным пропорционально.
    final weightSum = plan.targets
        .where((t) => available.containsKey(t.assetClass))
        .fold(0.0, (sum, t) => sum + t.weightPercent);
    final weights = <AssetClass, double>{
      for (final target in plan.targets)
        if (available.containsKey(target.assetClass))
          target.assetClass: target.weightPercent / weightSum,
    };

    // Количество «единиц» каждого класса на единицу капитала.
    var capital = 1.0;
    final units = <AssetClass, double>{};
    void rebalance(DateTime date) {
      for (final entry in weights.entries) {
        final price = prices[entry.key]![date]!;
        units[entry.key] = capital * entry.value / price;
      }
    }

    rebalance(dates.first);
    final equity = <double>[1];
    final byYear = <int, double>{};
    var yearStart = 1.0;
    var currentYear = dates.first.year;

    for (var i = 1; i < dates.length; i++) {
      final date = dates[i];
      capital = 0;
      for (final entry in units.entries) {
        capital += entry.value * prices[entry.key]![date]!;
      }
      equity.add(capital);

      if (date.year != currentYear) {
        byYear[currentYear] = capital / yearStart - 1;
        yearStart = capital;
        currentYear = date.year;
      }
      if (i % rebalanceEveryDays == 0) rebalance(date);
    }
    byYear[currentYear] = capital / yearStart - 1;

    final days = dates.last.difference(dates.first).inDays;
    final years = days / 365;
    final cagr = years <= 0 ? 0.0 : (math.pow(capital, 1 / years) - 1) * 100;

    var peak = equity.first;
    var drawdown = 0.0;
    for (final value in equity) {
      peak = math.max(peak, value);
      drawdown = math.min(drawdown, value / peak - 1);
    }

    final worst = byYear.entries.reduce((a, b) => a.value < b.value ? a : b);

    return PackageBacktest(
      from: dates.first,
      to: dates.last,
      cagr: cagr.toDouble(),
      maxDrawdown: drawdown * 100,
      volatility: _volatility(equity) * 100,
      worstYear: worst.key,
      worstYearReturn: worst.value * 100,
      equity: equity,
      stress: {
        for (final entry in byYear.entries)
          if (entry.key == 2020 || entry.key == 2022)
            '${entry.key}': entry.value * 100,
      },
      missing: missing,
    );
  }

  /// Даты, присутствующие во всех сериях.
  static List<DateTime> _commonDates(Iterable<List<Candle>> series) {
    Set<DateTime>? common;
    for (final candles in series) {
      final days = {for (final c in candles) _day(c.time)};
      common = common == null ? days : common.intersection(days);
    }
    final list = (common ?? <DateTime>{}).toList()..sort();
    return list;
  }

  static DateTime _day(DateTime time) =>
      DateTime.utc(time.year, time.month, time.day);

  /// Годовая волатильность дневных доходностей.
  static double _volatility(List<double> equity) {
    if (equity.length < 3) return 0;
    final returns = <double>[];
    for (var i = 1; i < equity.length; i++) {
      if (equity[i - 1] == 0) continue;
      returns.add(equity[i] / equity[i - 1] - 1);
    }
    if (returns.length < 2) return 0;
    final mean = returns.reduce((a, b) => a + b) / returns.length;
    final variance = returns
            .map((r) => (r - mean) * (r - mean))
            .reduce((a, b) => a + b) /
        (returns.length - 1);
    return math.sqrt(variance) * math.sqrt(252);
  }
}
