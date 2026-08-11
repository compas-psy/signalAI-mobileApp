import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/research/investment_signal.dart';

void main() {
  test('parses accumulate signal with measured components', () {
    final state = InvestmentSignalsState.fromJson({
      'market_day': '2026-08-11',
      'generated_at': '2026-08-11T06:00:00Z',
      'data_as_of': '2026-08-10T00:00:00Z',
      'universe_count': 40,
      'scored_count': 35,
      'eligible_count': 28,
      'signals': [
        {
          'rank': 1,
          'instrument_id': 'MOEX:EQ:SBER',
          'symbol': 'SBER',
          'title': 'Сбербанк',
          'action': 'ACCUMULATE',
          'action_label': 'НАБИРАТЬ',
          'score': 78.4,
          'price': 320.5,
          'horizon': '6–12 мес.',
          'fundamental_score': 74,
          'technical_score': 69,
          'technical_state': 'выше MA50',
          'catalyst_adjustment': 0,
          'why': ['дивиденды измерены', 'цена выше MA50'],
          'risks': [],
        }
      ],
      'reason': '',
    });

    expect(state.signals, hasLength(1));
    expect(state.signals.single.action, 'ACCUMULATE');
    expect(state.signals.single.isActionable, isTrue);
    expect(state.signals.single.price, 320.5);
    expect(state.universeCount, 40);
  });

  test('preserves explicit empty reason instead of looking broken', () {
    final state = InvestmentSignalsState.fromJson({
      'universe_count': 42,
      'scored_count': 0,
      'eligible_count': 0,
      'signals': [],
      'reason': 'закрытых D1 пока недостаточно',
    });

    expect(state.signals, isEmpty);
    expect(state.reason, contains('D1'));
    expect(state.failed, isFalse);
  });
}
