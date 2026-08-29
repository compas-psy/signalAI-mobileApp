import 'package:flutter_test/flutter_test.dart';
import 'package:signal_ai/data/api/control_progress.dart';

void main() {
  test('shows exact comparable sample progress and remaining count', () {
    final progress = ComparableSampleProgress(comparable: 17, required: 40);

    expect(progress.adequate, isFalse);
    expect(progress.remaining, 23);
    expect(progress.label, '17 / 40 · осталось 23');
  });

  test('never reports negative remaining once sample is adequate', () {
    final progress = ComparableSampleProgress(comparable: 44, required: 40);

    expect(progress.adequate, isTrue);
    expect(progress.remaining, 0);
    expect(progress.label, '44 / 40 · выборка набрана');
  });
}
