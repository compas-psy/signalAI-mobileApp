import 'package:flutter_test/flutter_test.dart';
import 'package:signal_ai/domain/research/equity_radar.dart';

void main() {
  test('weak companies remain visible in parsed daily radar', () {
    final state = EquityRadarState.fromJson({
      'generated_at': '2026-08-10T00:00:00Z',
      'cadence': 'daily_after_closed_d1',
      'method': 'research 45% · D1 technical 40% · liquidity 15%',
      'cards': [
        {
          'instrument_id': 'MOEX:EQ:JUNK',
          'symbol': 'JUNK',
          'title': 'Weak company',
          'score': 12.0,
          'label': 'сейчас мимо',
          'research_score': 10,
          'technical_score': 15,
          'liquidity_score': 30,
          'warnings': ['фундаментальных данных мало'],
        }
      ],
    });

    expect(state.cards, hasLength(1));
    expect(state.cards.single.symbol, 'JUNK');
    expect(state.cards.single.score, 12);
  });
}
