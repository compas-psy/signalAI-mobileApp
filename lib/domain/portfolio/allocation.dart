import '../ledger/money.dart';
import 'package_plan.dart';

/// Цена и лот инструмента — то, чем заявка становится исполнимой.
class InstrumentQuote {
  const InstrumentQuote({
    required this.symbol,
    required this.price,
    this.lotSize = 1,
    this.venue = 'MOEX',
  });

  final String symbol;

  /// Цена одной штуки.
  final Money price;

  /// Штук в лоте. Заявка ставится лотами, а не штуками, и это не мелочь:
  /// «купить 137 паёв» при лоте 10 нельзя выставить вообще.
  final int lotSize;

  final String venue;

  Money get lotPrice => price.scaleBy(lotSize);
}

/// Одна строка разбора пакета: сколько чего покупать.
class AllocationLine {
  const AllocationLine({
    required this.assetClass,
    required this.symbol,
    required this.weightPercent,
    required this.targetValue,
    this.quote,
    this.lots,
    this.plannedValue,
    this.actualUnits = 0,
    this.actualValue,
    this.reason,
  });

  final AssetClass assetClass;

  /// Инструмент, которым класс покупается.
  final String symbol;

  final double weightPercent;

  /// Сколько денег должно стоять в этом классе.
  final Money targetValue;

  /// Цена и лот. null — цены нет, количество не посчитано.
  final InstrumentQuote? quote;

  /// Целевое количество лотов.
  final int? lots;

  /// Во что обойдётся это количество лотов — целевая сумма с округлением
  /// вниз до целого лота.
  final Money? plannedValue;

  /// Сколько штук уже есть по книге.
  final double actualUnits;

  /// Во сколько оценивается то, что уже есть.
  final Money? actualValue;

  /// Почему количество не посчитано. null — посчитано.
  final String? reason;

  /// Штук к целевому весу.
  int? get units => lots == null ? null : lots! * (quote?.lotSize ?? 1);

  /// Сколько докупить (положительное) или продать (отрицательное), в штуках.
  int? get deltaUnits {
    final target = units;
    if (target == null) return null;
    return target - actualUnits.round();
  }

  /// Та же разница деньгами.
  Money? get deltaValue {
    final delta = deltaUnits;
    final quote = this.quote;
    if (delta == null || quote == null) return null;
    return quote.price.scaleBy(delta);
  }

  bool get buy => (deltaUnits ?? 0) > 0;
}

/// Разбор пакета до инструментов: что и сколько покупать.
///
/// Владелец сформулировал требование прямо: нужно понимать, сколько каждого
/// инструмента в штуках и в деньгах должно быть куплено. Проценты в терминал
/// не выставляются.
class TargetAllocation {
  const TargetAllocation({
    required this.lines,
    required this.total,
  });

  final List<AllocationLine> lines;

  /// Капитал, который распределяем.
  final Money total;

  /// Сколько денег ушло в лоты.
  Money get planned {
    var sum = Money.zero(total.currency);
    for (final line in lines) {
      final value = line.plannedValue;
      if (value != null && value.currency == total.currency) sum += value;
    }
    return sum;
  }

  /// Остаток, который не лёг в целые лоты. Он не исчезает — это кэш, и он
  /// должен быть назван: иначе сумма строк не сходится с капиталом, и
  /// доверие к разбору заканчивается на первом же сложении.
  Money get residual => total - planned;

  /// Строки, по которым количество посчитать не удалось.
  List<AllocationLine> get unpriced =>
      [for (final line in lines) if (line.reason != null) line];

  /// Разбор пакета по ценам инструментов.
  ///
  /// [quotes] — котировки по символу прокси-инструмента класса.
  /// [holdings] — сколько штук уже есть, по символу.
  static TargetAllocation of({
    required PackagePlan plan,
    required Money total,
    Map<String, InstrumentQuote> quotes = const {},
    Map<String, double> holdings = const {},
  }) {
    final lines = <AllocationLine>[];
    for (final target in plan.targets) {
      final symbol = target.assetClass.proxy;
      final targetValue = total.scaleBy(target.weightPercent / 100);
      final quote = quotes[symbol];
      final held = holdings[symbol] ?? 0;

      if (quote == null || quote.price.isZero) {
        lines.add(AllocationLine(
          assetClass: target.assetClass,
          symbol: symbol,
          weightPercent: target.weightPercent,
          targetValue: targetValue,
          actualUnits: held,
          reason: 'цены $symbol нет — количество не считаем, '
              'чтобы не выдать выдуманное число за расчёт',
        ));
        continue;
      }

      if (quote.price.currency != total.currency) {
        lines.add(AllocationLine(
          assetClass: target.assetClass,
          symbol: symbol,
          weightPercent: target.weightPercent,
          targetValue: targetValue,
          quote: quote,
          actualUnits: held,
          reason: '$symbol котируется в ${quote.price.currency.code}, '
              'а пакет считается в ${total.currency.code} — нужен курс',
        ));
        continue;
      }

      // Округляем вниз: недобор остаётся деньгами, перебор требует ещё одной
      // сделки и денег, которых может не быть.
      final lots = quote.lotPrice.isZero
          ? 0
          : (targetValue.minor / quote.lotPrice.minor).floor();
      if (lots == 0) {
        lines.add(AllocationLine(
          assetClass: target.assetClass,
          symbol: symbol,
          weightPercent: target.weightPercent,
          targetValue: targetValue,
          quote: quote,
          actualUnits: held,
          reason: 'на целевую сумму не набирается даже один лот '
              '${quote.lotPrice.currency.code} ${quote.lotSize} × $symbol',
        ));
        continue;
      }

      lines.add(AllocationLine(
        assetClass: target.assetClass,
        symbol: symbol,
        weightPercent: target.weightPercent,
        targetValue: targetValue,
        quote: quote,
        lots: lots,
        plannedValue: quote.lotPrice.scaleBy(lots),
        actualUnits: held,
        actualValue: quote.price.scaleBy(held),
      ));
    }

    return TargetAllocation(lines: lines, total: total);
  }
}
