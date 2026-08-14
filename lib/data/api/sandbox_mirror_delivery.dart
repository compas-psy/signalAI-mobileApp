import '../local_store.dart';

/// Durable lifecycle of one server-approved idea mirrored to T-Invest Sandbox.
enum SandboxMirrorDeliveryStatus {
  /// Intent is on disk, but broker outcome is not yet terminal.
  pending,

  /// Entry and protective stop were both accepted by the provider.
  completed,

  /// State cannot be repaired automatically without a dedicated reconciliation
  /// decision. Automatic order submission is blocked while this state holds.
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
      'sandbox_mirror_${_compactIdeaUuid(ideaId)}';
}

/// Stable provider-side identity frozen by #45:
/// - entry: `e-<idea UUID without hyphens>`
/// - protection: `s-<idea UUID without hyphens>`
///
/// Both are 34 characters, below T-Invest's 36-character limit, and derive
/// only from the immutable idea identity. A replay after process death
/// therefore reproduces exactly the same provider keys without random state.
String stableTInvestRequestId(String ideaId, String leg) {
  final prefix = switch (leg) {
    'entry' => 'e-',
    'protective-stop' => 's-',
    _ => throw ArgumentError.value(leg, 'leg', 'Unknown sandbox delivery leg'),
  };
  return '$prefix${_compactIdeaUuid(ideaId)}';
}

String _compactIdeaUuid(String ideaId) {
  final compact = ideaId.trim().toLowerCase().replaceAll('-', '');
  if (!RegExp(r'^[0-9a-f]{32}$').hasMatch(compact)) {
    throw FormatException('Idea id is not a UUID: $ideaId');
  }
  return compact;
}
