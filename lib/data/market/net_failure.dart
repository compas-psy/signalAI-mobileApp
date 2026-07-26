import 'dart:io';

/// Разновидность сетевого отказа.
///
/// Отличать их важно не ради красоты сообщений: «нет сети» и «биржа молчит»
/// требуют разных действий и от приложения (ретраить или нет), и от владельца
/// (чинить телефон или ждать биржу). Раньше всё сваливалось в одну кучу, и
/// пользователь видел «биржа не отвечает» там, где у приложения просто отняли
/// доступ к сети.
enum NetFailureKind {
  /// Имя хоста не резолвится. Системный DNS не работает или хост заблокирован
  /// на уровне резолвера. Повторять системным резолвером бессмысленно.
  dns,

  /// Сокет не открывается: сети нет вовсе либо она закрыта приложению.
  connection,

  /// Соединение установилось, но ответа не дождались.
  timeout,

  /// Хост ответил ошибкой 5xx или 429 — временно, повтор осмыслен.
  serverError,

  /// Хост ответил 4xx (кроме 429) либо прислал не то — повтор не поможет.
  badRequest,

  /// Ответ не разобрался.
  badFormat,
}

/// Ошибка загрузки рыночных данных с понятной причиной.
class MarketDataException implements Exception {
  MarketDataException(this.message, {this.kind = NetFailureKind.serverError});

  final String message;
  final NetFailureKind kind;

  /// Имеет ли смысл повторять запрос тем же способом.
  ///
  /// DNS-отказ сюда не входит: его лечит не повтор, а обходной резолвер.
  bool get retryable =>
      kind == NetFailureKind.serverError ||
      kind == NetFailureKind.timeout ||
      kind == NetFailureKind.connection;

  /// Отказ уровня «у приложения нет сети» — показывать не про биржу,
  /// а про устройство.
  bool get isConnectivity =>
      kind == NetFailureKind.dns || kind == NetFailureKind.connection;

  @override
  String toString() => 'MarketDataException: $message';

  /// Классификация исключения `dart:io` в понятную причину.
  static MarketDataException from(Object error, String host) {
    if (error is MarketDataException) return error;

    if (error is SocketException) {
      // «Failed host lookup» — единственный надёжный признак отказа резолвера
      // в dart:io: код ошибки платформозависим, текст — нет.
      final failedLookup = error.message.contains('Failed host lookup') ||
          error.osError?.errorCode == 7;
      if (failedLookup) {
        return MarketDataException(
          'Имя $host не удалось разрешить: DNS устройства не отвечает',
          kind: NetFailureKind.dns,
        );
      }
      return MarketDataException(
        'Не удалось соединиться с $host: ${error.osError?.message ?? error.message}',
        kind: NetFailureKind.connection,
      );
    }

    if (error is HandshakeException) {
      return MarketDataException(
        'Защищённое соединение с $host не установилось',
        kind: NetFailureKind.connection,
      );
    }

    return MarketDataException('$host не ответил вовремя', kind: NetFailureKind.timeout);
  }
}
