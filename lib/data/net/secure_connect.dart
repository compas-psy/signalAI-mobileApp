import 'dart:io';

/// Соединение с конкретным адресом и TLS по настоящему имени хоста.
///
/// Зачем так. При прямом (не через прокси) `https` `HttpClient` **не**
/// оборачивает сокет из `connectionFactory` в TLS — он берёт его как есть.
/// Значит рукопожатие обязаны сделать мы, иначе в 443-й порт уедет открытый
/// текст. Это же даёт то, ради чего всё затевалось: коннект идёт на IP,
/// полученный в обход сломанного DNS, а SNI и проверка цепочки выполняются
/// по домену ([tlsHost]).
///
/// `onBadCertificate` не передаётся ни при каких условиях: принимать
/// невалидный сертификат в приложении, которое подтверждает сделки, нельзя.
Future<Socket> connectSecure({
  required List<InternetAddress> addresses,
  required int port,
  required String tlsHost,
  required bool secure,
  SecurityContext? context,
  Duration timeout = const Duration(seconds: 8),
}) async {
  Object? lastError;
  StackTrace? lastStack;

  for (final address in addresses) {
    Socket? raw;
    try {
      raw = await Socket.connect(address, port, timeout: timeout);
      if (!secure) return raw;
      // host: — имя для SNI и сверки сертификата. DNS при этом не дёргается.
      return await SecureSocket.secure(raw, host: tlsHost, context: context)
          .timeout(timeout);
    } on Object catch (e, stack) {
      lastError = e;
      lastStack = stack;
      try {
        raw?.destroy();
      } on Object {
        // secure() уже забрал сокет себе — уничтожать нечего.
      }
    }
  }

  Error.throwWithStackTrace(
    lastError ?? SocketException('нет адресов для $tlsHost'),
    lastStack ?? StackTrace.current,
  );
}

/// Отказ именно резолвера, а не соединения.
///
/// Текст формирует dart:io, код ошибки приносит `getaddrinfo`: на Android это
/// 7 (EAI_NODATA) и 8 (EAI_NONAME), на других платформах — свои значения.
/// Различать важно: обходной резолвер имеет смысл только здесь, а на
/// «соединение отвергнуто» он бесполезен.
bool isHostLookupFailure(SocketException e) =>
    e.message.startsWith('Failed host lookup') ||
    const {7, 8, -2, -5, 11001, 11004}.contains(e.osError?.errorCode);
