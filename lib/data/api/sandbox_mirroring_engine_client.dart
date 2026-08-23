import 'dart:async';

import '../local_analysis_repository.dart';
import '../local_store.dart';
import 'engine_client.dart';
import 'integrations_client.dart';

/// Результат server-owned T-Invest Sandbox контура в thin-клиенте.
enum SandboxMirrorTone { success, warning, failure }

class SandboxMirrorResult {
  const SandboxMirrorResult(this.message, this.tone);

  final String message;
  final SandboxMirrorTone tone;
}

/// Write-only доступ thin-клиента к серверному T-Invest Sandbox credential.
///
/// Старые сборки держали token в Android Keystore и сами ходили к T-Invest.
/// Теперь Keystore используется только как одноразовый источник миграции:
/// token отправляется в encrypted server vault и локальная копия удаляется
/// исключительно после точного server confirmation. Значение секрета сервер
/// обратно никогда не возвращает.
abstract final class TInvestSandboxAccess {
  static const _slot = 'tinvest_sandbox_trade';
  static LocalAnalysisRepository? _repository;
  static IntegrationsClient? _client;

  static void attach(
    LocalAnalysisRepository repository, {
    IntegrationsClient? client,
  }) {
    _repository = repository;
    if (client != null) _client = client;
  }

  static void detachForTest() {
    _repository = null;
    _client = null;
  }

  static bool get available => _repository != null && _client != null;

  static bool _isExactSandboxSlot(ServerIntegration item) =>
      item.slot == _slot &&
      item.venue == 'TINVEST' &&
      item.purpose == 'trade' &&
      item.environment == 'sandbox' &&
      item.fields.length == 1 &&
      item.fields.single == 'token' &&
      item.configured;

  static Future<bool> configured([IntegrationsClient? client]) async {
    final server = client ?? _client;
    if (server == null) return false;
    final items = await server.list();
    return items.any(_isExactSandboxSlot);
  }

  /// Перенести legacy sandbox-token из Android Keystore в server vault.
  ///
  /// Если локальный legacy token ещё существует, он является источником
  /// миграции даже при уже настроенном server slot: сначала PUT + точное server
  /// confirmation, и только затем удаление Keystore. Это не позволяет молча
  /// потерять потенциально более свежий локальный credential. Если локального
  /// token уже нет, точный настроенный server slot означает завершённую миграцию
  /// и никакого лишнего локального delete не выполняется.
  static Future<bool> migrateToServer([IntegrationsClient? client]) async {
    final repo = _repository;
    final server = client ?? _client;
    if (repo == null || server == null) return false;

    final items = await server.list();
    final serverConfigured = items.any(_isExactSandboxSlot);

    final local = (await repo.vault.apiKey(
      exchange: 'tinvest',
      mode: 'sandbox',
    ))
        ?.trim();

    if (local == null || local.isEmpty) return serverConfigured;

    final saved = await server.save(_slot, {'token': local});
    if (!_isExactSandboxSlot(saved)) return false;

    await repo.vault.deleteKeys(exchange: 'tinvest', mode: 'sandbox');
    return true;
  }

  /// Новые token значения сразу сохраняются на сервере, не в Android.
  static Future<String> save(String token) async {
    final server = _client;
    final repo = _repository;
    if (server == null || repo == null) {
      throw StateError('Серверное хранилище T-Invest Sandbox недоступно');
    }

    final cleaned = token
        .trim()
        .replaceFirst(RegExp(r'^Bearer\s+', caseSensitive: false), '')
        .trim();
    if (cleaned.isEmpty) {
      throw ArgumentError.value(token, 'token', 'Sandbox-токен пуст');
    }

    final saved = await server.save(_slot, {'token': cleaned});
    if (!_isExactSandboxSlot(saved)) {
      throw StateError('Сервер не подтвердил точный T-Invest Sandbox credential');
    }

    // Удаляем возможный legacy secret только после server confirmation.
    await repo.vault.deleteKeys(exchange: 'tinvest', mode: 'sandbox');
    return 'токен сохранён в защищённом хранилище сервера';
  }

  static Future<void> remove() async {
    final server = _client;
    final repo = _repository;
    if (server == null || repo == null) return;
    await server.remove(_slot);
    await repo.vault.deleteKeys(exchange: 'tinvest', mode: 'sandbox');
  }
}

/// Thin client для server-paper решений.
///
/// В этой сборке телефон больше не создаёт [TInvestBroker] и не отправляет
/// broker POST. Он только подтверждает paper-решение серверу и обеспечивает
/// одноразовую миграцию legacy credential. Диагностическая реальная sandbox
/// заявка и дальнейшее provider I/O выполняются сервером.
class SandboxMirroringEngineClient extends EngineClient {
  SandboxMirroringEngineClient({
    required this.repository,
    required this.onResult,
    LocalStore? instrumentStore,
    IntegrationsClient? integrations,
    super.client,
    super.onHandledFailure,
  }) : _integrations = integrations ?? IntegrationsClient() {
    TInvestSandboxAccess.attach(repository, client: _integrations);
    unawaited(_migrateLegacyCredential());
  }

  final LocalAnalysisRepository repository;
  final IntegrationsClient _integrations;
  final void Function(SandboxMirrorResult result) onResult;

  Future<void> _migrateLegacyCredential() async {
    try {
      await TInvestSandboxAccess.migrateToServer(_integrations);
    } on Object {
      // Fail closed: upload failure keeps the legacy Keystore token in place.
      // Startup must not fall back to direct broker execution from the phone.
    }
  }

  @override
  Future<IdeaDecision> approvePaper(String ideaId) async {
    final decision = await super.approvePaper(ideaId);

    try {
      final ready = await TInvestSandboxAccess.migrateToServer(_integrations);
      _report(SandboxMirrorResult(
        ready
            ? 'Paper-сделка принята · T-Invest Sandbox credential находится на сервере; прямые заявки с телефона отключены'
            : 'Paper-сделка принята · T-Invest Sandbox на сервере не настроена; прямые заявки с телефона отключены',
        ready ? SandboxMirrorTone.success : SandboxMirrorTone.warning,
      ));
    } on Object catch (error, stackTrace) {
      reportHandledFailure(
        EngineFailureStage.sandboxReconciliation,
        error,
        stackTrace,
      );
      _report(const SandboxMirrorResult(
        'Paper-сделка принята · перенос T-Invest Sandbox на сервер не подтверждён; локальный token сохранён, прямой broker POST запрещён',
        SandboxMirrorTone.failure,
      ));
    }

    return decision;
  }

  /// Controller после await показывает общий server-paper toast. Результат
  /// sandbox ставим в event queue, чтобы он пришёл следом и не был затёрт.
  void _report(SandboxMirrorResult result) {
    Timer.run(() => onResult(result));
  }
}
