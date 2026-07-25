import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/mock/demo_repository.dart';
import 'package:signalai/domain/models/settings.dart';
import 'package:signalai/domain/models/signal.dart';
import 'package:signalai/domain/position_sizing.dart';

void main() {
  const risk = RiskProfile(
    deposit: 2400000,
    riskPercent: 0.75,
    dailyLossLimit: '−2% · автостоп',
    maxConcurrentTrades: 'до 3',
    pauseRule: 'пауза до завтра',
  );

  late List<TradingSignal> signals;

  setUp(() async {
    signals = (await DemoRepository().fetchDigest()).signals;
  });

  TradingSignal byId(String id) => signals.firstWhere((s) => s.id == id);

  test('риск на сделку — процент от депозита', () {
    expect(risk.riskRub, 18000);
  });

  test('объём во фьючерсных контрактах округляется вниз', () {
    // 18 000 ₽ риска / 550 ₽ на контракт = 32 контракта.
    expect(PositionSizing.quantity(byId('si'), risk), 32);
    expect(PositionSizing.quantityLabel(byId('si'), risk), '32 конт.');
  });

  test('крипта считается с множителем единицы', () {
    // 18 000 / 1 910 = 9 шагов по 0,01 BTC.
    expect(PositionSizing.quantityLabel(byId('btc'), risk), '0,09 BTC');
  });

  test('объём растёт вместе с процентом риска', () {
    final doubled = risk.copyWith(riskPercent: 1.5);
    expect(PositionSizing.quantity(byId('si'), doubled), 65);
  });

  test('кратность риска по тейкам', () {
    final si = byId('si');
    // TP1 87 450 → 88 200 при риске 550 пунктов = 1,4R.
    expect(PositionSizing.takeProfitR(si, si.takeProfits.first), '+1,4R');
    expect(PositionSizing.takeProfitR(si, si.takeProfits.last), '+3,5R');
  });

  test('шорт считается так же, как лонг', () {
    final br = byId('br');
    expect(br.riskPerUnit, closeTo(0.91, 1e-9));
    expect(PositionSizing.takeProfitR(br, br.takeProfits[1]), '+2,2R');
  });
}
