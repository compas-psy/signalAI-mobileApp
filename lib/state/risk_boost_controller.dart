import 'package:flutter/foundation.dart';

import '../data/api/api_client.dart';
import '../data/api/engine_client.dart';

/// Thin-client coordinator for the owner's manual «Рискнуть» flow.
///
/// It owns no risk math. Every economic value comes from [RiskPreview] and the
/// apply path sends that signed server preview back unchanged. Its main safety
/// responsibility is interaction state: a stale 409 may refresh what the owner
/// sees, but the refreshed terms are never applied by the same tap.
class RiskBoostController extends ChangeNotifier {
  RiskBoostController({required EngineClient engine}) : _engine = engine;

  final EngineClient _engine;

  RiskPreview? _preview;
  RiskOverrideResult? _result;
  bool _loading = false;
  bool _needsReview = false;
  String? _error;
  String? _message;

  String? _ideaId;
  String? _presetId;
  String? _currentMode;

  RiskPreview? get preview => _preview;
  RiskOverrideResult? get result => _result;
  bool get loading => _loading;
  bool get needsReview => _needsReview;
  String? get error => _error;
  String? get message => _message;

  /// The preview can be confirmed only while the exact signed terms shown to
  /// the owner are still locally unexpired. The server rechecks expiry and all
  /// risk state again, so this is UX fail-closed rather than an authority.
  bool get canConfirm {
    final current = _preview;
    return !_loading &&
        current != null &&
        current.canConfirm &&
        !current.isExpired;
  }

  Future<void> loadPreset({
    required String ideaId,
    required String presetId,
    required String currentMode,
  }) async {
    final idea = ideaId.trim();
    final preset = presetId.trim();
    final mode = currentMode.trim();
    if (idea.isEmpty || preset.isEmpty) {
      throw StateError('idea and preset are required for manual risk preview');
    }
    if (!_knownModes.contains(mode)) {
      throw StateError('server execution mode is not known');
    }
    if (!_boostPresets.contains(preset)) {
      throw StateError('manual risk apply requires a BOOST preset');
    }

    // Discard a previous replayable token before starting another request.
    // A failed preset switch must never leave the old preset confirmable.
    _preview = null;
    _result = null;
    _needsReview = false;
    _error = null;
    _message = null;
    _ideaId = idea;
    _presetId = preset;
    _currentMode = mode;
    _loading = true;
    notifyListeners();

    try {
      _preview = await _engine.previewRisk(
        ideaId: idea,
        presetId: preset,
        currentMode: mode,
      );
    } catch (failure) {
      _error = _describe(failure);
      rethrow;
    } finally {
      _loading = false;
      notifyListeners();
    }
  }

  /// Apply exactly the preview the owner has reviewed.
  ///
  /// A 409 means the signed terms are stale (market/risk/mode state changed).
  /// In that one case we fetch a new preview for display, then return without
  /// applying it. A second explicit owner tap is required to call this method
  /// again against the refreshed token.
  Future<void> confirm() async {
    final current = _preview;
    if (!canConfirm || current == null) {
      throw StateError('no current confirmable manual risk preview');
    }
    final idea = _ideaId;
    final preset = _presetId;
    final mode = _currentMode;
    if (idea == null || preset == null || mode == null) {
      throw StateError('manual risk preview identity is missing');
    }

    _loading = true;
    _error = null;
    _message = null;
    _result = null;
    notifyListeners();

    try {
      final applied = await _engine.applyRisk(
        preview: current,
        reason: 'owner confirmed reviewed manual risk preview',
      );
      _result = applied;
      _preview = null;
      _needsReview = false;
      _message = applied.created
          ? 'Параметры риска зафиксированы сервером. Сделка не создана.'
          : 'Эти параметры риска уже были зафиксированы. Сделка не создана.';
    } on ApiException catch (failure) {
      if (failure.statusCode != 409) {
        _error = _describe(failure);
        rethrow;
      }

      // The old signed token is dead immediately. Refresh is display-only:
      // there is intentionally no second apply call in this code path.
      _preview = null;
      try {
        final refreshed = await _engine.previewRisk(
          ideaId: idea,
          presetId: preset,
          currentMode: mode,
        );
        _preview = refreshed;
        _needsReview = true;
        _message =
            'Условия изменились. Проверьте обновлённые цифры и подтвердите ещё раз.';
      } catch (refreshFailure) {
        _needsReview = false;
        _error = _describe(refreshFailure);
      }
    } catch (failure) {
      _error = _describe(failure);
      rethrow;
    } finally {
      _loading = false;
      notifyListeners();
    }
  }

  /// Drop all replayable preview state when the sheet closes or the idea/mode
  /// changes. Nothing here is persisted between app sessions.
  void clear() {
    _preview = null;
    _result = null;
    _loading = false;
    _needsReview = false;
    _error = null;
    _message = null;
    _ideaId = null;
    _presetId = null;
    _currentMode = null;
    notifyListeners();
  }

  static const _knownModes = <String>{'PAPER', 'SANDBOX', 'CANARY', 'LIVE'};

  // AUTO is the strategy-owned baseline, not a manual override. The backend
  // rejects applying it; excluding it here prevents a misleading confirm CTA.
  static const _boostPresets = <String>{'BOOST_1', 'BOOST_2'};

  static String _describe(Object failure) => switch (failure) {
        ApiException error => error.message,
        _ => '$failure',
      };
}
