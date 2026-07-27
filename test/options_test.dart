import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/options/black76.dart';
import 'package:signalai/domain/options/execution_ticket.dart';
import 'package:signalai/domain/options/structure_builder.dart';
import 'package:signalai/domain/options/structures.dart';

final now = DateTime.utc(2026, 8, 1);
final expiry = DateTime.utc(2026, 9, 1); // 31 день

OptionContract contract({
  required double strike,
  required OptionKind kind,
  double? price,
  int openInterest = 5000,
  int trades = 200,
  DateTime? expiration,
}) =>
    OptionContract(
      secId: 'Si${kind.code}${strike.toInt()}',
      assetCode: 'SI',
      strike: strike,
      kind: kind,
      expiration: expiration ?? expiry,
      last: price,
      openInterest: openInterest,
      trades: trades,
      priceStep: 1,
    );

void main() {
  group('Блэк-76', () {
    // Эталон: F = 100, K = 100, T = 1, σ = 20%, r = 0.
    // Цена «при деньгах» = F·(2N(σ√T/2) − 1) = 100·(2·N(0,1) − 1) ≈ 7,9656.
    test('цена опциона при деньгах совпадает с эталоном', () {
      final call = Black76.price(
        futures: 100,
        strike: 100,
        years: 1,
        volatility: 0.2,
        kind: OptionKind.call,
      );
      expect(call, closeTo(7.9656, 0.002));
    });

    test('пут-колл паритет соблюдается', () {
      // При нулевой ставке колл − пут = F − K. Нарушение паритета означало бы
      // ошибку в формуле, которую на глаз не увидишь.
      const f = 95000.0;
      const k = 92000.0;
      final call = Black76.price(
        futures: f,
        strike: k,
        years: 0.25,
        volatility: 0.3,
        kind: OptionKind.call,
      );
      final put = Black76.price(
        futures: f,
        strike: k,
        years: 0.25,
        volatility: 0.3,
        kind: OptionKind.put,
      );
      expect(call - put, closeTo(f - k, 0.5));
    });

    test('на экспирации остаётся только внутренняя стоимость', () {
      expect(
        Black76.price(
            futures: 105, strike: 100, years: 0, volatility: 0.3, kind: OptionKind.call),
        closeTo(5, 1e-9),
      );
      expect(
        Black76.price(
            futures: 95, strike: 100, years: 0, volatility: 0.3, kind: OptionKind.call),
        closeTo(0, 1e-9),
      );
    });

    test('цена растёт с волатильностью и временем', () {
      double at(double vol, double years) => Black76.price(
            futures: 100,
            strike: 100,
            years: years,
            volatility: vol,
            kind: OptionKind.call,
          );
      expect(at(0.4, 1), greaterThan(at(0.2, 1)));
      expect(at(0.2, 1), greaterThan(at(0.2, 0.25)));
    });

    test('дельта колла в диапазоне 0…1, пута — −1…0', () {
      for (final strike in [80.0, 100.0, 120.0]) {
        final call = Black76.greeks(
          futures: 100,
          strike: strike,
          years: 0.5,
          volatility: 0.3,
          kind: OptionKind.call,
        );
        final put = Black76.greeks(
          futures: 100,
          strike: strike,
          years: 0.5,
          volatility: 0.3,
          kind: OptionKind.put,
        );
        expect(call.delta, inInclusiveRange(0, 1));
        expect(put.delta, inInclusiveRange(-1, 0));
        // Гамма и вега одинаковы у колла и пута одного страйка.
        expect(call.gamma, closeTo(put.gamma, 1e-9));
        expect(call.vega, closeTo(put.vega, 1e-9));
      }
    });

    test('дельта при деньгах около половины', () {
      final greeks = Black76.greeks(
        futures: 100,
        strike: 100,
        years: 0.25,
        volatility: 0.3,
        kind: OptionKind.call,
      );
      expect(greeks.delta, closeTo(0.5, 0.03));
    });

    test('тета покупателя отрицательна: время против него', () {
      final greeks = Black76.greeks(
        futures: 100,
        strike: 100,
        years: 0.25,
        volatility: 0.3,
        kind: OptionKind.call,
      );
      expect(greeks.theta, lessThan(0));
    });

    test('волатильность восстанавливается из цены', () {
      for (final vol in [0.15, 0.3, 0.55, 0.9]) {
        for (final strike in [90000.0, 100000.0, 110000.0]) {
          final price = Black76.price(
            futures: 100000,
            strike: strike,
            years: 0.2,
            volatility: vol,
            kind: OptionKind.call,
          );
          final implied = Black76.impliedVolatility(
            marketPrice: price,
            futures: 100000,
            strike: strike,
            years: 0.2,
            kind: OptionKind.call,
          );
          expect(implied, isNotNull);
          expect(implied!, closeTo(vol, 1e-3),
              reason: 'страйк $strike, волатильность $vol');
        }
      }
    });

    test('цена ниже внутренней стоимости волатильности не даёт', () {
      // Это не сбой расчёта, а неверная цена: придумывать по ней число нельзя.
      final implied = Black76.impliedVolatility(
        marketPrice: 1,
        futures: 100,
        strike: 80,
        years: 0.5,
        kind: OptionKind.call,
      );
      expect(implied, isNull);
    });
  });

  group('Конструкции', () {
    OptionStructure bullSpread() => OptionStructure(
          kind: StructureKind.bullCallSpread,
          futuresPrice: 100000,
          at: now,
          legs: [
            OptionLeg(
              contract: contract(strike: 100000, kind: OptionKind.call),
              side: LegSide.buy,
              quantity: 1,
              price: 3000,
            ),
            OptionLeg(
              contract: contract(strike: 105000, kind: OptionKind.call),
              side: LegSide.sell,
              quantity: 1,
              price: 1200,
            ),
          ],
        );

    test('колл-спред: дебет, ограниченные прибыль и убыток', () {
      final spread = bullSpread();
      expect(spread.isDebit, isTrue);
      expect(spread.netCashFlow, closeTo(-1800, 1e-9));
      // Максимальный убыток — уплаченный дебет.
      expect(spread.maxLoss, closeTo(-1800, 1));
      // Максимальная прибыль — ширина минус дебет.
      expect(spread.maxProfit, closeTo(5000 - 1800, 1));
    });

    test('колл-спред: выплата в характерных точках', () {
      final spread = bullSpread();
      expect(spread.payoffAt(95000), closeTo(-1800, 1), reason: 'ниже нижнего страйка');
      expect(spread.payoffAt(102000), closeTo(200, 1), reason: 'между страйками');
      expect(spread.payoffAt(110000), closeTo(3200, 1), reason: 'выше верхнего');
    });

    test('точка безубытка — страйк плюс дебет', () {
      final spread = bullSpread();
      expect(spread.breakEvens.single, closeTo(101800, 50));
    });

    test('железный кондор: премия получена, риск ограничен крыльями', () {
      final condor = OptionStructure(
        kind: StructureKind.ironCondor,
        futuresPrice: 100000,
        at: now,
        legs: [
          OptionLeg(
            contract: contract(strike: 90000, kind: OptionKind.put),
            side: LegSide.buy,
            quantity: 1,
            price: 300,
          ),
          OptionLeg(
            contract: contract(strike: 95000, kind: OptionKind.put),
            side: LegSide.sell,
            quantity: 1,
            price: 900,
          ),
          OptionLeg(
            contract: contract(strike: 105000, kind: OptionKind.call),
            side: LegSide.sell,
            quantity: 1,
            price: 1000,
          ),
          OptionLeg(
            contract: contract(strike: 110000, kind: OptionKind.call),
            side: LegSide.buy,
            quantity: 1,
            price: 350,
          ),
        ],
      );

      expect(condor.isDebit, isFalse, reason: 'кондор получает премию');
      expect(condor.netCashFlow, closeTo(1250, 1e-9));
      expect(condor.maxProfit, closeTo(1250, 1));
      // Убыток ограничен шириной крыла минус премия.
      expect(condor.maxLoss, closeTo(-(5000 - 1250), 1));
      expect(condor.payoffAt(100000), closeTo(1250, 1));
      expect(condor.payoffAt(120000), closeTo(-3750, 1));
    });

    test('покрытый колл считается вместе с базовой позицией', () {
      // Без учёта позиции покрытый колл выглядит бесплатной премией — это
      // самая дорогая ошибка новичка в опционах.
      final covered = OptionStructure(
        kind: StructureKind.coveredCall,
        futuresPrice: 100000,
        at: now,
        underlyingLots: 1,
        legs: [
          OptionLeg(
            contract: contract(strike: 105000, kind: OptionKind.call),
            side: LegSide.sell,
            quantity: 1,
            price: 1200,
          ),
        ],
      );

      expect(covered.payoffAt(100000), closeTo(1200, 1), reason: 'цена на месте');
      expect(covered.payoffAt(110000), closeTo(6200, 1), reason: 'выше страйка — прибыль заперта');
      expect(covered.payoffAt(90000), closeTo(-8800, 1), reason: 'падение не защищено');
    });

    test('греки конструкции складываются по ногам', () {
      final spread = bullSpread();
      final greeks = spread.greeks(0.3);
      // Купленный ближний колл даёт больше дельты, чем проданный дальний.
      expect(greeks.delta, greaterThan(0));
      expect(greeks.delta, lessThan(1));
    });
  });

  group('Сборка из цепочки', () {
    List<OptionContract> chain() => [
          for (final strike in [90000.0, 95000.0, 100000.0, 105000.0, 110000.0]) ...[
            contract(strike: strike, kind: OptionKind.call, price: 1000),
            contract(strike: strike, kind: OptionKind.put, price: 900),
          ],
        ];

    test('без позиции покрытый колл не предлагается', () {
      // Продажа непокрытого колла — неограниченный убыток; такого в выдаче
      // быть не может по построению, а не по настройке.
      final structures = StructureBuilder.build(
        chain: chain(),
        futuresPrice: 100000,
        at: now,
      );
      expect(structures.map((s) => s.kind), isNot(contains(StructureKind.coveredCall)));
      expect(structures.map((s) => s.kind), contains(StructureKind.bullCallSpread));
    });

    test('с позицией появляются покрытый колл и защитный пут', () {
      final structures = StructureBuilder.build(
        chain: chain(),
        futuresPrice: 100000,
        at: now,
        underlyingLots: 2,
      );
      final kinds = structures.map((s) => s.kind).toList();
      expect(kinds, contains(StructureKind.coveredCall));
      expect(kinds, contains(StructureKind.protectivePut));
      expect(kinds, contains(StructureKind.collar));
    });

    test('ни одна выданная конструкция не имеет неограниченного убытка', () {
      final structures = StructureBuilder.build(
        chain: chain(),
        futuresPrice: 100000,
        at: now,
        underlyingLots: 1,
      );
      expect(structures, isNotEmpty);
      for (final structure in structures) {
        expect(structure.maxLoss, isNotNull,
            reason: '${structure.kind.label} с неограниченным убытком');
      }
    });

    test('серия ближе пяти дней не берётся', () {
      // Гамма на последних днях делает результат случайным.
      final soon = [
        for (final strike in [95000.0, 100000.0, 105000.0]) ...[
          contract(
            strike: strike,
            kind: OptionKind.call,
            price: 500,
            expiration: now.add(const Duration(days: 2)),
          ),
          contract(
            strike: strike,
            kind: OptionKind.put,
            price: 500,
            expiration: now.add(const Duration(days: 2)),
          ),
        ],
      ];
      expect(
        StructureBuilder.build(chain: soon, futuresPrice: 100000, at: now),
        isEmpty,
      );
    });

    test('голая продажа колла остаётся неограниченным риском', () {
      // Проверка самого правила, а не только выдачи: без базовой позиции
      // проданный колл теряет вместе с ростом цены, и упереться убытку не во
      // что. Покрытый колл проходит только потому, что фьючерс у владельца
      // уже есть, а ниже нуля цена не уходит.
      final naked = OptionStructure(
        kind: StructureKind.coveredCall,
        futuresPrice: 100000,
        at: now,
        legs: [
          OptionLeg(
            contract: contract(strike: 105000, kind: OptionKind.call),
            side: LegSide.sell,
            quantity: 1,
            price: 1200,
          ),
        ],
      );
      expect(naked.maxLoss, isNull);
      expect(naked.addsUnboundedRisk, isTrue);

      final covered = OptionStructure(
        kind: StructureKind.coveredCall,
        futuresPrice: 100000,
        at: now,
        underlyingLots: 1,
        legs: naked.legs,
      );
      expect(covered.addsUnboundedRisk, isFalse);
      // Худший исход покрытого колла — обнуление фьючерса плюс премия.
      expect(covered.maxLoss, closeTo(-100000 + 1200, 1));
    });

    test('пустая цепочка не даёт конструкций и не падает', () {
      expect(
        StructureBuilder.build(chain: const [], futuresPrice: 100000, at: now),
        isEmpty,
      );
    });
  });

  group('Инструкция к исполнению', () {
    test('покупки идут раньше продаж — голой позиции не возникает', () {
      // Если сначала продать, а вторая нога не исполнится, в позиции остаётся
      // голая продажа. Порядок здесь — не косметика.
      final condor = StructureBuilder.build(
        chain: [
          for (final strike in [
            85000.0,
            90000.0,
            95000.0,
            100000.0,
            105000.0,
            110000.0,
            115000.0
          ]) ...[
            contract(strike: strike, kind: OptionKind.call, price: 800),
            contract(strike: strike, kind: OptionKind.put, price: 700),
          ],
        ],
        futuresPrice: 100000,
        at: now,
      ).firstWhere((s) => s.kind == StructureKind.ironCondor);

      final ticket = ExecutionTicket.of(condor);
      final sides = [for (final step in ticket.steps) step.side];
      final firstSell = sides.indexOf(LegSide.sell);
      final lastBuy = sides.lastIndexOf(LegSide.buy);
      expect(firstSell, greaterThan(lastBuy),
          reason: 'все покупки обязаны стоять раньше любой продажи');
    });

    test('шаги пронумерованы и содержат всё для терминала', () {
      final spread = OptionStructure(
        kind: StructureKind.bullCallSpread,
        futuresPrice: 100000,
        at: now,
        legs: [
          OptionLeg(
            contract: contract(strike: 100000, kind: OptionKind.call),
            side: LegSide.buy,
            quantity: 2,
            price: 3000,
          ),
          OptionLeg(
            contract: contract(strike: 105000, kind: OptionKind.call),
            side: LegSide.sell,
            quantity: 2,
            price: 1200,
          ),
        ],
      );
      final ticket = ExecutionTicket.of(spread);

      expect(ticket.steps.map((s) => s.order), [1, 2]);
      expect(ticket.steps.first.secId, 'SiC100000');
      expect(ticket.steps.first.quantity, 2);
      // Покупаем чуть выше расчётной цены, продаём чуть ниже: заявка должна
      // исполниться, а не висеть.
      expect(ticket.steps.first.limitPrice, greaterThan(3000));
      expect(ticket.steps.last.limitPrice, lessThan(1200));

      final text = ticket.toPlainText();
      expect(text, contains('SiC100000'));
      expect(text, contains('Дебет'));
      expect(text, contains('Сопровождение'));
    });

    test('неликвидная нога попадает в предупреждения', () {
      final spread = OptionStructure(
        kind: StructureKind.bullCallSpread,
        futuresPrice: 100000,
        at: now,
        legs: [
          OptionLeg(
            contract: contract(strike: 100000, kind: OptionKind.call),
            side: LegSide.buy,
            quantity: 1,
            price: 3000,
          ),
          OptionLeg(
            contract: contract(
              strike: 105000,
              kind: OptionKind.call,
              openInterest: 3,
              trades: 0,
            ),
            side: LegSide.sell,
            quantity: 1,
            price: 1200,
          ),
        ],
      );
      final ticket = ExecutionTicket.of(spread);
      expect(ticket.warnings.any((w) => w.contains('ликвидность')), isTrue);
    });

    test('близкая экспирация предупреждает про гамму', () {
      final spread = OptionStructure(
        kind: StructureKind.bullCallSpread,
        futuresPrice: 100000,
        at: now,
        legs: [
          OptionLeg(
            contract: contract(
              strike: 100000,
              kind: OptionKind.call,
              expiration: now.add(const Duration(days: 6)),
            ),
            side: LegSide.buy,
            quantity: 1,
            price: 500,
          ),
          OptionLeg(
            contract: contract(
              strike: 105000,
              kind: OptionKind.call,
              expiration: now.add(const Duration(days: 6)),
            ),
            side: LegSide.sell,
            quantity: 1,
            price: 200,
          ),
        ],
      );
      expect(
        ExecutionTicket.of(spread).warnings.any((w) => w.contains('гамма')),
        isTrue,
      );
    });
  });
}
