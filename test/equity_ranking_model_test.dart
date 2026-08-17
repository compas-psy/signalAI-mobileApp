import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/research/equity_ranking.dart';

Map<String, dynamic> _item(int rank, {bool late = false}) => {
      'rank': rank,
      'rank_change': rank == 1 ? null : 1,
      'instrument_id': 'MOEX:EQ:T${rank.toString().padLeft(2, '0')}',
      'symbol': 'T${rank.toString().padLeft(2, '0')}',
      'title': 'Company $rank',
      'score': 90 - rank,
      'tier': rank < 4 ? 'стоит смотреть' : 'наблюдать',
      'eligible': true,
      'fundamental_score': 72.0,
      'technical_score': 68.0,
      'early_score': late ? 22.0 : 81.0,
      'early_state': late ? 'поздно / не догонять' : 'ранняя подготовка',
      'early_eligible': !late,
      'chase_penalty': late ? 0.91 : 0.05,
      'why_now': ['оборот ускоряется'],
      'confirmation': 'закрытие выше максимума',
      'invalidation': 'потеря MA20',
      'return_5d': rank == 1 ? null : 0.021,
      'return_20d': 0.035,
      'return_3m': 0.11,
      'return_6m': 0.18,
      'breakout_distance': -0.018,
      'turnover_ratio': 1.42,
      'accumulation_score': 0.66,
      'compression_ratio': 0.64,
      'catalyst_adjustment': 0.0,
      'technical_state': 'восходящий D1',
      'price': 123.4,
      'drawdown_6m': -0.03,
      'volatility_3m': 0.24,
      'fundamental_facts': ['ROE измерен'],
      'technical_facts': ['цена выше MA50'],
      'warnings': late ? ['движение уже растянуто'] : <String>[],
      'hypothesis': null,
    };

void main() {
  test('parses full-universe early radar fields without replacing nulls', () {
    final state = EquityRankingState.fromJson({
      'market_day': '2026-08-17',
      'generated_at': '2026-08-17T07:00:00Z',
      'data_as_of': '2026-08-14T20:00:00Z',
      'methodology': 'equity_rank_v2_early',
      'universe_count': 12,
      'scored_count': 12,
      'items': [for (var rank = 1; rank <= 12; rank++) _item(rank, late: rank == 12)],
    });

    expect(state.items, hasLength(12));
    expect(state.items.map((item) => item.rank), orderedEquals(List.generate(12, (i) => i + 1)));

    final first = state.items.first;
    expect(first.earlyScore, 81.0);
    expect(first.earlyState, 'ранняя подготовка');
    expect(first.earlyEligible, isTrue);
    expect(first.whyNow, ['оборот ускоряется']);
    expect(first.confirmation, 'закрытие выше максимума');
    expect(first.invalidation, 'потеря MA20');
    expect(first.return5d, isNull);
    expect(first.return20d, 0.035);
    expect(first.breakoutDistance, -0.018);
    expect(first.turnoverRatio, 1.42);
    expect(first.accumulationScore, 0.66);
    expect(first.compressionRatio, 0.64);

    final late = state.items.last;
    expect(late.rankChange, 1);
    expect(late.earlyState, 'поздно / не догонять');
    expect(late.earlyEligible, isFalse);
    expect(late.chasePenalty, 0.91);
  });
}
