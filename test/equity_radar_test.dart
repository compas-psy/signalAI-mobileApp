import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/research/equity_radar.dart';

void main() {
  test('equity radar sorts cards by score descending', () {
    final state = EquityRadarState.fromJson({
      'generated_at': '2026-08-10T00:00:00Z',
      'as_of': '2026-08-09T00:00:00Z',
      'cadence': 'daily_after_closed_d1',
      'method': 'test',
      'cards': [
        {
          'instrument_id': 'MOEX:EQ:LOW',
          'symbol': 'LOW',
          'title': 'Low',
          'score': 22,
          'label': 'сейчас мимо',
          'research_score': 20,
          'technical_score': 30,
          'liquidity_score': 40,
        },
        {
          'instrument_id': 'MOEX:EQ:TOP',
          'symbol': 'TOP',
          'title': 'Top',
          'score': 81,
          'label': 'стоит смотреть',
          'research_score': 85,
          'technical_score': 80,
          'liquidity_score': 70,
        },
      ],
    });

    expect(state.cards.map((e) => e.symbol), ['TOP', 'LOW']);
    expect(state.cards.last.label, 'сейчас мимо');
  });
}
