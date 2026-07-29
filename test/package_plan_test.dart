import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/portfolio/package_plan.dart';

PackagePlan plan(List<PackageTarget> targets) => PackagePlan(
      id: 'test',
      title: 'Тест',
      thesis: '',
      horizonYears: 5,
      invalidation: '',
      targets: targets,
    );

void main() {
  group('Ограничения пакета (ТЗ §7.1)', () {
    test('пакеты по умолчанию не нарушают ни одного ограничения', () {
      for (final p in PackagePlan.defaults()) {
        expect(p.violations(), isEmpty, reason: '${p.title}: ${p.violations()}');
      }
    });

    test('сумма весов ровно сто', () {
      for (final p in PackagePlan.defaults()) {
        expect(p.totalWeight, closeTo(100, 0.01), reason: p.title);
      }
    });

    test('крипта не выше пяти процентов ни в одном пакете', () {
      // Жёсткий потолок ТЗ §7.1. Рискованный профиль соблазняет его поднять —
      // именно поэтому проверка есть, а не подразумевается.
      expect(PackagePlan.cryptoCapPercent, 5.0);
      for (final p in PackagePlan.defaults()) {
        expect(p.cryptoPercent, lessThanOrEqualTo(PackagePlan.cryptoCapPercent),
            reason: p.title);
      }
    });

    test('превышение потолка крипты названо отдельным нарушением', () {
      final broken = plan(const [
        PackageTarget(assetClass: AssetClass.stocks, weightPercent: 90),
        PackageTarget(assetClass: AssetClass.crypto, weightPercent: 10),
      ]);
      expect(broken.violations().join(), contains('крипта'));
      expect(broken.violations().length, 1, reason: 'сумма весов здесь верна');
    });

    test('два нарушения перечисляются оба', () {
      // Чинить их надо по отдельности, и видеть — тоже.
      final broken = plan(const [
        PackageTarget(assetClass: AssetClass.stocks, weightPercent: 87),
        PackageTarget(assetClass: AssetClass.crypto, weightPercent: 10),
      ]);
      expect(broken.violations().length, 2);
    });

    test('дубль класса и нулевой вес не проходят', () {
      final doubled = plan(const [
        PackageTarget(assetClass: AssetClass.stocks, weightPercent: 50),
        PackageTarget(assetClass: AssetClass.stocks, weightPercent: 50),
      ]);
      expect(doubled.violations().join(), contains('дважды'));

      final zero = plan(const [
        PackageTarget(assetClass: AssetClass.stocks, weightPercent: 100),
        PackageTarget(assetClass: AssetClass.gold, weightPercent: 0),
      ]);
      expect(zero.violations().join(), contains('нулевой вес'));
    });
  });

  group('Горизонт и профиль (ТЗ §7.1)', () {
    test('на каждом горизонте по одному пакету на профиль (ТЗ §7)', () {
      // Экран обещает переключатель горизонта и три карточки. Дыра в этой
      // сетке означает пустое место там, где владелец ждёт выбора.
      for (final horizon in PackageHorizon.values) {
        final packages = PackagePlan.forHorizon(horizon);
        expect(packages.length, PackageRiskProfile.values.length,
            reason: horizon.label);
        expect(packages.map((p) => p.riskProfile).toSet(),
            PackageRiskProfile.values.toSet(),
            reason: horizon.label);
      }
    });

    test('идентификаторы пакетов уникальны', () {
      final ids = PackagePlan.defaults().map((p) => p.id).toSet();
      expect(ids.length, PackagePlan.defaults().length);
    });

    test('на годовом горизонте акций меньше, чем на пятилетнем', () {
      // Просадку рынка акций за год пересидеть нельзя. Если это правило
      // сломается при правке весов, тест поймает раньше владельца.
      double equity(PackagePlan p) => p.targets
          .where((t) =>
              t.assetClass == AssetClass.stocks ||
              t.assetClass == AssetClass.dividendStocks)
          .fold(0.0, (sum, t) => sum + t.weightPercent);

      for (final profile in PackageRiskProfile.values) {
        final short = PackagePlan.forHorizon(PackageHorizon.oneYear)
            .firstWhere((p) => p.riskProfile == profile);
        final long = PackagePlan.forHorizon(PackageHorizon.fivePlus)
            .firstWhere((p) => p.riskProfile == profile);
        expect(equity(short), lessThan(equity(long)), reason: profile.label);
      }
    });

    test('коды переживают сериализацию, мусор даёт разумное значение', () {
      for (final p in PackagePlan.defaults()) {
        final back = PackagePlan.fromJson(p.toJson());
        expect(back.horizon, p.horizon, reason: p.title);
        expect(back.riskProfile, p.riskProfile, reason: p.title);
        expect(back.killConditions, p.killConditions, reason: p.title);
      }
      expect(PackageHorizon.parse('нет такого'), PackageHorizon.fivePlus);
      expect(PackageRiskProfile.parse(null), PackageRiskProfile.optimal);
    });

    test('у каждого пакета есть условия пересмотра', () {
      // Пакет без killConditions живёт вечно и превращается в кладбище
      // решений: перечитать его будет некому и незачем.
      for (final p in PackagePlan.defaults()) {
        expect(p.killConditions, isNotEmpty, reason: p.title);
      }
    });
  });
}
