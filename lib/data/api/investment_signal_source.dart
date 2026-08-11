import '../../domain/research/investment_signal.dart';
import 'api_client.dart';

class InvestmentSignalSource {
  InvestmentSignalSource({ApiClient? client}) : _api = client ?? ApiClient();

  final ApiClient _api;

  Future<InvestmentSignalsState> load() async {
    if (_api.baseUrl.isEmpty) {
      return const InvestmentSignalsState.failure(
        'Адрес движка не задан. Откройте «Настройки → Подключения».',
      );
    }
    try {
      return InvestmentSignalsState.fromJson(
        await _api.get('/api/v1/research/equity-signals?limit=12'),
      );
    } on Object catch (error) {
      return InvestmentSignalsState.failure(
        'Сервер не отдал инвестиционные сигналы: $error',
      );
    }
  }
}
