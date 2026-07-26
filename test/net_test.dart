import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/market/net_failure.dart';
import 'package:signalai/data/net/dns_resolver.dart';
import 'package:signalai/data/net/secure_connect.dart';

void main() {
  group('parseDnsJson', () {
    test('берёт A-записи и минимальный TTL', () {
      final records = parseDnsJson({
        'Status': 0,
        'Answer': [
          {'name': 'iss.moex.com', 'type': 1, 'TTL': 300, 'data': '178.21.9.1'},
          {'name': 'iss.moex.com', 'type': 1, 'TTL': 120, 'data': '178.21.9.2'},
        ],
      }, wantType: 1);

      expect(records, hasLength(2));
      expect(records.first.address.address, '178.21.9.1');
      expect(records.map((r) => r.ttl).reduce((a, b) => a < b ? a : b), 120);
    });

    test('CNAME и чужие типы пропускаются', () {
      final records = parseDnsJson({
        'Status': 0,
        'Answer': [
          {'type': 5, 'TTL': 300, 'data': 'edge.example.net.'},
          {'type': 1, 'TTL': 300, 'data': '1.2.3.4'},
        ],
      }, wantType: 1);

      expect(records, hasLength(1));
      expect(records.single.address.address, '1.2.3.4');
    });

    test('NXDOMAIN — пусто без исключения: имени просто нет', () {
      expect(parseDnsJson({'Status': 3}, wantType: 1), isEmpty);
    });

    test('прочий ненулевой статус — ошибка', () {
      expect(
        () => parseDnsJson({'Status': 2}, wantType: 1),
        throwsA(isA<DnsException>()),
      );
    });

    test('TTL зажимается: не меньше 30 и не больше часа', () {
      final short = parseDnsJson({
        'Status': 0,
        'Answer': [
          {'type': 1, 'TTL': 1, 'data': '1.2.3.4'},
        ],
      }, wantType: 1);
      final long = parseDnsJson({
        'Status': 0,
        'Answer': [
          {'type': 1, 'TTL': 999999, 'data': '1.2.3.4'},
        ],
      }, wantType: 1);

      expect(short.single.ttl, 30);
      expect(long.single.ttl, 3600);
    });

    test('мусор вместо адреса игнорируется', () {
      final records = parseDnsJson({
        'Status': 0,
        'Answer': [
          {'type': 1, 'TTL': 300, 'data': 'не адрес'},
          {'type': 1, 'TTL': 300, 'data': '9.9.9.9'},
        ],
      }, wantType: 1);

      expect(records.single.address.address, '9.9.9.9');
    });

    test('AAAA разбирается отдельным типом', () {
      final records = parseDnsJson({
        'Status': 0,
        'Answer': [
          {'type': 28, 'TTL': 300, 'data': '2606:4700::1111'},
        ],
      }, wantType: 28);

      expect(records.single.address.type, InternetAddressType.IPv6);
    });
  });

  group('DnsResolver', () {
    test('литеральный IP не резолвится, а возвращается как есть', () async {
      final resolver = DnsResolver(endpoints: const []);
      addTearDown(resolver.close);

      final result = await resolver.resolve('1.2.3.4');
      expect(result.single.address, '1.2.3.4');
    });

    test('без доступных резолверов возвращается пусто, а не исключение', () async {
      final resolver = DnsResolver(endpoints: const []);
      addTearDown(resolver.close);

      expect(await resolver.resolve('iss.moex.com'), isEmpty);
    });

    test('после отказа системного DNS резолвер помечает его подозрительным', () {
      var now = DateTime.utc(2026, 7, 26, 12);
      final resolver = DnsResolver(endpoints: const [], clock: () => now);
      addTearDown(resolver.close);

      expect(resolver.systemDnsSuspect, isFalse);
      resolver.noteSystemDnsFailure();
      expect(resolver.systemDnsSuspect, isTrue);

      // Через две минуты метка перестаёт действовать — пробуем систему снова.
      now = now.add(const Duration(minutes: 3));
      expect(resolver.systemDnsSuspect, isFalse);
    });
  });

  group('isHostLookupFailure', () {
    test('отказ резолва распознаётся по тексту', () {
      expect(
        isHostLookupFailure(const SocketException("Failed host lookup: 'iss.moex.com'")),
        isTrue,
      );
    });

    test('errno 7 — тот самый симптом с устройства', () {
      expect(
        isHostLookupFailure(const SocketException(
          'что угодно',
          osError: OSError('No address associated with hostname', 7),
        )),
        isTrue,
      );
    });

    test('отказ соединения — не проблема имени, обходить нечего', () {
      expect(
        isHostLookupFailure(const SocketException(
          'Connection refused',
          osError: OSError('Connection refused', 111),
        )),
        isFalse,
      );
    });
  });

  group('MarketDataException.from', () {
    test('отказ резолва классифицируется как dns и не ретраится', () {
      final failure = MarketDataException.from(
        const SocketException("Failed host lookup: 'api.bybit.com'"),
        'api.bybit.com',
      );

      expect(failure.kind, NetFailureKind.dns);
      expect(failure.retryable, isFalse);
      expect(failure.isConnectivity, isTrue);
    });

    test('отказ соединения ретраится и относится к связи', () {
      final failure = MarketDataException.from(
        const SocketException(
          'Connection refused',
          osError: OSError('Connection refused', 111),
        ),
        'iss.moex.com',
      );

      expect(failure.kind, NetFailureKind.connection);
      expect(failure.retryable, isTrue);
      expect(failure.isConnectivity, isTrue);
    });

    test('4xx не ретраится, 5xx ретраится', () {
      expect(
        MarketDataException('x', kind: NetFailureKind.badRequest).retryable,
        isFalse,
      );
      expect(
        MarketDataException('x', kind: NetFailureKind.serverError).retryable,
        isTrue,
      );
    });
  });
}
