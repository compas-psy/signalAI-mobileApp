import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/models/signal.dart';
import 'package:signalai/ui/widgets/zoomable_chart.dart';

SignalChart _chart(String timeframe, int count) => SignalChart(
      timeframeLabel: timeframe,
      candles: [
        for (var i = 0; i < count; i++)
          ChartCandle(
            i.toDouble(),
            i + 2.0,
            i - 1.0,
            i + 1.0,
            DateTime.utc(2026, 8, 1).add(Duration(hours: i)),
          ),
      ],
    );

void main() {
  test('semantic viewport slices bars and translates zone indexes', () {
    final chart = SignalChart(
      timeframeLabel: '1H',
      candles: [
        for (var i = 0; i < 100; i++)
          ChartCandle(
            i.toDouble(),
            i + 2.0,
            i - 1.0,
            i + 1.0,
            DateTime.utc(2026, 8, 1).add(Duration(hours: i)),
          ),
      ],
      zones: const [
        ChartZone(from: 10, to: 12, startIndex: 10, label: 'old'),
        ChartZone(from: 80, to: 82, startIndex: 80, label: 'visible'),
        ChartZone(from: 95, to: 97, startIndex: 95, label: 'future'),
      ],
    );

    final visible = sliceSignalChart(chart, start: 70, bars: 20);

    expect(visible.candles, hasLength(20));
    expect(visible.candles.first.open, 70);
    expect(visible.candles.last.open, 89);
    expect(visible.zones, hasLength(2));
    expect(visible.zones[0].startIndex, 0);
    expect(visible.zones[1].startIndex, 10);
    expect(visible.zones.map((z) => z.label), isNot(contains('future')));
  });

  test('initial viewport keeps accepted H1/D1 density and tightens H4', () {
    expect(initialVisibleBars(_chart('1h', 100)), 48);
    expect(initialVisibleBars(_chart('4h', 100)), 32);
    expect(initialVisibleBars(_chart('1d', 100)), 48);
    expect(initialVisibleBars(_chart('4H', 20)), 20);
  });

  testWidgets('viewport no longer uses InteractiveViewer bitmap scaling', (tester) async {
    await tester.pumpWidget(
      const Directionality(
        textDirection: TextDirection.ltr,
        child: Center(
          child: SizedBox(
            width: 380,
            height: 292,
            child: ZoomableChart(
              child: ColoredBox(color: Color(0xFF000000)),
            ),
          ),
        ),
      ),
    );

    expect(find.byType(InteractiveViewer), findsNothing);
    expect(find.byType(ColoredBox), findsOneWidget);
  });
}
