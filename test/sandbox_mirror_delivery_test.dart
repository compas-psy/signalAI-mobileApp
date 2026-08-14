import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/sandbox_mirror_delivery.dart';
import 'package:signalai/data/local_store.dart';

const _idea42 = '11111111-2222-4333-8444-555555555555';
const _idea77 = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee';
const _ideaMemory = '01234567-89ab-4cde-8fab-0123456789ab';

void main() {
  group('SandboxMirrorDeliveryStore', () {
    test('pending intent survives a new store instance and keeps provider ids', () async {
      final directory = await Directory.systemTemp.createTemp('signalai-mirror-');
      addTearDown(() => directory.delete(recursive: true));

      final first = SandboxMirrorDeliveryStore(LocalStore(directory: directory));
      final pending = SandboxMirrorDelivery.pending(_idea42);

      expect(await first.save(pending), isTrue);

      final second = SandboxMirrorDeliveryStore(LocalStore(directory: directory));
      final restored = await second.load(_idea42);

      expect(restored, isNotNull);
      expect(restored!.status, SandboxMirrorDeliveryStatus.pending);
      expect(restored.entryRequestId, pending.entryRequestId);
      expect(restored.protectiveStopRequestId, pending.protectiveStopRequestId);
    });

    test('completed state survives restart and is terminal', () async {
      final directory = await Directory.systemTemp.createTemp('signalai-mirror-');
      addTearDown(() => directory.delete(recursive: true));

      final first = SandboxMirrorDeliveryStore(LocalStore(directory: directory));
      final completed = SandboxMirrorDelivery.pending(_idea77).copyWith(
        status: SandboxMirrorDeliveryStatus.completed,
        exchangeOrderId: 'ORDER-77',
      );
      expect(await first.save(completed), isTrue);

      final restored = await SandboxMirrorDeliveryStore(
        LocalStore(directory: directory),
      ).load(_idea77);

      expect(restored!.terminal, isTrue);
      expect(restored.exchangeOrderId, 'ORDER-77');
    });

    test('refuses to claim durability when only memory storage is available', () async {
      final store = SandboxMirrorDeliveryStore(LocalStore.inMemory());

      expect(
        await store.save(SandboxMirrorDelivery.pending(_ideaMemory)),
        isFalse,
      );
    });
  });

  test('provider ids use the frozen e-/s- idea UUID identity', () {
    final first = SandboxMirrorDelivery.pending(_idea42);
    final second = SandboxMirrorDelivery.pending(_idea42);

    expect(first.entryRequestId, second.entryRequestId);
    expect(first.protectiveStopRequestId, second.protectiveStopRequestId);
    expect(
      first.entryRequestId,
      'e-11111111222243338444555555555555',
    );
    expect(
      first.protectiveStopRequestId,
      's-11111111222243338444555555555555',
    );
    expect(first.entryRequestId.length, 34);
    expect(first.protectiveStopRequestId.length, 34);
  });
}
