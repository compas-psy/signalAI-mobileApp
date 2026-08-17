import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/equity_ranking_source.dart';
import 'package:signalai/domain/research/equity_ranking.dart';
import 'package:signalai/ui/screens/investment_signals_screen.dart';

Map<String, dynamic> _item(int rank, {String? state, double? value}) => {
      'rank': rank,
      'rank_change': rank == 1 ? null : (rank.isEven ? 2 : -1),
      'instrument_id': 'MOEX:EQ:T${rank.toString().padLeft(2, '0')}',
      'symbol': 'T${rank.toString().padLeft(2, '0')}',
      'title': 'Company $rank',
      'score': 90 - rank,
      'tier': 'наблюдать',
      'eligible': true,
      'fundamental_score': 72.0,
      'technical_score': 68.0,
      'early_score': value ?? 62.0,
      'early_state': state ?? 'наблюдать',
      'early_eligible': state == 'ранняя подготовка' || state == 'формируется',
      'chase_penalty': state == 'поздно / не догонять' ? 0.88 : 0.05,
      'why_now': ['оборот последних 5 сессий 1.4× к предыдущим'],
      'confirmation': 'закрытие выше 63-дневного максимума',
      'invalidation': 'потеря MA20 и исчезновение оборота',
      'return_5d': rank == 1 ? null : 0.02,
      'return_20d': 0.04,
      'return_3m': 0.10,
      'return_6m': 0.18,
      'breakout_distance': -0.02,
      'turnover_ratio': 1.4,
      'accumulation_score': 0.66,
      'compression_ratio': 0.64,
      'catalyst_adjustment': 0.0,
      'technical_state': 'восходящий D1',
      'price': 120.0 + rank,
      'drawdown_6m': -0.03,
      'volatility_3m': 0.24,
      'fundamental_facts': ['ROE измерен'],
      'technical_facts': ['цена выше MA50'],
      'warnings': state == 'поздно / не догонять' ? ['движение уже растянуто'] : <String>[],
      'hypothesis': null,
    };

EquityRankingState _state() => EquityRankingState.fromJson({
      'market_day': '2026-08-17',
      'generated_at': '2026-08-17T07:00:00Z',
      'data_as_of': '2026-08-14T20:00:00Z',
      'methodology': 'equity_rank_v2_early',
      'universe_count': 12,
      'scored_count': 12,
      'items': [
        _item(1, state: 'ранняя подготовка', value: 84),
        _item(2, state: 'формируется', value: 72),
        for (var rank = 3; rank <= 11; rank++) _item(rank),
        _item(12, state: 'поздно / не догонять', value: 20),
      ],
    });

class _FakeSource extends EquityRankingSource {
  const _FakeSource(this.state);
  final EquityRankingState state;

  @override
  Future<EquityRankingState> load() async => state;
}

Future<void> _pump(WidgetTester tester) async {
  await tester.pumpWidget(
    MaterialApp(
      home: Scaffold(
        body: InvestmentSignalsScreen(source: _FakeSource(_state())),
      ),
    ),
  );
  await tester.pumpAndSettle();
}

Future<void> _scrollTo(WidgetTester tester, Finder finder) async {
  await tester.scrollUntilVisible(
    finder,
    300,
    scrollable: find.byType(Scrollable).first,
  );
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('default Все view exposes the complete server-ranked universe', (tester) async {
    await _pump(tester);

    expect(find.text('T01'), findsOneWidget);
    expect(find.textContaining('Показано 12 из 12'), findsOneWidget);

    // ListView is intentionally lazy: prove the tail is reachable instead of
    // requiring every off-screen row to be instantiated at the same time.
    await _scrollTo(tester, find.text('T12'));
    expect(find.text('T12'), findsOneWidget);
  });

  testWidgets('expanded row shows early evidence, confirmation, invalidation and safe null', (tester) async {
    await _pump(tester);

    await tester.tap(find.text('T01'));
    await tester.pumpAndSettle();

    expect(find.text('ПОЧЕМУ СЕЙЧАС'), findsOneWidget);
    expect(find.text('ПОДТВЕРЖДЕНИЕ'), findsOneWidget);
    expect(find.text('ИНВАЛИДАЦИЯ'), findsOneWidget);
    expect(find.textContaining('оборот последних 5 сессий'), findsOneWidget);
    expect(find.textContaining('5 дней · —'), findsOneWidget);
  });

  testWidgets('presentation filters change visibility and Все restores the universe', (tester) async {
    await _pump(tester);

    await tester.tap(find.text('Ранние'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Показано 2 из 12'), findsOneWidget);
    expect(find.text('T01'), findsOneWidget);
    expect(find.text('T02'), findsOneWidget);
    expect(find.text('T12'), findsNothing);

    await tester.tap(find.text('Поздно'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Показано 1 из 12'), findsOneWidget);
    await _scrollTo(tester, find.text('T12'));
    expect(find.text('T12'), findsOneWidget);
    expect(find.text('T01'), findsNothing);

    // Return to the filter strip before selecting the full universe again.
    await _scrollTo(tester, find.text('Все'));
    await tester.tap(find.text('Все'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Показано 12 из 12'), findsOneWidget);
    await _scrollTo(tester, find.text('T12'));
    expect(find.text('T12'), findsOneWidget);
  });
}
