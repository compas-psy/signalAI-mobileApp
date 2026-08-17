import 'api_client.dart';

class ServerCapitalAccount {
  const ServerCapitalAccount({
    required this.id,
    required this.title,
    required this.currency,
    required this.equity,
    this.freeMargin,
  });

  final String id;
  final String title;
  final String currency;
  final double equity;
  final double? freeMargin;

  factory ServerCapitalAccount.fromJson(Map<String, dynamic> json) =>
      ServerCapitalAccount(
        id: '${json['external_id'] ?? ''}',
        title: '${json['title'] ?? ''}',
        currency: '${json['currency'] ?? ''}'.toUpperCase(),
        equity: _number(json['equity']),
        freeMargin: json['free_margin'] == null ? null : _number(json['free_margin']),
      );
}

class ServerCapitalSource {
  const ServerCapitalSource({
    required this.id,
    required this.title,
    required this.status,
    required this.accounts,
    required this.equityByCurrency,
    this.syncedAt,
    this.note = '',
  });

  final String id;
  final String title;
  final String status;
  final DateTime? syncedAt;
  final String note;
  final List<ServerCapitalAccount> accounts;
  final Map<String, double> equityByCurrency;

  bool get fresh => status == 'fresh';
  bool get stale => status == 'stale';

  factory ServerCapitalSource.fromJson(Map<String, dynamic> json) {
    final rawAccounts = json['accounts'];
    final rawTotals = json['equity_by_currency'];
    return ServerCapitalSource(
      id: '${json['source'] ?? ''}',
      title: '${json['title'] ?? ''}',
      status: '${json['status'] ?? 'unavailable'}',
      syncedAt: DateTime.tryParse('${json['synced_at'] ?? ''}')?.toLocal(),
      note: '${json['note'] ?? ''}',
      accounts: [
        for (final raw in rawAccounts is List ? rawAccounts : const [])
          if (raw is Map<String, dynamic>) ServerCapitalAccount.fromJson(raw),
      ],
      equityByCurrency: {
        if (rawTotals is Map)
          for (final entry in rawTotals.entries)
            '${entry.key}'.toUpperCase(): _number(entry.value),
      },
    );
  }
}

class ServerCapitalSnapshot {
  const ServerCapitalSnapshot({
    required this.generatedAt,
    required this.incomplete,
    required this.sources,
  });

  final DateTime generatedAt;
  final bool incomplete;
  final List<ServerCapitalSource> sources;

  factory ServerCapitalSnapshot.fromJson(Map<String, dynamic> json) {
    final raw = json['sources'];
    return ServerCapitalSnapshot(
      generatedAt: DateTime.tryParse('${json['generated_at'] ?? ''}')?.toLocal() ??
          DateTime.fromMillisecondsSinceEpoch(0),
      incomplete: json['incomplete'] == true,
      sources: [
        for (final item in raw is List ? raw : const [])
          if (item is Map<String, dynamic>) ServerCapitalSource.fromJson(item),
      ],
    );
  }
}

class ServerCapitalClient {
  ServerCapitalClient({ApiClient? api}) : _api = api ?? ApiClient();

  final ApiClient _api;

  Future<ServerCapitalSnapshot> load() async =>
      ServerCapitalSnapshot.fromJson(await _api.get('/api/v1/capital'));

  void close() => _api.close();
}

double _number(Object? raw) => switch (raw) {
      num value => value.toDouble(),
      String value => double.tryParse(value) ?? 0,
      _ => 0,
    };
