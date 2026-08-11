import 'dart:async';

import '../../domain/broker/broker.dart';
import '../../domain/broker/tinvest_role.dart';
import '../broker/tinvest_broker.dart';
import '../local_analysis_repository.dart';
import '../local_store.dart';
import 'engine_client.dart';

/// Результат зеркала server-paper плана в настоящую T-Invest Sandbox.
enum SandboxMirrorTone { success, warning, failure }

class SandboxMirrorResult {
  const SandboxMirrorResult(this.message, this.tone);

  final String message;
  final SandboxMirrorTone tone;
}

/// Узкая точка доступа к sandbox-токену в thin-клиенте.
///
/// Серверные интеграции и локальный T-Invest sandbox сознательно разделены:
/// sandbox-token остаётся в Android Keystore и никогда не отправляется на VPS.
/// Этот фасад позволяет экрану «Подключения» показать/заменить **только факт**
/// наличия токена и сохранить новый, не раскрывая сохранённое значение.
abstract final class TInvestSandboxAccess {
  static LocalAnalysisRepository? _repository;

  static void attach(LocalAnalysisRepository repository) {
    _repository = repository;
  }

  static bool get available => _repository != null;

  static Future<bool> configured() async {
    final repo = _repository;
    if (repo == null) return false;
    return repo.vault.hasKeys(
      exchange: BrokerId.tinvest.name,
      mode: TInvestRole.sandbox.slot,
      needsSecret: false,
    );
  }

  /// Сохранить sandbox-токен локально без сетевой проверки.
  ///
  /// Запись секрета и доступность T-Invest API — разные факты. Раньше токен
  /// сначала успешно попадал в Android Keystore, а затем `checkAccess()` мог
  /// упасть из-за VPN/прокси/чужого сертификата, после чего экран утверждал,
  /// что «токен не принят». Это было ложным состоянием: секрет уже сохранён.
  ///
  /// TLS здесь намеренно не ослабляется. Строгая проверка сертификата остаётся
  /// в [TInvestBroker] и сработает при реальной sandbox-операции. Сохранение
  /// credential не должно зависеть от текущего сетевого маршрута телефона.
  static Future<String> save(String token) async {
    final repo = _repository;
    if (repo == null) {
      throw StateError('Локальное защищённое хранилище недоступно');
    }
    if (!await repo.vault.isAvailable) {
      throw StateError(
        'Защищённое хранилище недоступно на этом устройстве: токен '
        'сохранять некуда, а класть его в открытый файл нельзя.',
      );
    }

    final cleaned = token
        .trim()
        .replaceFirst(RegExp(r'^Bearer\s+', caseSensitive: false), '')
        .trim();
    if (cleaned.isEmpty) {
      throw ArgumentError.value(token, 'token', 'Sandbox-токен пуст');
    }

    await repo.vault.saveKeys(
      exchange: BrokerId.tinvest.name,
      mode: TInvestRole.sandbox.slot,
      apiKey: cleaned,
      apiSecret: '',
    );
    return 'токен сохранён на устройстве; проверка связи выполняется отдельно';
  }

  static Future<void> remove() async {
    final repo = _repository;
    if (repo == null) return;
    await repo.vault.deleteKeys(
      exchange: BrokerId.tinvest.name,
      mode: TInvestRole.sandbox.slot,
    );
  }
}

/// EngineClient с одним дополнительным действием: после **успешного**
/// server approve зеркалит подтверждённый FORTS-план в T-Invest Sandbox.
///
/// Порядок принципиален:
/// 1) сервер проверяет lifecycle/risk и создаёт server-paper;
/// 2) только затем телефон читает тот же TradePlan и отправляет его в sandbox;
/// 3) Bybit и любые WATCH-идеи сюда не попадают.
///
/// Токен брокера остаётся на устройстве в защищённом хранилище. Сервер его не
/// получает и не знает. Sandbox — проверка брокерской механики, а не второй
/// источник сигнала или риска.
class SandboxMirroringEngineClient extends EngineClient {
  SandboxMirroringEngineClient({
    required this.repository,
    required this.onResult,
    LocalStore? instrumentStore,
  }) : _instrumentStore = instrumentStore ?? LocalStore() {
    TInvestSandboxAccess.attach(repository);
  }

  final LocalAnalysisRepository repository;
  final LocalStore _instrumentStore;
  final void Function(SandboxMirrorResult result) onResult;

  @override
  Future<IdeaDecision> approvePaper(String ideaId) async {
    final decision = await super.approvePaper(ideaId);

    // Повтор server approve идемпотентен. Зеркало обязано быть таким же:
    // повторное нажатие/сетевой retry не выставляет второй брокерский ордер.
    if (decision.idempotentReplay) return decision;

    final idea = await detail(ideaId);
    if (idea == null || !idea.instrumentId.startsWith('MOEX:FUT:')) {
      return decision;
    }

    final plan = idea.plan;
    if (plan == null || plan.quantity <= 0 || plan.targets.isEmpty) {
      _report(const SandboxMirrorResult(
        'Paper-сделка принята · T-Invest Sandbox не отправлена: объём или план не рассчитан',
        SandboxMirrorTone.failure,
      ));
      return decision;
    }

    final token = await repository.vault.apiKey(
      exchange: BrokerId.tinvest.name,
      mode: TInvestRole.sandbox.slot,
    );
    if (token == null || token.trim().isEmpty) {
      _report(const SandboxMirrorResult(
        'Paper-сделка принята · для проверки MOEX задайте токен T-Invest Sandbox в Подключениях',
        SandboxMirrorTone.warning,
      ));
      return decision;
    }

    final broker = TInvestBroker(
      mode: TradingMode.testnet,
      role: TInvestRole.sandbox,
      token: () async => token,
      instrumentCache: StoredInstrumentCache(_instrumentStore),
    );
    try {
      final result = await broker.placeOrder(
        OrderRequest(
          symbol: _symbolOf(idea.instrumentId),
          long: idea.direction.isLong,
          quantity: plan.quantity,
          entry: plan.entry,
          stopLoss: plan.stop,
          takeProfit: plan.targets.first.price,
          stopEntry: plan.orderType.name == 'stopLimit',
        ),
      );
      _report(SandboxMirrorResult(
        result.accepted
            ? 'Paper-сделка принята · T-Invest Sandbox: вход и защитный стоп приняты'
            : 'Paper-сделка принята · T-Invest Sandbox отказала: ${result.message}',
        result.accepted ? SandboxMirrorTone.success : SandboxMirrorTone.failure,
      ));
    } on Object catch (error) {
      _report(SandboxMirrorResult(
        'Paper-сделка принята · T-Invest Sandbox: $error',
        SandboxMirrorTone.failure,
      ));
    } finally {
      broker.close();
    }
    return decision;
  }

  /// Controller после await показывает общий server-paper toast. Результат
  /// sandbox ставим в event queue, чтобы он пришёл следом и не был затёрт
  /// общим сообщением.
  void _report(SandboxMirrorResult result) {
    Timer.run(() => onResult(result));
  }

  static String _symbolOf(String instrumentId) {
    final cut = instrumentId.lastIndexOf(':');
    return cut < 0 ? instrumentId : instrumentId.substring(cut + 1);
  }
}
