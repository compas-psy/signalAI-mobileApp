import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/broker/broker.dart';
import 'package:signalai/domain/broker/trading_diagnostics.dart';
import 'package:signalai/domain/models/settings.dart';

/// Брокер, который отвечает заготовкой: диагностика проверяется без сети.
class FakeBroker implements Broker {
  FakeBroker({
    this.id = BrokerId.bybit,
    this.access = 'счёт UTA · баланс 1000 USDT',
    this.failure,
    this.openPositions = const [],
    this.openOrders = const [],
  });

  @override
  final BrokerId id;
  final String access;

  /// Причина отказа. Не null — брокер не принимает ключ.
  final String? failure;
  final List<BrokerPosition> openPositions;
  final List<BrokerOrder> openOrders;

  @override
  Future<String> checkAccess() async {
    final reason = failure;
    if (reason != null) throw BrokerException(reason);
    return access;
  }

  @override
  Future<List<BrokerPosition>> positions() async => openPositions;

  @override
  Future<List<BrokerOrder>> orders() async => openOrders;

  @override
  Future<List<BrokerPosition>> unprotectedPositions() async =>
      [for (final p in openPositions) if (p.unprotected) p];

  @override
  String get name => id.title;

  @override
  TradingMode get mode => TradingMode.testnet;

  @override
  Future<bool> get isReady async => failure == null;

  @override
  Future<OrderResult> placeOrder(OrderRequest request) async =>
      throw StateError('диагностика ордера не шлёт');

  @override
  Future<bool> placeProtectiveStop({
    required String symbol,
    required double stopPrice,
    required bool long,
    required double quantity,
  }) async =>
      throw StateError('диагностика ничего не выставляет');

  @override
  Future<int> cancelAllOrders() async => throw StateError('диагностика не отменяет');
}

class FakeProbe implements TradingProbe {
  FakeProbe({
    this.vault = true,
    this.method = ConfirmMethod.biometrics,
    this.tradingEnabled = true,
    this.killSwitch = false,
    this.keys = true,
    this.check,
    FakeBroker? broker,
  }) : broker = broker ?? FakeBroker();

  final bool vault;
  final ConfirmMethod method;
  final bool keys;
  final BrokerKeyCheck? check;
  final FakeBroker broker;

  @override
  final bool tradingEnabled;

  @override
  final bool killSwitch;

  @override
  Future<bool> get vaultAvailable async => vault;

  @override
  Future<ConfirmMethod> get confirmMethod async => method;

  @override
  List<BrokerId> get probedBrokers => const [BrokerId.bybit];

  @override
  String modeLabelOf(BrokerId broker) => 'testnet';

  @override
  Future<bool> hasBrokerKeys(BrokerId broker) async => keys;

  @override
  BrokerKeyCheck? keyCheckOf(BrokerId broker) => check;

  @override
  Broker brokerOf(BrokerId broker) => this.broker;
}

/// Проверка по имени: диагностика — это набор именованных фактов.
TradingCheck named(List<TradingCheck> checks, String startsWith) =>
    checks.firstWhere((c) => c.name.startsWith(startsWith));

void main() {
  group('Диагностика торговли', () {
    test('исправный контур проходит все проверки', () async {
      final checks = await diagnoseTradingWith(FakeProbe()).toList();

      expect(checks.every((c) => c.ok), isTrue,
          reason: 'непройденные: '
              '${checks.where((c) => !c.ok).map((c) => c.name).join(', ')}');
      expect(named(checks, 'Bybit: связь').details.first,
          contains('баланс 1000 USDT'));
    });

    test('отказ биржи показывается её словами, а не пересказом', () async {
      final probe = FakeProbe(
        broker: FakeBroker(failure: 'API key is invalid (retCode 10003)'),
      );

      final checks = await diagnoseTradingWith(probe).toList();
      final access = named(checks, 'Bybit: связь');

      expect(access.ok, isFalse);
      // Код биржи — единственное, по чему понятно, что чинить.
      expect(access.details.single, contains('10003'));
    });

    test('без ключей на биржу не ходим, но проверку не пропускаем', () async {
      final checks = await diagnoseTradingWith(FakeProbe(keys: false)).toList();

      expect(named(checks, 'Bybit: ключи').ok, isFalse);
      final access = named(checks, 'Bybit: связь');
      expect(access.ok, isFalse);
      expect(access.details.single, contains('ключи не заданы'));
      expect(
        checks.any((c) => c.name.startsWith('Bybit: позиции')),
        isFalse,
        reason: 'спрашивать позиции нечем',
      );
    });

    test('отвергнутый ключ не отмечается зелёным', () async {
      // Ключ лежит в хранилище, но биржа его не приняла. Зелёная отметка
      // рядом со словом «ОТКАЗ» обесценивает весь экран.
      final probe = FakeProbe(
        check: BrokerKeyCheck(
          ok: false,
          note: 'Bybit ответил 401 · API key is invalid.',
          at: DateTime(2026, 7, 26, 22, 27),
        ),
      );

      final keys = named(await diagnoseTradingWith(probe).toList(), 'Bybit: ключи');
      expect(keys.ok, isFalse);
      expect(keys.details.last, contains('22:27'));
      expect(keys.details.last, contains('401'));
    });

    test('нечем подтвердить — проверка красная и говорит, что включить', () async {
      final checks =
          await diagnoseTradingWith(FakeProbe(method: ConfirmMethod.none)).toList();
      final confirm = named(checks, 'Подтверждение');

      expect(confirm.ok, isFalse);
      expect(confirm.details.join(' '), contains('блокировку экрана'));
    });

    test('позиция без стопа делает проверку красной', () async {
      final probe = FakeProbe(
        broker: FakeBroker(openPositions: const [
          BrokerPosition(
            symbol: 'BTCUSDT',
            long: true,
            quantity: 1,
            entryPrice: 60000,
            unrealizedPnl: 0,
          ),
        ]),
      );

      final checks = await diagnoseTradingWith(probe).toList();
      final positions = named(checks, 'Bybit: позиции');

      expect(positions.ok, isFalse);
      expect(positions.details.join(' '), contains('БЕЗ СТОПА'));
    });

    test('падение брокера — результат проверки, а не падение экрана', () async {
      final probe = _ThrowingProbe();
      final checks = await diagnoseTradingWith(probe).toList();

      expect(checks.first.ok, isFalse);
      expect(checks.first.details.single, contains('ошибка'));
    });
  });

  group('Готовность торгового контура', () {
    TradingView view({
      bool enabled = true,
      bool killSwitch = false,
      bool vault = true,
      ConfirmMethod method = ConfirmMethod.biometrics,
      bool hasKeys = true,
      bool accepted = true,
    }) =>
        TradingView(
          brokers: [
            BrokerView(
              id: 'bybit',
              title: 'Bybit',
              modeLabel: 'testnet',
              live: false,
              hasKeys: hasKeys,
              liveAllowed: false,
              liveBlockedReason: 'мало сделок',
              keysAccepted: accepted,
            ),
          ],
          enabled: enabled,
          killSwitch: killSwitch,
          gateAllowed: false,
          gateReason: 'мало сделок',
          gateProgress: 0.1,
          vaultAvailable: vault,
          confirmMethod: method,
        );

    test('всё на месте — контур готов', () {
      expect(view().ready, isTrue);
      expect(view().blockingReason, isEmpty);
    });

    test('нечем подтвердить сделку — контур не готов', () {
      // Ровно тот случай, который раньше показывался как «ГОТОВ»: ключи есть,
      // торговля включена, а подтвердить заявку нечем — значит ни один ордер
      // уйти не может.
      final v = view(method: ConfirmMethod.none);
      expect(v.ready, isFalse);
      expect(v.blockingReason, contains('подтвердить'));
    });

    test('ключи есть, но биржа их не приняла — контур не готов', () {
      final v = view(accepted: false);
      expect(v.ready, isFalse);
      expect(v.blockingReason, contains('не подтвердила'));
    });

    test('аварийная остановка важнее всего остального', () {
      expect(view(killSwitch: true).blockingReason, contains('аварийная'));
    });
  });
}

/// Хранилище, которое падает: диагностика обязана это пережить.
class _ThrowingProbe extends FakeProbe {
  @override
  Future<bool> get vaultAvailable async => throw StateError('Keystore недоступен');
}
