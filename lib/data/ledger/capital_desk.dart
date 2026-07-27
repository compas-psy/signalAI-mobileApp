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
class CapitalState {
  const CapitalState({
    required this.snapshot,
    required this.accounts,
    required this.packages,
    required this.marks,
    required this.persistent,
  });

  /// Проекция книги.
  final LedgerSnapshot snapshot;

  final List<Account> accounts;
  final List<CapitalPackage> packages;

  /// Последние известные цены инструментов для переоценки позиций.
  final Map<String, Money> marks;

  /// Пишется ли книга на диск.
  final bool persistent;

  Currency get base => snapshot.base;

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

  final LedgerStore _ledger;
  final LocalStore _store;

  List<Account> _accounts = const [];
  List<CapitalPackage> _packages = const [];
  final Map<String, Money> _marks = {};

  List<Account> get accounts => List.unmodifiable(_accounts);
  List<CapitalPackage> get packages => List.unmodifiable(_packages);

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
    );
  }

  /// Выгрузка книги для резервной копии и разбора.
  Future<String> export() => _ledger.exportJsonl();
}
