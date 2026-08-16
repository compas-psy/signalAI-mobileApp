import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/api_client.dart';
import 'package:signalai/data/api/portfolio_headlines_client.dart';

class _FakeHeadlinesApi extends ApiClient {
  _FakeHeadlinesApi(this.response)
      : super(baseUrl: 'https://engine.test', deviceToken: '');

  final Map<String, dynamic> response;
  final List<String> paths = [];

  @override
  Future<Map<String, dynamic>> get(String path) async {
    paths.add(path);
    return response;
  }
}

void main() {
  test('owner portfolio asks headlines for the exact horizon', () async {
    final api = _FakeHeadlinesApi(const {
      'horizon_years': 5,
      'portfolios': [
        {
          'profile': 'CONSERVATIVE',
          'label': 'Консервативный',
          'status': 'missing',
          'reason': 'Нужен новый расчёт',
          'package': null,
        },
        {
          'profile': 'OPTIMAL',
          'label': 'Сбалансированный',
          'status': 'missing',
          'reason': 'Нужен новый расчёт',
          'package': null,
        },
        {
          'profile': 'AGGRESSIVE',
          'label': 'Доходный',
          'status': 'missing',
          'reason': 'Нужен новый расчёт',
          'package': null,
        },
      ],
    });

    final result = await PortfolioHeadlinesClient(client: api).fetch(
      horizonYears: 5,
    );

    expect(api.paths.single, '/api/v1/portfolio/headlines?horizon_years=5');
    expect(result.horizonYears, 5);
    expect(
      result.portfolios.map((item) => item.label),
      ['Консервативный', 'Сбалансированный', 'Доходный'],
    );
  });

  test('riskier and missing remain explicit owner-facing states', () async {
    final api = _FakeHeadlinesApi(const {
      'horizon_years': 1,
      'portfolios': [
        {
          'profile': 'CONSERVATIVE',
          'label': 'Консервативный',
          'status': 'ready',
          'reason': '',
          'package': null,
        },
        {
          'profile': 'OPTIMAL',
          'label': 'Сбалансированный',
          'status': 'riskier_than_target',
          'reason': 'Риск выше целевого',
          'package': null,
        },
        {
          'profile': 'AGGRESSIVE',
          'label': 'Доходный',
          'status': 'missing',
          'reason': 'Нет актуального состава',
          'package': null,
        },
      ],
    });

    final result = await PortfolioHeadlinesClient(client: api).fetch(
      horizonYears: 1,
    );

    expect(result.portfolios, hasLength(3));
    expect(result.portfolios[0].status.name, 'ready');
    expect(result.portfolios[1].status.name, 'riskierThanTarget');
    expect(result.portfolios[1].reason, 'Риск выше целевого');
    expect(result.portfolios[2].status.name, 'missing');
    expect(result.portfolios[2].reason, 'Нет актуального состава');
  });
}
