import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/engine_contract.dart';
import 'package:signalai/domain/portfolio/package.dart';

/// Разбор ответа портфельного контура.
///
/// Проверяется не «поля переложились», а то, ради чего контракт устроен
/// именно так: доли приезжают строками и не теряют точность, статус едет
/// даже при пустом списке пакетов, а отсутствующее не превращается в ноль.
void main() {
  Map<String, dynamic> answer({
    List<Map<String, dynamic>> packages = const [],
    String reason = '',
  }) =>
      {
        'status': {
          'stages': [
            {'key': 'universe', 'name': 'Вселенная инструментов', 'done': true,
              'detail': '12 бумаг'},
            {'key': 'optimisation', 'name': 'Оптимизация состава', 'done': false,
              'detail': ''},
          ],
          'packages_ready': packages.length,
          'universe': 12,
          'generated_at': '2026-07-30T09:00:00Z',
          'reason': reason,
        },
        'packages': packages,
      };

  Map<String, dynamic> package() => {
        'id': 'b1',
        'profile': 'OPTIMAL',
        'package': 'BALANCED',
        'horizon_years': 1,
        'expected_return_low': '0.075',
        'expected_return_high': '0.182',
        'target_volatility': '0.114',
        'drawdown_limit': '0.093',
        'cvar_95': '0.211',
        'rationale': 'расти с понятным риском',
        'stress': {'месяц': '-5.9%', 'год': '7.8%'},
        'generated_at': '2026-07-30T09:00:00Z',
        'valid_until': '2026-08-06T09:00:00Z',
        'mix': [
          {'asset_class': 'EQUITY', 'label': 'Акции', 'weight': '0.55'},
          {'asset_class': 'OFZ', 'label': 'ОФЗ', 'weight': '0.45'},
        ],
        'positions': [
          {
            'instrument_id': 'MOEX:EQ:SBER',
            'symbol': 'SBER',
            'title': 'Сбербанк',
            'asset_class': 'EQUITY',
            'target_weight': '0.31',
            'role': 'рост капитала',
            'thesis': 'доля 31% — дивиденды за год 9.4%',
            'kill_conditions': 'дивиденды отменены',
            'score': '0.72',
            'expected_return': '0.19',
          },
        ],
      };

  test('доли разбираются из строк без потери точности', () {
    final state = EngineContract.portfolio(answer(packages: [package()]));
    final one = state.packages.single;
    expect(one.size, PackageSize.balanced);
    expect(one.horizonYears, 1);
    expect(one.expectedLow, closeTo(0.075, 1e-12));
    expect(one.expectedHigh, closeTo(0.182, 1e-12));
    expect(one.positions.single.weight, closeTo(0.31, 1e-12));
    expect(one.mix.map((m) => m.label).toList(), ['Акции', 'ОФЗ']);
  });

  test('состав несёт роль, тезис и условие выхода', () {
    final state = EngineContract.portfolio(answer(packages: [package()]));
    final position = state.packages.single.positions.single;
    // Без этих трёх полей состав — это список тикеров, а не рекомендация.
    expect(position.role, isNotEmpty);
    expect(position.thesis, isNotEmpty);
    expect(position.killConditions, isNotEmpty);
  });

  test('пустой ответ объясняет, на каком шаге стоит работа', () {
    final state = EngineContract.portfolio(
      answer(reason: 'работа стоит на шаге «Оптимизация состава»'),
    );
    expect(state.packages, isEmpty);
    expect(state.isAvailable, isTrue);
    expect(state.reason, contains('Оптимизация состава'));
    expect(state.stages.first.detail, '12 бумаг');
    expect(state.stages.last.done, isFalse);
  });

  test('отсутствующий CVaR остаётся отсутствующим, а не нулём', () {
    final raw = package()..remove('cvar_95');
    final state = EngineContract.portfolio(answer(packages: [raw]));
    // Ноль на месте хвостового риска читался бы как «убытков не бывает».
    expect(state.packages.single.cvar95, isNull);
  });

  test('пакет с истёкшим сроком помечает себя протухшим', () {
    final raw = package()
      ..['valid_until'] = '2020-01-01T00:00:00Z'
      ..['generated_at'] = '2019-12-25T00:00:00Z';
    final state = EngineContract.portfolio(answer(packages: [raw]));
    expect(state.packages.single.isStale, isTrue);
  });

  test('горизонт отбирает свои пакеты', () {
    final five = package()
      ..['id'] = 'b5'
      ..['horizon_years'] = 5;
    final state = EngineContract.portfolio(answer(packages: [package(), five]));
    expect(state.horizons, [1, 5]);
    expect(state.forHorizon(5).single.id, 'b5');
  });

  test('недоступный движок отличается от посчитанной пустоты', () {
    const state = PortfolioState.unavailable('соединение оборвалось');
    expect(state.isAvailable, isFalse);
    expect(state.unavailableReason, 'соединение оборвалось');
  });
}
