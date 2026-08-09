import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/services.dart';
import 'package:flutter/widgets.dart';

import '../data/api/api_config.dart';
import '../data/api/engine_runtime.dart';
import '../data/local_store.dart';
import '../data/native_bridge.dart';
import '../data/net/resilient_http.dart';

const _channel = MethodChannel('ru.signalai.app/native');

/// Long-lived server → phone notification stream.
///
/// This is intentionally separate from the exact-alarm snapshot monitor.  SSE
/// is the low-latency primary path; the five-minute monitor remains a durable
/// reconciliation fallback if Android or a VPN kills the persistent socket.
@pragma('vm:entry-point')
Future<void> signalaiPushMain() async {
  WidgetsFlutterBinding.ensureInitialized();
  final store = LocalStore();
  const bridge = NativeBridge();

  var backoff = const Duration(seconds: 2);
  while (true) {
    try {
      final credentials = await restoreEngineRuntime(store, bridge);
      if (!credentials.ready) {
        await _report(credentials.issue ?? 'Server push: устройство не привязано');
        await Future<void>.delayed(const Duration(seconds: 30));
        continue;
      }

      final saved = await store.read('server_push');
      var cursor = (saved?['cursor'] as num?)?.toInt() ?? 0;
      Object? lastError;
      var connected = false;

      // Primary runtime URL first, optional compiled backup second.  This is
      // the same route order as ApiClient and lets a VPN/network handoff move
      // to the surviving HTTPS path without changing credentials.
      for (final endpoint in ApiConfig.endpoints) {
        try {
          cursor = await _consume(
            endpoint: endpoint,
            token: credentials.deviceToken,
            cursor: cursor,
            store: store,
            bridge: bridge,
          );
          connected = true;
          lastError = null;
          // A healthy SSE stream normally does not return.  If the server
          // closes it cleanly, reconnect from the same cursor immediately.
          break;
        } on Object catch (error) {
          lastError = error;
        }
      }

      if (connected) {
        backoff = const Duration(seconds: 2);
      } else {
        await _report('Server push: переподключение · ${_short(lastError)}');
        await Future<void>.delayed(backoff);
        backoff = _nextBackoff(backoff);
      }
    } on Object catch (error) {
      await _report('Server push: ${_short(error)}');
      await Future<void>.delayed(backoff);
      backoff = _nextBackoff(backoff);
    }
  }
}

Future<int> _consume({
  required String endpoint,
  required String token,
  required int cursor,
  required LocalStore store,
  required NativeBridge bridge,
}) async {
  final uri = _streamUri(endpoint, cursor);
  final client = resilientHttpClient()
    ..connectionTimeout = const Duration(seconds: 12)
    // SSE heartbeats arrive every ten seconds; this is only a generous
    // network-level guard against a half-open connection.
    ..idleTimeout = const Duration(minutes: 2);

  try {
    final request = await client.getUrl(uri).timeout(const Duration(seconds: 15));
    request.headers
      ..set(HttpHeaders.authorizationHeader, 'Bearer $token')
      ..set(HttpHeaders.acceptHeader, 'text/event-stream')
      ..set(HttpHeaders.cacheControlHeader, 'no-cache');
    final response = await request.close().timeout(const Duration(seconds: 20));
    if (response.statusCode != HttpStatus.ok) {
      final body = await utf8.decoder.bind(response).join();
      throw HttpException(
        'SSE ${response.statusCode}: ${body.length > 160 ? body.substring(0, 160) : body}',
        uri: uri,
      );
    }

    await _report('Server push подключён');
    var eventId = 0;
    final data = StringBuffer();

    await for (final line in response
        .transform(utf8.decoder)
        .transform(const LineSplitter())) {
      if (line.isEmpty) {
        if (data.isNotEmpty) {
          final raw = data.toString();
          data.clear();
          final parsed = jsonDecode(raw);
          if (parsed is Map<String, dynamic>) {
            final id = (parsed['id'] as num?)?.toInt() ?? eventId;
            if (id > cursor) {
              final key = '${parsed['key'] ?? 'server:$id'}';
              await bridge.notify(
                id: NativeBridge.noticeId(key),
                title: '${parsed['title'] ?? 'SignalAI'}',
                body: '${parsed['body'] ?? ''}',
                payload: '${parsed['payload'] ?? ''}',
              );
              cursor = id;
              // Save *after* posting.  If Android kills us between the two,
              // reconnect may replay the row, but the stable notification id
              // replaces the existing OS notification instead of duplicating.
              await store.write('server_push', {'cursor': cursor});
            }
          }
        }
        eventId = 0;
        continue;
      }
      if (line.startsWith(':')) continue;
      if (line.startsWith('id:')) {
        eventId = int.tryParse(line.substring(3).trim()) ?? eventId;
      } else if (line.startsWith('data:')) {
        if (data.isNotEmpty) data.write('\n');
        data.write(line.substring(5).trimLeft());
      }
    }

    throw const HttpException('server push stream closed');
  } finally {
    client.close(force: true);
  }
}

Uri _streamUri(String endpoint, int cursor) {
  final base = Uri.parse(endpoint);
  final prefix = base.path.endsWith('/')
      ? base.path.substring(0, base.path.length - 1)
      : base.path;
  return base.replace(
    path: '$prefix/api/v1/notifications/stream',
    queryParameters: {'after': '$cursor'},
  );
}

Duration _nextBackoff(Duration current) {
  final seconds = current.inSeconds;
  if (seconds < 5) return const Duration(seconds: 5);
  if (seconds < 15) return const Duration(seconds: 15);
  if (seconds < 30) return const Duration(seconds: 30);
  return const Duration(seconds: 60);
}

String _short(Object? error) {
  if (error == null) return 'канал недоступен';
  final text = '$error'.replaceAll(RegExp(r'\s+'), ' ').trim();
  return text.length <= 140 ? text : '${text.substring(0, 137)}…';
}

Future<void> _report(String summary) async {
  try {
    await _channel.invokeMethod<bool>('pushReport', {'summary': summary});
  } on Object {
    // Delivery does not depend on the diagnostic foreground-service subtitle.
  }
}
