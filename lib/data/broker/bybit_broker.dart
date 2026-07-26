import 'dart:convert';
import 'dart:io';

import '../../domain/broker/broker.dart';
import '../net/resilient_http.dart';

/// Подписыватель запроса: принимает строку, возвращает hex HMAC-SHA256.
///
/// Отдельный тип, а не прямая зависимость от хранилища: секрет считает
/// нативная сторона, а в тестах подпись подменяется без всякого Keystore.
typedef PayloadSigner = Future<String?> Function(String payload);

/// Торговый доступ к Bybit по V5 REST.
///
/// Публичные данные берёт [BybitClient]; здесь только приватная часть, где
/// нужен ключ: баланс, постановка ордера, позиции, снятие заявок.
///
/// Подпись по правилам V5: `timestamp + apiKey + recvWindow + payload`, где
/// payload — строка запроса для GET и тело как есть для POST. Тело поэтому
/// сериализуется один раз и уходит той же строкой, которую подписали: пересборка
/// JSON перед отправкой ломала бы подпись на ровном месте.
class BybitBroker implements Broker {
  BybitBroker({
    required this.mode,
    required PayloadSigner signer,
    required Future<String?> Function() apiKey,
    HttpClient? client,
    this.baseUrl,
    this.timeout = const Duration(seconds: 15),
  })  : _sign = signer,
        _apiKey = apiKey,
        _client = client ?? resilientHttpClient();

  @override
  final TradingMode mode;

  final PayloadSigner _sign;
  final Future<String?> Function() _apiKey;
  final HttpClient _client;
  final Duration timeout;

  @override
  BrokerId get id => BrokerId.bybit;

  @override
  String get name => 'Bybit';

  /// Адрес биржи. Подменяется в тестах на локальный сервер — иначе проверить
  /// подпись и тело запроса можно было бы только живым ключом.
  final String? baseUrl;

  String get _base =>
      baseUrl ??
      (mode == TradingMode.testnet
          ? 'https://api-testnet.bybit.com'
          : 'https://api.bybit.com');

  /// Окно, в течение которого биржа принимает подписанный запрос.
  static const _recvWindow = '5000';

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
    final json = await _get('/v5/account/wallet-balance', {'accountType': 'UNIFIED'});
    final list = (json['result']?['list'] as List<dynamic>? ?? const []);
    if (list.isEmpty) return 'Ключ принят, кошелёк пуст';
    final account = list.first as Map<String, dynamic>;
    final equity = account['totalEquity'];
    return 'Ключ принят · капитал ${equity ?? '—'} USDT';
  }

  @override
  Future<OrderResult> placeOrder(OrderRequest request) async {
    final body = <String, dynamic>{
      'category': 'linear',
      'symbol': request.symbol,
      'side': request.long ? 'Buy' : 'Sell',
      'orderType': 'Limit',
      'qty': _num(request.quantity),
      'price': _num(request.entry),
      'timeInForce': 'GTC',
      // Стоп и цель уходят вместе с ордером: позиция, у которой защита
      // выставляется «потом», при обрыве связи остаётся голой.
      'stopLoss': _num(request.stopLoss),
      'takeProfit': _num(request.takeProfit),
      'tpslMode': 'Full',
      'positionIdx': 0,
    };
    try {
      final json = await _post('/v5/order/create', body);
      final id = json['result']?['orderId'] as String? ?? '';
      return OrderResult(
        accepted: id.isNotEmpty,
        orderId: id,
        message: id.isNotEmpty ? 'Заявка принята биржей' : 'Биржа не вернула номер заявки',
      );
    } on BrokerException catch (e) {
      return OrderResult.rejected(e.message);
    }
  }

  @override
  Future<List<BrokerPosition>> positions() async {
    final json = await _get('/v5/position/list', {
      'category': 'linear',
      'settleCoin': 'USDT',
    });
    final list = (json['result']?['list'] as List<dynamic>? ?? const []);
    final result = <BrokerPosition>[];
    for (final item in list) {
      final row = item as Map<String, dynamic>;
      final size = _toDouble(row['size']) ?? 0;
      if (size == 0) continue;
      result.add(BrokerPosition(
        symbol: row['symbol'] as String? ?? '',
        long: (row['side'] as String? ?? 'Buy') == 'Buy',
        quantity: size,
        entryPrice: _toDouble(row['avgPrice']) ?? 0,
        unrealizedPnl: _toDouble(row['unrealisedPnl']) ?? 0,
        // Bybit отдаёт стоп прямо в позиции: пустая строка или ноль означают,
        // что защиты нет.
        stopLoss: _toDouble(row['stopLoss']) ?? 0,
      ));
    }
    return result;
  }

  @override
  Future<List<BrokerOrder>> orders() async {
    final json = await _get('/v5/order/realtime', {
      'category': 'linear',
      'settleCoin': 'USDT',
    });
    final list = (json['result']?['list'] as List<dynamic>? ?? const []);
    return [
      for (final item in list.cast<Map<String, dynamic>>())
        BrokerOrder(
          orderId: item['orderId'] as String? ?? '',
          symbol: item['symbol'] as String? ?? '',
          long: (item['side'] as String? ?? 'Buy') == 'Buy',
          quantity: _toDouble(item['qty']) ?? 0,
          price: _toDouble(item['price']) ?? 0,
          status: item['orderStatus'] as String? ?? '',
        ),
    ];
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
    try {
      await _post('/v5/position/trading-stop', {
        'category': 'linear',
        'symbol': symbol,
        'stopLoss': _num(stopPrice),
        'tpslMode': 'Full',
        'positionIdx': 0,
      });
      return true;
    } on BrokerException {
      return false;
    }
  }

  // Аварийная остановка снимает обычные заявки и намеренно не трогает защиту
  // позиции: «стоп, всё» означает «не открывать новое», а не «снять стопы».
  @override
  Future<int> cancelAllOrders() async {
    final json = await _post('/v5/order/cancel-all', {
      'category': 'linear',
      'settleCoin': 'USDT',
    });
    return (json['result']?['list'] as List<dynamic>? ?? const []).length;
  }

  // ── Транспорт ───────────────────────────────────────────────────────────

  Future<Map<String, dynamic>> _get(String path, Map<String, String> query) async {
    final queryString = query.entries.map((e) => '${e.key}=${e.value}').join('&');
    final uri = Uri.parse('$_base$path${queryString.isEmpty ? '' : '?$queryString'}');
    return _send('GET', uri, queryString, null);
  }

  Future<Map<String, dynamic>> _post(String path, Map<String, dynamic> body) async {
    // Подписывается ровно та строка, которая уйдёт в тело.
    final payload = jsonEncode(body);
    return _send('POST', Uri.parse('$_base$path'), payload, payload);
  }

  Future<Map<String, dynamic>> _send(
    String method,
    Uri uri,
    String payload,
    String? body,
  ) async {
    final key = await _apiKey();
    if (key == null || key.isEmpty) {
      throw const BrokerException('Ключ API не задан');
    }
    final timestamp = DateTime.now().toUtc().millisecondsSinceEpoch.toString();
    final signature = await _sign('$timestamp$key$_recvWindow$payload');
    if (signature == null || signature.isEmpty) {
      throw const BrokerException(
        'Не удалось подписать запрос: секрет недоступен на этом устройстве',
      );
    }

    try {
      final request = method == 'GET'
          ? await _client.getUrl(uri).timeout(timeout)
          : await _client.postUrl(uri).timeout(timeout);
      request.headers
        ..set('X-BAPI-API-KEY', key)
        ..set('X-BAPI-TIMESTAMP', timestamp)
        ..set('X-BAPI-RECV-WINDOW', _recvWindow)
        ..set('X-BAPI-SIGN', signature)
        ..set(HttpHeaders.contentTypeHeader, 'application/json');
      if (body != null) request.write(body);

      final response = await request.close().timeout(timeout);
      final text = await response.transform(utf8.decoder).join().timeout(timeout);
      if (response.statusCode >= 400) {
        // Тело ответа выбрасывать нельзя: причина отказа именно в нём. Голое
        // «Bybit ответил 401» не отличает просроченный ключ от неверного
        // адреса площадки и от IP-фильтра — чинить по такому сообщению нечего.
        throw BrokerException(
          'Bybit ответил ${response.statusCode}${_reason(text)}'
          '${response.statusCode == 401 ? '. Ключ отклонён: он просрочен, '
              'отозван, введён от другой площадки (testnet ↔ live) либо '
              'ограничен по IP' : ''}',
        );
      }
      final decoded = jsonDecode(text);
      if (decoded is! Map<String, dynamic>) {
        throw const BrokerException('Bybit вернул неожиданный формат ответа');
      }
      final code = decoded['retCode'];
      if (code != null && code != 0) {
        throw BrokerException('Bybit: ${decoded['retMsg'] ?? 'ошибка $code'}');
      }
      return decoded;
    } on BrokerException {
      rethrow;
    } on Object catch (e) {
      throw BrokerException('Нет связи с Bybit: $e');
    }
  }

  /// Причина отказа из тела ответа: `retMsg`, если он там есть, иначе само
  /// тело, обрезанное до читаемого. Пустая строка — сказать нечего.
  static String _reason(String body) {
    final text = body.trim();
    if (text.isEmpty) return '';
    try {
      final decoded = jsonDecode(text);
      if (decoded is Map<String, dynamic>) {
        final message = decoded['retMsg'] ?? decoded['msg'] ?? decoded['message'];
        final code = decoded['retCode'];
        if (message != null && '$message'.isNotEmpty) {
          return ' · $message${code == null ? '' : ' (retCode $code)'}';
        }
      }
    } on FormatException {
      // Не JSON — покажем как есть: это обычно страница шлюза.
    }
    return ' · ${text.length > 160 ? '${text.substring(0, 157)}…' : text}';
  }

  /// Числа уходят строками: биржа отвергает экспоненциальную запись, в которую
  /// Dart сваливается на малых объёмах вроде 0.001.
  static String _num(double value) {
    if (value == value.roundToDouble() && value.abs() < 1e15) {
      return value.toStringAsFixed(0);
    }
    return value.toStringAsFixed(8).replaceFirst(RegExp(r'0+$'), '').replaceFirst(RegExp(r'\.$'), '');
  }

  static double? _toDouble(Object? value) => switch (value) {
        num n => n.toDouble(),
        String s => double.tryParse(s),
        _ => null,
      };

  void close() => _client.close(force: true);
}
