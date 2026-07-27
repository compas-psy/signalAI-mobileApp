import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/core/format.dart';
import 'package:signalai/data/ledger/broker_import.dart';
import 'package:signalai/data/ledger/capital_desk.dart';
import 'package:signalai/data/ledger/ledger_store.dart';
import 'package:signalai/domain/broker/broker.dart';
import 'package:signalai/domain/ledger/ledger_event.dart';
import 'package:signalai/domain/ledger/money.dart';
import 'package:signalai/domain/ledger/projections.dart';

const rub = Currency.rub;

Money rubles(num amount) => Money.of(amount, rub);

var _seq = 0;

LedgerEvent event({
  required LedgerEventType type,
  required num cash,
  String? id,
  String account = 'brk',
  String? instrument,
  num? quantity,
  num? price,
  DateTime? at,
  Money? fee,
  Money? tax,
  Contour? contour,
  ReconcileStatus reconcile = ReconcileStatus.pending,
  Currency currency = rub,
  double? fxRate,
}) =>
    LedgerEvent(
      id: id ?? 'e${(++_seq).toString().padLeft(3, '0')}',
      type: type,
      effectiveAt: at ?? DateTime.utc(2026, 1, 1 + _seq),
      accountId: account,
      cashImpact: Money.of(cash, currency),
      instrument: instrument,
      quantity: quantity == null ? null : Quantity.of(quantity),
      price: price == null ? null : Money.of(price, currency),
      fee: fee,
      tax: tax,
      contour: contour,
      reconcile: reconcile,
      fxRate: fxRate,
    );

void main() {
  setUp(() => _seq = 0);

  group('Деньги в фиксированной точке', () {
    test('сложение не накапливает ошибку двоичной плавающей точки', () {
      // 0,1 + 0,2 в double даёт 0,30000000000000004 — здесь ровно 30 копеек.
      final sum = Money.of(0.1, rub) + Money.of(0.2, rub);
      expect(sum.minor, 30);
      expect(sum, Money.of(0.3, rub));
    });

    test('тысяча копеечных операций даёт ровно десять рублей', () {
      var total = Money.zero(rub);
      for (var i = 0; i < 1000; i++) {
        total += Money.of(0.01, rub);
      }
      expect(total.minor, 1000);
    });

    test('разные валюты не складываются', () {
      expect(
        () => Money.of(100, rub) + Money.of(5, Currency.usd),
        throwsArgumentError,
      );
    });

    test('разбор строки не теряет копейки', () {
      expect(Money.parse('1 234,56', rub).minor, 123456);
      expect(Money.parse('−31 562,08', rub).minor, -3156208);
      expect(Money.parse('0.07', rub).minor, 7);
      // Лишние знаки округляются, а не отбрасываются.
      expect(Money.parse('10.005', rub).minor, 1001);
    });

    test('умножение на количество округляет к ближайшему', () {
      expect(Money.of(121.45, rub).multiplyBy(Quantity.units(3)).minor, 36435);
      expect(Money.of(100, rub).multiplyBy(Quantity.of(0.335)).minor, 3350);
    });

    test('пересчёт валюты сохраняет масштаб целевой валюты', () {
      expect(Money.of(10, Currency.usd).convert(rub, 78.5).minor, 78500);
      expect(Money.of(0.5, Currency.btc).currency.scale, 8);
    });

    test('формат ru-RU: узкий пробел, запятая, типографский минус', () {
      expect(fmtMoney(rubles(8420600)), '8 420 600 ₽');
      expect(fmtMoney(rubles(-31562.08)), '−31 562,08 ₽');
      expect(fmtMoney(Money.of(18.4, Currency.usdt)), '18,40 USDT');
      expect(fmtMoney(rubles(1200), sign: true), '+1 200 ₽');
    });
  });

  group('Лоты и реализованный результат', () {
    test('покупка → частичная продажа → полная продажа', () {
      final events = [
        event(type: LedgerEventType.deposit, cash: 100000),
        event(
          type: LedgerEventType.buy,
          cash: -30000,
          instrument: 'SBER',
          quantity: 100,
          price: 300,
        ),
        event(
          type: LedgerEventType.sell,
          cash: 16000,
          instrument: 'SBER',
          quantity: 50,
          price: 320,
        ),
      ];
      var snap = const LedgerProjector().project(events);
      expect(snap.positions.single.quantity, Quantity.units(50));
      expect(snap.pnl.realized, rubles(1000)); // 50 × (320 − 300)

      events.add(event(
        type: LedgerEventType.sell,
        cash: 15500,
        instrument: 'SBER',
        quantity: 50,
        price: 310,
      ));
      snap = const LedgerProjector().project(events);
      expect(snap.positions, isEmpty, reason: 'позиция закрыта полностью');
      expect(snap.pnl.realized, rubles(1500)); // + 50 × (310 − 300)
    });

    test('несколько лотов по разным ценам закрываются FIFO', () {
      final events = [
        event(type: LedgerEventType.buy, cash: -10000, instrument: 'GAZP', quantity: 100, price: 100),
        event(type: LedgerEventType.buy, cash: -12000, instrument: 'GAZP', quantity: 100, price: 120),
        // Продаём 150: сначала весь первый лот (100 по 100), потом 50 по 120.
        event(type: LedgerEventType.sell, cash: 19500, instrument: 'GAZP', quantity: 150, price: 130),
      ];
      final snap = const LedgerProjector().project(events);
      // 100 × (130 − 100) + 50 × (130 − 120) = 3000 + 500
      expect(snap.pnl.realized, rubles(3500));
      expect(snap.positions.single.quantity, Quantity.units(50));
      expect(snap.positions.single.averagePrice, rubles(120));
    });

    test('шорт: продажа без позиции и закрытие покупкой', () {
      final events = [
        event(type: LedgerEventType.sell, cash: 12000, instrument: 'SiH6', quantity: 10, price: 1200),
      ];
      var snap = const LedgerProjector().project(events);
      expect(snap.positions.single.isLong, isFalse);
      expect(snap.positions.single.quantity, Quantity.units(-10));

      events.add(event(
          type: LedgerEventType.buy, cash: -11500, instrument: 'SiH6', quantity: 10, price: 1150));
      snap = const LedgerProjector().project(events);
      expect(snap.positions, isEmpty);
      expect(snap.pnl.realized, rubles(500)); // 10 × (1200 − 1150)
    });

    test('разворот одной сделкой: закрыть лонг и открыть шорт', () {
      final events = [
        event(type: LedgerEventType.buy, cash: -10000, instrument: 'BR', quantity: 10, price: 1000),
        event(type: LedgerEventType.sell, cash: 16500, instrument: 'BR', quantity: 15, price: 1100),
      ];
      final snap = const LedgerProjector().project(events);
      expect(snap.pnl.realized, rubles(1000)); // 10 × 100
      expect(snap.positions.single.quantity, Quantity.units(-5));
    });

    test('нереализованный результат считается по текущей цене', () {
      final snap = const LedgerProjector().project([
        event(type: LedgerEventType.buy, cash: -30000, instrument: 'LKOH', quantity: 10, price: 3000),
      ]);
      final position = snap.positions.single;
      expect(position.unrealized(rubles(3200)), rubles(2000));
      expect(position.unrealized(rubles(2900)), rubles(-1000));
    });
  });

  group('Денежные потоки и доходы', () {
    test('пополнение и вывод не входят в прибыль', () {
      final snap = const LedgerProjector().project([
        event(type: LedgerEventType.deposit, cash: 500000),
        event(type: LedgerEventType.withdrawal, cash: -100000),
      ]);
      expect(snap.pnl.net, Money.zero(rub), reason: 'потоки — не результат');
      expect(snap.contributed, rubles(500000));
      expect(snap.withdrawn, rubles(100000));
      expect(snap.netContributed, rubles(400000));
      expect(snap.cashInBase(), rubles(400000));
    });

    test('перевод между счетами не меняет совокупный капитал', () {
      final snap = const LedgerProjector().project([
        event(type: LedgerEventType.deposit, cash: 200000, account: 'brk'),
        event(type: LedgerEventType.transfer, cash: -50000, account: 'brk'),
        event(type: LedgerEventType.transfer, cash: 50000, account: 'crypto'),
      ]);
      expect(snap.cashInBase(), rubles(200000));
      expect(snap.pnl.net, Money.zero(rub));
      expect(snap.cash['brk']!['RUB'], rubles(150000));
      expect(snap.cash['crypto']!['RUB'], rubles(50000));
    });

    test('дивиденды, купоны и проценты — доход, комиссии и налог — издержка', () {
      final snap = const LedgerProjector().project([
        event(type: LedgerEventType.dividend, cash: 8000),
        event(type: LedgerEventType.coupon, cash: 3000),
        event(type: LedgerEventType.interest, cash: 500),
        event(type: LedgerEventType.fee, cash: -450),
        event(type: LedgerEventType.tax, cash: -1495),
      ]);
      expect(snap.pnl.income, rubles(11500));
      expect(snap.pnl.fees, rubles(450));
      expect(snap.pnl.taxes, rubles(1495));
      expect(snap.pnl.net, rubles(9555));
    });

    test('фандинг считается в обе стороны', () {
      final snap = const LedgerProjector().project([
        event(type: LedgerEventType.funding, cash: -120),
        event(type: LedgerEventType.funding, cash: 45),
      ]);
      expect(snap.pnl.fees, rubles(120));
      expect(snap.pnl.income, rubles(45));
      expect(snap.pnl.net, rubles(-75));
    });

    test('вариационная маржа идёт в реализованный результат', () {
      final snap = const LedgerProjector().project([
        event(type: LedgerEventType.variationMargin, cash: 2400),
        event(type: LedgerEventType.variationMargin, cash: -900),
      ]);
      expect(snap.pnl.realized, rubles(1500));
    });

    test('комиссия сделки отдельным полем попадает в издержки', () {
      final snap = const LedgerProjector().project([
        event(
          type: LedgerEventType.buy,
          cash: -30045,
          instrument: 'SBER',
          quantity: 100,
          price: 300,
          fee: rubles(45),
        ),
      ]);
      expect(snap.pnl.fees, rubles(45));
      expect(snap.pnl.net, rubles(-45));
    });

    test('валютная операция без курса не искажает базовый итог', () {
      final withRate = const LedgerProjector().project([
        event(
          type: LedgerEventType.dividend,
          cash: 100,
          currency: Currency.usd,
          fxRate: 78.5,
        ),
      ]);
      expect(withRate.pnl.income, rubles(7850));
      expect(withRate.foreignCash()['USD'], Money.of(100, Currency.usd));

      final withoutRate = const LedgerProjector().project([
        event(type: LedgerEventType.dividend, cash: 100, currency: Currency.usd),
      ]);
      expect(withoutRate.pnl.income, Money.zero(rub),
          reason: 'курса нет — выдумывать его нельзя');
      expect(withoutRate.foreignCash()['USD'], Money.of(100, Currency.usd));
    });
  });

  group('Порядок и целостность книги', () {
    test('события применяются по времени операции, а не порядку записи', () {
      final late = LedgerEvent(
        id: 'a-late-import',
        type: LedgerEventType.buy,
        effectiveAt: DateTime.utc(2026, 1, 1),
        accountId: 'brk',
        cashImpact: rubles(-10000),
        instrument: 'SBER',
        quantity: Quantity.units(100),
        price: rubles(100),
      );
      final early = LedgerEvent(
        id: 'b-known',
        type: LedgerEventType.sell,
        effectiveAt: DateTime.utc(2026, 1, 5),
        accountId: 'brk',
        cashImpact: rubles(11000),
        instrument: 'SBER',
        quantity: Quantity.units(100),
        price: rubles(110),
      );
      // Импорт пришёл в обратном порядке — результат обязан совпасть.
      final direct = const LedgerProjector().project([late, early]);
      final reversed = const LedgerProjector().project([early, late]);
      expect(direct.pnl.realized, rubles(1000));
      expect(reversed.pnl.realized, direct.pnl.realized);
      expect(reversed.positions.length, direct.positions.length);
    });

    test('срез на дату не видит будущих операций', () {
      final events = [
        event(type: LedgerEventType.deposit, cash: 100000, at: DateTime.utc(2026, 1, 10)),
        event(type: LedgerEventType.deposit, cash: 50000, at: DateTime.utc(2026, 2, 10)),
      ];
      final snap = const LedgerProjector().project(events, asOf: DateTime.utc(2026, 1, 31));
      expect(snap.cashInBase(), rubles(100000));
    });

    test('корректировка не стирает исходную запись', () {
      final events = [
        event(type: LedgerEventType.fee, cash: -1000, id: 'wrong'),
        LedgerEvent(
          id: 'fix',
          type: LedgerEventType.correction,
          effectiveAt: DateTime.utc(2026, 3, 1),
          accountId: 'brk',
          cashImpact: rubles(400),
          correctsId: 'wrong',
          note: 'комиссия удержана дважды',
        ),
      ];
      final snap = const LedgerProjector().project(events);
      expect(snap.eventCount, 2, reason: 'обе записи остаются в книге');
      expect(snap.cash['brk']!['RUB'], rubles(-600));
    });

    test('расхождения считаются и видны в срезе', () {
      final snap = const LedgerProjector().project([
        event(type: LedgerEventType.deposit, cash: 1000, reconcile: ReconcileStatus.matched),
        event(type: LedgerEventType.fee, cash: -10, reconcile: ReconcileStatus.mismatch),
      ]);
      expect(snap.mismatches, 1);
    });
  });

  group('Хранилище книги', () {
    test('повторный импорт одной выписки не удваивает записи', () async {
      final store = LedgerStore.inMemory();
      final batch = [event(type: LedgerEventType.deposit, cash: 1000, id: 'dep-1')];

      expect(await store.append(batch), 1);
      expect(await store.append(batch), 0, reason: 'дубль по id отброшен');
      expect((await store.load()).length, 1);
    });

    test('событие переживает сериализацию без потерь', () async {
      final store = LedgerStore.inMemory();
      final original = LedgerEvent(
        id: 'x1',
        type: LedgerEventType.buy,
        effectiveAt: DateTime.utc(2026, 5, 20, 10, 30),
        receivedAt: DateTime.utc(2026, 5, 20, 10, 31),
        accountId: 'tinvest',
        cashImpact: rubles(-30045),
        instrument: 'SBER',
        quantity: Quantity.units(100),
        price: rubles(300),
        fee: rubles(45),
        contour: Contour.core,
        packageId: 'balanced',
        tradeId: 't-7',
        ideaId: 'i-9',
        source: LedgerSource.broker,
        reconcile: ReconcileStatus.matched,
        brokerRef: 'ORD-123',
        note: 'ретест зоны',
        fxRate: 1,
      );
      await store.append([original]);
      final restored = LedgerEvent.fromJson(original.toJson());

      expect(restored.id, original.id);
      expect(restored.type, original.type);
      expect(restored.effectiveAt, original.effectiveAt);
      expect(restored.cashImpact, original.cashImpact);
      expect(restored.quantity, original.quantity);
      expect(restored.price, original.price);
      expect(restored.fee, original.fee);
      expect(restored.contour, Contour.core);
      expect(restored.source, LedgerSource.broker);
      expect(restored.reconcile, ReconcileStatus.matched);
      expect(restored.brokerRef, 'ORD-123');
      expect(restored.note, 'ретест зоны');
    });

    test('выгрузка начинается с версии схемы', () async {
      final store = LedgerStore.inMemory();
      await store.append([event(type: LedgerEventType.deposit, cash: 1)]);
      final dump = await store.exportJsonl();
      expect(dump.split('\n').first, contains('"schema":${LedgerStore.schemaVersion}'));
    });
  });

  group('Импорт выписки брокера', () {
    BrokerOperation op(String type, num payment, {String id = 'op1', num qty = 0, num price = 0}) =>
        BrokerOperation(
          id: id,
          type: type,
          description: type,
          at: DateTime.utc(2026, 4, 1),
          payment: payment.toDouble(),
          currency: 'RUB',
          quantity: qty.toDouble(),
          price: price.toDouble(),
          instrument: 'SBER',
        );

    List<LedgerEvent> importOf(List<BrokerOperation> ops) => BrokerImport.fromOperations(
          accountId: 'tinvest',
          venue: 'tinvest',
          operations: ops,
        );

    test('типы выписки раскладываются по смыслу, а не по остатку', () {
      final events = importOf([
        op('OPERATION_TYPE_BUY', -30000, id: 'a', qty: 100, price: 300),
        op('OPERATION_TYPE_SELL', 32000, id: 'b', qty: 100, price: 320),
        op('OPERATION_TYPE_BROKER_FEE', -45, id: 'c'),
        op('OPERATION_TYPE_DIVIDEND', 8000, id: 'd'),
        op('OPERATION_TYPE_TAX', -1040, id: 'e'),
        op('OPERATION_TYPE_INPUT', 100000, id: 'f'),
        op('OPERATION_TYPE_ACCRUING_VARMARGIN', 2400, id: 'g'),
      ]);

      expect(events.map((e) => e.type).toList(), [
        LedgerEventType.buy,
        LedgerEventType.sell,
        LedgerEventType.fee,
        LedgerEventType.dividend,
        LedgerEventType.tax,
        LedgerEventType.deposit,
        LedgerEventType.variationMargin,
      ]);
    });

    test('неизвестный тип не подгоняется под похожий', () {
      // Молчаливое «всё остальное — комиссия» однажды превратит дивиденд в
      // издержку и тихо занизит результат.
      final events = importOf([op('OPERATION_TYPE_ПРИДУМАННЫЙ', 500, id: 'x')]);

      expect(events.single.type, LedgerEventType.correction);
      expect(events.single.note, contains('не разобрано'));
      expect(BrokerImport.unresolved(events), 1);
      // Деньги при этом всё равно учтены.
      expect(events.single.cashImpact, rubles(500));
    });

    test('импорт помечен сверенным и хранит ссылку брокера', () {
      final events = importOf([op('OPERATION_TYPE_BUY', -100, qty: 1, price: 100)]);
      expect(events.single.source, LedgerSource.broker);
      expect(events.single.reconcile, ReconcileStatus.matched);
      expect(events.single.brokerRef, 'op1');
      expect(events.single.id, 'tinvest:op1');
    });

    test('повторный импорт той же выписки не удваивает книгу', () async {
      final store = LedgerStore.inMemory();
      final events = importOf([
        op('OPERATION_TYPE_INPUT', 100000, id: 'i1'),
        op('OPERATION_TYPE_BUY', -30000, id: 'i2', qty: 100, price: 300),
      ]);

      expect(await store.append(events), 2);
      expect(await store.append(events), 0);

      final snap = const LedgerProjector().project(await store.load());
      expect(snap.cashInBase(), rubles(70000));
      expect(snap.contributed, rubles(100000));
    });

    test('журнал Bybit раскладывается по своей таблице типов', () {
      // У Bybit не выписка, а журнал изменений баланса: сделка деривативом
      // не покупает «бумагу», результат приходит вармаржой.
      final events = BrokerImport.fromOperations(
        accountId: 'bybit',
        venue: 'bybit',
        operations: [
          BrokerOperation(
            id: 'b1',
            type: 'TRADE',
            description: 'TRADE Buy',
            at: DateTime.utc(2026, 4, 1),
            payment: 128.5,
            currency: 'USDT',
            instrument: 'BTCUSDT',
          ),
          BrokerOperation(
            id: 'b2',
            type: 'SETTLEMENT',
            description: 'фандинг',
            at: DateTime.utc(2026, 4, 1, 8),
            payment: -3.2,
            currency: 'USDT',
            instrument: 'BTCUSDT',
          ),
          BrokerOperation(
            id: 'b3',
            type: 'TRANSFER_IN',
            description: 'пополнение',
            at: DateTime.utc(2026, 4, 2),
            payment: 500,
            currency: 'USDT',
          ),
        ],
      );

      expect(events.map((e) => e.type).toList(), [
        LedgerEventType.variationMargin,
        LedgerEventType.funding,
        LedgerEventType.deposit,
      ]);
      expect(events.first.id, 'bybit:b1');
      expect(events.first.cashImpact.currency.code, 'USDT');
    });

    test('словарь одной площадки не применяется к другой', () {
      // OPERATION_TYPE_BUY — тип Т-Инвестиций; в журнале Bybit его быть не
      // может, и подставлять чужую таблицу нельзя.
      final events = BrokerImport.fromOperations(
        accountId: 'bybit',
        venue: 'bybit',
        operations: [
          BrokerOperation(
            id: 'x',
            type: 'OPERATION_TYPE_BUY',
            description: 'чужой тип',
            at: DateTime.utc(2026, 4, 1),
            payment: -100,
            currency: 'USDT',
          ),
        ],
      );
      expect(events.single.type, LedgerEventType.correction);
    });

    test('идентификаторы площадок не пересекаются', () {
      expect(BrokerImport.eventId('bybit', '1'), isNot(BrokerImport.eventId('tinvest', '1')));
    });
  });

  group('Кривая капитала и дельты', () {
    CapitalState stateWith({
      required List<EquityPoint> curve,
      required num equity,
      required num contributed,
    }) =>
        CapitalState(
          snapshot: const LedgerProjector().project([
            event(type: LedgerEventType.deposit, cash: contributed),
            if (equity - contributed != 0)
              event(
                type: LedgerEventType.dividend,
                cash: equity - contributed,
              ),
          ]),
          accounts: const [],
          packages: const [],
          marks: const {},
          persistent: true,
          curve: curve,
        );

    test('пополнение не выдаётся за заработок', () {
      // Ровно то, ради чего в отметке хранится внесённое: без этого перевод
      // своих же денег на счёт выглядел бы как прибыль дня.
      final now = DateTime.utc(2026, 7, 27);
      final state = stateWith(
        curve: [
          EquityPoint(
            at: now.subtract(const Duration(days: 2)),
            equity: rubles(1000000),
            contributed: rubles(1000000),
          ),
        ],
        equity: 1200000,
        contributed: 1200000,
      );

      final day = state.deltas(now: now).firstWhere((d) => d.label == 'День');
      expect(day.amount, rubles(0),
          reason: 'капитал вырос только на внесённые деньги');
    });

    test('настоящий результат виден за вычетом потоков', () {
      final now = DateTime.utc(2026, 7, 27);
      final state = stateWith(
        curve: [
          EquityPoint(
            at: now.subtract(const Duration(days: 2)),
            equity: rubles(1000000),
            contributed: rubles(1000000),
          ),
        ],
        equity: 1150000,
        contributed: 1100000,
      );

      final day = state.deltas(now: now).firstWhere((d) => d.label == 'День');
      expect(day.amount, rubles(50000));
      expect(day.percent, closeTo(5, 1e-9));
    });

    test('без отметок за период дельта не выдумывается', () {
      // «+0%» на месте отсутствующей истории — это ложь, а не заглушка.
      final now = DateTime.utc(2026, 7, 27);
      final state = stateWith(
        curve: [
          EquityPoint(
            at: now.subtract(const Duration(hours: 1)),
            equity: rubles(1000000),
            contributed: rubles(1000000),
          ),
        ],
        equity: 1000000,
        contributed: 1000000,
      );

      final labels = [for (final d in state.deltas(now: now)) d.label];
      expect(labels, isNot(contains('День')));
      expect(labels, isNot(contains('Месяц')));
    });

    test('кривая из одной точки не рисуется', () {
      final state = stateWith(
        curve: [
          EquityPoint(
            at: DateTime.utc(2026, 7, 27),
            equity: rubles(1000000),
            contributed: rubles(1000000),
          ),
        ],
        equity: 1000000,
        contributed: 1000000,
      );
      expect(state.equityCurve.length, 1);
    });
  });
}
