import '../local_store.dart';

/// Durable lifecycle of one server-approved idea mirrored to T-Invest Sandbox.
enum SandboxMirrorDeliveryStatus {
  pending,
  completed,
  repairRequired,
  notApplicable,
}

class SandboxMirrorDelivery {
  const SandboxMirrorDelivery({
    required this.ideaId,
    required this.entryRequestId,
    required this.protectiveStopRequestId,
    required this.status,
    required this.createdAt,
    required this.updatedAt,
    this.exchangeOrderId = '',
    this.lastError = '',
    this.protectiveStopVerifiedAt,
  });

  factory SandboxMirrorDelivery.pending(String ideaId) {
    final now = DateTime.now().toUtc();
    return SandboxMirrorDelivery(
      ideaId: ideaId,
      entryRequestId: stableTInvestRequestId(ideaId, 'entry'),
      protectiveStopRequestId: stableTInvestRequestId(ideaId, 'protective-stop'),
      status: SandboxMirrorDeliveryStatus.pending,
      createdAt: now,
      updatedAt: now,
    );
  }

  final String ideaId;
  final String entryRequestId;
  final String protectiveStopRequestId;
  final SandboxMirrorDeliveryStatus status;
  final DateTime createdAt;

  /// Last durable change/reconciliation of this delivery record.
  final DateTime updatedAt;

  final String exchangeOrderId;
  final String lastError;

  /// Provider accepted both the sandbox entry and its matching protective
  /// stop (or a later reconciliation explicitly found both).  The mirror
  /// never marks completed when the stop leg is rejected.
  final DateTime? protectiveStopVerifiedAt;

  bool get protectionVerified => protectiveStopVerifiedAt != null;

  bool get terminal => status == SandboxMirrorDeliveryStatus.completed ||
      status == SandboxMirrorDeliveryStatus.notApplicable;

  SandboxMirrorDelivery copyWith({
    SandboxMirrorDeliveryStatus? status,
    String? exchangeOrderId,
    String? lastError,
    DateTime? updatedAt,
    DateTime? protectiveStopVerifiedAt,
  }) {
    final nextStatus = status ?? this.status;
    final changed = status != null ||
        exchangeOrderId != null ||
        lastError != null ||
        protectiveStopVerifiedAt != null;
    final at = (updatedAt ?? (changed ? DateTime.now().toUtc() : this.updatedAt)).toUtc();
    final verified = protectiveStopVerifiedAt ??
        this.protectiveStopVerifiedAt ??
        (nextStatus == SandboxMirrorDeliveryStatus.completed ? at : null);
    return SandboxMirrorDelivery(
      ideaId: ideaId,
      entryRequestId: entryRequestId,
      protectiveStopRequestId: protectiveStopRequestId,
      status: nextStatus,
      createdAt: createdAt,
      updatedAt: at,
      exchangeOrderId: exchangeOrderId ?? this.exchangeOrderId,
      lastError: lastError ?? this.lastError,
      protectiveStopVerifiedAt: verified,
    );
  }

  Map<String, dynamic> toJson() => {
        'version': 2,
        'idea_id': ideaId,
        'entry_request_id': entryRequestId,
        'protective_stop_request_id': protectiveStopRequestId,
        'status': status.name,
        'created_at': createdAt.toIso8601String(),
        'updated_at': updatedAt.toIso8601String(),
        'exchange_order_id': exchangeOrderId,
        'last_error': lastError,
        if (protectiveStopVerifiedAt != null)
          'protective_stop_verified_at':
              protectiveStopVerifiedAt!.toIso8601String(),
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
    final created = DateTime.tryParse(json['created_at'] as String? ?? '')?.toUtc() ??
        DateTime.fromMillisecondsSinceEpoch(0, isUtc: true);
    return SandboxMirrorDelivery(
      ideaId: ideaId,
      entryRequestId: entryRequestId,
      protectiveStopRequestId: protectiveStopRequestId,
      status: status,
      createdAt: created,
      // v1 records had no update timestamp.  created_at is the honest lower
      // bound; inventing "now" would make an old unresolved delivery look fresh.
      updatedAt: DateTime.tryParse(json['updated_at'] as String? ?? '')?.toUtc() ?? created,
      exchangeOrderId: json['exchange_order_id'] as String? ?? '',
      lastError: json['last_error'] as String? ?? '',
      protectiveStopVerifiedAt: DateTime.tryParse(
        json['protective_stop_verified_at'] as String? ?? '',
      )?.toUtc(),
    );
  }
}

class SandboxMirrorDeliveryStore {
  SandboxMirrorDeliveryStore(this._store);

  final LocalStore _store;

  Future<SandboxMirrorDelivery?> load(String ideaId) async =>
      SandboxMirrorDelivery.fromJson(await _store.read(_name(ideaId)));

  Future<bool> save(SandboxMirrorDelivery delivery) =>
      _store.writeDurably(_name(delivery.ideaId), delivery.toJson());

  static String _name(String ideaId) =>
      'sandbox_mirror_${_compactIdeaUuid(ideaId)}';
}

/// Stable provider-side identity frozen by #45:
/// `e-<idea UUID without hyphens>` and `s-<idea UUID without hyphens>`.
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
