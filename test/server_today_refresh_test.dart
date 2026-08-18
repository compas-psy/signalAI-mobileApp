import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/mock/demo_repository.dart';
import 'package:signalai/state/app_controller.dart';
import 'package:signalai/state/app_scope.dart';
import 'package:signalai/ui/screens/server_today_screen.dart';

void main() {
  testWidgets('Today pull-to-refresh asks the controller for fresh data',
      (tester) async {
    final controller = AppController(
      DemoRepository(),
      thinMode: true,
    );
    addTearDown(controller.dispose);

    await tester.pumpWidget(
      MaterialApp(
        home: AppScope(
          controller: controller,
          child: const ServerTodayScreen(),
        ),
      ),
    );
    await tester.pump();

    // The production screen must expose the platform-standard pull gesture,
    // including when its content is shorter than the viewport.
    expect(find.byType(RefreshIndicator), findsOneWidget);
    final list = tester.widget<ListView>(find.byType(ListView));
    expect(list.physics, isA<AlwaysScrollableScrollPhysics>());

    expect(controller.ideas, isEmpty);

    await tester.drag(find.byType(ListView), const Offset(0, 360));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 250));

    // DemoRepository makes the refresh observable without any network fixture.
    // Production thin mode uses the exact same AppController.refreshIdeas path,
    // which requests /today + data status + paper trades from the server.
    expect(controller.ideas, isNotEmpty);
  });
}
