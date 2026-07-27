import 'dart:math' as math;

import 'structures.dart';

/// Шаг исполнения: одна заявка, которую владелец ставит в терминале брокера.
class TicketStep {
  const TicketStep({
    required this.order,
    required this.secId,
    required this.side,
    required this.quantity,
    required this.limitPrice,
    required this.reason,
  });

  /// Порядковый номер шага.
  final int order;

  /// Полный код контракта — его и вводят в терминале.
  final String secId;

  final LegSide side;
  final int quantity;

  /// Лимитная цена заявки.
  final double limitPrice;

  /// Почему этот шаг именно здесь.
  final String reason;
}

/// Инструкция к исполнению конструкции.
///
/// Приложение не отправляет многоногие заявки: у брокера они исполняются
/// ногами, и частичное исполнение оставляет незакрытый риск. Поэтому оно
/// делает то, что честнее и полезнее, — выдаёт точный порядок действий,
/// который владелец повторяет в терминале.
class ExecutionTicket {
  const ExecutionTicket({
    required this.title,
    required this.steps,
    required this.netCashFlow,
    required this.maxLoss,
    required this.maxProfit,
    required this.breakEvens,
    required this.checks,
    required this.management,
    this.warnings = const [],
  });

  final String title;
  final List<TicketStep> steps;

  /// Итоговый денежный поток: минус — платим, плюс — получаем.
  final double netCashFlow;

  final double? maxLoss;
  final double? maxProfit;
  final List<double> breakEvens;

  /// Что проверить после исполнения.
  final List<String> checks;

  /// План сопровождения: при каком движении что делаем.
  final List<String> management;

  /// Предупреждения: ликвидность, близкая экспирация.
  final List<String> warnings;

  /// Собирает инструкцию по конструкции.
  ///
  /// Порядок ног — главное здесь. Сначала выставляются **покупки**: они
  /// ограничивают риск. Если сначала продать, а вторая нога не исполнится,
  /// в позиции остаётся голая продажа — именно так теряют больше, чем
  /// собирались заработать за год.
  static ExecutionTicket of(
    OptionStructure structure, {
    double slippageSteps = 1,
  }) {
    final buys = [for (final leg in structure.legs) if (leg.side.isBuy) leg];
    final sells = [for (final leg in structure.legs) if (!leg.side.isBuy) leg];

    final steps = <TicketStep>[];
    var order = 1;

    for (final leg in buys) {
      steps.add(TicketStep(
        order: order++,
        secId: leg.contract.secId,
        side: LegSide.buy,
        quantity: leg.quantity,
        // Покупаем чуть выше цены расчёта: заявка должна исполниться, а не
        // висеть — незакрытая нога опаснее лишнего шага цены.
        limitPrice: _round(
          leg.price + leg.contract.priceStep * slippageSteps,
          leg.contract.priceStep,
        ),
        reason: buys.length == structure.legs.length
            ? 'нога конструкции'
            : 'сначала защита: она ограничивает риск',
      ));
    }

    for (final leg in sells) {
      steps.add(TicketStep(
        order: order++,
        secId: leg.contract.secId,
        side: LegSide.sell,
        quantity: leg.quantity,
        limitPrice: _round(
          math.max(0, leg.price - leg.contract.priceStep * slippageSteps),
          leg.contract.priceStep,
        ),
        reason: buys.isEmpty
            ? 'нога конструкции'
            : 'после защиты: голой позиции не возникает ни на секунду',
      ));
    }

    final warnings = <String>[
      for (final secId in structure.illiquidLegs)
        'Низкая ликвидность: $secId — войти можно, выйти сложно',
      if (structure.daysToExpiry <= 10)
        'До экспирации ${structure.daysToExpiry} дн. — гамма растёт, позиция '
            'меняется быстрее, чем вы успеваете решать',
      if (structure.underlyingLots > 0)
        'Конструкция строится против позиции в базовом активе '
            '(${structure.underlyingLots} лот.) — без неё риск другой',
    ];

    return ExecutionTicket(
      title: structure.kind.label,
      steps: steps,
      netCashFlow: structure.netCashFlow,
      maxLoss: structure.maxLoss,
      maxProfit: structure.maxProfit,
      breakEvens: structure.breakEvens,
      checks: [
        'Все ${steps.length} заявок исполнены — частично исполненная '
            'конструкция имеет другой риск, чем задумано',
        'В портфеле ровно ${structure.legs.length} опционных позиций по этой серии',
        if (structure.underlyingLots > 0)
          'Позиция в базовом активе на месте: ${structure.underlyingLots} лот.',
        'Требуемое обеспечение списано и на счёте остался запас',
      ],
      management: _management(structure),
      warnings: warnings,
    );
  }

  /// Текст для терминала: копируется целиком.
  String toPlainText() {
    final buffer = StringBuffer()
      ..writeln(title.toUpperCase())
      ..writeln('');
    for (final step in steps) {
      buffer.writeln('${step.order}. ${step.side.label} ${step.secId} · '
          '${step.quantity} шт. · лимит ${_fmt(step.limitPrice)}');
      buffer.writeln('   ${step.reason}');
    }
    buffer
      ..writeln('')
      ..writeln(netCashFlow < 0
          ? 'Дебет: ${_fmt(netCashFlow.abs())} (платим)'
          : 'Кредит: ${_fmt(netCashFlow)} (получаем)');
    if (maxLoss != null) buffer.writeln('Максимальный убыток: ${_fmt(maxLoss!)}');
    if (maxProfit != null) buffer.writeln('Максимальная прибыль: ${_fmt(maxProfit!)}');
    if (breakEvens.isNotEmpty) {
      buffer.writeln('Безубыток: ${breakEvens.map(_fmt).join(' и ')}');
    }
    if (warnings.isNotEmpty) {
      buffer.writeln('');
      for (final warning in warnings) {
        buffer.writeln('! $warning');
      }
    }
    buffer
      ..writeln('')
      ..writeln('После исполнения проверить:');
    for (final check in checks) {
      buffer.writeln('— $check');
    }
    buffer
      ..writeln('')
      ..writeln('Сопровождение:');
    for (final rule in management) {
      buffer.writeln('— $rule');
    }
    return buffer.toString();
  }

  static List<String> _management(OptionStructure structure) => switch (structure.kind) {
        StructureKind.bullCallSpread || StructureKind.bearPutSpread => const [
            'Прибыль 60–70% от максимума — закрывать, не досиживая до '
                'экспирации: последние проценты стоят недель риска',
            'Базовый актив ушёл против на ширину спреда — замысел сломан, '
                'закрывать',
            'За 5 дней до экспирации закрывать в любом случае: гамма делает '
                'результат случайным',
          ],
        StructureKind.ironCondor => const [
            'Цена подошла к проданному страйку — сокращать или закрывать это '
                'крыло, не дожидаясь пробоя',
            'Прибыль 50% от полученной премии — закрывать: остаток премии не '
                'стоит оставшегося риска',
            'Волатильность выросла заметно — конструкция против нас, выходить',
          ],
        StructureKind.coveredCall => const [
            'Базовый актив выше проданного страйка — позиция уйдёт по нему; '
                'это плановый исход, а не убыток',
            'Хотите оставить позицию — откупать колл и продавать следующий '
                'страйк выше',
            'Падение базового актива конструкция не защищает — стоп по самой '
                'позиции остаётся обязательным',
          ],
        StructureKind.protectivePut || StructureKind.collar => const [
            'Пут — страховка: её стоимость это плата за сон, а не убыток',
            'Базовый актив вырос — поднимать защиту вслед за ним новым путом',
            'До экспирации пута решить: продлевать защиту или выходить из '
                'позиции',
          ],
      };

  static double _round(double value, double step) =>
      step <= 0 ? value : (value / step).roundToDouble() * step;

  static String _fmt(double value) =>
      value.abs() >= 100 ? value.toStringAsFixed(0) : value.toStringAsFixed(2);
}
