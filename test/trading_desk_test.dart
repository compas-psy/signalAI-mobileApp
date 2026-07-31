import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/broker/secure_vault.dart';
import 'package:signalai/data/local_analysis_repository.dart';
import 'package:signalai/data/market/iss_client.dart';
import 'package:signalai/data/local_store.dart';
import 'package:signalai/data/repository.dart';
import 'package:signalai/domain/broker/broker.dart';
import 'package:signalai/domain/enums.dart';
import 'package:signalai/domain/ledger/signal_ledger.dart';
import 'package:signalai/domain/models/digest.dart';
import 'package:signalai/domain/models/signal.dart';
import 'package:signalai/state/app_controller.dart';

import 'support/offline_http.dart';

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
  Future<bool> hasKeys({
    required String exchange,
    required String mode,
    bool needsSecret = true,
  }) async =>
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

/// Журнал с доказанной бумажной статистикой: выборка набрана, стратегия в
/// плюсе, убытки есть. Нужен там, где проверяется не допуск по бумаге, а то,
/// что стоит за ним, — иначе первый же гейт закрывает дорогу и до второго
/// дело не доходит.
SignalLedger provenLedger() {
  final ledger = SignalLedger();
  final start = DateTime(2025, 1, 1);
  for (var i = 0; i < 25; i++) {
    ledger.trades.add(PaperTrade(
      id: 'trade-$i',
      symbol: 'TSTU6',
      strategyId: 'forts',
      long: true,
      entry: 100,
      stopLoss: 98,
      tpPrices: const [106],
      tpShares: const [100],
      score: 80,
      createdAt: start.add(Duration(days: i)),
      status: PaperStatus.closed,
      // Убытки обязаны быть: без них профит-фактор не считается вовсе и
      // гейт отвечает «убыточных сделок пока не было» — другая ветка.
      resultR: i.isEven ? 3 : -1,
      closedAt: start.add(Duration(days: i, hours: 2)),
    ));
  }
  return ledger;
}

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
      final repository = LocalAnalysisRepository(
      iss: offlineIss(),
      bybit: offlineBybit(),
      store: store, vault: vault);

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
          LocalAnalysisRepository(
      iss: offlineIss(),
      bybit: offlineBybit(),
      store: LocalStore.inMemory(), vault: FakeVault());
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
      final first = LocalAnalysisRepository(
      iss: offlineIss(),
      bybit: offlineBybit(),
      store: store, vault: vault);
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
      final second = LocalAnalysisRepository(
      iss: offlineIss(),
      bybit: offlineBybit(),
      store: store, vault: vault);
      await second.fetchSettings();

      final check = second.keyCheckOf(BrokerId.bybit);
      expect(check?.ok, isFalse);
      expect(check?.note, isNotEmpty);
    });

    test('настройки показывают ключи заданными, но не принятыми', () async {
      final store = LocalStore.inMemory();
      final vault = FakeVault();
      final repository = LocalAnalysisRepository(
      iss: offlineIss(),
      bybit: offlineBybit(),
      store: store, vault: vault);
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
      final first = LocalAnalysisRepository(
      iss: offlineIss(),
      bybit: offlineBybit(),
      store: store, vault: vault);
      await first.setTradingMode(BrokerId.bybit, TradingMode.live);

      final second = LocalAnalysisRepository(
      iss: offlineIss(),
      bybit: offlineBybit(),
      store: store, vault: vault);
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
      final first = LocalAnalysisRepository(
      iss: offlineIss(),
      bybit: offlineBybit(),
      store: store, vault: vault);
      await first.setTradingMode(BrokerId.bybit, TradingMode.live);

      final second = LocalAnalysisRepository(
      iss: offlineIss(),
      bybit: offlineBybit(),
      store: store, vault: vault);
      // Первое обращение к репозиторию — пишущее.
      await second.setBackgroundEnabled(true);

      final third = LocalAnalysisRepository(
      iss: offlineIss(),
      bybit: offlineBybit(),
      store: store, vault: vault);
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
          LocalAnalysisRepository(
      iss: offlineIss(),
      bybit: offlineBybit(),
      store: LocalStore.inMemory(), vault: vault);

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
          LocalAnalysisRepository(
      iss: offlineIss(),
      bybit: offlineBybit(),
      store: LocalStore.inMemory(), vault: vault);

      expect(await repository.readableMode(BrokerId.bybit), TradingMode.testnet);
      await repository.setTradingMode(BrokerId.bybit, TradingMode.live);
      expect(await repository.readableMode(BrokerId.bybit), TradingMode.live);
    });

    test('без ключей вовсе площадка нечитаема', () async {
      final repository =
          LocalAnalysisRepository(
      iss: offlineIss(),
      bybit: offlineBybit(),
      store: LocalStore.inMemory(), vault: FakeVault());
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
      final repository = LocalAnalysisRepository(
      iss: offlineIss(),
      bybit: offlineBybit(),
      store: store, vault: vault);
      await repository.setTradingEnabled(true);

      // Режим остаётся тренировочным — ключей для него нет.
      await expectLater(
        repository.confirmSignal('sig-1'),
        throwsA(isA<FeatureUnavailableException>()),
      );
    });
  });

  group('Площадка на экране счетов', () {
    test('без ключей площадка называет причину, а не исчезает', () {
      const venue = VenueStatus(
        id: BrokerId.bybit,
        mode: TradingMode.testnet,
        keyModes: {},
        readable: null,
        check: null,
      );
      expect(venue.hasKeys, isFalse);
      expect(venue.problem, contains('ключи не заданы'));
    });

    test('расхождение режима объясняется прямым текстом', () {
      const venue = VenueStatus(
        id: BrokerId.bybit,
        mode: TradingMode.testnet,
        keyModes: {TradingMode.live},
        readable: TradingMode.live,
        check: null,
      );
      expect(venue.problem, contains('testnet'));
      expect(venue.problem, contains('live'));
      expect(venue.problem, contains('заявки'));
    });

    test('всё сходится — жалоб нет', () {
      const venue = VenueStatus(
        id: BrokerId.bybit,
        mode: TradingMode.live,
        keyModes: {TradingMode.live},
        readable: TradingMode.live,
        check: null,
      );
      expect(venue.problem, isNull);
    });

    test('отвергнутый биржей ключ не выдаётся за рабочий', () {
      final venue = VenueStatus(
        id: BrokerId.bybit,
        mode: TradingMode.live,
        keyModes: const {TradingMode.live},
        readable: TradingMode.live,
        check: BrokerKeyCheck(ok: false, note: 'ключ от другой площадки', at: DateTime.now()),
      );
      expect(venue.problem, contains('другой площадки'));
    });
  });

  group('Живой счёт как наблюдение', () {
    test('Bybit переключается на live при закрытом допуске', () async {
      final repository =
          LocalAnalysisRepository(
      iss: offlineIss(),
      bybit: offlineBybit(),
      store: LocalStore.inMemory(), vault: FakeVault());

      await repository.setTradingMode(BrokerId.bybit, TradingMode.live);
      expect(repository.tradingState.modeOf(BrokerId.bybit), TradingMode.live);
    });

    test('Т-Инвестиции на live переключаются: наблюдать боевой счёт можно',
        () async {
      // Токен Invest API бывает выдан только на боевой контур — песочницы у
      // него нет вовсе. Запрет на переключение отрезал бы площадку целиком:
      // приложение держало её в режиме «песочница», не находило по этой паре
      // ключей и показывало виртуальный счёт вместо настоящего.
      final repository =
          LocalAnalysisRepository(
      iss: offlineIss(),
      bybit: offlineBybit(),
      store: LocalStore.inMemory(), vault: FakeVault());

      await repository.setTradingMode(BrokerId.tinvest, TradingMode.live);

      expect(repository.tradingState.modeOf(BrokerId.tinvest), TradingMode.live);
    });

    test('ордер в Т-Инвестиции на живом счёте не уходит: стоп не подтверждён',
        () async {
      // Защитный стоп в Т-Инвестициях ставится отдельной заявкой, и пока не
      // подтверждено, что он встаёт вместе с позицией, вход означал бы
      // позицию без защиты.
      //
      // Раньше здесь стоял безусловный отказ: подтверждать было некому, и
      // снять запрет было нечем. Теперь подтверждение приходит из песочницы,
      // и проверка настоящая — поэтому журнал в этом тесте заполнен: иначе
      // сработал бы допуск по бумажной статистике и до механики дело бы не
      // дошло. Отказ должен быть именно про стоп.
      final store = LocalStore.inMemory();
      await seedDigest(store, [signal(market: Market.forts, symbol: 'TSTU6')]);
      await store.write('ledger', provenLedger().toJson());
      final vault = FakeVault();
      vault.stored['tinvest.trade.key'] = 'T';
      final repository = LocalAnalysisRepository(
      iss: offlineIss(),
      bybit: offlineBybit(),
      store: store, vault: vault);
      await repository.setTradingEnabled(true);
      await repository.setTradingMode(BrokerId.tinvest, TradingMode.live);

      await expectLater(
        repository.confirmSignal('sig-1'),
        throwsA(isA<FeatureUnavailableException>().having(
          (e) => e.message,
          'message',
          contains('стоп'),
        )),
      );
    });

    test('ордер на живой счёт при закрытом допуске не уходит', () async {
      final store = LocalStore.inMemory();
      await seedDigest(store, [signal(market: Market.crypto, symbol: 'BTCUSDT')]);
      final vault = FakeVault();
      // Ключи лежат в живом слоте — дело не в них.
      vault.stored['bybit.live.key'] = 'K';
      vault.stored['bybit.live.secret'] = 'S';
      final repository = LocalAnalysisRepository(
      iss: offlineIss(),
      bybit: offlineBybit(),
      store: store, vault: vault);
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
      final repository = LocalAnalysisRepository(
      iss: offlineIss(),
      bybit: offlineBybit(),
      store: store, vault: FakeVault());

      expect(repository.paperNoteFor('TSTU6'), isNull);

      final answer = await repository.trackOnPaper('sig-1');
      expect(answer, contains('заведена'));
      expect(repository.ledger.trades, hasLength(1));
      expect(repository.paperNoteFor('TSTU6'), contains('ждёт входа'));
    });

    test('повторное нажатие не создаёт вторую сделку', () async {
      final store = LocalStore.inMemory();
      await seedDigest(store, [signal()]);
      final repository = LocalAnalysisRepository(
      iss: offlineIss(),
      bybit: offlineBybit(),
      store: store, vault: FakeVault());

      await repository.trackOnPaper('sig-1');
      final answer = await repository.trackOnPaper('sig-1');

      expect(answer, contains('уже ведётся'));
      expect(repository.ledger.trades, hasLength(1));
    });

    test('запись переживает перезапуск', () async {
      final store = LocalStore.inMemory();
      await seedDigest(store, [signal()]);
      final first = LocalAnalysisRepository(
      iss: offlineIss(),
      bybit: offlineBybit(),
      store: store, vault: FakeVault());
      await first.trackOnPaper('sig-1');

      final second = LocalAnalysisRepository(
      iss: offlineIss(),
      bybit: offlineBybit(),
      store: store, vault: FakeVault());
      await second.fetchTrades();
      expect(second.paperNoteFor('TSTU6'), isNotNull);
    });

    test('биржа недоступна — журнал всё равно ведётся, и в сеть не уходим',
        () async {
      // Тест, случайно уходящий на биржу, — ловушка с отложенным
      // срабатыванием: локально `iss.moex.com` недоступен и отказ приходит за
      // секунду, а на раннере соединение висит до тайм-аута. Именно на этом
      // падала сборка APK на трёх зелёных у разработчика тестах.
      final http = OfflineHttp();
      final store = LocalStore.inMemory();
      await seedDigest(store, [signal()]);
      final repository = LocalAnalysisRepository(
        iss: IssClient(http: http),
        bybit: offlineBybit(),
        store: store,
        vault: FakeVault(),
      );

      final answer = await repository.trackOnPaper('sig-1');
      expect(answer, contains('заведена'));
      expect(repository.ledger.trades, hasLength(1));
      // Клиент действительно подменён: спецификацию спрашивали у подставного
      // HTTP, а не у настоящего. Иначе тест доказывал бы офлайновость,
      // сходив в интернет.
      expect(http.requested, isNotEmpty);
      expect(http.requested.first.host, 'iss.moex.com');
    });

    test('идея, которой нет в дайджесте, всё равно заводится на бумаге',
        () async {
      // Идеи приходят с движка, дайджест считает устройство, и общих
      // идентификаторов у них нет. Поиск по идентификатору отвечал «идея
      // больше не в выдаче» на идею, открытую прямо сейчас, — и сработавший
      // сигнал не попадал в журнал вовсе. А журнал открывает допуск к живым
      // деньгам: статистика набиралась по сделкам, которых владелец не видел.
      final store = LocalStore.inMemory();
      await seedDigest(store, const []);
      final repository = LocalAnalysisRepository(
        iss: offlineIss(),
        bybit: offlineBybit(),
        store: store,
        vault: FakeVault(),
      );

      final answer = await repository.trackSignalOnPaper(
        signal(id: 'с-движка', symbol: 'BTCUSDT', market: Market.crypto),
      );

      expect(answer, contains('заведена'));
      expect(repository.ledger.trades, hasLength(1));
      expect(repository.paperNoteFor('BTCUSDT'), isNotNull);
    });

    test('повтор по тому же инструменту второй записи не создаёт', () async {
      // Автозапись вызывается на каждом обновлении ленты, и защита от
      // повторов обязана жить в журнале, а не в вызывающем коде.
      final store = LocalStore.inMemory();
      await seedDigest(store, const []);
      final repository = LocalAnalysisRepository(
        iss: offlineIss(),
        bybit: offlineBybit(),
        store: store,
        vault: FakeVault(),
      );
      final idea = signal(id: 'с-движка', symbol: 'BTCUSDT', market: Market.crypto);

      await repository.trackSignalOnPaper(idea);
      final answer = await repository.trackSignalOnPaper(idea);

      expect(answer, contains('уже ведётся'));
      expect(repository.ledger.trades, hasLength(1));
    });

    test('идея не из выдачи — отказ с внятной причиной', () async {
      final store = LocalStore.inMemory();
      await seedDigest(store, [signal()]);
      final repository = LocalAnalysisRepository(
      iss: offlineIss(),
      bybit: offlineBybit(),
      store: store, vault: FakeVault());

      await expectLater(
        repository.trackOnPaper('нет-такой'),
        throwsA(isA<FeatureUnavailableException>()),
      );
    });
  });
}
