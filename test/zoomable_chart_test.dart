import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/ui/widgets/zoomable_chart.dart';

void main() {
  testWidgets('chart viewport enables pinch scale and free pan', (tester) async {
    await tester.pumpWidget(
      const Directionality(
        textDirection: TextDirection.ltr,
        child: Center(
          child: SizedBox(
            width: 380,
            height: 292,
            child: ZoomableChart(child: ColoredBox(color: Color(0xFF000000))),
          ),
        ),
      ),
    );

    final viewer = tester.widget<InteractiveViewer>(find.byType(InteractiveViewer));
    expect(viewer.scaleEnabled, isTrue);
    expect(viewer.panEnabled, isTrue);
    expect(viewer.panAxis, PanAxis.free);
    expect(viewer.minScale, 1);
    expect(viewer.maxScale, greaterThanOrEqualTo(6));
  });
}
