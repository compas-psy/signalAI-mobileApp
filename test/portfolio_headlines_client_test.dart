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

  test('headline preserves per-position evidence and model diff', () async {
    final api = _FakeHeadlinesApi(const {
      'horizon_years': 1,
      'portfolios': [
        {
          'profile': 'OPTIMAL',
          'label': 'Сбалансированный',
          'status': 'ready',
          'reason': '',
          'package': {
            'id': 'model-1',
            'profile': 'OPTIMAL',
            'package': 'BALANCED',
            'horizon_years': 1,
            'expected_return_low': '0.08',
            'expected_return_high': '0.14',
            'target_volatility': '0.12',
            'drawdown_limit': '0.18',
            'cvar_95': '0.04',
            'rationale': 'test',
            'generated_at': '2026-08-16T10:00:00Z',
            'valid_until': '2026-08-17T10:00:00Z',
            'positions': [
              {
                'instrument_id': 'EQ:MOEX:AAA',
                'symbol': 'AAA',
                'title': 'AAA',
                'asset_class': 'EQUITY',
                'target_weight': '0.5',
                'role': 'core',
                'thesis': 'test',
                'kill_conditions': 'test',
                'evidence': {
                  'summary': 'mature research supports AAA',
                  'hypothesis_ids': ['hyp-new'],
                },
              },
            ],
            'changes': {
              'added': ['EQ:MOEX:CCC'],
              'removed': ['EQ:MOEX:BBB'],
              'weight_changed': ['EQ:MOEX:AAA'],
            },
          },
        },
      ],
    });

    final result = await PortfolioHeadlinesClient(client: api).fetch(
      horizonYears: 1,
    );
    final headline = result.portfolios.single;

    expect(
      headline.evidenceByInstrument['EQ:MOEX:AAA']?['summary'],
      'mature research supports AAA',
    );
    expect(headline.changes.added, ['EQ:MOEX:CCC']);
    expect(headline.changes.removed, ['EQ:MOEX:BBB']);
    expect(headline.changes.weightChanged, ['EQ:MOEX:AAA']);
  });
}
