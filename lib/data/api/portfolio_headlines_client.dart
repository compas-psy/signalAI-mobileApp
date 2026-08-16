import '../../domain/portfolio/headline.dart';
import '../../domain/portfolio/package.dart';
import 'api_client.dart';
import 'engine_contract.dart';

/// Thin read-only client for the owner-facing investment portfolio choices.
///
/// This is intentionally separate from [EngineClient]-style trading flows:
/// the endpoint is advisory and exposes exactly one server-selected package
/// per risk profile at one explicit horizon.
class PortfolioHeadlinesClient {
  PortfolioHeadlinesClient({ApiClient? client}) : _api = client ?? ApiClient();

  final ApiClient _api;

  Future<PortfolioHeadlines> fetch({required int horizonYears}) async {
    if (_api.baseUrl.isEmpty) {
      return PortfolioHeadlines.unavailable(
        'Адрес движка не задан. Портфели считает сервер — задайте адрес в «Настройках» → «Подключения».',
        horizonYears: horizonYears,
      );
    }
    try {
      final json = await _api.get(
        '/api/v1/portfolio/headlines?horizon_years=$horizonYears',
      );
      final rawItems = json['portfolios'];
      final items = <PortfolioHeadline>[];
      if (rawItems is List) {
        for (final raw in rawItems) {
          if (raw is! Map<String, dynamic>) continue;
          final rawPackage = raw['package'];
          items.add(
            PortfolioHeadline(
              profile: '${raw['profile'] ?? ''}',
              label: '${raw['label'] ?? ''}',
              status: PortfolioHeadlineStatus.parse(raw['status']),
              reason: '${raw['reason'] ?? ''}',
              package: _package(rawPackage),
              evidenceByInstrument: _evidence(rawPackage),
              changes: _changes(rawPackage),
            ),
          );
        }
      }
      return PortfolioHeadlines(
        horizonYears: (json['horizon_years'] as num?)?.toInt() ?? horizonYears,
        portfolios: items,
      );
    } catch (error) {
      return PortfolioHeadlines.unavailable(
        _reason(error),
        horizonYears: horizonYears,
      );
    }
  }

  /// Reuse the canonical package parser without duplicating money/ratio rules.
  static EnginePackage? _package(Object? raw) {
    if (raw is! Map<String, dynamic>) return null;
    final parsed = EngineContract.portfolio({
      'packages': [raw],
      'status': const <String, dynamic>{},
    }).packages;
    return parsed.isEmpty ? null : parsed.single;
  }

  static Map<String, Map<String, dynamic>> _evidence(Object? raw) {
    if (raw is! Map<String, dynamic>) return const {};
    final result = <String, Map<String, dynamic>>{};
    for (final position in (raw['positions'] as List? ?? const [])) {
      if (position is! Map<String, dynamic>) continue;
      final instrumentId = '${position['instrument_id'] ?? ''}';
      final evidence = position['evidence'];
      if (instrumentId.isNotEmpty && evidence is Map) {
        result[instrumentId] = {
          for (final entry in evidence.entries) '${entry.key}': entry.value,
        };
      }
    }
    return result;
  }

  static PortfolioModelChanges _changes(Object? raw) {
    if (raw is! Map<String, dynamic>) return const PortfolioModelChanges();
    final changes = raw['changes'];
    if (changes is! Map) return const PortfolioModelChanges();
    List<String> values(String key) => [
          for (final value in (changes[key] as List? ?? const [])) '$value',
        ];
    return PortfolioModelChanges(
      added: values('added'),
      removed: values('removed'),
      weightChanged: values('weight_changed'),
    );
  }

  static String _reason(Object error) {
    final text = error.toString();
    return text.length > 200 ? '${text.substring(0, 200)}…' : text;
  }
}
