import 'package:flutter/widgets.dart';

import '../data/api/api_client.dart';

@immutable
class RiskOnPreview {
  const RiskOnPreview({
    required this.ideaId,
    required this.riskSnapshotId,
    required this.venue,
    required this.account,
    required this.allowed,
    required this.blockers,
    required this.baseRiskPct,
    required this.effectiveRiskPct,
    required this.hardCapRiskPct,
    required this.baseQuantity,
    required this.effectiveQuantity,
    required this.effectiveRiskAmount,
    required this.effectiveLeverage,
    required this.hardCapLeverage,
    required this.bindingLimit,
    required this.previewHash,
  });

  final String ideaId;
  final String riskSnapshotId;
  final String venue;
  final String account;
  final bool allowed;
  final List<String> blockers;
  final String baseRiskPct;
  final String effectiveRiskPct;
  final String hardCapRiskPct;
  final String baseQuantity;
  final String effectiveQuantity;
  final String effectiveRiskAmount;
  final String? effectiveLeverage;
  final String hardCapLeverage;
  final String bindingLimit;
  final String previewHash;
}

@immutable
class RiskOnResult {
  const RiskOnResult({
    required this.riskOverrideId,
    required this.created,
    required this.previewHash,
    required this.venue,
    required this.account,
    required this.effectiveRiskPct,
    required this.effectiveQuantity,
    required this.effectiveLeverage,
    required this.hardCapRiskPct,
    required this.hardCapLeverage,
  });

  final String riskOverrideId;
  final bool created;
  final String previewHash;
  final String venue;
  final String account;
  final String effectiveRiskPct;
  final String effectiveQuantity;
  final String? effectiveLeverage;
  final String hardCapRiskPct;
  final String? hardCapLeverage;
}

/// Thin-client state for the owner RISK ON action.
///
/// The phone supplies only execution scope (venue/account). Risk, quantity and
/// leverage are always read back from the server preview, never calculated or
/// accepted as mobile input. Confirmation sends the shown preview hash and an
/// idempotency key; the server recalculates before persisting the override.
class RiskOnController extends ChangeNotifier {
  RiskOnController({
    required this.ideaId,
    ApiClient? api,
  })  : _api = api ?? ApiClient(),
        _ownsApi = api == null;

  final String ideaId;
  final ApiClient _api;
  final bool _ownsApi;

  bool _loading = false;
  String? _error;
  RiskOnPreview? _preview;
  RiskOnResult? _result;

  bool get loading => _loading;
  String? get error => _error;
  RiskOnPreview? get previewData => _preview;
  RiskOnResult? get result => _result;

  Future<RiskOnPreview> preview({
    required String venue,
    required String account,
  }) async {
    final cleanVenue = venue.trim();
    final cleanAccount = account.trim();
    if (cleanVenue.isEmpty || cleanAccount.isEmpty) {
      const message = 'Укажите площадку и счёт для RISK ON.';
      _preview = null;
      _result = null;
      _error = message;
      notifyListeners();
      throw StateError(message);
    }
    _loading = true;
    _error = null;
    _preview = null;
    _result = null;
    notifyListeners();
    try {
      final data = await _api.post(
        '/api/v1/execution/risk-on/preview',
        body: {
          'idea_id': ideaId,
          'venue': cleanVenue,
          'account': cleanAccount,
        },
      );
      final result = RiskOnPreview(
        ideaId: '${data['idea_id'] ?? ideaId}',
        riskSnapshotId: '${data['risk_snapshot_id'] ?? ''}',
        venue: '${data['venue'] ?? cleanVenue}',
        account: '${data['account'] ?? cleanAccount}',
        allowed: data['allowed'] == true,
        blockers: _strings(data['blockers']),
        baseRiskPct: '${data['base_risk_pct'] ?? ''}',
        effectiveRiskPct: '${data['effective_risk_pct'] ?? ''}',
        hardCapRiskPct: '${data['hard_cap_risk_pct'] ?? ''}',
        baseQuantity: '${data['base_quantity'] ?? ''}',
        effectiveQuantity: '${data['effective_quantity'] ?? ''}',
        effectiveRiskAmount: '${data['effective_risk_amount'] ?? ''}',
        effectiveLeverage: _nullableString(data['effective_leverage']),
        hardCapLeverage: '${data['hard_cap_leverage'] ?? ''}',
        bindingLimit: '${data['binding_limit'] ?? ''}',
        previewHash: '${data['preview_hash'] ?? ''}',
      );
      // Blocked previews intentionally have no confirmable hash: they still
      // must reach the UI so the owner sees the exact server blockers. An
      // allowed preview without a hash cannot be confirmed safely and fails
      // closed instead.
      if (result.allowed && result.previewHash.trim().isEmpty) {
        throw StateError('server returned empty RISK ON preview hash');
      }
      _preview = result;
      return result;
    } catch (error) {
      _error = '$error';
      rethrow;
    } finally {
      _loading = false;
      notifyListeners();
    }
  }

  Future<RiskOnResult> confirm() async {
    final pending = _preview;
    if (pending == null || !pending.allowed) {
      throw StateError('no allowed RISK ON preview to confirm');
    }
    final key = _stableRiskOnIdempotencyKey(ideaId, pending.previewHash);
    _loading = true;
    _error = null;
    notifyListeners();
    try {
      final data = await _api.post(
        '/api/v1/execution/risk-on/confirm',
        idempotencyKey: key,
        body: {
          'idea_id': ideaId,
          'venue': pending.venue,
          'account': pending.account,
          'preview_hash': pending.previewHash,
          'owner_confirmed': true,
        },
      );
      final result = RiskOnResult(
        riskOverrideId: '${data['risk_override_id'] ?? ''}',
        created: data['created'] == true,
        previewHash: '${data['preview_hash'] ?? pending.previewHash}',
        venue: '${data['venue'] ?? pending.venue}',
        account: '${data['account'] ?? pending.account}',
        effectiveRiskPct: '${data['effective_risk_pct'] ?? ''}',
        effectiveQuantity: '${data['effective_quantity'] ?? ''}',
        effectiveLeverage: _nullableString(data['effective_leverage']),
        hardCapRiskPct: '${data['hard_cap_risk_pct'] ?? ''}',
        hardCapLeverage: _nullableString(data['hard_cap_leverage']),
      );
      _result = result;
      _preview = null;
      return result;
    } on ApiException catch (error) {
      _error = error.toString();
      // A server conflict means the shown economics/snapshot are no longer a
      // valid object to confirm. Force a new preview instead of offering a
      // stale second tap. Transport failures keep the preview and stable key.
      if (error.statusCode == 409) _preview = null;
      rethrow;
    } catch (error) {
      _error = '$error';
      rethrow;
    } finally {
      _loading = false;
      notifyListeners();
    }
  }

  void clear() {
    _preview = null;
    _result = null;
    _error = null;
    notifyListeners();
  }

  @override
  void dispose() {
    if (_ownsApi) _api.close();
    super.dispose();
  }
}

List<String> _strings(Object? raw) {
  if (raw is! List) return const [];
  return raw.map((item) => '$item').toList(growable: false);
}

String? _nullableString(Object? raw) {
  if (raw == null) return null;
  final value = '$raw'.trim();
  return value.isEmpty || value.toLowerCase() == 'null' ? null : value;
}

String _stableRiskOnIdempotencyKey(String ideaId, String previewHash) {
  final normalized = previewHash.trim();
  if (normalized.isEmpty) throw StateError('RISK ON preview hash is empty');
  final hashPrefix = normalized.length > 40 ? normalized.substring(0, 40) : normalized;
  final ideaPrefix = ideaId.length > 12 ? ideaId.substring(0, 12) : ideaId;
  return 'mobile-risk-on-$ideaPrefix-$hashPrefix';
}
