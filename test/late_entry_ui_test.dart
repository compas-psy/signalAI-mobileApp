import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/live_idea_source.dart';
import 'package:signalai/data/mock/demo_repository.dart';
import 'package:signalai/main.dart';
import 'package:signalai/state/app_scope.dart';
import 'package:signalai/ui/screens/ideas_screen.dart';
import 'package:signalai/ui/widgets/idea_chart_card.dart';

/// Regression for the owner-visible contradiction:
/// the chart said «ВХОД ПОЗДНИЙ» while the sticky bar still said
/// «Можно действовать» and offered a new paper trade.
void main() {
  testWidgets('late market progress removes the paper confirmation action',
      (tester) async {
    tester.view.physicalSize = const Size(412, 892);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(SignalAiApp(repository: DemoRepository()));
    await tester.pump(const Duration(milliseconds: 300));
    await tester.pump(const Duration(milliseconds: 400));
    await tester.tap(find.text('Идеи').last);
    await tester.pump(const Duration(milliseconds: 300));

    final controller = AppScope.read(tester.element(find.byType(IdeasScreen)));
    controller.openSignal(controller.currentSignal!.id);
    await tester.pump(const Duration(milliseconds: 400));

    // Use dynamic deliberately for the RED phase: current production widget
    // has no way to lift live progress to the detail screen, so this test
    // fails at runtime instead of failing to compile before the API exists.
    final dynamic chart = tester.widget<IdeaChartCard>(find.byType(IdeaChartCard));
    chart.onProgress(IdeaMarketProgress(
      status: 'MISSED_BEFORE_ENTRY',
      summary: 'Рынок дошёл до TP2 раньше подтверждённого касания входа.',
      asOf: DateTime.utc(2026, 9, 1, 17, 40),
      tpHitCount: 2,
      late: true,
      ambiguous: false,
      entryZoneSeen: false,
      entryNowAvailable: false,
    ));
    await tester.pump();

    expect(find.text('Сделка упущена · вход запрещён'), findsOneWidget);
    expect(find.text('Вход запрещён'), findsOneWidget);
    expect(find.text('Подтвердить план'), findsNothing);
    expect(find.text('Подтвердить paper-сделку'), findsNothing);
  });
}
