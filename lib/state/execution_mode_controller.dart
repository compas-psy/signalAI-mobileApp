import 'package:flutter/widgets.dart';

import '../data/api/api_client.dart';

/// Server-owned execution lifecycle exposed to the thin client.
enum ServerExecutionMode {
  paper('PAPER'),
  sandbox('SANDBOX'),
  canary('CANARY'),
  live('LIVE'),
  unknown('НЕИЗВЕСТЕН');

  const ServerExecutionMode(this.label);
  final String label;

  static ServerExecutionMode parse(Object? raw) => switch ('$raw'.toUpperCase()) {
        'PAPER' => paper,
        'SANDBOX' => sandbox,
        'CANARY' => canary,
        'LIVE' => live,
        _ => unknown,
      };
}

@immutable
class ExecutionModePreview {
  const ExecutionModePreview({
    required this.current,
    required this.target,
    required this.allowed,
    required this.blockers,
  });

  final ServerExecutionMode current;
  final ServerExecutionMode target;
  final bool allowed;
  final List<String> blockers;
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

  /// The preview endpoint deliberately reports the missing *second* owner
  /// confirmation as a blocker. It is confirmable only when that is the sole
  /// remaining blocker (or the server already reports the preview as allowed).
  bool get confirmable => allowed ||
      (blockers.isNotEmpty &&
          blockers.every((item) => item == 'explicit owner confirmation missing'));
}

@immutable
class LiveActivationResult {
  const LiveActivationResult({
    required this.previewHash,
    required this.idempotencyKey,
    required this.status,
    required this.mode,
    required this.blockers,
  });

  final String previewHash;
  final String idempotencyKey;
  final String status;
  final ServerExecutionMode mode;
  final List<String> blockers;
}

/// One shared thin-client controller. It never owns an independent local mode:
/// every state transition is read from, previewed by and written to the server.
class ExecutionModeController extends ChangeNotifier {
  ExecutionModeController({ApiClient? api})
      : _api = api ?? ApiClient(),
        _ownsApi = api == null;

  ApiClient _api;
  final bool _ownsApi;

  ServerExecutionMode _mode = ServerExecutionMode.unknown;
  bool _loading = false;
  String? _error;
  ExecutionModePreview? _preview;
  LiveActivationPreview? _livePreview;
  LiveActivationResult? _liveResult;

  ServerExecutionMode get mode => _mode;
  bool get modeKnown => _mode != ServerExecutionMode.unknown;
  bool get loading => _loading;
  String? get error => _error;
  ExecutionModePreview? get preview => _preview;
  LiveActivationPreview? get livePreview => _livePreview;
  LiveActivationResult? get liveResult => _liveResult;

  Future<void> load() async {
    _loading = true;
    _error = null;
    notifyListeners();
    try {
      final data = await _api.get('/api/v1/execution/mode');
      _mode = ServerExecutionMode.parse(data['mode']);
      if (_mode == ServerExecutionMode.unknown) {
        _error = 'сервер вернул неизвестный execution mode';
      }
    } catch (error) {
      _mode = ServerExecutionMode.unknown;
      _error = '$error';
    } finally {
      _loading = false;
      notifyListeners();
    }
  }

  /// Recreate the default API client after owner changes engine address/token.
  /// Injected test clients are intentionally never replaced.
  Future<void> reconnect() async {
    if (_ownsApi) {
      _api.close();
      _api = ApiClient();
    }
    _mode = ServerExecutionMode.unknown;
    _preview = null;
    _livePreview = null;
    _liveResult = null;
    await load();
  }

  Future<ExecutionModePreview> previewMode(ServerExecutionMode target) async {
    if (target == ServerExecutionMode.unknown) {
      throw StateError('unknown mode cannot be requested');
    }
    _error = null;
    _livePreview = null;
    _liveResult = null;
    notifyListeners();
    try {
      // PAPER -> SANDBOX gains promotion authority only from a real provider
      // round trip. The endpoint is idempotent for the current release and
      // credential generation; lower-risk transitions never call a broker.
      if (_mode == ServerExecutionMode.paper &&
          target == ServerExecutionMode.sandbox) {
        await _api.post(
          '/api/v1/tinvest-sandbox/smoke',
          idempotencyKey: 'mobile-sandbox-roundtrip-v1',
        );
      }
      final data = await _api.post(
        '/api/v1/execution/mode/preview',
        body: {'target': target.label},
      );
      final result = ExecutionModePreview(
        current: ServerExecutionMode.parse(data['current']),
        target: ServerExecutionMode.parse(data['target']),
        allowed: data['allowed'] == true,
        blockers: _strings(data['blockers']),
      );
      _preview = result;
      notifyListeners();
      return result;
    } catch (error) {
      _error = '$error';
      notifyListeners();
      rethrow;
    }
  }

  Future<void> confirmModeChange({required String reason}) async {
    final pending = _preview;
    if (pending == null || !pending.allowed) {
      throw StateError('no allowed execution mode preview to confirm');
    }
    if (pending.target == ServerExecutionMode.live) {
      throw StateError('LIVE requires the dedicated two-step activation flow');
    }
    try {
      final data = await _api.post(
        '/api/v1/execution/mode/change',
        body: {
          'target': pending.target.label,
          'reason': reason,
        },
      );
      _mode = ServerExecutionMode.parse(data['mode']);
      _preview = null;
      _error = null;
      notifyListeners();
    } catch (error) {
      _error = '$error';
      notifyListeners();
      rethrow;
    }
  }

  Future<LiveActivationPreview> previewLive() async {
    _error = null;
    _preview = null;
    _liveResult = null;
    notifyListeners();
    try {
      final data = await _api.post('/api/v1/execution/live/preview');
      final hardCaps = <String, String>{};
      final rawCaps = data['hard_caps'];
      if (rawCaps is Map) {
        for (final entry in rawCaps.entries) {
          hardCaps['${entry.key}'] = '${entry.value}';
        }
      }
      final result = LiveActivationPreview(
        previewHash: '${data['preview_hash'] ?? ''}',
        fromMode: ServerExecutionMode.parse(data['from_mode']),
        targetMode: ServerExecutionMode.parse(data['target_mode']),
        venue: '${data['venue'] ?? 'NOT_CONFIGURED'}',
        account: '${data['account'] ?? 'NOT_CONFIGURED'}',
        capitalRub: '${data['capital_rub'] ?? ''}',
        hardCaps: hardCaps,
        configHash: '${data['config_hash'] ?? ''}',
        allowed: data['allowed'] == true,
        blockers: _strings(data['blockers']),
      );
      _livePreview = result;
      notifyListeners();
      return result;
    } catch (error) {
      _error = '$error';
      notifyListeners();
      rethrow;
    }
  }

  Future<LiveActivationResult> confirmLive({String? idempotencyKey}) async {
    final pending = _livePreview;
    if (pending == null || !pending.confirmable) {
      throw StateError('LIVE preview still has server blockers');
    }
    final key = (idempotencyKey?.trim().isNotEmpty ?? false)
        ? idempotencyKey!.trim()
        : _stableLiveIdempotencyKey(pending.previewHash);
    try {
      final data = await _api.post(
        '/api/v1/execution/live/confirm',
        idempotencyKey: key,
        body: {
          'preview_hash': pending.previewHash,
          'owner_confirmed': true,
        },
      );
      final result = LiveActivationResult(
        previewHash: '${data['preview_hash'] ?? pending.previewHash}',
        idempotencyKey: '${data['idempotency_key'] ?? key}',
        status: '${data['status'] ?? 'UNKNOWN'}',
        mode: ServerExecutionMode.parse(data['mode']),
        blockers: _strings(data['blockers']),
      );
      _liveResult = result;
      _mode = result.mode;
      _error = null;
      if (result.status == 'APPLIED') _livePreview = null;
      notifyListeners();
      return result;
    } catch (error) {
      // Keep the same preview. The next tap derives the same idempotency key,
      // so a lost response can be retried without creating a second activation.
      _error = '$error';
      notifyListeners();
      rethrow;
    }
  }

  void clearPendingPreview() {
    _preview = null;
    _livePreview = null;
    _liveResult = null;
    _error = null;
    notifyListeners();
  }

  @override
  void dispose() {
    if (_ownsApi) _api.close();
    super.dispose();
  }
}

class ExecutionModeScope extends InheritedNotifier<ExecutionModeController> {
  const ExecutionModeScope({
    super.key,
    required ExecutionModeController controller,
    required super.child,
  }) : super(notifier: controller);

  static ExecutionModeController of(BuildContext context) {
    final scope = context.dependOnInheritedWidgetOfExactType<ExecutionModeScope>();
    assert(scope != null, 'ExecutionModeScope is missing');
    return scope!.notifier!;
  }

  static ExecutionModeController? maybeOf(BuildContext context) => context
      .dependOnInheritedWidgetOfExactType<ExecutionModeScope>()
      ?.notifier;
}

List<String> _strings(Object? raw) {
  if (raw is! List) return const [];
  return raw.map((item) => '$item').toList(growable: false);
}

String _stableLiveIdempotencyKey(String previewHash) {
  final normalized = previewHash.trim();
  if (normalized.isEmpty) throw StateError('LIVE preview hash is empty');
  final prefix = normalized.length > 48 ? normalized.substring(0, 48) : normalized;
  return 'mobile-live-$prefix';
}
