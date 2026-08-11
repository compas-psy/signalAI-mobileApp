import '../../domain/idea/idea.dart';
import '../../domain/models/signal.dart';
import 'api_client.dart';

/// What happened to an immutable idea after it appeared.
class IdeaMarketProgress {
  const IdeaMarketProgress({
    required this.status,
    required this.summary,
    required this.asOf,
    required this.tpHitCount,
    required this.late,
    required this.ambiguous,
    required this.entryZoneSeen,
    required this.entryNowAvailable,
    this.currentPrice,
    this.entryWasAvailable,
    this.entryAt,
    this.stopHit = false,
    this.currentBarForming = false,
  });

  final String status;
  final String summary;
  final DateTime asOf;
  final double? currentPrice;
  final bool? entryWasAvailable;
  final DateTime? entryAt;
  final bool entryZoneSeen;
  final bool entryNowAvailable;
  final int tpHitCount;
  final bool stopHit;
  final bool late;
  final bool ambiguous;
  final bool currentBarForming;

  bool get blocksNewEntry => late || stopHit || status == 'MISSED_BEFORE_ENTRY';

  factory IdeaMarketProgress.fromJson(Map<String, dynamic> json) =>
      IdeaMarketProgress(
        status: '${json['status'] ?? ''}',
        summary: '${json['summary'] ?? ''}',
        asOf: DateTime.tryParse('${json['as_of'] ?? ''}') ?? DateTime.now(),
        currentPrice: _number(json['current_price']),
        entryWasAvailable: json['entry_was_available'] is bool
            ? json['entry_was_available'] as bool
            : null,
        entryAt: DateTime.tryParse('${json['entry_at'] ?? ''}'),
        entryZoneSeen: json['entry_zone_seen'] == true,
        entryNowAvailable: json['entry_now_available'] == true,
        tpHitCount: (json['tp_hit_count'] as num?)?.toInt() ?? 0,
        stopHit: json['stop_hit'] == true,
        late: json['late'] == true,
        ambiguous: json['ambiguous'] == true,
        currentBarForming: json['current_bar_forming'] == true,
      );
}

class LiveIdeaData {
  const LiveIdeaData({
    this.chart,
    this.progress,
    this.generatedAt,
    this.liveOverlay = false,
  });

  final SignalChart? chart;
  final IdeaMarketProgress? progress;
  final DateTime? generatedAt;
  final bool liveOverlay;
}

/// Read-only live market overlay for an already-created idea.
///
/// This source never creates or modifies a signal. The server keeps signal
/// calculations on closed bars; this endpoint exists only to answer "where is
/// price now and what happened after the signal?" on the detail screen.
class LiveIdeaSource {
  LiveIdeaSource({ApiClient? client}) : _api = client ?? ApiClient();

  final ApiClient _api;

  Future<LiveIdeaData> load(Idea idea, {required String timeframe}) async {
    if (_api.baseUrl.isEmpty) return const LiveIdeaData();
    SignalChart? chart;
    IdeaMarketProgress? progress;
    DateTime? generatedAt;
    var liveOverlay = false;

    try {
      final json = await _api.get(
        '/api/v1/market/${idea.instrumentId}/display-bars'
        '?timeframe=${_canonical(timeframe)}&limit=200',
      );
      generatedAt = DateTime.tryParse('${json['generated_at'] ?? ''}');
      liveOverlay = json['live_overlay'] == true;
      final raw = json['bars'];
      if (raw is List) {
        final candles = <ChartCandle>[];
        for (final item in raw) {
          if (item is! Map<String, dynamic>) continue;
          final open = _number(item['open']);
          final high = _number(item['high']);
          final low = _number(item['low']);
          final close = _number(item['close']);
          if (open == null || high == null || low == null || close == null) {
            continue;
          }
          candles.add(ChartCandle(
            open,
            high,
            low,
            close,
            DateTime.tryParse('${item['open_time'] ?? ''}')?.toLocal(),
          ));
        }
        if (candles.length >= 2) {
          chart = SignalChart(
            timeframeLabel: '${json['timeframe'] ?? timeframe}',
            candles: candles,
          );
        }
      }
    } on Object {
      // The immutable/audited chart passed by the controller remains visible.
    }

    if (idea.instrumentId.startsWith('MOEX:FUT:')) {
      try {
        progress = IdeaMarketProgress.fromJson(
          await _api.get('/api/v1/ideas/${idea.id}/market-progress'),
        );
      } on Object {
        // Progress is an enhancement. Failure must not hide the idea itself.
      }
    }

    return LiveIdeaData(
      chart: chart,
      progress: progress,
      generatedAt: generatedAt,
      liveOverlay: liveOverlay,
    );
  }

  static String _canonical(String raw) => switch (raw.trim().toLowerCase()) {
        'h1' || '1h' => '1h',
        'd1' || '1d' => '1d',
        '10m' || 'm10' => '10m',
        // MOEX ISS has no direct 4h live candle. The persisted chart remains
        // 4h and the live source degrades to 1h rather than mislabelling H1 as H4.
        'h4' || '4h' => '1h',
        _ => '1h',
      };
}

double? _number(Object? value) => switch (value) {
      num v => v.toDouble(),
      String v => double.tryParse(v.replaceAll(',', '.')),
      _ => null,
    };
