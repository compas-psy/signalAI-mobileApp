import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/api_client.dart';
import 'package:signalai/state/execution_mode_controller.dart';
import 'package:signalai/ui/widgets/execution_mode_banner.dart';

class _BannerApi extends ApiClient {
  _BannerApi({this.mode = 'CANARY', this.fail = false})
      : super(baseUrl: 'https://engine.test', deviceToken: '');

  final String mode;
  final bool fail;

  @override
  Future<Map<String, dynamic>> get(String path) async {
    if (fail) throw ApiException('mode unavailable', statusCode: 503);
    return {'mode': mode, 'updated_at': '2026-08-19T08:00:00Z'};
  }
}

void main() {
  testWidgets('banner always names the server-owned mode', (tester) async {
    final controller = ExecutionModeController(api: _BannerApi());
    await controller.load();

    await tester.pumpWidget(
      MaterialApp(
        home: ExecutionModeBanner(
          controller: controller,
          onManage: () {},
        ),
      ),
    );

    expect(find.textContaining('РЕЖИМ · CANARY'), findsOneWidget);
    expect(find.text('Управление'), findsOneWidget);
  });

  testWidgets('mode fetch failure is explicit and never defaults to PAPER',
      (tester) async {
    final controller = ExecutionModeController(api: _BannerApi(fail: true));
    await controller.load();

    await tester.pumpWidget(
      MaterialApp(
        home: ExecutionModeBanner(
          controller: controller,
          onManage: () {},
        ),
      ),
    );

    expect(find.textContaining('РЕЖИМ · НЕИЗВЕСТЕН'), findsOneWidget);
    expect(find.textContaining('PAPER'), findsNothing);
    expect(find.textContaining('серверный режим недоступен'), findsOneWidget);
  });
}
