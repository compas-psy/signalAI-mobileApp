import 'dart:async';

import '../../domain/broker/broker.dart';
import '../../domain/broker/tinvest_role.dart';
import '../broker/tinvest_broker.dart';
import '../broker/tinvest_sandbox_mirror_reconciler.dart';
import '../local_analysis_repository.dart';
import '../local_store.dart';
import 'api_client.dart';
import 'engine_client.dart';
import 'sandbox_mirror_delivery.dart';

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
/// 2) телефон **до broker call** сохраняет долговечный delivery intent;
/// 3) первый delivery выставляет entry + protection со стабильными IDs;
/// 4) любой replay/restart сначала читает provider state и только потом
///    достраивает отсутствующую часть;
/// 5) Bybit и любые WATCH-идеи сюда не попадают.
///
/// Токен брокера остаётся на устройстве в защищённом хранилище. Сервер его не
/// получает и не знает. Sandbox — проверка брокерской механики, а не второй
/// источник сигнала или риска.
class SandboxMirroringEngineClient extends EngineClient {
  SandboxMirroringEngineClient({
    required this.repository,
    required this.onResult,
    LocalStore? instrumentStore,
    ApiClient? client,
    EngineFailureReporter? onHandledFailure,
  })  : _instrumentStore = instrumentStore ?? LocalStore(),
        super(client: client, onHandledFailure: onHandledFailure) {
    TInvestSandboxAccess.attach(repository);
  }

  final LocalAnalysisRepository repository;
  final LocalStore _instrumentStore;
  final void Function(SandboxMirrorResult result) onResult;

  SandboxMirrorDeliveryStore get _deliveries =>
      SandboxMirrorDeliveryStore(_instrumentStore);

  @override
  Future<IdeaDecision> approvePaper(String ideaId) async {
    final decision = await super.approvePaper(ideaId);

    var delivery = await _deliveries.load(ideaId);
    if (delivery?.terminal ?? false) return decision;
    final isRecovery = delivery != null || decision.idempotentReplay;

    // `idempotentReplay=true` protects the server decision only. It is not a
    // reason to skip broker recovery: a lost first server response can leave
    // a valid paper decision without any sandbox delivery at all.
    delivery ??= SandboxMirrorDelivery.pending(ideaId);
    if (!await _deliveries.save(delivery)) {
      _report(const SandboxMirrorResult(
        'Paper-сделка принята · T-Invest Sandbox не отправлена: '
        'не удалось надёжно записать состояние доставки на устройство',
        SandboxMirrorTone.failure,
      ));
      return decision;
    }

    final idea = await detail(ideaId);
    if (idea == null) {
      await _markRepair(delivery, 'Не удалось получить полный TradePlan идеи');
      _report(const SandboxMirrorResult(
        'Paper-сделка принята · T-Invest Sandbox не отправлена: '
        'полный TradePlan недоступен, требуется сверка',
        SandboxMirrorTone.failure,
      ));
      return decision;
    }
    if (!idea.instrumentId.startsWith('MOEX:FUT:')) {
      await _deliveries.save(delivery.copyWith(
        status: SandboxMirrorDeliveryStatus.notApplicable,
      ));
      return decision;
    }

    final plan = idea.plan;
    if (plan == null || plan.quantity <= 0 || plan.targets.isEmpty) {
      await _markRepair(delivery, 'Объём или TradePlan не рассчитан');
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

    final symbol = _symbolOf(idea.instrumentId);
    final cache = StoredInstrumentCache(_instrumentStore);
    final broker = TInvestBroker(
      mode: TradingMode.testnet,
      role: TInvestRole.sandbox,
      token: () async => token,
      instrumentCache: cache,
    );
    final reconciler = TInvestSandboxMirrorReconciler(token: token);
    try {
      final request = OrderRequest(
        symbol: symbol,
        long: idea.direction.isLong,
        quantity: plan.quantity,
        entry: plan.entry,
        stopLoss: plan.stop,
        takeProfit: plan.targets.first.price,
        stopEntry: plan.orderType.name == 'stopLimit',
        requestId: delivery.entryRequestId,
        protectiveStopRequestId: delivery.protectiveStopRequestId,
      );

      if (isRecovery) {
        final accounts = await broker.accounts();
        if (accounts.isEmpty) {
          await _markRepair(delivery, 'T-Invest Sandbox не вернула торговый счёт');
          _report(const SandboxMirrorResult(
            'Paper-сделка принята · T-Invest Sandbox требует сверки: торговый счёт не найден',
            SandboxMirrorTone.failure,
          ));
          return decision;
        }

        final instrument = await cache.get(symbol);
        final alignedStop = _align(plan.stop, instrument?.priceStep ?? 0);
        final probe = await reconciler.probe(
          accountId: accounts.first.id,
          entryRequestId: delivery.entryRequestId,
          symbol: symbol,
          long: idea.direction.isLong,
          stopPrice: alignedStop,
        );

        switch (probe.status) {
          case TInvestSandboxMirrorProbeStatus.protected:
            await _complete(delivery, probe.exchangeOrderId);
            _report(const SandboxMirrorResult(
              'Paper-сделка уже существует · T-Invest Sandbox сверена: вход и защитный стоп уже на месте',
              SandboxMirrorTone.success,
            ));
            return decision;

          case TInvestSandboxMirrorProbeStatus.entryWithoutProtection:
            final repaired = await reconciler.ensureProtectiveStop(
              accountId: accounts.first.id,
              instrumentUid: probe.instrumentUid,
              lots: probe.lotsRequested,
              long: idea.direction.isLong,
              stopPrice: alignedStop,
              requestId: delivery.protectiveStopRequestId,
            );
            if (repaired) {
              await _complete(delivery, probe.exchangeOrderId);
              _report(const SandboxMirrorResult(
                'Paper-сделка уже существует · T-Invest Sandbox: недостающий защитный стоп восстановлен',
                SandboxMirrorTone.success,
              ));
            } else {
              await _markRepair(delivery, 'Вход найден, защитный стоп восстановить не удалось');
              _report(const SandboxMirrorResult(
                'Paper-сделка уже существует · T-Invest Sandbox требует сверки: вход есть, защитный стоп не восстановлен',
                SandboxMirrorTone.failure,
              ));
            }
            return decision;

          case TInvestSandboxMirrorProbeStatus.ambiguous:
            await _markRepair(delivery, probe.message);
            _report(SandboxMirrorResult(
              'Paper-сделка уже существует · T-Invest Sandbox требует ручной сверки: ${probe.message}',
              SandboxMirrorTone.failure,
            ));
            return decision;

          case TInvestSandboxMirrorProbeStatus.unavailable:
            await _deliveries.save(delivery.copyWith(lastError: probe.message));
            _report(SandboxMirrorResult(
              'Paper-сделка уже существует · состояние T-Invest Sandbox не подтверждено: '
              '${probe.message}. Новый ордер не отправлен.',
              SandboxMirrorTone.failure,
            ));
            return decision;

          case TInvestSandboxMirrorProbeStatus.absent:
            // Provider explicitly confirmed that this stable entry request id
            // is absent. Only now is a retry allowed to reach placeOrder().
            break;
        }
      }

      final result = await broker.placeOrder(request);
      if (result.accepted) {
        final persisted = await _complete(delivery, result.orderId);
        _report(SandboxMirrorResult(
          persisted
              ? 'Paper-сделка принята · T-Invest Sandbox: вход и защитный стоп приняты'
              : 'Paper-сделка принята · T-Invest Sandbox приняла вход и стоп, '
                  'но отметка завершения не записалась; следующий retry сначала сверит провайдера',
          persisted ? SandboxMirrorTone.success : SandboxMirrorTone.warning,
        ));
      } else {
        await _markRepair(delivery, result.message);
        _report(SandboxMirrorResult(
          'Paper-сделка принята · T-Invest Sandbox отказала: ${result.message}',
          SandboxMirrorTone.failure,
        ));
      }
    } on Object catch (error) {
      // Неизвестно, дошёл ли первый provider POST. Durable intent остаётся,
      // поэтому следующий вызов не повторит POST вслепую: сначала выполнит
      // GetSandboxOrderState по тому же entry request id.
      await _deliveries.save(delivery.copyWith(lastError: '$error'));
      _report(SandboxMirrorResult(
        'Paper-сделка принята · T-Invest Sandbox: $error. '
        'Результат неоднозначен; новый ордер без сверки не отправится.',
        SandboxMirrorTone.failure,
      ));
    } finally {
      reconciler.close();
      broker.close();
    }
    return decision;
  }

  Future<bool> _complete(
    SandboxMirrorDelivery delivery,
    String exchangeOrderId,
  ) =>
      _deliveries.save(delivery.copyWith(
        status: SandboxMirrorDeliveryStatus.completed,
        exchangeOrderId: exchangeOrderId,
        lastError: '',
      ));

  Future<void> _markRepair(
    SandboxMirrorDelivery delivery,
    String error,
  ) async {
    await _deliveries.save(delivery.copyWith(
      status: SandboxMirrorDeliveryStatus.repairRequired,
      lastError: error,
    ));
  }

  /// Controller после await показывает общий server-paper toast. Результат
  /// sandbox ставим в event queue, чтобы он пришёл следом и не был затёрт
  /// общим сообщением.
  void _report(SandboxMirrorResult result) {
    if (result.tone == SandboxMirrorTone.failure) {
      reportHandledFailure(
        EngineFailureStage.sandboxReconciliation,
        StateError(result.message),
        StackTrace.current,
      );
    }
    Timer.run(() => onResult(result));
  }

  static double _align(double price, double step) {
    if (step <= 0) return price;
    return (price / step).round() * step;
  }

  static String _symbolOf(String instrumentId) {
    final cut = instrumentId.lastIndexOf(':');
    return cut < 0 ? instrumentId : instrumentId.substring(cut + 1);
  }
}
