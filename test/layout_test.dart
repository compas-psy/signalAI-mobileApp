import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/ui/layout.dart';

void main() {
  group('Разметка по ширине', () {
    test('телефон — одна колонка и нижняя навигация', () {
      final pane = Pane.of(412); // типичный телефон
      expect(pane, Pane.compact);
      expect(pane.usesSideNav, isFalse);
      expect(pane.usesTwoPane, isFalse);
    });

    test('планшет в портрете — боковая навигация, одна колонка', () {
      // MatePad в портрете ≈ 800 dp.
      final pane = Pane.of(800);
      expect(pane, Pane.medium);
      expect(pane.usesSideNav, isTrue);
      expect(pane.usesTwoPane, isFalse,
          reason: 'в портрете две колонки были бы теснотой');
    });

    test('планшет в альбоме — две колонки', () {
      // MatePad в альбоме ≈ 1280 dp.
      final pane = Pane.of(1280);
      expect(pane, Pane.expanded);
      expect(pane.usesSideNav, isTrue);
      expect(pane.usesTwoPane, isTrue);
    });

    test('границы классов совпадают со стандартными 600/900', () {
      expect(Pane.of(599), Pane.compact);
      expect(Pane.of(600), Pane.medium);
      expect(Pane.of(899), Pane.medium);
      expect(Pane.of(900), Pane.expanded);
    });
  });
}
