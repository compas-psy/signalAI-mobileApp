import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/api_config.dart';
import 'package:signalai/data/api/integrations_client.dart';

void main() {
  tearDown(() {
    ApiConfig.setBaseUrl('');
    ApiConfig.setBackupUrl('');
  });

  test('integration status never needs secret values to render', () {
    final item = ServerIntegration.fromJson({
      'slot': 'bybit_trade',
      'venue': 'BYBIT',
      'title': 'Live · торговля',
      'purpose': 'исполнение',
      'environment': 'live',
      'fields': ['api_key', 'api_secret'],
      'configured': true,
      'updated_at': '2026-08-07T10:00:00Z',
    });

    expect(item.slot, 'bybit_trade');
    expect(item.fields, ['api_key', 'api_secret']);
    expect(item.configured, isTrue);
    expect(item.updatedAt, isNotNull);
  });

  test('primary and backup routes are ordered and deduplicated', () {
    ApiConfig.setBaseUrl('https://api.example.ru');
    ApiConfig.setBackupUrl('https://relay.example.net');
    expect(ApiConfig.endpoints, [
      'https://api.example.ru',
      'https://relay.example.net',
    ]);

    ApiConfig.setBackupUrl('https://api.example.ru');
    expect(ApiConfig.endpoints, ['https://api.example.ru']);
  });
}
