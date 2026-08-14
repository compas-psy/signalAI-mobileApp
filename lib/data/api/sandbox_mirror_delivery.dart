import 'dart:convert';

import '../local_store.dart';

/// Durable lifecycle of one server-approved idea mirrored to T-Invest Sandbox.
enum SandboxMirrorDeliveryStatus {
  /// Intent is on disk, but broker outcome is not yet terminal.
  pending,

  /// Entry and protective stop were both accepted by the provider.
  completed,

  /// Provider outcome is unknown/rejected and the same stable ids may be retried.
  repairRequired,

  /// The server-approved idea does not require a T-Invest Sandbox mirror.
  notApplicable,
}

class SandboxMirrorDelivery {
  const SandboxMirrorDelivery({
    required this.ideaId,
    required this.entryRequestId,
    required this.protectiveStopRequestId,
    required this.status,
    this.exchangeOrderId = '',
    this.lastError = '',
  });

  factory SandboxMirrorDelivery.pending(String ideaId) => SandboxMirrorDelivery(
        ideaId: ideaId,
        entryRequestId: stableTInvestRequestId(ideaId, 'entry'),
        protectiveStopRequestId: stableTInvestRequestId(ideaId, 'protective-stop'),
        status: SandboxMirrorDeliveryStatus.pending,
      );

  final String ideaId;
  final String entryRequestId;
  final String protectiveStopRequestId;
  final SandboxMirrorDeliveryStatus status;
  final String exchangeOrderId;
  final String lastError;

  bool get terminal => status == SandboxMirrorDeliveryStatus.completed ||
      status == SandboxMirrorDeliveryStatus.notApplicable;

  SandboxMirrorDelivery copyWith({
    SandboxMirrorDeliveryStatus? status,
    String? exchangeOrderId,
    String? lastError,
  }) =>
      SandboxMirrorDelivery(
        ideaId: ideaId,
        entryRequestId: entryRequestId,
        protectiveStopRequestId: protectiveStopRequestId,
        status: status ?? this.status,
        exchangeOrderId: exchangeOrderId ?? this.exchangeOrderId,
        lastError: lastError ?? this.lastError,
      );

  Map<String, dynamic> toJson() => {
        'version': 1,
        'idea_id': ideaId,
        'entry_request_id': entryRequestId,
        'protective_stop_request_id': protectiveStopRequestId,
        'status': status.name,
        'exchange_order_id': exchangeOrderId,
        'last_error': lastError,
      };

  static SandboxMirrorDelivery? fromJson(Map<String, dynamic>? json) {
    if (json == null) return null;
    final ideaId = json['idea_id'] as String? ?? '';
    final entryRequestId = json['entry_request_id'] as String? ?? '';
    final protectiveStopRequestId =
        json['protective_stop_request_id'] as String? ?? '';
    if (ideaId.isEmpty || entryRequestId.isEmpty || protectiveStopRequestId.isEmpty) {
      return null;
    }
    final rawStatus = json['status'] as String? ?? '';
    final status = SandboxMirrorDeliveryStatus.values.firstWhere(
      (value) => value.name == rawStatus,
      orElse: () => SandboxMirrorDeliveryStatus.repairRequired,
    );
    return SandboxMirrorDelivery(
      ideaId: ideaId,
      entryRequestId: entryRequestId,
      protectiveStopRequestId: protectiveStopRequestId,
      status: status,
      exchangeOrderId: json['exchange_order_id'] as String? ?? '',
      lastError: json['last_error'] as String? ?? '',
    );
  }
}

/// One file per idea: a completed record is deliberately retained so an
/// idempotent server replay after an app restart cannot look like fresh work.
class SandboxMirrorDeliveryStore {
  SandboxMirrorDeliveryStore(this._store);

  final LocalStore _store;

  Future<SandboxMirrorDelivery?> load(String ideaId) async =>
      SandboxMirrorDelivery.fromJson(await _store.read(_name(ideaId)));

  /// False means the state exists only in process memory (or could not be
  /// written at all), so the caller must not start a broker side effect.
  Future<bool> save(SandboxMirrorDelivery delivery) =>
      _store.writeDurably(_name(delivery.ideaId), delivery.toJson());

  static String _name(String ideaId) =>
      'sandbox_mirror_${stableTInvestRequestId(ideaId, 'delivery').replaceAll('-', '')}';
}

/// Deterministic RFC-4122-layout UUIDv8 for provider idempotency.
///
/// T-Invest keeps caller-provided order ids as idempotency keys. A custom
/// UUIDv8 lets the same logical SignalAI idea reproduce exactly the same ids
/// after process death without a package dependency or random state.
String stableTInvestRequestId(String ideaId, String leg) {
  final left = _fnv64('signalai|a|$ideaId|$leg');
  final right = _fnv64('signalai|b|$ideaId|$leg');
  final raw = '${_hex64(left)}${_hex64(right)}'.split('');

  // Version 8 = application-defined UUID payload; keep RFC variant 10xx.
  raw[12] = '8';
  final variant = int.parse(raw[16], radix: 16);
  raw[16] = ((variant & 0x3) | 0x8).toRadixString(16);

  final hex = raw.join();
  return '${hex.substring(0, 8)}-'
      '${hex.substring(8, 12)}-'
      '${hex.substring(12, 16)}-'
      '${hex.substring(16, 20)}-'
      '${hex.substring(20, 32)}';
}

int _fnv64(String value) {
  const offset = 0xcbf29ce484222325;
  const prime = 0x100000001b3;
  const mask = 0xffffffffffffffff;
  var hash = offset;
  for (final byte in utf8.encode(value)) {
    hash ^= byte;
    hash = (hash * prime) & mask;
  }
  return hash;
}

String _hex64(int value) => value.toRadixString(16).padLeft(16, '0');
