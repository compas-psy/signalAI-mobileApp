import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/idea/paper_position.dart';
import 'package:signalai/ui/formatters/paper_position_origin.dart';

PaperPosition serverTrade({String ideaId = 'idea-1'}) => PaperPosition(
      id: 'trade-1',
      ideaId: ideaId,
      instrumentId: 'MOEX:FUT:PXU6',
      symbol: 'PXU6',
      long: false,
      pending: false,
      entry: 111313,
      initialStop: 115610,
      currentStop: 115610,
      tpPrices: const [109000, 106000, 103000],
      tpsTaken: 0,
      fromServer: true,
    );

void main() {
  test('server trade with durable idea link names server origin', () {
    expect(
      paperPositionOrigin(serverTrade(), idea: null, canOpenIdea: true),
      'серверный PAPER · разбор открыт',
    );
  });

  test('server trade outside current feed stays server-owned', () {
    expect(
      paperPositionOrigin(serverTrade(), idea: null, canOpenIdea: false),
      'серверный PAPER · идея вне текущей выдачи',
    );
  });

  test('local trade keeps explicit device origin', () {
    final local = PaperPosition(
      id: 'local-1',
      ideaId: 'local-signal',
      symbol: 'BTCUSDT',
      long: true,
      pending: false,
      entry: 100,
      initialStop: 95,
      currentStop: 95,
      tpPrices: const [110],
      tpsTaken: 0,
    );
    expect(
      paperPositionOrigin(local, idea: null, canOpenIdea: true),
      'из расчёта на устройстве · разбор открыт',
    );
  });
}
