/// Какой из посчитанных составов показать на вкладке.
///
/// Движок считает две независимые оси: профиль риска (консервативный,
/// оптимальный, агрессивный) и размер пакета (простой, сбалансированный,
/// максимальный) — девять сочетаний на каждый горизонт. На экране это одна
/// шкала из трёх вкладок, и раньше каждая вкладка искала ровно одно
/// сочетание — диагональ: консервативный+простой, оптимальный+сбалансированный,
/// агрессивный+максимальный.
///
/// Из-за этого шесть сочетаний из девяти были недостижимы. Движок сообщал
/// «составов 2, прошли проверку 2», а экран не показывал ни одного: оба
/// прошедших лежали вне диагонали. Владелец видел исправный конвейер и
/// пустоту под ним.
///
/// Здесь диагональ остаётся предпочтением, но перестаёт быть условием. Если
/// точного сочетания нет, берётся ближайшее — и об этом говорится прямо.
/// Подменять состав молча нельзя: «сбалансированный» и «простой» — разные
/// обещания по риску, и владелец должен видеть, что смотрит не на то, что
/// выбрал.
library;

import 'package.dart';

/// Выбранный состав и чем он отличается от запрошенного.
class PackagePick {
  const PackagePick({this.package, this.note = ''});

  final EnginePackage? package;

  /// Чем показанное отличается от выбранного. Пусто — совпадает точно.
  final String note;

  bool get isExact => package != null && note.isEmpty;
}

/// Расстояние между размерами пакета по шкале риска.
int _distance(PackageSize a, PackageSize b) =>
    (PackageSize.values.indexOf(a) - PackageSize.values.indexOf(b)).abs();

/// Найти состав для вкладки среди посчитанных на этот горизонт.
///
/// [wantProfile] — код профиля, [wantSize] — размер. Порядок предпочтений:
/// точное сочетание; тот же профиль с другим размером (ближайшим);
/// тот же размер с другим профилем. Профиль уступает последним: он задаёт
/// допустимый риск, и менять его молчаливее, чем размер, нельзя.
PackagePick pickPackage(
  List<EnginePackage> packages, {
  required String wantProfile,
  required PackageSize wantSize,
}) {
  if (packages.isEmpty) return const PackagePick();

  for (final p in packages) {
    if (p.profile == wantProfile && p.size == wantSize) {
      return PackagePick(package: p);
    }
  }

  final sameProfile = [for (final p in packages) if (p.profile == wantProfile) p]
    ..sort((a, b) => _distance(a.size, wantSize).compareTo(
        _distance(b.size, wantSize)));
  if (sameProfile.isNotEmpty) {
    final found = sameProfile.first;
    return PackagePick(
      package: found,
      note: 'Точного варианта «${wantSize.label.toLowerCase()}» нет — показан '
          '«${found.size.label.toLowerCase()}» того же профиля риска.',
    );
  }

  final sameSize = [for (final p in packages) if (p.size == wantSize) p];
  if (sameSize.isNotEmpty) {
    return PackagePick(
      package: sameSize.first,
      note: 'Состава с таким профилем риска нет — показан «'
          '${wantSize.label.toLowerCase()}» другого профиля. Риск может '
          'отличаться от выбранного: смотрите числа в шапке.',
    );
  }

  return const PackagePick();
}
