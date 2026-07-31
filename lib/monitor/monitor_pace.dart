/// Как часто фоновому контуру просыпаться.
///
/// «Постоянное наблюдение» раньше значило буквально: полный прогон раз в час,
/// круглосуточно, независимо от того, есть ли за чем следить и сколько
/// осталось заряда. На телефоне это заметно — а телефон с разряженной батареей
/// не следит вообще ни за чем, то есть режим отменял сам себя.
///
/// Решение здесь принимается по двум фактам и ничему больше: **что** надо
/// сторожить и **чем** за это платим. Часы работы биржи в расчёт не берутся
/// намеренно: расписание меняется, бывают вечерние сессии и праздники, а
/// крипта не закрывается вовсе. Отсутствие новых баров контур видит и без
/// календаря.
library;

/// Состояние питания устройства.
class PowerState {
  const PowerState({
    this.percent = -1,
    this.charging = false,
    this.saver = false,
  });

  /// Заряд в процентах. −1 — неизвестен.
  ///
  /// Именно −1, а не 100: подставить сотню значит решить, что заряд полный,
  /// и разрядить телефон на этом допущении.
  final int percent;

  final bool charging;

  /// Включён системный режим энергосбережения. Это прямое указание владельца
  /// системе экономить, и спорить с ним приложение не вправе.
  final bool saver;

  static PowerState fromMap(Map<Object?, Object?> map) => PowerState(
        percent: (map['percent'] as num?)?.toInt() ?? -1,
        charging: map['charging'] as bool? ?? false,
        saver: map['saver'] as bool? ?? false,
      );

  /// Заряд низкий и не восполняется.
  bool get low => !charging && percent >= 0 && percent <= 20;

  /// Заряда почти нет.
  bool get critical => !charging && percent >= 0 && percent <= 10;
}

/// Что контур решил делать до следующего раза.
class MonitorPace {
  const MonitorPace({
    required this.sleep,
    required this.skipRun,
    required this.reason,
  });

  final Duration sleep;

  /// Прогон целиком пропускается: расход не оправдан тем, что он даст.
  final bool skipRun;

  /// Почему выбран такой шаг — уходит в служебную строку уведомления.
  ///
  /// Без причины «контур ничего не делал три часа» неотличимо от «контур
  /// сломался», и владелец узнаёт об этом в худший момент.
  final String reason;
}

/// Базовый шаг: часовой бар — самый мелкий, на котором работает разбор.
const _base = Duration(hours: 1);

/// Шаг, когда сторожить нечего: ни открытых сделок, ни выставленных заявок,
/// ни живых идей.
const _idle = Duration(hours: 3);

/// Шаг при низком заряде.
const _saving = Duration(hours: 4);

/// Выбрать шаг наблюдения.
///
/// [watching] — сколько объектов на сопровождении: открытые и выставленные
/// сделки плюс идеи, ждущие решения. Ноль значит, что просыпаться незачем:
/// новую идею контур всё равно найдёт следующим прогоном, а сторожить нечего.
MonitorPace paceFor({
  required int watching,
  required PowerState power,
}) {
  // Открытая позиция важнее заряда. Незащищённая сделка стоит дороже, чем
  // проценты батареи, и экономить на её сопровождении нельзя — можно только
  // реже искать новые идеи.
  if (power.critical && watching == 0) {
    return MonitorPace(
      sleep: _saving,
      skipRun: true,
      reason: 'заряд ${power.percent}% — жду; сторожить нечего',
    );
  }
  if (power.critical) {
    return MonitorPace(
      sleep: _saving,
      skipRun: false,
      reason: 'заряд ${power.percent}%: слежу за позициями, новые идеи реже',
    );
  }
  if (power.saver || power.low) {
    final why = power.saver ? 'энергосбережение' : 'заряд ${power.percent}%';
    return MonitorPace(
      sleep: _saving,
      skipRun: false,
      reason: '$why: проверяю раз в ${_saving.inHours} ч',
    );
  }
  if (watching == 0) {
    return const MonitorPace(
      sleep: _idle,
      skipRun: false,
      reason: 'открытых сделок нет — проверяю раз в 3 ч',
    );
  }
  return MonitorPace(
    sleep: _base,
    skipRun: false,
    reason: 'на сопровождении $watching — проверяю каждый час',
  );
}
