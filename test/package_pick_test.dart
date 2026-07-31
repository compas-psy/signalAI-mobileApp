/// Какой состав показать на вкладке.
///
/// Движок считает девять сочетаний «профиль × размер» на горизонт, а вкладок
/// на экране три. Пока вкладка искала ровно одно сочетание — диагональ, —
/// шесть из девяти были недостижимы: движок сообщал «прошли проверку 2», а
/// экран не показывал ни одного.
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/portfolio/package.dart';
import 'package:signalai/domain/portfolio/package_pick.dart';

EnginePackage pkg(String profile, PackageSize size) => EnginePackage(
      id: '$profile-${size.code}',
      profile: profile,
      size: size,
      horizonYears: 1,
      expectedLow: 0.05,
      expectedHigh: 0.12,
      volatility: 0.08,
      drawdown: 0.1,
      rationale: '',
      generatedAt: DateTime(2026, 7, 31),
      validUntil: DateTime(2026, 8, 30),
    );

void main() {
  group('Выбор состава для вкладки', () {
    test('точное сочетание берётся без оговорок', () {
      final pick = pickPackage(
        [pkg('OPTIMAL', PackageSize.balanced)],
        wantProfile: 'OPTIMAL',
        wantSize: PackageSize.balanced,
      );
      expect(pick.isExact, isTrue);
      expect(pick.note, isEmpty);
      expect(pick.package!.id, 'OPTIMAL-BALANCED');
    });

    test('состав вне диагонали больше не прячется', () {
      // Ровно то, что видел владелец: составы посчитаны и проверены, а
      // экран пуст, потому что оба лежат вне диагонали.
      final pick = pickPackage(
        [pkg('OPTIMAL', PackageSize.simple)],
        wantProfile: 'OPTIMAL',
        wantSize: PackageSize.balanced,
      );
      expect(pick.package, isNotNull);
      expect(pick.isExact, isFalse);
      expect(pick.note, contains('простой'));
    });

    test('подмена не бывает молчаливой', () {
      // «Сбалансированный» и «максимальный» — разные обещания по риску.
      final pick = pickPackage(
        [pkg('AGGRESSIVE', PackageSize.balanced)],
        wantProfile: 'OPTIMAL',
        wantSize: PackageSize.balanced,
      );
      expect(pick.package, isNotNull);
      expect(pick.note, contains('профилем риска'));
      expect(pick.note, contains('Риск может отличаться'));
    });

    test('свой профиль важнее своего размера', () {
      // Профиль задаёт допустимый риск. Менять его молчаливее, чем размер,
      // нельзя — поэтому он уступает последним.
      final pick = pickPackage(
        [
          pkg('AGGRESSIVE', PackageSize.balanced),
          pkg('OPTIMAL', PackageSize.maxPotential),
        ],
        wantProfile: 'OPTIMAL',
        wantSize: PackageSize.balanced,
      );
      expect(pick.package!.profile, 'OPTIMAL');
    });

    test('из двух чужих размеров берётся ближайший', () {
      final pick = pickPackage(
        [
          pkg('OPTIMAL', PackageSize.maxPotential),
          pkg('OPTIMAL', PackageSize.balanced),
        ],
        wantProfile: 'OPTIMAL',
        wantSize: PackageSize.simple,
      );
      expect(pick.package!.size, PackageSize.balanced);
    });

    test('пусто остаётся пустым', () {
      final pick = pickPackage(
        const [],
        wantProfile: 'OPTIMAL',
        wantSize: PackageSize.balanced,
      );
      expect(pick.package, isNull);
      expect(pick.isExact, isFalse);
    });
  });
}
