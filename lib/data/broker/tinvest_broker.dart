import 'dart:convert';
import 'dart:io';

import '../../domain/broker/broker.dart';
import '../../domain/broker/tinvest_role.dart';
import '../local_store.dart';
import '../market/net_failure.dart';
import '../net/resilient_http.dart';

/// Курс инструмента в формате Т-Инвестиций: целая часть и миллиардные доли.
///
/// Отдельный разбор нужен потому, что биржа не отдаёт цену числом: `123.45`
/// приходит как `{units: "123", nano: 450000000}`. Наивное сложение через
/// double теряет точность на копейках, а на срочном рынке цена — это шаг,
/// кратный `minPriceIncrement`.
double quotationToDouble(Object? value) {
  if (value is num) return value.toDouble();
  if (value is! Map) return 0;
  final units = _int(value['units']);
  final nano = _int(value['nano']);
  return units + nano / 1000000000;
}

/// Обратное преобразование: число в пару «единицы + нано».
Map<String, dynamic> doubleToQuotation(double value) {
  final negative = value < 0;
  final abs = value.abs();
  final units = abs.floor();
  // Округление именно здесь: иначе 0.1 превращается в 99999999 нано.
  var nano = ((abs - units) * 1000000000).round();
  var wholeUnits = units;
  if (nano >= 1000000000) {
    wholeUnits += 1;
    nano -= 1000000000;
  }
  return {
    'units': (negative ? -wholeUnits : wholeUnits).toString(),
    'nano': negative ? -nano : nano,
  };
}

int _int(Object? value) => switch (value) {
      int v => v,
      num v => v.toInt(),
      String v => int.tryParse(v) ?? 0,
      _ => 0,
    };

/// Счёт у Т-Инвестиций.
///
/// Токен Invest API привязан к пользователю, а не к счёту: `GetAccounts`
/// возвращает все открытые счета вместе с уровнем доступа. Поэтому «токен
/// сделан под фьючерсный счёт» — это про права, а не про видимость: основной
/// брокерский счёт тем же токеном виден, и капитал по нему считается.
/// Бумага на инвестиционном счёте.
///
/// Не [BrokerPosition]: та описывает торговую позицию — сторону, плавающий
/// результат, наличие защитного стопа. Здесь ничего этого нет и быть не
/// должно. Инвестиционный счёт не торгуется приложением, и вопрос к нему
/// один: что лежит и сколько это стоит сейчас. Из этих чисел считаются доли,
/// а из долей — расхождение с целевым составом.
class TInvestHolding {
  const TInvestHolding({
    required this.symbol,
    required this.quantity,
    required this.marketValue,
    this.averagePrice = 0,
    this.marketPrice = 0,
  });

  final String symbol;
  final double quantity;
  final double averagePrice;
  final double marketPrice;
  final double marketValue;

  /// В форме, которую принимает движок. Числа уходят строками: доли
  /// считаются точной арифметикой, и двоичная дробь по дороге туда лишняя.
  Map<String, dynamic> toJson() => {
        'symbol': symbol,
        'quantity': quantity.toString(),
        'market_value': marketValue.toStringAsFixed(2),
        if (averagePrice > 0) 'average_price': averagePrice.toStringAsFixed(4),
        if (marketPrice > 0) 'market_price': marketPrice.toStringAsFixed(4),
      };
}

/// Снимок инвестиционного счёта целиком: чей он и что на нём.
class InvestSnapshot {
  const InvestSnapshot({
    required this.accountId,
    required this.holdings,
    this.title = '',
  });

  final String accountId;
  final String title;
  final List<TInvestHolding> holdings;
}

class TInvestAccount {
  const TInvestAccount({
    required this.id,
    required this.name,
    required this.type,
    required this.accessLevel,
    this.status = '',
    this.openedAt,
  });

  final String id;
  final String name;

  /// ACCOUNT_TYPE_TINKOFF, ACCOUNT_TYPE_TINKOFF_IIS, ACCOUNT_TYPE_INVEST_BOX.
  final String type;

  /// ACCOUNT_ACCESS_LEVEL_FULL_ACCESS / READ_ONLY / NO_ACCESS.
  final String accessLevel;

  /// ACCOUNT_STATUS_OPEN / NEW / CLOSED.
  final String status;

  /// Закрытый счёт: в выдаче он есть, но ни торговать, ни считать по нему
  /// нечего.
  bool get closed => status.endsWith('CLOSED');

  final DateTime? openedAt;

  /// Можно ли отправлять заявки с этого счёта.
  ///
  /// Запрещаем только там, где брокер прямо сказал «только чтение».
  /// Отсутствие поля — это «не знаю», а не «нельзя»: песочница и старые
  /// ответы его не возвращают, и трактовать молчание как запрет значит
  /// сломать торговлю на ровном месте. Настоящий запрет приходит от биржи
  /// отказом на заявку, и он честнее нашей догадки.
  bool get tradable => !accessLevel.endsWith('READ_ONLY');

  /// Индивидуальный инвестиционный счёт: другой налоговый режим, и в
  /// интерфейсе он не должен выглядеть как обычный брокерский.
  bool get isIis => type.contains('IIS');

  /// Человеческая подпись: имя счёта у брокера может быть пустым.
  String get title => name.trim().isEmpty
      ? (isIis ? 'ИИС' : 'Брокерский счёт')
      : name.trim();

  static TInvestAccount fromJson(Map<String, dynamic> json) => TInvestAccount(
        id: json['id'] as String? ?? '',
        name: json['name'] as String? ?? '',
        type: json['type'] as String? ?? '',
        accessLevel: json['accessLevel'] as String? ?? '',
        status: json['status'] as String? ?? '',
        openedAt: DateTime.tryParse(json['openedDate'] as String? ?? ''),
      );
}

/// Разрешённый инструмент срочного рынка.
class TInvestInstrument {
  const TInvestInstrument({
    required this.uid,
    required this.ticker,
    required this.lot,
    required this.priceStep,
  });

  final String uid;
  final String ticker;

  /// Сколько контрактов в одном лоте заявки.
  final int lot;

  /// Минимальный шаг цены: заявка не кратная ему будет отклонена.
  final double priceStep;

  Map<String, dynamic> toJson() =>
      {'uid': uid, 'ticker': ticker, 'lot': lot, 'step': priceStep};

  factory TInvestInstrument.fromJson(Map<String, dynamic> j) => TInvestInstrument(
        uid: j['uid'] as String? ?? '',
        ticker: j['ticker'] as String? ?? '',
        lot: (j['lot'] as num?)?.toInt() ?? 1,
        priceStep: (j['step'] as num?)?.toDouble() ?? 0,
      );
}

/// Торговый доступ к российскому рынку через Т-Инвестиции.
///
/// Транспорт — REST-шлюз Invest API: `POST` на
/// `/rest/tinkoff.public.invest.api.contract.v1.<Сервис>/<Метод>` с телом JSON.
/// gRPC не нужен, и это сохраняет нулевую зависимость от пакетов pub.
///
/// Про хранение токена честно: у Bybit секрет никогда не покидает нативную
/// сторону — подпись HMAC считается там же, в обёртке Keystore. Здесь
/// авторизация bearer-токеном, то есть токен обязан попасть в заголовок, а
/// значит и в память Dart. Он шифруется на диске так же, но заявлять «не
/// попадает в Dart» про него было бы неправдой.
class TInvestBroker implements Broker {
  TInvestBroker({
    required this.mode,
    required Future<String?> Function() token,
    required this.instrumentCache,
    this.role = TInvestRole.trade,
    HttpClient? client,
    this.baseUrl,
    this.timeout = const Duration(seconds: 20),
  })  : _token = token,
        _client = client ?? resilientHttpClient();

  /// Роль токена. Она же — граница прав: инвестиционный токен выдан только
  /// на чтение, и заявку он не отправит, даже если её попросят отправить.
  final TInvestRole role;

  @override
  final TradingMode mode;

  final Future<String?> Function() _token;
  final HttpClient _client;
  final Duration timeout;

  /// Кэш «тикер → инструмент». Соответствие живёт столько же, сколько
  /// контракт, поэтому спрашивать биржу при каждой заявке незачем.
  final TInvestInstrumentCache instrumentCache;

  /// Адрес шлюза. Подменяется в тестах на локальный сервер.
  final String? baseUrl;

  @override
  BrokerId get id => BrokerId.tinvest;

  @override
  String get name => 'Т-Инвестиции';

  bool get _sandbox => role.isSandbox || mode == TradingMode.testnet;

  /// Отказать, если роль токена не даёт торговать.
  ///
  /// Проверка стоит первой строкой в каждом методе, который умеет отправить
  /// заявку, — до сети, до счёта, до всего. Правило «инвестиции — это только
  /// рекомендации» обязано держаться кодом, а не тем, что вызывающий помнит
  /// о нём. Один забытый вызов — и рекомендация превращается в сделку с
  /// основным капиталом.
  void _requireTrading(String what) {
    if (role.canTrade) return;
    throw BrokerException(
      '$what: токен «${role.title}» выдан только на чтение. '
      'Инвестиционный контур даёт рекомендации — сделки по ним '
      'вы совершаете сами.',
    );
  }

  /// T‑Bank рекомендует отдельный host песочницы. Сервис SandboxService
  /// доступен и через prod proxy, но смешивать host prod и sandbox методы —
  /// отличный способ однажды отправить не тот запрос не в тот контур.
  String get _base => baseUrl ?? (_sandbox
      ? 'https://sandbox-invest-public-api.tbank.ru/rest'
      : 'https://invest-public-api.tbank.ru/rest');

  static const _ns = 'tinkoff.public.invest.api.contract.v1';
  static const _sandboxAccountName = 'SignalAI risk sandbox';
  static const _sandboxRiskCapitalRub = 300000;

  /// Все счета токена. Кэшируется список, а не один счёт: раньше здесь
  /// хранился `accounts.first`, и основной капитал владельца приложение
  /// просто не видело.
  List<TInvestAccount>? _accounts;

  /// Счёт, с которого разрешено торговать. Задаётся снаружи (настройками);
  /// null — берётся первый с полным доступом.
  String? tradingAccountId;

  /// Счета, которые приложению разрешено читать.
  ///
  /// Пустое множество означает «ограничения нет» — так работает песочница и
  /// так же ведут себя настройки до первой выдачи доступа. Как только в
  /// списке появился хоть один счёт, все остальные перестают существовать
  /// для приложения: их не видно ни в капитале, ни в выборе торгового.
  Set<String> allowedAccountIds = const {};

  @override
  Future<bool> get isReady async {
    try {
      await checkAccess();
      return true;
    } on BrokerException {
      return false;
    }
  }

  @override
  Future<String> checkAccess() async {
    final list = await accounts();
    final trading = await _account();
    final tradable = list.where((a) => a.tradable).length;
    return 'Токен принят · счетов ${list.length}'
        '${tradable < list.length ? ' (торговых $tradable)' : ''}'
        ' · торгуем со счёта $trading'
        '${_sandbox ? ' (песочница)' : ''}';
  }

  /// Все счета токена. Закрытые отбрасываются: торговать на них нельзя, а в
  /// капитале они дали бы нули.
  ///
  /// В песочнице SignalAI использует отдельный именованный счёт. Если его
  /// ещё нет, он создаётся и **один раз** пополняется на выбранный владельцем
  /// риск-капитал 300 000 ₽. Существующий счёт повторно не пополняем: иначе
  /// каждый запуск приложения искусственно раздувал бы тренировочный капитал.
  Future<List<TInvestAccount>> accounts({bool force = false}) async {
    final known = _accounts;
    if (known != null && !force) return known;

    final service = _sandbox ? 'SandboxService' : 'UsersService';
    final method = _sandbox ? 'GetSandboxAccounts' : 'GetAccounts';
    final json = await _call(service, method, const {});
    final list = [
      for (final item in json['accounts'] as List<dynamic>? ?? const [])
        TInvestAccount.fromJson(item as Map<String, dynamic>),
    ]
        .where((a) =>
            a.id.isNotEmpty && !a.closed && !a.accessLevel.endsWith('NO_ACCESS'))
        .where((a) => allowedAccountIds.isEmpty || allowedAccountIds.contains(a.id))
        .toList();

    if (_sandbox) {
      final dedicated = list
          .where((a) => a.name.trim() == _sandboxAccountName)
          .firstOrNull;
      if (dedicated != null) return _accounts = [dedicated];

      final opened = await _call(
        'SandboxService',
        'OpenSandboxAccount',
        const {'name': _sandboxAccountName},
      );
      final id = opened['accountId'] as String?;
      if (id == null || id.isEmpty) {
        throw const BrokerException('Не удалось открыть счёт песочницы');
      }
      await _call(
        'SandboxService',
        'SandboxPayIn',
        {
          'accountId': id,
          'amount': {
            'currency': 'rub',
            'units': _sandboxRiskCapitalRub.toString(),
            'nano': 0,
          },
        },
      );
      return _accounts = [
        TInvestAccount(
          id: id,
          name: _sandboxAccountName,
          type: 'ACCOUNT_TYPE_TINKOFF',
          accessLevel: 'ACCOUNT_ACCESS_LEVEL_FULL_ACCESS',
          status: 'ACCOUNT_STATUS_OPEN',
        ),
      ];
    }

    if (list.isEmpty) {
      throw const BrokerException('У токена нет доступных счетов');
    }
    return _accounts = list;
  }

  /// Счёт, с которого уходят заявки.
  ///
  /// Только он: остальные счета читаются, но не торгуют. Это защита, а не
  /// удобство — заявка, ушедшая не с того счёта, ломает и учёт, и налоги.
  Future<String> _account() async {
    final list = await accounts();
    final chosen = tradingAccountId;
    if (chosen != null) {
      final match = list.where((a) => a.id == chosen).firstOrNull;
      if (match == null) {
        throw BrokerException('Счёт $chosen токену недоступен');
      }
      if (!match.tradable) {
        throw BrokerException(
          'Счёт ${match.title} доступен только на чтение — торговать с него нельзя',
        );
      }
      return match.id;
    }
    final tradable = list.where((a) => a.tradable).firstOrNull;
    if (tradable == null) {
      throw const BrokerException(
        'У токена нет счёта с полным доступом: он выдан только на чтение',
      );
    }
    return tradable.id;
  }

  /// Тикер → инструмент срочного рынка.
  Future<TInvestInstrument> _instrument(String ticker) async {
    final cached = await instrumentCache.get(ticker);
    if (cached != null) return cached;

    final json = await _call('InstrumentsService', 'FutureBy', {
      'idType': 'INSTRUMENT_ID_TYPE_TICKER',
      'classCode': 'SPBFUT',
      'id': ticker,
    });
    final data = json['instrument'] as Map<String, dynamic>?;
    final uid = data?['uid'] as String?;
    if (data == null || uid == null) {
      throw BrokerException('Т-Инвестиции не знают контракт $ticker');
    }
    final instrument = TInvestInstrument(
      uid: uid,
      ticker: ticker,
      lot: (data['lot'] as num?)?.toInt() ?? 1,
      priceStep: quotationToDouble(data['minPriceIncrement']),
    );
    await instrumentCache.put(ticker, instrument);
    return instrument;
  }

  @override
  Future<OrderResult> placeOrder(OrderRequest request) async {
    _requireTrading('Заявка не отправлена');
    // Вход стоп-заявкой брокеру пока не отправляется: у Т-Инвестиций это
    // отдельный тип (стоп-лимит), и его постановка вместе с защитным стопом
    // не проверена на песочнице. Честный отказ лучше кривой заявки.
    if (request.stopEntry) {
      return const OrderResult.rejected(
        'Вход стоп-заявкой для Т-Инвестиций ещё не реализован — '
        'идея ведётся на бумаге',
      );
    }
    try {
      final account = await _account();
      final instrument = await _instrument(request.symbol);
      final lots = _lots(request.quantity, instrument);
      if (lots < 1) {
        return const OrderResult.rejected('Объём меньше одного лота');
      }

      final entry = await _call(
        _sandbox ? 'SandboxService' : 'OrdersService',
        _sandbox ? 'PostSandboxOrder' : 'PostOrder',
        {
          'accountId': account,
          'instrumentId': instrument.uid,
          'quantity': lots.toString(),
          'price': doubleToQuotation(_align(request.entry, instrument.priceStep)),
          'direction': request.long ? 'ORDER_DIRECTION_BUY' : 'ORDER_DIRECTION_SELL',
          'orderType': 'ORDER_TYPE_LIMIT',
          'orderId': _idempotencyKey(request),
          if (_sandbox) ...{
            'timeInForce': 'TIME_IN_FORCE_DAY',
            'priceType': 'PRICE_TYPE_POINT',
            'confirmMarginTrade': true,
          },
        },
      );
      final orderId = entry['orderId'] as String? ?? '';

      // Защита обязана остаться в том же контуре, что и вход. Раньше
      // PostSandboxOrder сразу после себя вызывал production StopOrdersService:
      // тестовая заявка была принята, а защита уходила вообще другим API.
      try {
        final stopId = _stopIdempotencyKey(request);
        await _call(
          _sandbox ? 'SandboxService' : 'StopOrdersService',
          _sandbox ? 'PostSandboxStopOrder' : 'PostStopOrder',
          {
            'accountId': account,
            'instrumentId': instrument.uid,
            'quantity': lots.toString(),
            'stopPrice': doubleToQuotation(_align(request.stopLoss, instrument.priceStep)),
            'direction': request.long
                ? 'STOP_ORDER_DIRECTION_SELL'
                : 'STOP_ORDER_DIRECTION_BUY',
            'stopOrderType': 'STOP_ORDER_TYPE_STOP_LOSS',
            'expirationType': 'STOP_ORDER_EXPIRATION_TYPE_GOOD_TILL_CANCEL',
            if (_sandbox) ...{
              'orderId': stopId,
              'priceType': 'PRICE_TYPE_POINT',
              'confirmMarginTrade': true,
            },
          },
        );
      } on BrokerException catch (e) {
        await _cancelQuietly(account, orderId);
        return OrderResult.rejected(
          'Защитный стоп не принят (${e.message}) — заявка на вход снята. '
          'Позицию без стопа приложение не оставляет.',
        );
      }

      return OrderResult(
        accepted: orderId.isNotEmpty,
        orderId: orderId,
        message: orderId.isNotEmpty
            ? 'Заявка и стоп приняты${_sandbox ? ' в песочнице' : ''}'
            : 'Брокер не вернул номер заявки',
      );
    } on BrokerException catch (e) {
      return OrderResult.rejected(e.message);
    }
  }

  @override
  Future<List<BrokerPosition>> positions({String? accountId}) async {
    final account = accountId ?? await _account();
    // Портфель, а не GetPositions: только он отдаёт среднюю цену входа и
    // плавающий результат. Без них блок «На бирже» показывал бы нули.
    final json = await _call(
      _sandbox ? 'SandboxService' : 'OperationsService',
      _sandbox ? 'GetSandboxPortfolio' : 'GetPortfolio',
      {'accountId': account},
    );

    final protectedUids = await _protectedInstruments(account);
    final result = <BrokerPosition>[];
    for (final item in json['positions'] as List<dynamic>? ?? const []) {
      final row = item as Map<String, dynamic>;
      if ((row['instrumentType'] as String? ?? '') != 'futures') continue;
      final quantity = quotationToDouble(row['quantity']);
      if (quantity == 0) continue;

      final uid = row['instrumentUid'] as String?;
      result.add(BrokerPosition(
        symbol: await _tickerOf(uid, row['figi'] as String?),
        long: quantity > 0,
        quantity: quantity.abs(),
        entryPrice: quotationToDouble(row['averagePositionPrice']),
        unrealizedPnl: quotationToDouble(row['expectedYield']),
        protected: uid != null && protectedUids.contains(uid),
      ));
    }
    return result;
  }

  Future<List<TInvestHolding>> holdings(String accountId) async {
    final json = await _call(
      _sandbox ? 'SandboxService' : 'OperationsService',
      _sandbox ? 'GetSandboxPortfolio' : 'GetPortfolio',
      {'accountId': accountId},
    );

    const kinds = {'share', 'etf', 'bond'};
    final result = <TInvestHolding>[];
    for (final item in json['positions'] as List<dynamic>? ?? const []) {
      final row = item as Map<String, dynamic>;
      if (!kinds.contains(row['instrumentType'] as String? ?? '')) continue;
      final quantity = quotationToDouble(row['quantity']);
      if (quantity == 0) continue;
      final price = quotationToDouble(row['currentPrice']);
      result.add(TInvestHolding(
        symbol: await _tickerOf(
          row['instrumentUid'] as String?,
          row['figi'] as String?,
        ),
        quantity: quantity,
        averagePrice: quotationToDouble(row['averagePositionPrice']),
        marketPrice: price,
        marketValue: price * quantity,
      ));
    }
    return result;
  }

  Future<Map<String, double>> cashBalances(String accountId) async {
    final json = await _call(
      _sandbox ? 'SandboxService' : 'OperationsService',
      _sandbox ? 'GetSandboxPortfolio' : 'GetPortfolio',
      {'accountId': accountId},
    );
    final result = <String, double>{};
    for (final item in json['positions'] as List<dynamic>? ?? const []) {
      final row = item as Map<String, dynamic>;
      if ((row['instrumentType'] as String? ?? '') != 'currency') continue;
      final code = (row['figi'] as String? ?? '').toLowerCase().contains('usd')
          ? 'USD'
          : 'RUB';
      final quantity = quotationToDouble(row['quantity']);
      result[code] = (result[code] ?? 0) + quantity;
    }
    if (result.isEmpty) {
      final total = json['totalAmountCurrencies'];
      if (total is Map<String, dynamic>) {
        final code = (total['currency'] as String? ?? 'rub').toUpperCase();
        result[code] = quotationToDouble(total);
      }
    }
    return result;
  }

  Future<List<BrokerOperation>> operations({
    required DateTime from,
    DateTime? to,
    String? accountId,
  }) async {
    final account = accountId ?? await _account();
    final json = await _call(
      _sandbox ? 'SandboxService' : 'OperationsService',
      _sandbox ? 'GetSandboxOperations' : 'GetOperations',
      {
        'accountId': account,
        'from': from.toUtc().toIso8601String(),
        'to': (to ?? DateTime.now()).toUtc().toIso8601String(),
        'state': 'OPERATION_STATE_EXECUTED',
      },
    );

    final result = <BrokerOperation>[];
    for (final item in json['operations'] as List<dynamic>? ?? const []) {
      final row = item as Map<String, dynamic>;
      final date = DateTime.tryParse(row['date'] as String? ?? '');
      if (date == null) continue;
      result.add(BrokerOperation(
        id: row['id'] as String? ?? '',
        type: row['operationType'] as String? ?? '',
        description: row['type'] as String? ?? '',
        at: date.toUtc(),
        payment: quotationToDouble(row['payment']),
        price: quotationToDouble(row['price']),
        quantity: (row['quantity'] as num?)?.toDouble() ??
            double.tryParse(row['quantity'] as String? ?? '') ??
            0,
        currency: (row['currency'] as String? ?? 'rub').toUpperCase(),
        instrument: await _tickerOf(
          row['instrumentUid'] as String?,
          row['figi'] as String?,
        ),
      ));
    }
    return result;
  }

  Future<String> _tickerOf(String? uid, String? figi) async {
    final known = await instrumentCache.tickerFor(uid);
    if (known != null) return known;
    if (uid != null) {
      try {
        final json = await _call('InstrumentsService', 'FutureBy', {
          'idType': 'INSTRUMENT_ID_TYPE_UID',
          'id': uid,
        });
        final data = json['instrument'] as Map<String, dynamic>?;
        final ticker = data?['ticker'] as String?;
        if (data != null && ticker != null && ticker.isNotEmpty) {
          await instrumentCache.put(
            ticker,
            TInvestInstrument(
              uid: uid,
              ticker: ticker,
              lot: (data['lot'] as num?)?.toInt() ?? 1,
              priceStep: quotationToDouble(data['minPriceIncrement']),
            ),
          );
          return ticker;
        }
      } on BrokerException {
        // Брокер не ответил — покажем то, что есть, но не выдумаем тикер.
      }
    }
    final fallback = figi ?? '';
    return fallback.isEmpty ? (uid ?? '—') : fallback;
  }

  Future<Set<String>> _protectedInstruments(String account) async {
    try {
      final json = await _call(
        _sandbox ? 'SandboxService' : 'StopOrdersService',
        _sandbox ? 'GetSandboxStopOrders' : 'GetStopOrders',
        {'accountId': account},
      );
      return {
        for (final item in json['stopOrders'] as List<dynamic>? ?? const [])
          ?(item as Map<String, dynamic>)['instrumentUid'] as String?,
      };
    } on BrokerException {
      return const {};
    }
  }

  @override
  Future<List<BrokerPosition>> unprotectedPositions() async =>
      [for (final p in await positions()) if (p.unprotected) p];

  @override
  Future<bool> placeProtectiveStop({
    required String symbol,
    required double stopPrice,
    required bool long,
    required double quantity,
  }) async {
    _requireTrading('Защитный стоп не выставлен');
    try {
      final account = await _account();
      final instrument = await _instrument(symbol);
      final lots = _lots(quantity, instrument);
      if (lots < 1) return false;
      await _call(
        _sandbox ? 'SandboxService' : 'StopOrdersService',
        _sandbox ? 'PostSandboxStopOrder' : 'PostStopOrder',
        {
          'accountId': account,
          'instrumentId': instrument.uid,
          'quantity': lots.toString(),
          'stopPrice': doubleToQuotation(_align(stopPrice, instrument.priceStep)),
          'direction':
              long ? 'STOP_ORDER_DIRECTION_SELL' : 'STOP_ORDER_DIRECTION_BUY',
          'stopOrderType': 'STOP_ORDER_TYPE_STOP_LOSS',
          'expirationType': 'STOP_ORDER_EXPIRATION_TYPE_GOOD_TILL_CANCEL',
          if (_sandbox) ...{
            'orderId': _protectiveStopId(symbol, long),
            'priceType': 'PRICE_TYPE_POINT',
            'confirmMarginTrade': true,
          },
        },
      );
      return true;
    } on BrokerException {
      return false;
    }
  }

  @override
  Future<List<BrokerOrder>> orders() async {
    final account = await _account();
    final json = await _call(
      _sandbox ? 'SandboxService' : 'OrdersService',
      _sandbox ? 'GetSandboxOrders' : 'GetOrders',
      {'accountId': account},
    );

    final result = <BrokerOrder>[];
    for (final item in json['orders'] as List<dynamic>? ?? const []) {
      final row = item as Map<String, dynamic>;
      result.add(BrokerOrder(
        orderId: row['orderId'] as String? ?? '',
        symbol: await _tickerOf(
          row['instrumentUid'] as String?,
          row['figi'] as String?,
        ),
        long: (row['direction'] as String? ?? '') == 'ORDER_DIRECTION_BUY',
        quantity: _int(row['lotsRequested']).toDouble(),
        price: quotationToDouble(row['initialSecurityPrice']),
        status: _statusLabel(row['executionReportStatus'] as String?),
      ));
    }
    return result;
  }

  @override
  Future<int> cancelAllOrders() async {
    final account = await _account();
    var cancelled = 0;
    for (final order in await orders()) {
      await _cancelQuietly(account, order.orderId);
      cancelled++;
    }
    return cancelled;
  }

  Future<void> _cancelQuietly(String account, String orderId) async {
    if (orderId.isEmpty) return;
    try {
      await _call(
        _sandbox ? 'SandboxService' : 'OrdersService',
        _sandbox ? 'CancelSandboxOrder' : 'CancelOrder',
        {'accountId': account, 'orderId': orderId},
      );
    } on BrokerException {
      // Заявка могла уже исполниться или быть снята — это не повод падать.
    }
  }

  int _lots(double quantity, TInvestInstrument instrument) {
    final lot = instrument.lot <= 0 ? 1 : instrument.lot;
    return (quantity / lot).floor();
  }

  static double _align(double price, double step) {
    if (step <= 0) return price;
    return (price / step).round() * step;
  }

  static String _idempotencyKey(OrderRequest request) {
    final seconds = DateTime.now().toUtc().millisecondsSinceEpoch ~/ 1000;
    final key = '${request.symbol}-${request.long ? 'B' : 'S'}-$seconds';
    return key.length <= 36 ? key : key.substring(key.length - 36);
  }

  static String _stopIdempotencyKey(OrderRequest request) {
    final micros = DateTime.now().toUtc().microsecondsSinceEpoch;
    final key = 'SL-${request.symbol}-${request.long ? 'L' : 'S'}-$micros';
    return key.length <= 36 ? key : key.substring(key.length - 36);
  }

  static String _protectiveStopId(String symbol, bool long) {
    final micros = DateTime.now().toUtc().microsecondsSinceEpoch;
    final key = 'SL-$symbol-${long ? 'L' : 'S'}-$micros';
    return key.length <= 36 ? key : key.substring(key.length - 36);
  }

  static String _statusLabel(String? status) => switch (status) {
        'EXECUTION_REPORT_STATUS_NEW' => 'выставлена',
        'EXECUTION_REPORT_STATUS_PARTIALLYFILL' => 'частично исполнена',
        'EXECUTION_REPORT_STATUS_FILL' => 'исполнена',
        'EXECUTION_REPORT_STATUS_REJECTED' => 'отклонена',
        'EXECUTION_REPORT_STATUS_CANCELLED' => 'снята',
        _ => 'в работе',
      };

  Future<Map<String, dynamic>> _call(
    String service,
    String method,
    Map<String, dynamic> body,
  ) async {
    final token = await _token();
    if (token == null || token.isEmpty) {
      throw const BrokerException('Токен Т-Инвестиций не задан');
    }

    final uri = Uri.parse('$_base/$_ns.$service/$method');
    try {
      final request = await _client.postUrl(uri).timeout(timeout);
      request.headers
        ..set(HttpHeaders.authorizationHeader, 'Bearer $token')
        ..set(HttpHeaders.contentTypeHeader, 'application/json')
        ..set(HttpHeaders.acceptHeader, 'application/json');
      request.write(jsonEncode(body));

      final response = await request.close().timeout(timeout);
      final text = await response.transform(utf8.decoder).join().timeout(timeout);
      if (response.statusCode >= 400) {
        throw BrokerException(_errorText(response.statusCode, text));
      }
      final decoded = jsonDecode(text);
      if (decoded is! Map<String, dynamic>) {
        throw const BrokerException('Брокер вернул неожиданный формат ответа');
      }
      return decoded;
    } on BrokerException {
      rethrow;
    } on Object catch (e) {
      throw BrokerException(brokerFailureText(e, uri.host));
    }
  }

  static String _errorText(int status, String body) {
    if (status == 401) return 'Токен не принят: проверьте, что он не отозван';
    if (status == 403) return 'У токена нет прав на эту операцию';
    if (status == 429) return 'Слишком часто: брокер ограничил частоту запросов';
    try {
      final decoded = jsonDecode(body);
      if (decoded is Map<String, dynamic>) {
        final message = decoded['message'] ?? decoded['description'];
        if (message is String && message.isNotEmpty) return message;
      }
    } on Object {
      // Тело не JSON — покажем код.
    }
    return 'Брокер ответил $status';
  }

  void close() => _client.close(force: true);
}

/// Кэш соответствия «тикер ↔ инструмент».
abstract interface class TInvestInstrumentCache {
  Future<TInvestInstrument?> get(String ticker);
  Future<void> put(String ticker, TInvestInstrument instrument);

  Future<String?> tickerFor(String? uid);
}

class StoredInstrumentCache implements TInvestInstrumentCache {
  StoredInstrumentCache(this._store);

  final LocalStore _store;
  Map<String, TInvestInstrument>? _memory;

  static const _file = 'tinvest_instruments';

  Future<Map<String, TInvestInstrument>> _load() async {
    final known = _memory;
    if (known != null) return known;
    final json = await _store.read(_file);
    final result = <String, TInvestInstrument>{};
    for (final entry in json?.entries ?? const Iterable<MapEntry<String, dynamic>>.empty()) {
      final value = entry.value;
      if (value is Map<String, dynamic>) {
        result[entry.key] = TInvestInstrument.fromJson(value);
      }
    }
    return _memory = result;
  }

  @override
  Future<TInvestInstrument?> get(String ticker) async => (await _load())[ticker];

  @override
  Future<void> put(String ticker, TInvestInstrument instrument) async {
    final known = await _load();
    known[ticker] = instrument;
    await _store.write(_file, {
      for (final e in known.entries) e.key: e.value.toJson(),
    });
  }

  @override
  Future<String?> tickerFor(String? uid) async {
    if (uid == null || uid.isEmpty) return null;
    for (final e in (await _load()).entries) {
      if (e.value.uid == uid) return e.key;
    }
    return null;
  }
}
