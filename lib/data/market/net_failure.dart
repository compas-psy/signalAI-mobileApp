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

  /// Площадка закрыта для страны владельца.
  ///
  /// Отдельно от [badRequest] потому, что требует другого действия и другого
  /// объяснения. Bybit отвечает 403 с телом, где CloudFront прямо пишет
  /// «configured to block access from your country». Показать это как
  /// «ответил 403» значит отправить владельца искать ошибку в своих ключах —
  /// а ключи ни при чём, и повторять запрос бессмысленно навсегда.
  geoBlocked,

  /// TLS до хоста перехвачен: в цепочке чужой сертификат.
  ///
  /// Это не «нет сети», хотя выглядит похоже. Сеть есть, соединение
  /// устанавливается, но подменяется сертификат — так ведёт себя
  /// перехватывающий прокси. Прежняя классификация относила такой отказ к
  /// «нет сети» и вдобавок ретраила: диагноз ровно обратный настоящему.
  tlsIntercepted,
}

/// Текст отказа брокера, пропущенный через тот же классификатор.
///
/// Брокеры раньше выбрасывали сырое `$e`, и владелец получал в журнал
/// `HandshakeException: ... CERTIFICATE_VERIFY_FAILED: self signed
/// certificate` под подписью «нет связи». Диагноз обратный настоящему:
/// связь есть, подменяется сертификат, и лечится это в настройках сети, а
/// не ожиданием. Отдельная функция, а не поле в `BrokerException`: та живёт
/// в доменном слое и о сетевых деталях знать не должна.
String brokerFailureText(Object error, String host) {
  final failure = MarketDataException.from(error, host);
  final advice = failure.advice;
  return advice.isEmpty ? failure.message : '${failure.message}. $advice';
}

/// Ошибка загрузки рыночных данных с понятной причиной.
class MarketDataException implements Exception {
  MarketDataException(this.message, {this.kind = NetFailureKind.serverError});

  final String message;
  final NetFailureKind kind;

  /// Имеет ли смысл повторять запрос тем же способом.
  ///
  /// DNS-отказ сюда не входит: его лечит не повтор, а обходной резолвер.
  /// Гео-блок и перехваченный TLS сюда не входят: первый не изменится от
  /// повтора никогда, второй — свойство сети владельца, а не заминка.
  bool get retryable =>
      kind == NetFailureKind.serverError ||
      kind == NetFailureKind.timeout ||
      kind == NetFailureKind.connection;

  /// Отказ уровня «у приложения нет сети» — показывать не про биржу,
  /// а про устройство.
  bool get isConnectivity =>
      kind == NetFailureKind.dns || kind == NetFailureKind.connection;

  /// Отказ не наш и не чинится приложением: площадка или сеть владельца.
  ///
  /// Нужен, чтобы журнал не предлагал «попробовать ещё раз» там, где
  /// повтор не изменит ничего, и чтобы такие строки не копились как
  /// поломки продукта.
  bool get isEnvironment =>
      kind == NetFailureKind.geoBlocked || kind == NetFailureKind.tlsIntercepted;

  /// Что владельцу с этим делать. Пустая строка — совета нет.
  String get advice => switch (kind) {
        NetFailureKind.geoBlocked =>
          'Площадка закрыта для вашей страны. Данные по ней берёт сервер — '
              'приложение обойти это не может.',
        NetFailureKind.tlsIntercepted =>
          'Соединение подменяется в сети: в цепочке сертификатов чужой. '
              'Проверьте VPN, антивирус и профиль сети.',
        NetFailureKind.dns => 'DNS устройства не отвечает.',
        _ => '',
      };

  @override
  String toString() => 'MarketDataException: $message';

  /// Маркеры, по которым узнаётся отказ по стране.
  ///
  /// Ищутся в теле ответа: сама по себе тройка 403 не отличает «нет прав у
  /// ключа» от «страна заблокирована», а разница решает, куда владельцу
  /// идти. Тело раньше выбрасывалось вместе с причиной.
  static const _geoMarkers = [
    'block access from your country',
    'cloudfront',
    'not available in your region',
    'geo',
  ];

  /// Отказ площадки по коду и телу ответа.
  ///
  /// Отдельно от [from], потому что HTTP-ошибка приходит без исключения
  /// `dart:io`: у неё есть код и тело, и именно в теле лежит причина.
  static MarketDataException fromResponse(
    int status,
    String host, {
    String body = '',
  }) {
    final lowered = body.toLowerCase();
    if (status == 403 && _geoMarkers.any(lowered.contains)) {
      return MarketDataException(
        '$host закрыт для вашей страны',
        kind: NetFailureKind.geoBlocked,
      );
    }
    if (status == 429 || status >= 500) {
      return MarketDataException(
        '$host ответил $status',
        kind: NetFailureKind.serverError,
      );
    }
    return MarketDataException(
      '$host ответил $status',
      kind: NetFailureKind.badRequest,
    );
  }

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
      // Подменённый сертификат отличается от «сеть не пустила» настолько,
      // что путать их вредно: первое чинится в настройках сети владельца,
      // второе — ожиданием. И повторять первое бессмысленно.
      final text = error.toString().toLowerCase();
      final intercepted = text.contains('certificate_verify_failed') ||
          text.contains('self signed') ||
          text.contains('self-signed');
      return MarketDataException(
        intercepted
            ? 'Соединение с $host перехвачено: в цепочке чужой сертификат'
            : 'Защищённое соединение с $host не установилось',
        kind: intercepted
            ? NetFailureKind.tlsIntercepted
            : NetFailureKind.connection,
      );
    }

    return MarketDataException('$host не ответил вовремя', kind: NetFailureKind.timeout);
  }
}
