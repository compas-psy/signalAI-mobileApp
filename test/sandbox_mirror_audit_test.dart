import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/sandbox_mirror_delivery.dart';

void main() {
  test('completed sandbox mirror persists protection verification audit', () {
    final updated = DateTime.utc(2026, 8, 17, 10, 34, 31);
    final verified = DateTime.utc(2026, 8, 17, 10, 35, 2);
    final delivery = SandboxMirrorDelivery.pending(
      '11111111-2222-3333-4444-555555555555',
    ).copyWith(
      status: SandboxMirrorDeliveryStatus.completed,
      exchangeOrderId: 'sandbox-order-42',
      updatedAt: updated,
      protectiveStopVerifiedAt: verified,
    );

    final restored = SandboxMirrorDelivery.fromJson(delivery.toJson());

    expect(restored, isNotNull);
    expect(restored!.exchangeOrderId, 'sandbox-order-42');
    expect(restored.updatedAt, updated);
    expect(restored.protectiveStopVerifiedAt, verified);
    expect(restored.protectionVerified, isTrue);
  });

  test('repair state keeps the last reconciliation error', () {
    final updated = DateTime.utc(2026, 8, 17, 10, 36);
    final delivery = SandboxMirrorDelivery.pending(
      'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
    ).copyWith(
      status: SandboxMirrorDeliveryStatus.repairRequired,
      lastError: 'защитный стоп не найден',
      updatedAt: updated,
    );

    final restored = SandboxMirrorDelivery.fromJson(delivery.toJson())!;

    expect(restored.lastError, contains('стоп'));
    expect(restored.updatedAt, updated);
    expect(restored.protectionVerified, isFalse);
  });
}
