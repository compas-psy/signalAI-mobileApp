import 'dart:io';

import 'dns_resolver.dart';
import 'secure_connect.dart';

/// `HttpClient`, переживающий сломанный системный DNS.
///
/// Пока системный резолвер работает, путь обычный — ничего не меняется.
/// Как только он отказывает именно на резолве имени, адрес берётся по
/// DNS-over-HTTPS, а соединение устанавливается на этот адрес с TLS по
/// настоящему домену: SNI и проверка сертификата остаются полными
/// (см. [connectSecure]). Ослабления TLS нет нигде.
HttpClient resilientHttpClient({
  SecurityContext? context,
  DnsResolver? resolver,
}) {
  final dns = resolver ?? DnsResolver.shared;
  final client = HttpClient(context: context);

  client.connectionFactory = (Uri uri, String? proxyHost, int? proxyPort) async {
    // Через HTTP-прокси TLS накладывает сам dart:io после CONNECT — отдаём
    // голый сокет и не вмешиваемся.
    if (proxyHost != null && proxyPort != null) {
      return Socket.startConnect(proxyHost, proxyPort);
    }

    final host = uri.host;
    final port = uri.port;
    final isSecure = uri.isScheme('https');

    var cancelled = false;
    ConnectionTask<Socket>? inner;

    Future<Socket> attempt() async {
      if (!dns.systemDnsSuspect) {
        try {
          inner = isSecure
              ? await SecureSocket.startConnect(host, port, context: context)
              : await Socket.startConnect(host, port);
          return await inner!.socket;
        } on SocketException catch (e) {
          // Не отказ резолва — обходить нечего, это честная ошибка сети.
          if (!isHostLookupFailure(e)) rethrow;
          dns.noteSystemDnsFailure();
        }
      }

      final addresses = await dns.resolve(host);
      if (addresses.isEmpty) {
        throw SocketException(
          'Failed host lookup: $host',
          osError: const OSError('обходной резолвер тоже не дал адреса', 7),
        );
      }
      try {
        return await connectSecure(
          addresses: addresses,
          port: port,
          tlsHost: host,
          secure: isSecure,
          context: context,
        );
      } on SocketException {
        // За CDN адрес мог протухнуть — в следующий раз спросим заново.
        dns.invalidate(host);
        rethrow;
      }
    }

    final socket = attempt().then((s) {
      if (cancelled) {
        s.destroy();
        throw const SocketException('соединение отменено');
      }
      return s;
    });

    return ConnectionTask.fromSocket<Socket>(socket, () {
      cancelled = true;
      inner?.cancel();
    });
  };

  return client;
}
