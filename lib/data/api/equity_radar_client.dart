import '../../domain/research/equity_radar.dart';
import 'api_client.dart';

class EquityRadarClient {
  EquityRadarClient({ApiClient? client}) : _api = client ?? ApiClient();

  final ApiClient _api;

  Future<EquityRadarState> load() async {
    final json = await _api.get('/api/v1/investment-radar');
    return EquityRadarState.fromJson(json);
  }
}
