import '../../domain/ledger/account.dart';
import '../../domain/ledger/ledger_event.dart';
import '../../domain/ledger/money.dart';
import '../../domain/ledger/projections.dart';
import '../local_store.dart';
import 'ledger_store.dart';

/// Тип решения в очереди «Сегодня».
enum DecisionKind {
  idea('ИДЕЯ'),
  expiration('ЭКСПИРАЦИЯ'),
  reconcile('СВЕРКА'),
  unprotected('БЕЗ ЗАЩИТЫ'),
  cash('КЭШ'),
  rebalance('РЕБАЛАНС');

  const DecisionKind(this.label);
  final String label;
}

/// Срочность решения.
enum DecisionUrgency {
  now('сейчас'),
  today('сегодня'),
  week('на неделе');

  const DecisionUrgency(this.label);
  final String label;
}

/// Пункт очереди решений: что именно требует человека.
class Decision {
  const Decision({
    required this.kind,
    required this.title,
    required this.context,
    required this.urgency,
    this.target,
  });

  final DecisionKind kind;
  final String title;

  /// Одна строка объяснения — почему это здесь.
  final String context;

  final DecisionUrgency urgency;

  /// Куда ведёт нажатие: идентификатор идеи, счёта, позиции.
  final String? target;
}

/// Состояние капитала: всё, что показывают «Сегодня» и «Капитал».
///
/// Каждое число здесь — проекция книги, а не отдельно хранимое значение.
/// Поэтому «сходится ли баланс» не может разойтись с «что показано»: это
/// один и тот же расчёт.
/// Отметка капитала на момент времени.
///
/// Хранится вместе с внесённым: иначе пополнение выглядит как заработок, и
/// «капитал вырос на 200 000» означало бы, что владелец перевёл на счёт свои
/// же деньги. Разница результатов считается за вычетом потоков.
class EquityPoint {
  const EquityPoint({
    required this.at,
    required this.equity,
    required this.contributed,
  });

  final DateTime at;
  final Money equity;

  /// Внесено минус выведено на этот момент.
  final Money contributed;

  Map<String, dynamic> toJson() => {
        'at': at.toIso8601String(),
        'equity': equity.minor,
        'contributed': contributed.minor,
        'currency': equity.currency.code,
      };

  static EquityPoint fromJson(Map<String, dynamic> json) {
    final currency = Currency.parse(json['currency'] as String? ?? 'RUB');
    return EquityPoint(
      at: DateTime.tryParse(json['at'] as String? ?? '')?.toUtc() ??
          DateTime.now().toUtc(),
      equity: Money((json['equity'] as num?)?.toInt() ?? 0, currency),
      contributed: Money((json['contributed'] as num?)?.toInt() ?? 0, currency),
    );
  }
}

/// Изменение капитала за период — результат, а не движение денег.
class CapitalDelta {
  const CapitalDelta({
    required this.label,
    required this.from,
    required this.amount,
    required this.percent,
  });

  final String label;
  final DateTime from;

  /// Заработано за период: изменение капитала за вычетом пополнений и выводов.
  final Money amount;

  /// Тот же результат в процентах к капиталу на начало периода.
  final double percent;
}

class CapitalState {
  const CapitalState({
    required this.snapshot,
    required this.accounts,
    required this.packages,
    required this.marks,
    required this.persistent,
    this.curve = const [],
  });

  /// Проекция книги.
  final LedgerSnapshot snapshot;

  final List<Account> accounts;
  final List<CapitalPackage> packages;

  /// Последние известные цены инструментов для переоценки позиций.
  final Map<String, Money> marks;

  /// Пишется ли книга на диск.
  final bool persistent;

  /// Отметки капитала: кривая строится из них, а не пересчётом прошлого.
  final List<EquityPoint> curve;

  Currency get base => snapshot.base;

  /// Кривая капитала для спарклайна. Меньше двух точек — рисовать нечего.
  List<double> get equityCurve =>
      [for (final point in curve) point.equity.minor / 100];

  /// Результат за день, месяц и с начала года.
  ///
  /// Считается по отметкам капитала за вычетом потоков: пополнение — не
  /// заработок. Периоды, для которых отметок нет, не появляются вовсе —
  /// приложение не показывает «+0%» там, где просто нет истории.
  List<CapitalDelta> deltas({DateTime? now}) {
    final at = (now ?? DateTime.now()).toUtc();
    final periods = <(String, DateTime)>[
      ('День', at.subtract(const Duration(days: 1))),
      ('Месяц', at.subtract(const Duration(days: 30))),
      ('С начала года', DateTime.utc(at.year)),
    ];

    final result = <CapitalDelta>[];
    for (final (label, from) in periods) {
      final base = _pointAtOrBefore(from);
      if (base == null) continue;
      if (base.equity.currency != this.base) continue;
      // Результат = изменение капитала минус то, что владелец сам довнёс.
      final flows = snapshot.netContributed - base.contributed;
      final amount = totalEquity - base.equity - flows;
      result.add(CapitalDelta(
        label: label,
        from: base.at,
        amount: amount,
        percent: base.equity.isZero ? 0 : amount.minor / base.equity.minor * 100,
      ));
    }
    return result;
  }

  /// Последняя отметка не позже [moment]. null — истории за период нет.
  EquityPoint? _pointAtOrBefore(DateTime moment) {
    EquityPoint? found;
    for (final point in curve) {
      if (point.at.isAfter(moment)) break;
      found = point;
    }
    return found;
  }

  /// Есть ли вообще что показывать. Пустая книга — не ошибка, а состояние
  /// «ещё не заводили»; интерфейс обязан говорить именно это.
  bool get isEmpty => snapshot.eventCount == 0;

  /// Рыночная стоимость открытых позиций.
  Money get positionsValue {
    var total = Money.zero(base);
    for (final position in snapshot.positions) {
      final mark = marks[position.instrument];
      if (mark == null || mark.currency != base) {
        // Цены нет — берём себестоимость: занижать или завышать капитал
        // выдуманной ценой нельзя, а показать позицию по вложенному честно.
        if (position.costBasis.currency == base) total += position.costBasis.abs;
        continue;
      }
      total += mark.multiplyBy(position.quantity.abs);
    }
    return total;
  }

  /// Совокупный капитал: деньги плюс позиции по рынку.
  Money get totalEquity => snapshot.cashInBase() + positionsValue;

  /// Нереализованный результат по открытым позициям.
  Money get unrealized {
    var total = Money.zero(base);
    for (final position in snapshot.positions) {
      final mark = marks[position.instrument];
      if (mark == null || mark.currency != base) continue;
      total += position.unrealized(mark);
    }
    return total;
  }

  /// Инвестиционный результат за всё время: что капитал заработал сверх
  /// внесённого.
  Money get investmentResult => totalEquity - snapshot.netContributed;

  /// Свободные деньги в базовой валюте.
  Money get freeCash => snapshot.cashInBase();

  /// Аллокация по контурам: факт против цели пакетов.
  List<ContourAllocation> allocation() {
    final byContour = <Contour, Money>{
      for (final c in Contour.values) c: Money.zero(base),
    };
    for (final position in snapshot.positions) {
      final contour = position.contour ?? Contour.tactical;
      final mark = marks[position.instrument];
      final value = mark == null || mark.currency != base
          ? position.costBasis.abs
          : mark.multiplyBy(position.quantity.abs);
      if (value.currency != base) continue;
      byContour[contour] = byContour[contour]! + value;
    }
    // Свободные деньги — это ядро: они ждут распределения, а не рискуют.
    byContour[Contour.core] = byContour[Contour.core]! + freeCash;

    final total = byContour.values.fold(Money.zero(base), (a, b) => a + b);
    return [
      for (final entry in byContour.entries)
        ContourAllocation(
          contour: entry.key,
          value: entry.value,
          targetPercent: _targetFor(entry.key),
          actualPercent: total.isZero ? 0 : entry.value.minor / total.minor * 100,
        ),
    ];
  }

  double _targetFor(Contour contour) => switch (contour) {
        Contour.core => 60,
        Contour.tactical => 30,
        Contour.risk => 10,
      };
}

/// Владелец книги капитала: чтение, запись и производные состояния.
class CapitalDesk {
  CapitalDesk({LedgerStore? ledger, LocalStore? store})
      : _ledger = ledger ?? LedgerStore(),
        _store = store ?? LocalStore();

  static const _accountsKey = 'capital_accounts';
  static const _packagesKey = 'capital_packages';
  static const _curveKey = 'capital_curve';

  /// Сколько точек кривой храним. Три года ежедневных отметок — этого хватает
  /// и на дельты за год, и на график, а файл остаётся крошечным.
  static const _curveLimit = 1100;

  final LedgerStore _ledger;
  final LocalStore _store;

  List<Account> _accounts = const [];
  List<CapitalPackage> _packages = const [];
  List<EquityPoint> _curve = const [];
  final Map<String, Money> _marks = {};

  List<Account> get accounts => List.unmodifiable(_accounts);
  List<CapitalPackage> get packages => List.unmodifiable(_packages);

  /// Кривая капитала: отметки на моменты, когда капитал реально считали.
  List<EquityPoint> get curve => List.unmodifiable(_curve);

  /// Читает счета и пакеты. Книга читается лениво при расчёте состояния.
  Future<void> load() async {
    final accountsDoc = await _store.read(_accountsKey);
    if (accountsDoc != null) {
      _accounts = [
        for (final a in (accountsDoc['items'] as List? ?? const []))
          Account.fromJson(a as Map<String, dynamic>),
      ];
    }
    final packagesDoc = await _store.read(_packagesKey);
    if (packagesDoc != null) {
      _packages = [
        for (final p in (packagesDoc['items'] as List? ?? const []))
          CapitalPackage.fromJson(p as Map<String, dynamic>),
      ];
    }
    final curveDoc = await _store.read(_curveKey);
    if (curveDoc != null) {
      _curve = [
        for (final p in (curveDoc['items'] as List? ?? const []))
          EquityPoint.fromJson(p as Map<String, dynamic>),
      ];
    }
  }

  /// Отмечает капитал на сегодня.
  ///
  /// Кривая капитала строится из отметок, а не пересчётом прошлого: чтобы
  /// узнать, сколько стоил портфель месяц назад, нужны цены того дня, а их
  /// никто не хранит. Пересчёт прошлого сегодняшними ценами дал бы красивый
  /// график, который врёт ровно на величину движения рынка.
  ///
  /// Одна отметка в день: последняя за день заменяет предыдущую.
  Future<void> markEquity(CapitalState state, {DateTime? at}) async {
    if (state.isEmpty) return;
    final now = (at ?? DateTime.now()).toUtc();
    final day = DateTime.utc(now.year, now.month, now.day);
    final point = EquityPoint(
      at: now,
      equity: state.totalEquity,
      contributed: state.snapshot.netContributed,
    );
    final kept = <EquityPoint>[
      for (final p in _curve)
        if (DateTime.utc(p.at.year, p.at.month, p.at.day) != day) p,
      point,
    ];
    kept.sort((a, b) => a.at.compareTo(b.at));
    _curve = kept.length > _curveLimit
        ? kept.sublist(kept.length - _curveLimit)
        : kept;
    await _store.write(_curveKey, {
      'items': [for (final p in _curve) p.toJson()],
    });
  }

  Future<void> saveAccounts(List<Account> accounts) async {
    _accounts = List.unmodifiable(accounts);
    await _store.write(_accountsKey, {'items': [for (final a in accounts) a.toJson()]});
  }

  Future<void> savePackages(List<CapitalPackage> packages) async {
    _packages = List.unmodifiable(packages);
    await _store.write(_packagesKey, {'items': [for (final p in packages) p.toJson()]});
  }

  /// Последняя цена инструмента для переоценки позиций.
  void mark(String instrument, Money price) => _marks[instrument] = price;

  /// Записывает операции в книгу. Возвращает, сколько записей добавлено.
  Future<int> record(List<LedgerEvent> events) => _ledger.append(events);

  /// Все операции книги.
  Future<List<LedgerEvent>> events() => _ledger.load();

  /// Текущее состояние капитала.
  Future<CapitalState> state({DateTime? asOf}) async {
    final events = await _ledger.load();
    return CapitalState(
      snapshot: const LedgerProjector().project(events, asOf: asOf),
      accounts: _accounts,
      packages: _packages,
      marks: Map.unmodifiable(_marks),
      persistent: await _ledger.persistent,
      curve: _curve,
    );
  }

  /// Выгрузка книги для резервной копии и разбора.
  Future<String> export() => _ledger.exportJsonl();
}
