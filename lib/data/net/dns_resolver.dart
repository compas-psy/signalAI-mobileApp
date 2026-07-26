import 'dart:convert';
import 'dart:io';

import 'secure_connect.dart';

/// Резолвер DNS-over-HTTPS с фиксированными адресами.
///
/// [ips] нужны, чтобы обратиться к резолверу, когда системный DNS мёртв:
/// иначе для резолва резолвера снова понадобился бы резолв. [host] — имя для
/// SNI и проверки сертификата (RFC 6066 запрещает класть в SNI IP-литерал,
/// поэтому берём домен, а не адрес).
class DohEndpoint {
  const DohEndpoint({required this.host, required this.path, required this.ips});

  final String host;
  final String path;
  final List<String> ips;
}

/// Резолверы по порядку приоритета. Google первым — его блокируют реже.
const kDohEndpoints = <DohEndpoint>[
  DohEndpoint(host: 'dns.google', path: '/resolve', ips: ['8.8.8.8', '8.8.4.4']),
  DohEndpoint(
    host: 'cloudflare-dns.com',
    path: '/dns-query',
    ips: ['1.1.1.1', '1.0.0.1'],
  ),
];

class DnsException implements Exception {
  DnsException(this.message);

  final String message;

  @override
  String toString() => message;
}

/// Одна запись ответа резолвера.
class DnsRecord {
  const DnsRecord(this.address, this.ttl);

  final InternetAddress address;
  final int ttl;
}

/// Разбор JSON-ответа DoH (формат Google и Cloudflare).
///
/// Вынесен наружу и не трогает сеть — поэтому проверяется на фикстурах.
/// `type`: 1 = A, 28 = AAAA. CNAME (5) пропускаем: конечный адрес приходит
/// отдельной записью. `Status`: 0 = NOERROR, 3 = NXDOMAIN.
List<DnsRecord> parseDnsJson(Map<String, dynamic> json, {required int wantType}) {
  final status = (json['Status'] as num?)?.toInt() ?? -1;
  // NXDOMAIN — имени нет; другие резолверы тоже его не найдут.
  if (status == 3) return const [];
  if (status != 0) throw DnsException('резолвер вернул Status=$status');

  final answers = json['Answer'] as List<dynamic>? ?? const [];
  final records = <DnsRecord>[];
  for (final raw in answers) {
    if (raw is! Map<String, dynamic>) continue;
    if ((raw['type'] as num?)?.toInt() != wantType) continue;
    final data = raw['data'];
    final address = data is String ? InternetAddress.tryParse(data.trim()) : null;
    if (address == null) continue;
    // Слишком короткий TTL превращает резолвер в источник трафика, слишком
    // длинный — в источник протухших адресов за CDN.
    final ttl = (raw['TTL'] as num?)?.toInt() ?? 300;
    records.add(DnsRecord(address, ttl.clamp(30, 3600)));
  }
  return records;
}

class _CacheEntry {
  const _CacheEntry(this.addresses, this.expiresAt);

  final List<InternetAddress> addresses;
  final DateTime expiresAt;
}

/// Резолвер имён, переживающий сломанный системный DNS.
///
/// Порядок принципиален: сначала системный резолвер, и только при отказе
/// именно резолва — DoH. Ходить в DoH всегда нельзя: сломаются локальные и
/// корпоративные имена, а в мобильных сетях — DNS64.
class DnsResolver {
  DnsResolver({
    HttpClient? client,
    this.endpoints = kDohEndpoints,
    DateTime Function()? clock,
    this.timeout = const Duration(seconds: 5),
  })  : _client = client ?? _bootstrapClient(),
        _now = clock ?? DateTime.now;

  static final DnsResolver shared = DnsResolver();

  final HttpClient _client;
  final List<DohEndpoint> endpoints;
  final DateTime Function() _now;
  final Duration timeout;

  final Map<String, _CacheEntry> _cache = {};
  final Map<String, Future<List<InternetAddress>>> _inflight = {};
  DateTime? _systemDnsFailedAt;

  /// Пришлось ли уже обходить системный резолвер — видно в диагностике.
  bool get usedFallback => _usedFallback;
  bool _usedFallback = false;

  /// Системный резолвер недавно отказал: не платим его таймаут на каждом
  /// соединении ближайшие две минуты.
  bool get systemDnsSuspect {
    final at = _systemDnsFailedAt;
    return at != null && _now().difference(at) < const Duration(minutes: 2);
  }

  void noteSystemDnsFailure() => _systemDnsFailedAt = _now();

  /// Клиент для самого резолвера: ходит только на зашитые адреса, поэтому
  /// рекурсии «резолвим резолвер» не возникает по построению.
  static HttpClient _bootstrapClient() {
    final byHost = <String, List<InternetAddress>>{
      for (final e in kDohEndpoints)
        e.host: [for (final ip in e.ips) InternetAddress(ip)],
    };
    final client = HttpClient()
      ..connectionTimeout = const Duration(seconds: 6)
      ..userAgent = 'SignalAI/1.0 (personal)';
    client.connectionFactory = (uri, proxyHost, proxyPort) async {
      if (proxyHost != null && proxyPort != null) {
        return Socket.startConnect(proxyHost, proxyPort);
      }
      final addresses = byHost[uri.host];
      if (addresses == null) {
        throw DnsException('bootstrap: чужой хост ${uri.host}');
      }
      return ConnectionTask.fromSocket<Socket>(
        connectSecure(
          addresses: addresses,
          port: uri.port,
          tlsHost: uri.host,
          secure: true,
        ),
        () {},
      );
    };
    return client;
  }

  /// Адреса хоста: кэш → DoH. Системный резолвер вызывающий код пробует сам.
  Future<List<InternetAddress>> resolve(String host) {
    final literal = InternetAddress.tryParse(host);
    if (literal != null) return Future.value([literal]);

    final hit = _cache[host];
    if (hit != null && hit.expiresAt.isAfter(_now())) {
      return Future.value(hit.addresses);
    }

    // Схлопываем параллельные запросы одного имени в один поход к резолверу.
    return _inflight.putIfAbsent(host, () async {
      try {
        final addresses = await _resolveViaDoh(host);
        if (addresses.isNotEmpty) _usedFallback = true;
        return addresses;
      } finally {
        _inflight.remove(host);
      }
    });
  }

  /// Сбросить адреса хоста: за CDN они меняются, и залипший IP хуже повтора.
  void invalidate(String host) => _cache.remove(host);

  Future<List<InternetAddress>> _resolveViaDoh(String host) async {
    for (final endpoint in endpoints) {
      for (final type in const [1, 28]) {
        try {
          final records = await _query(endpoint, host, type);
          if (records.isEmpty) continue;
          final addresses = [for (final r in records) r.address];
          final ttl = records.map((r) => r.ttl).reduce((a, b) => a < b ? a : b);
          _cache[host] = _CacheEntry(
            addresses,
            _now().add(Duration(seconds: ttl)),
          );
          return addresses;
        } on Object {
          continue; // следующий тип записи, затем следующий резолвер
        }
      }
    }
    return const [];
  }

  Future<List<DnsRecord>> _query(DohEndpoint endpoint, String host, int type) async {
    final uri = Uri.https(endpoint.host, endpoint.path, {
      'name': host,
      'type': type == 1 ? 'A' : 'AAAA',
    });
    final request = await _client.getUrl(uri).timeout(timeout);
    request.headers.set(HttpHeaders.acceptHeader, 'application/dns-json');
    final response = await request.close().timeout(timeout);
    final body = await response.transform(utf8.decoder).join();
    if (response.statusCode != 200) {
      throw DnsException('${endpoint.host} ответил ${response.statusCode}');
    }
    final decoded = jsonDecode(body);
    if (decoded is! Map<String, dynamic>) {
      throw DnsException('${endpoint.host}: ответ не JSON');
    }
    return parseDnsJson(decoded, wantType: type);
  }

  void close() => _client.close(force: true);
}
