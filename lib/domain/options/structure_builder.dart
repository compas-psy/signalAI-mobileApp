import 'dart:math' as math;

import 'black76.dart';
import 'structures.dart';

/// Сборка конструкций из реальной цепочки биржи.
///
/// Ни одна конструкция не собирается «примерно»: если в цепочке нет нужного
/// страйка с ценой, конструкция не предлагается вовсе. Подставить соседний
/// страйк молча — значит показать риск и прибыль не той сделки, которую
/// человек потом выставит.
abstract final class StructureBuilder {
  /// Все конструкции, которые удалось собрать по цепочке.
  ///
  /// [underlyingLots] — сколько лотов базового актива уже в позиции: от этого
  /// зависит, доступны ли покрытый колл и collar. Продажа непокрытого колла
  /// не предлагается никогда.
  static List<OptionStructure> build({
    required List<OptionContract> chain,
    required double futuresPrice,
    required DateTime at,
    int underlyingLots = 0,
    int width = 2,
  }) {
    if (chain.isEmpty || futuresPrice <= 0) return const [];

    // Работаем с одной серией: смешивать экспирации в вертикальном спреде
    // нельзя — это уже календарный спред с другим риском.
    final expiry = _nearestUsefulExpiry(chain, at);
    if (expiry == null) return const [];
    final series = [
      for (final c in chain)
        if (c.expiration == expiry && c.price != null) c,
    ];
    if (series.length < 2) return const [];

    final calls = _sorted(series, OptionKind.call);
    final puts = _sorted(series, OptionKind.put);
    final result = <OptionStructure>[];

    OptionStructure? spread(
      List<OptionContract> side,
      StructureKind kind,
      bool bullish,
    ) {
      final near = _atTheMoney(side, futuresPrice);
      if (near == null) return null;
      final index = side.indexOf(near);
      final farIndex = bullish ? index + width : index - width;
      if (farIndex < 0 || farIndex >= side.length) return null;
      final far = side[farIndex];
      return OptionStructure(
        kind: kind,
        futuresPrice: futuresPrice,
        at: at,
        legs: [
          OptionLeg(contract: near, side: LegSide.buy, quantity: 1, price: near.price!),
          OptionLeg(contract: far, side: LegSide.sell, quantity: 1, price: far.price!),
        ],
      );
    }

    // Бычий колл-спред: покупаем ближний колл, продаём дальний.
    final bull = spread(calls, StructureKind.bullCallSpread, true);
    if (bull != null) result.add(bull);

    // Медвежий пут-спред: покупаем ближний пут, продаём дальний вниз.
    final bear = spread(puts, StructureKind.bearPutSpread, false);
    if (bear != null) result.add(bear);

    // Железный кондор: продаём ближние крылья, покупаем дальние. Риск
    // ограничен именно покупкой дальних — без них это голая продажа.
    final condor = _ironCondor(calls, puts, futuresPrice, at, width);
    if (condor != null) result.add(condor);

    if (underlyingLots > 0) {
      // Покрытый колл: продажа колла против имеющейся позиции. Без позиции
      // это голая продажа, и её здесь нет по построению.
      final call = _byOffset(calls, futuresPrice, width);
      if (call != null) {
        result.add(OptionStructure(
          kind: StructureKind.coveredCall,
          futuresPrice: futuresPrice,
          at: at,
          underlyingLots: underlyingLots,
          legs: [
            OptionLeg(
              contract: call,
              side: LegSide.sell,
              quantity: underlyingLots,
              price: call.price!,
            ),
          ],
        ));
      }

      // Защитный пут: страховка позиции.
      final put = _byOffset(puts, futuresPrice, -width);
      if (put != null) {
        result.add(OptionStructure(
          kind: StructureKind.protectivePut,
          futuresPrice: futuresPrice,
          at: at,
          underlyingLots: underlyingLots,
          legs: [
            OptionLeg(
              contract: put,
              side: LegSide.buy,
              quantity: underlyingLots,
              price: put.price!,
            ),
          ],
        ));
      }

      // Collar: защитный пут оплачивается проданным коллом.
      if (call != null && put != null) {
        result.add(OptionStructure(
          kind: StructureKind.collar,
          futuresPrice: futuresPrice,
          at: at,
          underlyingLots: underlyingLots,
          legs: [
            OptionLeg(
              contract: put,
              side: LegSide.buy,
              quantity: underlyingLots,
              price: put.price!,
            ),
            OptionLeg(
              contract: call,
              side: LegSide.sell,
              quantity: underlyingLots,
              price: call.price!,
            ),
          ],
        ));
      }
    }

    // Опционы, добавляющие неограниченный риск, не показываются никогда —
    // это правило продукта, а не настройка. Убыток базовой позиции, который у
    // владельца уже есть, конструкции не приписывается.
    return [
      for (final structure in result)
        if (!structure.addsUnboundedRisk) structure,
    ];
  }

  /// Средняя подразумеваемая волатильность серии — для греков.
  ///
  /// Биржа отдаёт IV по каждому страйку; берём медиану ближних к деньгам,
  /// чтобы одиночный выброс на мёртвом страйке не сдвинул всю оценку.
  static double? seriesVolatility(
    List<OptionContract> chain,
    double futuresPrice,
    DateTime at,
  ) {
    final near = [
      for (final c in chain)
        if (c.impliedVolatility != null &&
            c.impliedVolatility! > 0 &&
            (c.strike - futuresPrice).abs() / futuresPrice < 0.15)
          c.impliedVolatility! / 100,
    ]..sort();
    if (near.isNotEmpty) return near[near.length ~/ 2];

    // Биржа не дала IV — считаем сами из цены ближайшего к деньгам опциона.
    final atm = _atTheMoney(chain, futuresPrice);
    if (atm?.price == null) return null;
    return Black76.impliedVolatility(
      marketPrice: atm!.price!,
      futures: futuresPrice,
      strike: atm.strike,
      years: atm.yearsTo(at),
      kind: atm.kind,
    );
  }

  static OptionStructure? _ironCondor(
    List<OptionContract> calls,
    List<OptionContract> puts,
    double futuresPrice,
    DateTime at,
    int width,
  ) {
    final shortCall = _byOffset(calls, futuresPrice, width);
    final shortPut = _byOffset(puts, futuresPrice, -width);
    // Крыло — обязательная часть кондора: без него это голая продажа. Если
    // страйка на нужном расстоянии нет, берём самый дальний доступный: риск
    // шире, но ограничен, а это разница между спредом и катастрофой.
    final longCall = _byOffset(calls, futuresPrice, width * 2) ??
        _outermost(calls, shortCall, up: true);
    final longPut = _byOffset(puts, futuresPrice, -width * 2) ??
        _outermost(puts, shortPut, up: false);
    if (shortCall == null || longCall == null || shortPut == null || longPut == null) {
      return null;
    }
    return OptionStructure(
      kind: StructureKind.ironCondor,
      futuresPrice: futuresPrice,
      at: at,
      legs: [
        OptionLeg(contract: longPut, side: LegSide.buy, quantity: 1, price: longPut.price!),
        OptionLeg(contract: shortPut, side: LegSide.sell, quantity: 1, price: shortPut.price!),
        OptionLeg(contract: shortCall, side: LegSide.sell, quantity: 1, price: shortCall.price!),
        OptionLeg(contract: longCall, side: LegSide.buy, quantity: 1, price: longCall.price!),
      ],
    );
  }

  /// Ближайшая серия, до экспирации которой осталось хотя бы несколько дней.
  ///
  /// Серия, истекающая завтра, — это не «дешёвая премия», а лотерея: гамма
  /// там такая, что позиция меняется быстрее, чем человек успевает решить.
  static DateTime? _nearestUsefulExpiry(List<OptionContract> chain, DateTime at) {
    final dates = <DateTime>{
      for (final c in chain)
        if (c.daysTo(at) >= 5) c.expiration,
    }.toList()
      ..sort();
    return dates.firstOrNull;
  }

  static List<OptionContract> _sorted(List<OptionContract> chain, OptionKind kind) => [
        for (final c in chain)
          if (c.kind == kind) c,
      ]..sort((a, b) => a.strike.compareTo(b.strike));

  static OptionContract? _atTheMoney(List<OptionContract> side, double futuresPrice) {
    if (side.isEmpty) return null;
    var best = side.first;
    for (final contract in side) {
      if ((contract.strike - futuresPrice).abs() <
          (best.strike - futuresPrice).abs()) {
        best = contract;
      }
    }
    return best;
  }

  /// Страйк на [offset] шагов от денег: положительный — вверх, отрицательный
  /// — вниз.
  static OptionContract? _byOffset(
    List<OptionContract> side,
    double futuresPrice,
    int offset,
  ) {
    final atm = _atTheMoney(side, futuresPrice);
    if (atm == null) return null;
    final index = side.indexOf(atm) + offset;
    if (index < 0 || index >= side.length) return null;
    final contract = side[index];
    return contract.price == null ? null : contract;
  }

  /// Самый дальний страйк за [beyond] с известной ценой — крыло кондора,
  /// когда страйка на расчётном расстоянии в цепочке нет.
  static OptionContract? _outermost(
    List<OptionContract> side,
    OptionContract? beyond, {
    required bool up,
  }) {
    if (beyond == null) return null;
    final candidates = [
      for (final c in side)
        if (c.price != null &&
            (up ? c.strike > beyond.strike : c.strike < beyond.strike))
          c,
    ];
    if (candidates.isEmpty) return null;
    return up ? candidates.last : candidates.first;
  }

  /// Ширина спреда в деньгах — для подписи «риск ограничен N пунктами».
  static double spreadWidth(OptionStructure structure) {
    final strikes = [for (final leg in structure.legs) leg.contract.strike];
    if (strikes.length < 2) return 0;
    return strikes.reduce(math.max) - strikes.reduce(math.min);
  }
}
