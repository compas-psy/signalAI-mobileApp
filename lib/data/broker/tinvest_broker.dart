import 'dart:convert';
import 'dart:io';

import '../../domain/broker/broker.dart';
import '../local_store.dart';
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
    HttpClient? client,
    this.baseUrl,
    this.timeout = const Duration(seconds: 20),
  })  : _token = token,
        _client = client ?? resilientHttpClient();

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

  bool get _sandbox => mode == TradingMode.testnet;

  String get _base => baseUrl ?? 'https://invest-public-api.tinkoff.ru/rest';

  static const _ns = 'tinkoff.public.invest.api.contract.v1';

  /// Номер счёта, на котором работаем. Определяется при проверке доступа.
  String? _accountId;

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
    final account = await _account();
    return 'Токен принят · счёт $account'
        '${_sandbox ? ' (песочница)' : ''}';
  }

  /// Счёт для работы. В песочнице при отсутствии счёта открывается новый.
  Future<String> _account() async {
    final known = _accountId;
    if (known != null) return known;

    final service = _sandbox ? 'SandboxService' : 'UsersService';
    final method = _sandbox ? 'GetSandboxAccounts' : 'GetAccounts';
    final json = await _call(service, method, const {});
    final accounts = (json['accounts'] as List<dynamic>? ?? const [])
        .cast<Map<String, dynamic>>()
        // Закрытые счета в выдаче есть, торговать на них нельзя.
        .where((a) => (a['status'] as String? ?? '') != 'ACCOUNT_STATUS_CLOSED')
        .toList();

    if (accounts.isEmpty) {
      if (!_sandbox) {
        throw const BrokerException('У токена нет доступных счетов');
      }
      final opened = await _call('SandboxService', 'OpenSandboxAccount', const {});
      final id = opened['accountId'] as String?;
      if (id == null) throw const BrokerException('Не удалось открыть счёт песочницы');
      return _accountId = id;
    }
    return _accountId = accounts.first['id'] as String;
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
          // Ключ идемпотентности: повтор при обрыве связи не создаст вторую
          // заявку. Секунды в ключе достаточно — чаще мы не отправляем.
          'orderId': _idempotencyKey(request),
        },
      );
      final orderId = entry['orderId'] as String? ?? '';

      // Защита ставится отдельным запросом: Т-Инвестиции, в отличие от Bybit,
      // не принимают стоп внутри заявки. Если он не встал — вход снимается.
      // Позиция без стопа хуже, чем отсутствие позиции.
      try {
        await _call(
          'StopOrdersService',
          'PostStopOrder',
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
            ? 'Заявка и стоп приняты'
            : 'Брокер не вернул номер заявки',
      );
    } on BrokerException catch (e) {
      return OrderResult.rejected(e.message);
    }
  }

  @override
  Future<List<BrokerPosition>> positions() async {
    final account = await _account();
    final json = await _call(
      _sandbox ? 'SandboxService' : 'OperationsService',
      _sandbox ? 'GetSandboxPositions' : 'GetPositions',
      {'accountId': account},
    );

    final result = <BrokerPosition>[];
    for (final item in json['futures'] as List<dynamic>? ?? const []) {
      final row = item as Map<String, dynamic>;
      final balance = _int(row['balance']).toDouble();
      if (balance == 0) continue;
      result.add(BrokerPosition(
        symbol: await instrumentCache.tickerFor(row['instrumentUid'] as String?) ??
            (row['figi'] as String? ?? ''),
        long: balance > 0,
        quantity: balance.abs(),
        // GetPositions не отдаёт среднюю цену и плавающий результат: за ними
        // нужен отдельный портфельный запрос. Показывать ноль честнее, чем
        // выдумать число.
        entryPrice: 0,
        unrealizedPnl: 0,
      ));
    }
    return result;
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
        symbol: await instrumentCache.tickerFor(row['instrumentUid'] as String?) ??
            (row['figi'] as String? ?? ''),
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

  /// Объём заявки в лотах.
  int _lots(double quantity, TInvestInstrument instrument) {
    final lot = instrument.lot <= 0 ? 1 : instrument.lot;
    return (quantity / lot).floor();
  }

  /// Цена, кратная шагу контракта: иначе брокер отклонит заявку.
  static double _align(double price, double step) {
    if (step <= 0) return price;
    return (price / step).round() * step;
  }

  static String _idempotencyKey(OrderRequest request) {
    final seconds = DateTime.now().toUtc().millisecondsSinceEpoch ~/ 1000;
    final key = '${request.symbol}-${request.long ? 'B' : 'S'}-$seconds';
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

    try {
      final uri = Uri.parse('$_base/$_ns.$service/$method');
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
      throw BrokerException('Нет связи с Т-Инвестициями: $e');
    }
  }

  /// Причина отказа человеческими словами.
  ///
  /// Брокер кладёт описание в `message`; код 401 означает ровно одно, и
  /// показывать вместо этого «ошибка 401» — заставлять гадать.
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

  /// Обратный поиск: по идентификатору инструмента вернуть тикер.
  Future<String?> tickerFor(String? uid);
}

/// Кэш инструментов на диске.
///
/// Соответствие «тикер → uid» живёт столько же, сколько сам контракт, поэтому
/// спрашивать брокера при каждой заявке незачем: лишний запрос в момент
/// отправки — лишняя точка отказа там, где она дороже всего.
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
