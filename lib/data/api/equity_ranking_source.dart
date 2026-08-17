import '../../domain/research/equity_ranking.dart';
import 'api_client.dart';

/// Thin data source for Portfolio → Signals.
///
/// It intentionally shares [ApiConfig] through [ApiClient], so a server
/// address/token changed in Settings is picked up without a second settings
/// store or controller field.
class EquityRankingSource {
  const EquityRankingSource({ApiClient? client}) : _api = client;

  final ApiClient? _api;

  Future<EquityRankingState> load() async {
    final api = _api ?? ApiClient();
    if (api.baseUrl.isEmpty) {
      return const EquityRankingState.unavailable(
        'Адрес движка не задан. Рейтинг компаний считает сервер.',
      );
    }
    try {
      return EquityRankingState.fromJson(
        await api.get('/api/v1/research/equity-ranking'),
      );
    } catch (error) {
      final text = error.toString();
      return EquityRankingState.unavailable(
        text.length > 220 ? '${text.substring(0, 220)}…' : text,
      );
    }
  }
}
