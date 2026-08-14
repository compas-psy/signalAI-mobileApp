import 'dart:convert';
import 'dart:io';

import 'tinvest_broker.dart' show doubleToQuotation, quotationToDouble;

enum TInvestSandboxMirrorProbeStatus {
  /// The provider has no entry with this client request id.
  absent,

  /// The entry exists, but no matching protective stop is visible.
  entryWithoutProtection,

  /// Entry and exactly one active/executed matching protective stop are visible.
  protected,

  /// Provider state is internally ambiguous or terminal in a way that cannot
  /// be repaired by silently placing another order.
  ambiguous,

  /// Provider state could not be read safely; do not submit anything.
  unavailable,
}

class TInvestSandboxMirrorProbe {
  const TInvestSandboxMirrorProbe({
    required this.status,
    this.exchangeOrderId = '',
    this.instrumentUid = '',
    this.lotsRequested = 0,
    this.message = '',
  });

  final TInvestSandboxMirrorProbeStatus status;
  final String exchangeOrderId;
  final String instrumentUid;
  final int lotsRequested;
  final String message;
}

/// Read-before-replay guard for the owner-managed T-Invest Sandbox mirror.
///
/// This is deliberately separate from [TInvestBroker.placeOrder]. The broker
/// owns first delivery; this class owns recovery after an ambiguous response
/// or process restart. Recovery never starts with another POST: it first asks
/// the provider for the entry by the original client idempotency key.
class TInvestSandboxMirrorReconciler {
  TInvestSandboxMirrorReconciler({
    required this.token,
    this.baseUrl,
    this.timeout = const Duration(seconds: 20),
    HttpClient? client,
  }) : _client = client ?? HttpClient();

  final String token;
  final String? baseUrl;
  final Duration timeout;
  final HttpClient _client;

  static const _ns = 'tinkoff.public.invest.api.contract.v1';

  String get _base =>
      baseUrl ?? 'https://sandbox-invest-public-api.tbank.ru/rest';

  Future<TInvestSandboxMirrorProbe> probe({
    required String accountId,
    required String entryRequestId,
    required String symbol,
    required bool long,
    required double stopPrice,
    DateTime? stopNotBefore,
  }) async {
    final entry = await _post(
      'GetSandboxOrderState',
      {
        'accountId': accountId,
        'orderId': entryRequestId,
        'orderIdType': 'ORDER_ID_TYPE_REQUEST',
        'priceType': 'PRICE_TYPE_POINT',
      },
      allowNotFound: true,
    );
    if (entry.statusCode == 404) {
      return const TInvestSandboxMirrorProbe(
        status: TInvestSandboxMirrorProbeStatus.absent,
      );
    }
    if (!entry.ok) {
      return TInvestSandboxMirrorProbe(
        status: TInvestSandboxMirrorProbeStatus.unavailable,
        message: entry.error,
      );
    }

    final exchangeOrderId = entry.body['orderId'] as String? ?? '';
    final instrumentUid = entry.body['instrumentUid'] as String? ?? '';
    final entryTicker = entry.body['ticker'] as String? ?? symbol;
    final lots = _int(entry.body['lotsRequested']);
    final entryStatus = entry.body['executionReportStatus'] as String? ?? '';
    if (entryStatus == 'EXECUTION_REPORT_STATUS_CANCELLED' ||
        entryStatus == 'EXECUTION_REPORT_STATUS_REJECTED') {
      return TInvestSandboxMirrorProbe(
        status: TInvestSandboxMirrorProbeStatus.ambiguous,
        exchangeOrderId: exchangeOrderId,
        instrumentUid: instrumentUid,
        lotsRequested: lots,
        message: 'Стабильный entry уже имеет терминальный статус $entryStatus',
      );
    }

    final stops = await _post(
      'GetSandboxStopOrders',
      {
        'accountId': accountId,
        'status': 'STOP_ORDER_STATUS_ALL',
      },
    );
    if (!stops.ok) {
      return TInvestSandboxMirrorProbe(
        status: TInvestSandboxMirrorProbeStatus.unavailable,
        exchangeOrderId: exchangeOrderId,
        instrumentUid: instrumentUid,
        lotsRequested: lots,
        message: stops.error,
      );
    }

    final expectedDirection =
        long ? 'STOP_ORDER_DIRECTION_SELL' : 'STOP_ORDER_DIRECTION_BUY';
    final liveMatches = <Map<String, dynamic>>[];
    final terminalMatches = <Map<String, dynamic>>[];
    for (final raw in stops.body['stopOrders'] as List<dynamic>? ?? const []) {
      if (raw is! Map<String, dynamic>) continue;
      final ticker = raw['ticker'] as String? ?? '';
      final uid = raw['instrumentUid'] as String? ?? '';
      final sameInstrument =
          (instrumentUid.isNotEmpty && uid == instrumentUid) || ticker == entryTicker;
      if (!sameInstrument) continue;
      if ((raw['direction'] as String? ?? '') != expectedDirection) continue;
      final rawLots = _int(raw['lotsRequested']);
      if (lots > 0 && rawLots > 0 && rawLots != lots) continue;
      final actualStop = quotationToDouble(raw['stopPrice']);
      if (!_samePrice(actualStop, stopPrice)) continue;

      final created = DateTime.tryParse(raw['createDate'] as String? ?? '');
      if (stopNotBefore != null &&
          created != null &&
          created.isBefore(stopNotBefore.toUtc().subtract(const Duration(minutes: 1)))) {
        continue;
      }

      final status = raw['status'] as String? ?? '';
      if (status == 'STOP_ORDER_STATUS_ACTIVE' ||
          status == 'STOP_ORDER_STATUS_EXECUTED') {
        liveMatches.add(raw);
      } else {
        terminalMatches.add(raw);
      }
    }

    if (liveMatches.length > 1 || terminalMatches.isNotEmpty) {
      final reason = liveMatches.length > 1
          ? 'T-Invest Sandbox вернула несколько подходящих защитных стопов'
          : 'Подходящий защитный стоп найден, но он уже снят/истёк';
      return TInvestSandboxMirrorProbe(
        status: TInvestSandboxMirrorProbeStatus.ambiguous,
        exchangeOrderId: exchangeOrderId,
        instrumentUid: instrumentUid,
        lotsRequested: lots,
        message: reason,
      );
    }
    if (liveMatches.isEmpty) {
      return TInvestSandboxMirrorProbe(
        status: TInvestSandboxMirrorProbeStatus.entryWithoutProtection,
        exchangeOrderId: exchangeOrderId,
        instrumentUid: instrumentUid,
        lotsRequested: lots,
      );
    }
    return TInvestSandboxMirrorProbe(
      status: TInvestSandboxMirrorProbeStatus.protected,
      exchangeOrderId: exchangeOrderId,
      instrumentUid: instrumentUid,
      lotsRequested: lots,
    );
  }

  /// Repair only the missing protective leg after the entry was found by
  /// provider reconciliation. The same stable request id is reused, so a
  /// delayed/ambiguous first stop response cannot create a second stop.
  Future<bool> ensureProtectiveStop({
    required String accountId,
    required String instrumentUid,
    required int lots,
    required bool long,
    required double stopPrice,
    required String requestId,
  }) async {
    if (accountId.isEmpty || instrumentUid.isEmpty || lots < 1) return false;
    final response = await _post(
      'PostSandboxStopOrder',
      {
        'accountId': accountId,
        'instrumentId': instrumentUid,
        'quantity': lots.toString(),
        'stopPrice': doubleToQuotation(stopPrice),
        'direction': long
            ? 'STOP_ORDER_DIRECTION_SELL'
            : 'STOP_ORDER_DIRECTION_BUY',
        'stopOrderType': 'STOP_ORDER_TYPE_STOP_LOSS',
        'expirationType': 'STOP_ORDER_EXPIRATION_TYPE_GOOD_TILL_CANCEL',
        'orderId': requestId,
        'priceType': 'PRICE_TYPE_POINT',
        'confirmMarginTrade': true,
      },
    );
    return response.ok;
  }

  Future<_RestResult> _post(
    String method,
    Map<String, dynamic> body, {
    bool allowNotFound = false,
  }) async {
    final uri = Uri.parse('$_base/$_ns.SandboxService/$method');
    try {
      final request = await _client.postUrl(uri).timeout(timeout);
      request.headers
        ..set(HttpHeaders.authorizationHeader, 'Bearer $token')
        ..set(HttpHeaders.contentTypeHeader, 'application/json')
        ..set(HttpHeaders.acceptHeader, 'application/json');
      request.write(jsonEncode(body));
      final response = await request.close().timeout(timeout);
      final text = await response.transform(utf8.decoder).join().timeout(timeout);
      final status = response.statusCode;
      if (status == 404 && allowNotFound) {
        return const _RestResult(statusCode: 404);
      }
      if (status >= 400) {
        return _RestResult(
          statusCode: status,
          error: 'T-Invest Sandbox ответила $status',
        );
      }
      final decoded = text.isEmpty ? <String, dynamic>{} : jsonDecode(text);
      if (decoded is! Map<String, dynamic>) {
        return const _RestResult(
          statusCode: 500,
          error: 'T-Invest Sandbox вернула неожиданный формат ответа',
        );
      }
      return _RestResult(statusCode: status, body: decoded);
    } on Object catch (error) {
      return _RestResult(statusCode: 0, error: '$error');
    }
  }

  static bool _samePrice(double left, double right) {
    final scale = right.abs() > 1 ? right.abs() : 1.0;
    return (left - right).abs() <= scale * 1e-9;
  }

  static int _int(Object? value) => switch (value) {
        int v => v,
        num v => v.toInt(),
        String v => int.tryParse(v) ?? 0,
        _ => 0,
      };

  void close() => _client.close(force: true);
}

class _RestResult {
  const _RestResult({
    required this.statusCode,
    this.body = const {},
    this.error = '',
  });

  final int statusCode;
  final Map<String, dynamic> body;
  final String error;

  bool get ok => statusCode >= 200 && statusCode < 300;
}
