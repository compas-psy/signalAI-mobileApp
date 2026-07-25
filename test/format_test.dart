import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/core/format.dart';

void main() {
  group('fmt', () {
    test('разделяет разряды узким неразрывным пробелом', () {
      expect(fmt(2400000, 0), '2 400 000');
      expect(fmt(87450, 0), '87 450');
    });

    test('дробная часть через запятую', () {
      expect(fmt(82.14, 2), '82,14');
      expect(fmt(342.6, 1), '342,6');
    });

    test('минус — типографский U+2212', () {
      expect(fmt(-18000, 0), '−18 000');
    });

    test('округляет до заданного числа знаков', () {
      expect(fmt(4209.4, 0), '4 209');
    });
  });

  test('riskPercentLabel убирает лишний ноль', () {
    expect(riskPercentLabel(0.75), '0,75%');
    expect(riskPercentLabel(1), '1%');
    expect(riskPercentLabel(2), '2%');
  });

  test('rMultiple печатает знак и запятую', () {
    expect(rMultiple(2.1), '+2,1R');
    expect(rMultiple(-1), '−1,0R');
  });
}
