import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/ledger/money.dart';
import 'package:signalai/domain/portfolio/package_plan.dart';
import 'package:signalai/domain/portfolio/rebalance.dart';
import 'package:signalai/ui/widgets/package_widgets.dart';

Money rub(int units) => Money(units * 100, Currency.rub);

final plan = PackagePlan(
  id: 'test',
  title: 'Тест',
  thesis: '',
  horizonYears: 5,
  invalidation: '',
  targets: const [
    PackageTarget(
        assetClass: AssetClass.stocks, weightPercent: 60, bandPercent: 5),
    PackageTarget(assetClass: AssetClass.ofz, weightPercent: 40, bandPercent: 5),
  ],
);

Future<void> pump(WidgetTester tester, List<ClassPosition>? positions) =>
    tester.pumpWidget(
      Directionality(
        textDirection: TextDirection.ltr,
        child: SingleChildScrollView(
          child: PackageTable(plan: plan, positions: positions),
        ),
      ),
    );

List<ClassPosition> positionsFor(int stocks, int ofz) => Rebalancer.positions(
      plan: plan,
      values: {
        AssetClass.stocks: rub(stocks),
        AssetClass.ofz: rub(ofz),
      },
      total: rub(stocks + ofz),
    );

void main() {
  testWidgets('таблица несёт все шесть колонок ТЗ §7', (tester) async {
    await pump(tester, positionsFor(600000, 400000));

    // Инструмент и роль.
    expect(find.textContaining(AssetClass.stocks.proxy), findsOneWidget);
    // Текущая и целевая доли в одной строке.
    expect(find.textContaining('→ 60%'), findsOneWidget);
    // Тезис класса.
    expect(find.text(AssetClass.stocks.thesis), findsOneWidget);
  });

  testWidgets('внутри полосы действие — не трогать', (tester) async {
    await pump(tester, positionsFor(620000, 380000));
    expect(find.textContaining('в полосе'), findsNWidgets(2));
  });

  testWidgets('перевес зовёт сократить, недовес — докупить', (tester) async {
    await pump(tester, positionsFor(700000, 300000));
    expect(find.textContaining('сократить до 60%'), findsOneWidget);
    expect(find.textContaining('докупить до 40%'), findsOneWidget);
  });

  testWidgets('без книги действие не притворяется выводом', (tester) async {
    // «Держать» без фактических долей читается как проверенное решение,
    // хотя это просто отсутствие данных.
    await pump(tester, null);
    expect(find.textContaining('доля не посчитана'), findsNWidgets(2));
    expect(find.textContaining('в полосе'), findsNothing);
    // Целевые доли при этом показаны.
    expect(find.text('60%'), findsOneWidget);
  });
}
