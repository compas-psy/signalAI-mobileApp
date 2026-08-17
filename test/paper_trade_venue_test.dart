import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/engine_contract.dart';

void main() {
  test('paper trade keeps canonical instrument id after idea leaves current feed', () {
    final trade = EngineContract.paperTrades([
      {
        'id': '834ca7f6-78b9-4735-b8a4-615b938bc40b',
        'idea_id': '0010c1c4-55e0-4cda-9ebd-986bd832e0d0',
        'instrument_id': 'MOEX:FUT:PXU6',
        'symbol': 'PXU6',
        'direction': 'SHORT',
        'status': 'OPEN',
        'entry': '111313',
        'initial_stop': '115610',
        'current_stop': '115610',
        'tp_prices': ['109000', '106000', '103000'],
        'tps_taken': 0,
        'opened_at': '2026-08-17T09:00:00Z',
        'expires_at': '2026-08-22T09:00:00Z',
      }
    ]).single;

    expect(trade.instrumentId, 'MOEX:FUT:PXU6');
    expect(trade.isForts, isTrue);
  });

  test('crypto paper trade is not marked as FORTS', () {
    final trade = EngineContract.paperTrades([
      {
        'id': 'e83c060f-d427-4921-8790-04ad1c1b36a6',
        'idea_id': 'c361af82-f96c-4639-b77b-c19253d4ab68',
        'instrument_id': 'CRYPTO:PERP:DOGEUSDT',
        'symbol': 'DOGEUSDT',
        'direction': 'SHORT',
        'status': 'PENDING',
        'entry': '0.0698',
        'initial_stop': '0.0705',
        'current_stop': '0.0705',
        'tp_prices': ['0.068', '0.067', '0.066'],
        'tps_taken': 0,
        'opened_at': '2026-08-17T12:06:07Z',
        'expires_at': '2026-08-22T12:06:07Z',
      }
    ]).single;

    expect(trade.instrumentId, 'CRYPTO:PERP:DOGEUSDT');
    expect(trade.isForts, isFalse);
  });
}
