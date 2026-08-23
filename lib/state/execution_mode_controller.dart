import 'dart:convert';

import 'package:flutter/foundation.dart';

import '../data/api/api_client.dart';

/// Server-owned execution lifecycle. The phone never stores authoritative mode.
enum ServerExecutionMode {
  paper('PAPER'),
  sandbox('SANDBOX'),
  canary('CANARY'),
  live('LIVE');

  const ServerExecutionMode(this.wire);
  final String wire;

  static ServerExecutionMode parse(Object? value) {
    final raw = '$value'.toUpperCase();
    return values.firstWhere(
      (item) => item.wire == raw,
      orElse: () => throw FormatException('Неизвестный режим исполнения: $value'),
    );
  }
}

@immutable
class ExecutionModePreview {
  const ExecutionModePreview({
    required this.current,
    required this.target,
    required this.allowed,
    required this.blockers,
    required this.evidenceNotes,
    this.correlationId,
  });

  final ServerExecutionMode current;
  final ServerExecutionMode target;
  final bool allowed;
  final List<String> blockers;
  final List<String> evidenceNotes;
  final String? correlationId;

  factory ExecutionModePreview.fromJson(Map<String, dynamic> json) =>
      ExecutionModePreview(
        current: ServerExecutionMode.parse(json['current']),
        target: ServerExecutionMode.parse(json['target']),
        allowed: json['allowed'] == true,
        blockers: (json['blockers'] as List? ?? const [])
            .map((value) => '$value')
            .toList(growable: false),
        evidenceNotes: (json['evidence_notes'] as List? ?? const [])
            .map((value) => '$value')
            .toList(growable: false),
        correlationId: json['correlation_id']?.toString(),
      );
}

@immutable
class LiveActivationPreview {
  const LiveActivationPreview({
    required this.previewHash,
    required this.fromMode,
    required this.targetMode,
    required this.venue,
    required this.account,
    required this.capitalRub,
    required this.hardCaps,
    required this.configHash,
    required this.allowed,
    required this.blockers,
  });

  final String previewHash;
  final ServerExecutionMode fromMode;
  final ServerExecutionMode targetMode;
  final String venue;
  final String account;
  final String capitalRub;
  final Map<String, String> hardCaps;
  final String configHash;
  final bool allowed;
  final List<String> blockers;

  bool get confirmable =>
      fromMode == ServerExecutionMode.canary &&
      targetMode == ServerExecutionMode.live &&
      blockers.every((item) => item == 'explicit owner confirmation missing');

  factory LiveActivationPreview.fromJson(Map<String, dynamic> json) {
    final caps = <String, String>{};
    final rawCaps = json['hard_caps'];
    if (rawCaps is Map) {
      for (final entry in rawCaps.entries) {
        caps['${entry.key}'] = '${entry.value}';
      }
    }
    return LiveActivationPreview(
      previewHash: '${json['preview_hash'] ?? ''}',
      fromMode: ServerExecutionMode.parse(json['from_mode']),
      targetMode: ServerExecutionMode.parse(json['target_mode']),
      venue: '${json['venue'] ?? ''}',
      account: '${json['account'] ?? ''}',
      capitalRub: '${json['capital_rub'] ?? ''}',
      hardCaps: Map.unmodifiable(caps),
      configHash: '${json['config_hash'] ?? ''}',
      allowed: json['allowed'] == true,
      blockers: (json['blockers'] as List? ?? const [])
          .map((value) => '$value')
          .toList(growable: false),
    );
  }
}

class ExecutionModeController extends ChangeNotifier {
  ExecutionModeController({ApiClient? api}) : _api = api ?? ApiClient();

  final ApiClient _api;
  ServerExecutionMode? _mode;
  ExecutionModePreview? _preview;
  LiveActivationPreview? _livePreview;
  bool _busy = false;
  String? _error;

  ServerExecutionMode get mode => _mode ?? ServerExecutionMode.paper;
  bool get modeKnown => _mode != null;
  ExecutionModePreview? get preview => _preview;
  LiveActivationPreview? get livePreview => _livePreview;
  bool get busy => _busy;
  String? get error => _error;

  Future<void> load() async {
    await _guard(() async {
      final data = await _api.get('/api/v1/execution/mode');
      _mode = ServerExecutionMode.parse(data['mode']);
      _preview = null;
      _livePreview = null;
    });
  }

  Future<void> reconnect() async {
    _preview = null;
    _livePreview = null;
    _error = null;
    await load();
  }

  Future<ExecutionModePreview> previewMode(ServerExecutionMode target) async {
    late ExecutionModePreview parsed;
    await _guard(() async {
      // PAPER -> SANDBOX is the only generic risk-increasing transition whose
      // technical proof can be established without a strategy scope. Run (or
      // idempotently reconcile) the provider-confirmed LIMIT BUY/SELL round
      // trip first; the subsequent promotion preview reads the proof persisted
      // by that server endpoint. Downshifts never contact a broker.
      if (_mode == ServerExecutionMode.paper &&
          target == ServerExecutionMode.sandbox) {
        await _api.post(
          '/api/v1/tinvest-sandbox/smoke',
          idempotencyKey: 'mobile-sandbox-roundtrip-v1',
        );
      }
      final data = await _api.post(
        '/api/v1/execution/mode/preview',
        body: {'target': target.wire},
      );
      parsed = ExecutionModePreview.fromJson(data);
      _preview = parsed;
      _livePreview = null;
    });
    return parsed;
  }

  Future<void> confirmModeChange({required String reason}) async {
    final candidate = _preview;
    if (candidate == null || !candidate.allowed) {
      throw StateError('Сначала нужен разрешённый server preview перехода');
    }
    await _guard(() async {
      final data = await _api.post(
        '/api/v1/execution/mode/change',
        body: {
          'target': candidate.target.wire,
          'reason': reason,
        },
      );
      _mode = ServerExecutionMode.parse(data['mode']);
      _preview = null;
      _livePreview = null;
    });
  }

  Future<LiveActivationPreview> previewLive() async {
    late LiveActivationPreview parsed;
    await _guard(() async {
      final data = await _api.post('/api/v1/execution/live/preview');
      parsed = LiveActivationPreview.fromJson(data);
      _livePreview = parsed;
      _preview = null;
    });
    return parsed;
  }

  Future<void> confirmLive({required String idempotencyKey}) async {
    final candidate = _livePreview;
    if (candidate == null || !candidate.confirmable) {
      throw StateError('LIVE preview не допускает подтверждение');
    }
    await _guard(() async {
      final data = await _api.post(
        '/api/v1/execution/live/confirm',
        body: {
          'preview_hash': candidate.previewHash,
          'owner_confirmed': true,
        },
        idempotencyKey: idempotencyKey,
      );
      _mode = ServerExecutionMode.parse(data['mode']);
      _preview = null;
      _livePreview = null;
    });
  }

  void clearPreview() {
    if (_preview == null && _livePreview == null && _error == null) return;
    _preview = null;
    _livePreview = null;
    _error = null;
    notifyListeners();
  }

  Future<void> _guard(Future<void> Function() action) async {
    if (_busy) throw StateError('Операция режима уже выполняется');
    _busy = true;
    _error = null;
    notifyListeners();
    try {
      await action();
    } on ApiException catch (error) {
      _error = _apiErrorMessage(error);
      rethrow;
    } on Object catch (error) {
      _error = '$error';
      rethrow;
    } finally {
      _busy = false;
      notifyListeners();
    }
  }

  String _apiErrorMessage(ApiException error) {
    try {
      final decoded = jsonDecode(error.body);
      if (decoded is Map && decoded['detail'] != null) {
        final detail = decoded['detail'];
        if (detail is String) return detail;
        return jsonEncode(detail);
      }
    } on FormatException {
      // Keep bounded ApiException fallback below.
    }
    return 'HTTP ${error.statusCode}';
  }
}
