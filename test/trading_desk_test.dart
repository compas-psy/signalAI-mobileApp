import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/broker/secure_vault.dart';
import 'package:signalai/data/local_analysis_repository.dart';
import 'package:signalai/data/local_store.dart';
import 'package:signalai/data/repository.dart';
import 'package:signalai/domain/broker/broker.dart';
import 'package:signalai/domain/enums.dart';
import 'package:signalai/domain/models/digest.dart';
import 'package:signalai/domain/models/signal.dart';

/// Хранилище ключей в памяти.
///
/// Секрет намеренно не отдаётся наружу — как и настоящее: [sign] возвращает
/// null, поэтому подписать запрос нечем и брокер отказывает, не выходя в сеть.
/// Это тот же путь, что и при неверном ключе.
class FakeVault extends SecureVault {
  FakeVault({this.available = true, this.method = ConfirmMethod.biometrics});

  final bool available;
  final ConfirmMethod method;
  final Map<String, String> stored = {};

  @override
  Future<bool> get isAvailable async => available;

  @override
  Future<ConfirmMethod> get confirmMethod async => method;

  @override
  Future<bool> get biometricsAvailable async => method.available;

  @override
  Future<void> saveKeys({
    required String exchange,
    required String mode,
    required String apiKey,
    required String apiSecret,
  }) async {
    stored['$exchange.$mode.key'] = apiKey;
    stored['$exchange.$mode.secret'] = apiSecret;
  }

  @override
  Future<void> deleteKeys({required String exchange, required String mode}) async {
    stored.remove('$exchange.$mode.key');
    stored.remove('$exchange.$mode.secret');
  }

  @override
  Future<bool> hasKeys({required String exchange, required String mode}) async =>
      stored.containsKey('$exchange.$mode.key');

  @override
  Future<String?> apiKey({required String exchange, required String mode}) async =>
      stored['$exchange.$mode.key'];

  @override
  Future<String?> sign({
    required String exchange,
    required String mode,
    required String payload,
  }) async =>
      null;
}

TradingSignal signal({
  String id = 'sig-1',
  String symbol = 'TSTU6',
  Market market = Market.forts,
}) =>
    TradingSignal(
      id: id,
      symbol: symbol,
      name: 'Тест',
      market: market,
      direction: Direction.long,
      horizon: Horizon.swing,
      horizonLabel: '',
      score: 80,
      entry: 100,
      stopLoss: 98,
      takeProfits: const [TakeProfit(index: 1, price: 106, sharePercent: 100)],
      priceDecimals: 0,
      riskReward: '3,0',
      chips: const [],
      note: '',
      factors: const [],
      events: const [],
      unitRisk: 2,
      unitRiskLabel: '',
      unitMultiplier: 1,
      unitDecimals: 0,
      unitName: 'конт.',
      lastPrice: '100',
      changeLabel: '+0,5%',
      changeUp: true,
      status: SignalStatus.proposed,
    );

/// Кэш дайджеста на диске — так же, как после холодного старта.
Future<void> seedDigest(LocalStore store, List<TradingSignal> signals) =>
    store.write('digest', {
      'at': DateTime.now().toIso8601String(),
      'digest': DailyDigest(
        title: 'Утренний дайджест',
        subtitle: '',
        deliveryBadges: const [],
        regime: const [],
        regimeNote: '',
        events: const [],
        signals: signals,
        signalsQuota: '1 из 5',
      ).toJson(),
    });

void main() {
  group('Ключи площадки', () {
    test('отвергнутый ключ остаётся в хранилище вместе с причиной', () async {
      final store = LocalStore.inMemory();
      final vault = FakeVault();
      final repository = LocalAnalysisRepository(store: store, vault: vault);

      await expectLater(
        repository.saveBrokerKeys(
          broker: BrokerId.bybit,
          mode: TradingMode.testnet,
          apiKey: 'KEY',
          apiSecret: 'SECRET',
        ),
        throwsA(isA<FeatureUnavailableException>()),
      );

      // Ключ на месте: набирать длинный секрет заново из-за отказа биржи
      // (а тем более из-за пропавшей на секунду сети) владелец не обязан.
      expect(await repository.hasBrokerKeys(BrokerId.bybit), isTrue);

      final check = repository.keyCheckOf(BrokerId.bybit);
      expect(check, isNotNull);
      expect(check!.ok, isFalse);
      expect(check.note, isNotEmpty);
    });

    test('местный сбой не выдаётся за отказ обеих площадок', () async {
      // Подписи нет — отказ местный, до сети дело не дошло. Кросс-проверка
      // в этом случае не запускается: «ключ не принят нигде» было бы ложью.
      final repository =
          LocalAnalysisRepository(store: LocalStore.inMemory(), vault: FakeVault());
      await repository
          .saveBrokerKeys(
            broker: BrokerId.bybit,
            mode: TradingMode.testnet,
            apiKey: 'KEY',
            apiSecret: 'SECRET',
          )
          .catchError((Object _) => '');

      final note = repository.keyCheckOf(BrokerId.bybit)?.note ?? '';
      expect(note, isNot(contains('обеих площадках')));
      expect(note, isNot(contains('ПРИЧИНА НАЙДЕНА')));
    });

    test('причина отказа переживает перезапуск приложения', () async {
      final store = LocalStore.inMemory();
      final vault = FakeVault();
      final first = LocalAnalysisRepository(store: store, vault: vault);
      await first
          .saveBrokerKeys(
            broker: BrokerId.bybit,
            mode: TradingMode.testnet,
            apiKey: 'KEY',
            apiSecret: 'SECRET',
          )
          .catchError((Object _) => '');

      // Тост давно исчез, приложение перезапущено — вопрос «работает или
      // нет» обязан иметь ответ и теперь.
      final second = LocalAnalysisRepository(store: store, vault: vault);
      await second.fetchSettings();

      final check = second.keyCheckOf(BrokerId.bybit);
      expect(check?.ok, isFalse);
      expect(check?.note, isNotEmpty);
    });

    test('настройки показывают ключи заданными, но не принятыми', () async {
      final store = LocalStore.inMemory();
      final vault = FakeVault();
      final repository = LocalAnalysisRepository(store: store, vault: vault);
      await repository
          .saveBrokerKeys(
            broker: BrokerId.bybit,
            mode: TradingMode.testnet,
            apiKey: 'KEY',
            apiSecret: 'SECRET',
          )
          .catchError((Object _) => '');

      final trading = (await repository.fetchSettings()).trading!;
      final bybit = trading.brokers.firstWhere((b) => b.id == 'bybit');

      expect(bybit.hasKeys, isTrue);
      expect(bybit.keysAccepted, isFalse);
      expect(bybit.keyNote, isNotEmpty);
      expect(trading.ready, isFalse, reason: 'торговать этим ключом нельзя');
    });
  });

  group('Состояние площадок переживает запуск', () {
    test('параллельное чтение при старте не сбрасывает режим на тестнет', () async {
      // Владелец сообщил: «в приложении всегда сбрасывается на тестнет».
      // Причина была здесь: запуск читает журнал, стратегии и настройки
      // одновременно, и загрузка состояния взводила флаг «загружено» до
      // первого await — второй и третий вызовы работали с дефолтами.
      final store = LocalStore.inMemory();
      final vault = FakeVault();
      final first = LocalAnalysisRepository(store: store, vault: vault);
      await first.setTradingMode(BrokerId.bybit, TradingMode.live);

      final second = LocalAnalysisRepository(store: store, vault: vault);
      await Future.wait([
        second.fetchTrades(),
        second.fetchStrategies(),
        second.fetchSettings(),
      ]);

      expect(second.tradingState.modeOf(BrokerId.bybit), TradingMode.live);
    });

    test('запись состояния не опережает его чтение', () async {
      // Любая запись до загрузки вернула бы на диск дефолты. Путей записи
      // много — бэктест, оптимизация, проверка ключа, — поэтому защита стоит
      // в самой записи, а не в каждом вызывающем.
      final store = LocalStore.inMemory();
      final vault = FakeVault();
      final first = LocalAnalysisRepository(store: store, vault: vault);
      await first.setTradingMode(BrokerId.bybit, TradingMode.live);

      final second = LocalAnalysisRepository(store: store, vault: vault);
      // Первое обращение к репозиторию — пишущее.
      await second.setBackgroundEnabled(true);

      final third = LocalAnalysisRepository(store: store, vault: vault);
      await third.fetchSettings();
      expect(third.tradingState.modeOf(BrokerId.bybit), TradingMode.live);
    });
  });

  group('Ключи и режим площадки', () {
    test('ключ живого режима виден, даже когда переключатель на тестнете',
        () async {
      final vault = FakeVault();
      vault.stored['bybit.live.key'] = 'K';
      vault.stored['bybit.live.secret'] = 'S';
      final repository =
          LocalAnalysisRepository(store: LocalStore.inMemory(), vault: vault);

      // Текущий режим — testnet: ключей для него нет.
      expect(await repository.hasBrokerKeys(BrokerId.bybit), isFalse);
      // Но площадку есть чем читать, и приложение обязано это знать: иначе
      // Bybit молча пропадает из капитала.
      expect(await repository.brokerKeyModes(BrokerId.bybit),
          {TradingMode.live});
      expect(await repository.readableMode(BrokerId.bybit), TradingMode.live);
    });

    test('текущий режим имеет приоритет, когда ключи есть для обоих', () async {
      final vault = FakeVault();
      for (final mode in ['live', 'testnet']) {
        vault.stored['bybit.$mode.key'] = 'K';
        vault.stored['bybit.$mode.secret'] = 'S';
      }
      final repository =
          LocalAnalysisRepository(store: LocalStore.inMemory(), vault: vault);

      expect(await repository.readableMode(BrokerId.bybit), TradingMode.testnet);
      await repository.setTradingMode(BrokerId.bybit, TradingMode.live);
      expect(await repository.readableMode(BrokerId.bybit), TradingMode.live);
    });

    test('без ключей вовсе площадка нечитаема', () async {
      final repository =
          LocalAnalysisRepository(store: LocalStore.inMemory(), vault: FakeVault());
      expect(await repository.readableMode(BrokerId.bybit), isNull);
      expect(await repository.brokerKeyModes(BrokerId.bybit), isEmpty);
    });

    test('послабление режима не распространяется на отправку ордера', () async {
      // Читать живой счёт ключом live при переключателе на testnet безопасно.
      // Отправлять туда заявку — нет: ошибка режима в исполнении это деньги.
      final store = LocalStore.inMemory();
      await seedDigest(store, [signal(market: Market.crypto, symbol: 'BTCUSDT')]);
      final vault = FakeVault();
      vault.stored['bybit.live.key'] = 'K';
      vault.stored['bybit.live.secret'] = 'S';
      final repository = LocalAnalysisRepository(store: store, vault: vault);
      await repository.setTradingEnabled(true);

      // Режим остаётся тренировочным — ключей для него нет.
      await expectLater(
        repository.confirmSignal('sig-1'),
        throwsA(isA<FeatureUnavailableException>()),
      );
    });
  });

  group('Живой счёт как наблюдение', () {
    test('Bybit переключается на live при закрытом допуске', () async {
      final repository =
          LocalAnalysisRepository(store: LocalStore.inMemory(), vault: FakeVault());

      await repository.setTradingMode(BrokerId.bybit, TradingMode.live);
      expect(repository.tradingState.modeOf(BrokerId.bybit), TradingMode.live);
    });

    test('Т-Инвестиции на live не переключаются, пока стоп не проверен', () async {
      final repository =
          LocalAnalysisRepository(store: LocalStore.inMemory(), vault: FakeVault());

      await expectLater(
        repository.setTradingMode(BrokerId.tinvest, TradingMode.live),
        throwsA(isA<FeatureUnavailableException>()),
      );
    });

    test('ордер на живой счёт при закрытом допуске не уходит', () async {
      final store = LocalStore.inMemory();
      await seedDigest(store, [signal(market: Market.crypto, symbol: 'BTCUSDT')]);
      final vault = FakeVault();
      // Ключи лежат в живом слоте — дело не в них.
      vault.stored['bybit.live.key'] = 'K';
      vault.stored['bybit.live.secret'] = 'S';
      final repository = LocalAnalysisRepository(store: store, vault: vault);
      await repository.setTradingEnabled(true);
      await repository.setTradingMode(BrokerId.bybit, TradingMode.live);

      await expectLater(
        repository.confirmSignal('sig-1'),
        throwsA(isA<FeatureUnavailableException>().having(
          (e) => e.message,
          'message',
          contains('наблюдения'),
        )),
      );
    });
  });

  group('Идея на бумаге', () {
    test('идею можно завести в журнал без ключей и без биржи', () async {
      final store = LocalStore.inMemory();
      await seedDigest(store, [signal()]);
      final repository = LocalAnalysisRepository(store: store, vault: FakeVault());

      expect(repository.paperNoteFor('TSTU6'), isNull);

      final answer = await repository.trackOnPaper('sig-1');
      expect(answer, contains('заведена'));
      expect(repository.ledger.trades, hasLength(1));
      expect(repository.paperNoteFor('TSTU6'), contains('ждёт входа'));
    });

    test('повторное нажатие не создаёт вторую сделку', () async {
      final store = LocalStore.inMemory();
      await seedDigest(store, [signal()]);
      final repository = LocalAnalysisRepository(store: store, vault: FakeVault());

      await repository.trackOnPaper('sig-1');
      final answer = await repository.trackOnPaper('sig-1');

      expect(answer, contains('уже ведётся'));
      expect(repository.ledger.trades, hasLength(1));
    });

    test('запись переживает перезапуск', () async {
      final store = LocalStore.inMemory();
      await seedDigest(store, [signal()]);
      final first = LocalAnalysisRepository(store: store, vault: FakeVault());
      await first.trackOnPaper('sig-1');

      final second = LocalAnalysisRepository(store: store, vault: FakeVault());
      await second.fetchTrades();
      expect(second.paperNoteFor('TSTU6'), isNotNull);
    });

    test('идея не из выдачи — отказ с внятной причиной', () async {
      final store = LocalStore.inMemory();
      await seedDigest(store, [signal()]);
      final repository = LocalAnalysisRepository(store: store, vault: FakeVault());

      await expectLater(
        repository.trackOnPaper('нет-такой'),
        throwsA(isA<FeatureUnavailableException>()),
      );
    });
  });
}
