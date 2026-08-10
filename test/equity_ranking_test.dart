import 'package:flutter_test/flutter_test.dart';
import 'package:signal_ai/domain/research/equity_ranking.dart';

void main() {
  test('daily ranking preserves server order and score components', () {
    final state = EquityRankingState.fromJson({
      'market_day': '2026-08-10',
      'generated_at': '2026-08-10T00:05:00Z',
      'universe_count': 2,
      'scored_count': 2,
      'items': [
        {
          'rank': 1,
          'instrument_id': 'MOEX:EQ:AAA',
          'symbol': 'AAA',
          'title': 'Alpha',
          'score': 81.4,
          'tier': 'стоит смотреть',
          'eligible': true,
          'fundamental_score': 84,
          'technical_score': 75,
          'catalyst_adjustment': 2.1,
          'technical_state': 'восходящий D1',
          'fundamental_facts': ['дивиденды 10%'],
          'technical_facts': ['цена выше MA50'],
          'warnings': [],
          'hypothesis': {
            'title': 'спрос ускоряется',
            'direction': 'positive',
            'state': 'confirmed_hypothesis',
            'evidence_score': 0.8,
            'economic_score': 0.7,
            'priority': 82,
          },
        },
        {
          'rank': 2,
          'instrument_id': 'MOEX:EQ:BBB',
          'symbol': 'BBB',
          'title': 'Beta',
          'score': 31,
          'tier': 'вне отбора',
          'eligible': false,
          'fundamental_score': 40,
          'technical_score': 20,
          'catalyst_adjustment': 0,
          'warnings': ['ликвидность ниже порога'],
        },
      ],
    });

    expect(state.isAvailable, isTrue);
    expect(state.items.map((item) => item.symbol), ['AAA', 'BBB']);
    expect(state.items.first.score, 81.4);
    expect(state.items.first.hypothesis?.positive, isTrue);
    expect(state.items.last.eligible, isFalse);
    expect(state.items.last.warnings.single, contains('ликвидность'));
  });
}
