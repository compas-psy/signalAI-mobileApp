import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/ui/formatters/capital_amount.dart';

void main() {
  test('tiny nonzero broker capital stays visibly nonzero', () {
    expect(formatCapitalAmount(0.00234039), '0.002340');
  });

  test('normal balances keep compact formatting', () {
    expect(formatCapitalAmount(1234.56), '1235');
    expect(formatCapitalAmount(12.345), '12.35');
    expect(formatCapitalAmount(0), '0.00');
  });
}
