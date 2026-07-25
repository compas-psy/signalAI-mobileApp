import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/ui/widgets/svg_path.dart';
import 'package:signalai/ui/widgets/vector_icon.dart';

void main() {
  test('простая ломаная разбирается и попадает в границы', () {
    final path = parseSvgPath('M13 2L4 14h6l-1 8 9-12h-6l1-8z');
    final bounds = path.getBounds();
    expect(bounds.left, 4);
    expect(bounds.top, 2);
    expect(bounds.right, 18);
    expect(bounds.bottom, 22);
  });

  test('относительные дуги с флагами разбираются', () {
    final path = parseSvgPath('M6 9a6 6 0 1 1 12 0');
    expect(path.getBounds().isEmpty, isFalse);
  });

  test('иконка шестерёнки разбирается целиком', () {
    for (final d in Icons.navSettings.paths) {
      final bounds = parseSvgPath(d).getBounds();
      // Вся иконка живёт в системе координат 24×24.
      expect(bounds.left, greaterThanOrEqualTo(-1));
      expect(bounds.right, lessThanOrEqualTo(25));
    }
  });

  test('все иконки макета разбираются без ошибок', () {
    final specs = [
      Icons.bell,
      Icons.chevronLeft,
      Icons.shield,
      Icons.info,
      Icons.check,
      Icons.navIdeas,
      Icons.navTrades,
      Icons.navSettings,
    ];
    for (final spec in specs) {
      for (final d in spec.paths) {
        expect(() => parseSvgPath(d), returnsNormally);
      }
    }
  });

  test('некорректный путь даёт понятную ошибку', () {
    expect(() => parseSvgPath('12 34'), throwsFormatException);
  });
}
