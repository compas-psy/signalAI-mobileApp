import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/idea/paper_position.dart';

void main() {
  test('server OPEN after all three targets means trailing runner', () {
    final trade = PaperPosition(
      id: 'trade-v2',
      ideaId: 'idea-v2',
      symbol: 'BTCUSDT',
      long: true,
      pending: false,
      status: PaperPositionStatus.open,
      entry: 100,
      initialStop: 90,
      currentStop: 124,
      tpPrices: const [110, 120, 130],
      tpsTaken: 3,
      fromServer: true,
    );

    expect(trade.runnerActive, isTrue);
  });

  test('legacy closed trade after TP3 is not a runner', () {
    final trade = PaperPosition(
      id: 'trade-legacy',
      symbol: 'BTCUSDT',
      long: true,
      pending: false,
      status: PaperPositionStatus.closed,
      entry: 100,
      initialStop: 90,
      currentStop: 110,
      tpPrices: const [110, 120, 130],
      tpsTaken: 3,
      fromServer: true,
    );

    expect(trade.runnerActive, isFalse);
  });

  test('local fallback never invents server runner state', () {
    final trade = PaperPosition(
      id: 'local',
      symbol: 'BTCUSDT',
      long: true,
      pending: false,
      status: PaperPositionStatus.open,
      entry: 100,
      initialStop: 90,
      currentStop: 120,
      tpPrices: const [110, 120, 130],
      tpsTaken: 3,
      fromServer: false,
    );

    expect(trade.runnerActive, isFalse);
  });
}
