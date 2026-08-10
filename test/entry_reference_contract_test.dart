import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/engine_contract.dart';

void main() {
  test('engine preserves exact server entry_reference', () {
    final idea = EngineContract.idea({
      'id': '00000000-0000-0000-0000-000000000001',
      'instrument_id': 'MOEX:FUT:TEST',
      'symbol': 'TEST',
      'direction': 'LONG',
      'status': 'TRIGGERED',
      'signal_time': '2026-08-10T12:00:00Z',
      'expires_at': '2026-08-15T12:00:00Z',
      'score': 70,
      'plan': {
        'order_intent': 'LIMIT_RETEST',
        'entry_low': '10',
        'entry_high': '12',
        'entry_reference': '10.25',
        'stop': '9',
        'tp1': '13',
        'tp2': '14',
        'tp3': '15',
        'invalidation': 'test',
      },
      'sizing': {
        'quantity': '1',
        'quantity_step': '1',
        'risk_amount': '1000',
        'risk_pct': '0.005',
        'risk_per_unit': '1.25',
        'quote_currency': 'RUB',
      },
    });

    expect(idea.plan, isNotNull);
    expect(idea.plan!.entryLow, 10);
    expect(idea.plan!.entryHigh, 12);
    expect(idea.plan!.entry, 10.25);
    expect(EngineContract.signalFrom(idea).entry, 10.25);
  });
}
