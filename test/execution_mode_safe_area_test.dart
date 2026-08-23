import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/ui/execution_mode_shell.dart';

void main() {
  testWidgets('execution banner consumes top system inset exactly once',
      (tester) async {
    const bannerKey = Key('mode-banner');
    const bodyKey = Key('body');

    await tester.pumpWidget(
      const MediaQuery(
        data: MediaQueryData(
          size: Size(400, 800),
          padding: EdgeInsets.only(top: 36, bottom: 24),
        ),
        child: Directionality(
          textDirection: TextDirection.ltr,
          child: SizedBox(
            width: 400,
            height: 800,
            child: ExecutionModeInsetLayout(
              banner: SizedBox(key: bannerKey, height: 24),
              child: SafeArea(
                child: SizedBox(key: bodyKey),
              ),
            ),
          ),
        ),
      ),
    );

    expect(tester.getTopLeft(find.byKey(bannerKey)).dy, 36);
    // 36 px status inset + 24 px banner. The inner AppShell-like SafeArea must
    // not add the same top inset for a second time.
    expect(tester.getTopLeft(find.byKey(bodyKey)).dy, 60);
  });
}
