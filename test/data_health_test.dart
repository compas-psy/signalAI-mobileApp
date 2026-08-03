import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/state/navigation.dart';
import 'package:signalai/ui/widgets/section_header.dart';

/// Здоровье данных в шапке.
///
/// Готовая деградация в приложении вычислялась давно — `lastResultPartial`,
/// оговорка в `sourceNote`, журнал прогонов, — но не показывалась нигде.
/// Экран выглядел одинаково и когда всё посчитано, и когда половина
/// источников молчала: владелец видел пустую ленту и сделал единственный
/// доступный вывод — «приложение выглядит мёртвым».
Widget _host(Widget child) => Directionality(
      textDirection: TextDirection.ltr,
      child: Center(child: SizedBox(width: 420, child: child)),
    );

Widget _header({
  required DataHealth health,
  String detail = '',
  VoidCallback? onHealth,
}) =>
    _host(SectionHeader(
      section: AppSection.ideas,
      pill: 0,
      onPill: (_) {},
      mode: RiskMode.normal,
      dataAt: 'данные 12:40 · только что',
      health: health,
      healthDetail: detail,
      onHealth: onHealth,
    ));

void main() {
  testWidgets('при полных данных чип не занимает место', (tester) async {
    await tester.pumpWidget(_header(health: DataHealth.full, detail: 'что-то'));

    expect(find.byType(DataHealthChip), findsNothing);
    // Норма — состояние по умолчанию. Постоянно горящий чип приучает его не
    // замечать ровно к тому моменту, когда он наконец что-то значит.
    expect(find.text('что-то'), findsNothing);
  });

  testWidgets('неполный расчёт виден и назван причиной', (tester) async {
    await tester.pumpWidget(_header(
      health: DataHealth.partial,
      detail: 'Внимание: расчёт неполный — api.bybit.com закрыт для вашей страны',
    ));

    expect(find.byType(DataHealthChip), findsOneWidget);
    expect(find.text('ДАННЫЕ НЕПОЛНЫЕ'), findsOneWidget);
    expect(find.textContaining('закрыт для вашей страны'), findsOneWidget);
  });

  testWidgets('отсутствие расчёта — отдельное состояние, а не «неполное»',
      (tester) async {
    await tester.pumpWidget(_header(
      health: DataHealth.blind,
      detail: 'последний пересчёт не удался',
    ));

    expect(find.text('ДАННЫХ НЕТ'), findsOneWidget);
  });

  testWidgets('чип нажимается: подробность доступна целиком', (tester) async {
    var taps = 0;
    await tester.pumpWidget(_header(
      health: DataHealth.partial,
      detail: 'коротко',
      onHealth: () => taps++,
    ));

    await tester.tap(find.byType(DataHealthChip));
    expect(taps, 1);
  });

  test('деградация — это не режим риска', () {
    // CAUTION означает «допуск к живым деньгам урезан» — утверждение про
    // риск. Метить им молчащую биржу значит врать в обе стороны: владелец
    // видит ограничение риска там, где его нет, и не видит причины, по
    // которой пропали идеи.
    expect(DataHealth.full.isDegraded, isFalse);
    expect(DataHealth.partial.isDegraded, isTrue);
    expect(DataHealth.blind.isDegraded, isTrue);
  });
}
