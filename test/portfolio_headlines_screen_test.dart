import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/api_client.dart';
import 'package:signalai/data/api/portfolio_headlines_client.dart';
import 'package:signalai/ui/screens/portfolio_headlines_screen.dart';

class _FakeApi extends ApiClient {
  _FakeApi(this.responses)
      : super(baseUrl: 'https://engine.test', deviceToken: '');

  final Map<int, Map<String, dynamic>> responses;
  final List<String> paths = [];

  @override
  Future<Map<String, dynamic>> get(String path) async {
    paths.add(path);
    final years = int.parse(path.split('=').last);
    return responses[years]!;
  }
}

Widget _host(Widget child) => Directionality(
      textDirection: TextDirection.ltr,
      child: SizedBox(width: 420, height: 900, child: child),
    );

Map<String, dynamic> _missing(int years) => {
      'horizon_years': years,
      'portfolios': const [
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
    };

void main() {
  testWidgets('shows exactly three owner-facing strategies and explicit states',
      (tester) async {
    final api = _FakeApi({1: _missing(1)});
    final client = PortfolioHeadlinesClient(client: api);

    await tester.pumpWidget(_host(
      PortfolioHeadlinesScreen(horizonYears: 1, client: client),
    ));
    await tester.pumpAndSettle();

    expect(find.text('Консервативный'), findsOneWidget);
    expect(find.text('Сбалансированный'), findsOneWidget);
    expect(find.text('Доходный'), findsOneWidget);
    expect(find.text('Риск выше профиля'), findsOneWidget);
    expect(find.text('Нет состава'), findsOneWidget);
    expect(find.text('Нет актуального состава'), findsOneWidget);
  });

  testWidgets('changing horizon makes a strict new headline request',
      (tester) async {
    final api = _FakeApi({1: _missing(1), 5: _missing(5)});
    final client = PortfolioHeadlinesClient(client: api);

    await tester.pumpWidget(_host(
      PortfolioHeadlinesScreen(horizonYears: 1, client: client),
    ));
    await tester.pumpAndSettle();
    await tester.pumpWidget(_host(
      PortfolioHeadlinesScreen(horizonYears: 5, client: client),
    ));
    await tester.pumpAndSettle();

    expect(api.paths, [
      '/api/v1/portfolio/headlines?horizon_years=1',
      '/api/v1/portfolio/headlines?horizon_years=5',
    ]);
    expect(find.text('Три стратегии · горизонт 5+ лет'), findsOneWidget);
  });

  testWidgets('ready card exposes return risk composition evidence and changes',
      (tester) async {
    final api = _FakeApi({
      1: {
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
              'stress': {'год': '-0.18'},
              'mix': [
                {'asset_class': 'EQUITY', 'label': 'Акции', 'weight': '1.0'},
              ],
              'positions': [
                {
                  'instrument_id': 'EQ:MOEX:AAA',
                  'symbol': 'AAA',
                  'title': 'AAA',
                  'asset_class': 'EQUITY',
                  'target_weight': '1.0',
                  'role': 'core',
                  'thesis': 'test thesis',
                  'kill_conditions': 'test kill',
                  'evidence': {'summary': 'mature research supports AAA'},
                },
              ],
              'changes': {
                'added': ['EQ:MOEX:AAA'],
                'removed': [],
                'weight_changed': [],
              },
              'generated_at': '2026-08-16T10:00:00Z',
              'valid_until': '2026-08-17T10:00:00Z',
            },
          },
        ],
      },
    });

    await tester.pumpWidget(_host(
      PortfolioHeadlinesScreen(
        horizonYears: 1,
        client: PortfolioHeadlinesClient(client: api),
      ),
    ));
    await tester.pumpAndSettle();

    expect(find.text('ОЖИДАЕМАЯ ДОХОДНОСТЬ'), findsOneWidget);
    expect(find.text('Целевая волатильность'), findsOneWidget);
    expect(find.text('СОСТАВ'), findsOneWidget);
    expect(find.text('mature research supports AAA'), findsOneWidget);
    expect(find.text('+1'), findsOneWidget);
    expect(
      find.textContaining('не отправляет инвестиционные заявки автоматически'),
      findsOneWidget,
    );
  });
}
