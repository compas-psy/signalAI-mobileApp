import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/sandbox_mirror_delivery.dart';
import 'package:signalai/data/local_store.dart';

void main() {
  group('SandboxMirrorDeliveryStore', () {
    test('pending intent survives a new store instance and keeps provider ids', () async {
      final directory = await Directory.systemTemp.createTemp('signalai-mirror-');
      addTearDown(() => directory.delete(recursive: true));

      final first = SandboxMirrorDeliveryStore(LocalStore(directory: directory));
      final pending = SandboxMirrorDelivery.pending('idea-42');

      expect(await first.save(pending), isTrue);

      final second = SandboxMirrorDeliveryStore(LocalStore(directory: directory));
      final restored = await second.load('idea-42');

      expect(restored, isNotNull);
      expect(restored!.status, SandboxMirrorDeliveryStatus.pending);
      expect(restored.entryRequestId, pending.entryRequestId);
      expect(restored.protectiveStopRequestId, pending.protectiveStopRequestId);
    });

    test('completed state survives restart and is terminal', () async {
      final directory = await Directory.systemTemp.createTemp('signalai-mirror-');
      addTearDown(() => directory.delete(recursive: true));

      final first = SandboxMirrorDeliveryStore(LocalStore(directory: directory));
      final completed = SandboxMirrorDelivery.pending('idea-77').copyWith(
        status: SandboxMirrorDeliveryStatus.completed,
        exchangeOrderId: 'ORDER-77',
      );
      expect(await first.save(completed), isTrue);

      final restored = await SandboxMirrorDeliveryStore(
        LocalStore(directory: directory),
      ).load('idea-77');

      expect(restored!.terminal, isTrue);
      expect(restored.exchangeOrderId, 'ORDER-77');
    });

    test('refuses to claim durability when only memory storage is available', () async {
      final store = SandboxMirrorDeliveryStore(LocalStore.inMemory());

      expect(await store.save(SandboxMirrorDelivery.pending('idea-memory')), isFalse);
    });
  });

  test('provider ids are stable UUIDs and separate entry from protective stop', () {
    final first = SandboxMirrorDelivery.pending('idea-stable');
    final second = SandboxMirrorDelivery.pending('idea-stable');

    expect(first.entryRequestId, second.entryRequestId);
    expect(first.protectiveStopRequestId, second.protectiveStopRequestId);
    expect(first.entryRequestId, isNot(first.protectiveStopRequestId));
    expect(
      RegExp(r'^[0-9a-f]{8}-[0-9a-f]{4}-8[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')
          .hasMatch(first.entryRequestId),
      isTrue,
    );
  });
}
