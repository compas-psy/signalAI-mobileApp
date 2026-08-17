import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/mock/demo_ideas.dart';
import 'package:signalai/domain/idea/idea_funnel.dart';
import 'package:signalai/domain/idea/paper_position.dart';

void main() {
  test('pending и open сделки не смешиваются в одну корзину', () {
    final now = DateTime.utc(2026, 8, 17, 10);
    final trades = [
      const PaperPosition(
        id: 'pending-px',
        symbol: 'PXU6',
        long: false,
        pending: true,
        entry: 11313,
        initialStop: 11561,
        currentStop: 11561,
        tpPrices: [11000, 10800],
        tpsTaken: 0,
        status: PaperPositionStatus.pending,
        fromServer: true,
      ),
      const PaperPosition(
        id: 'open-bt',
        symbol: 'BTQ6',
        long: false,
        pending: false,
        entry: 63923,
        initialStop: 65210,
        currentStop: 65210,
        tpPrices: [62000, 61000],
        tpsTaken: 0,
        status: PaperPositionStatus.open,
        fromServer: true,
      ),
    ];

    final funnel = IdeaFunnelSnapshot.from(
      ideas: DemoIdeas.all(now),
      trades: trades,
      now: now,
    );

    expect(funnel.pending.map((trade) => trade.symbol), ['PXU6']);
    expect(funnel.open.map((trade) => trade.symbol), ['BTQ6']);
    expect(funnel.decisions, isNotEmpty);
    expect(funnel.forming, isNotEmpty);
  });

  test('живая сделка вытесняет новую идею по тому же инструменту', () {
    final now = DateTime.utc(2026, 8, 17, 10);
    final ideas = DemoIdeas.all(now);
    final btc = ideas.firstWhere((idea) => idea.symbolOrId == 'BTCUSDT');
    final funnel = IdeaFunnelSnapshot.from(
      ideas: ideas,
      trades: [
        PaperPosition(
          id: 'btc-paper',
          ideaId: 'older-btc-idea',
          symbol: 'BTCUSDT',
          long: true,
          pending: true,
          entry: 118100,
          initialStop: 115800,
          currentStop: 115800,
          tpPrices: const [121400],
          tpsTaken: 0,
          status: PaperPositionStatus.pending,
          fromServer: true,
        ),
      ],
      now: now,
    );

    expect(funnel.forming.where((idea) => idea.id == btc.id), isEmpty);
    expect(funnel.decisions.where((idea) => idea.id == btc.id), isEmpty);
    expect(funnel.pending.single.symbol, 'BTCUSDT');
  });
}
