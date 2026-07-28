import 'package:flutter/widgets.dart';

import '../../core/format.dart';
import '../../data/ledger/capital_desk.dart';
import '../../domain/ledger/account.dart';
import '../../domain/ledger/ledger_event.dart';
import '../../domain/ledger/money.dart';
import '../../domain/portfolio/package_plan.dart';
import '../../state/app_controller.dart';
import '../../state/app_scope.dart';
import '../../state/navigation.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../layout.dart';
import '../widgets/common.dart';
import '../widgets/operation_sheet.dart';
import '../widgets/package_widgets.dart';
import '../widgets/vector_icon.dart';
import 'package_detail_screen.dart';
import '../widgets/segmented.dart';

/// Раздел «Капитал»: обзор, счета, пакеты, книга операций, аналитика.
///
/// Книга здесь не витрина, а рабочая поверхность: операцию можно завести
/// руками, и любое число раздела считается из тех же записей.
class CapitalScreen extends StatelessWidget {
  const CapitalScreen({super.key, required this.pill});

  final int pill;

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.of(context);
    final state = controller.capital;

    if (state == null) {
      return _Unavailable(loading: controller.capitalLoading);
    }

    return switch (CapitalPill.values[pill.clamp(0, CapitalPill.values.length - 1)]) {
      CapitalPill.overview => _Overview(state: state),
      CapitalPill.accounts => _Accounts(state: state, controller: controller),
      CapitalPill.packages => _Packages(state: state),
      CapitalPill.book => _Book(state: state, controller: controller),
      CapitalPill.analytics => _Analytics(state: state),
    };
  }
}

EdgeInsets get _pad => const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 90);

/// Обзор: четыре числа и разрезы капитала.
class _Overview extends StatelessWidget {
  const _Overview({required this.state});

  final CapitalState state;

  @override
  Widget build(BuildContext context) {
    final snapshot = state.snapshot;
    return ListView(
      padding: _pad,
      children: [
        MetricRow(tiles: [
          MetricTile(
            label: 'Внесено',
            value: fmtMoney(snapshot.netContributed),
            hint: 'пополнения минус выводы',
          ),
          MetricTile(
            label: 'Стоимость',
            value: fmtMoney(state.totalEquity),
            hint: 'деньги плюс позиции',
          ),
        ]),
        const SizedBox(height: 8),
        MetricRow(tiles: [
          MetricTile(
            label: 'Реализовано',
            value: fmtMoney(snapshot.pnl.realized, sign: true),
            color: snapshot.pnl.realized.isNegative ? C.red : C.green,
            hint: 'по закрытым сделкам',
          ),
          MetricTile(
            label: 'Нереализовано',
            value: fmtMoney(state.unrealized, sign: true),
            color: state.unrealized.isNegative ? C.red : C.green,
            hint: 'по открытым',
          ),
        ]),
        const SizedBox(height: 12),
        CardGrid(children: [
          SectionCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const SectionLabel('Доходы'),
                const SizedBox(height: 6),
                KeyValueRow(
                  name: 'Дивиденды, купоны, проценты',
                  value: fmtMoney(snapshot.pnl.income),
                  valueStyle: T.mono(12, weight: 600, color: C.info),
                ),
                KeyValueRow(
                  name: 'Инвестиционный результат',
                  value: fmtMoney(state.investmentResult, sign: true),
                  showDivider: false,
                  valueStyle: T.mono(12,
                      weight: 600,
                      color: state.investmentResult.isNegative ? C.red : C.green),
                ),
              ],
            ),
          ),
          SectionCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const SectionLabel('Издержки'),
                const SizedBox(height: 6),
                KeyValueRow(
                  name: 'Комиссии и фандинг',
                  value: fmtMoney(snapshot.pnl.fees),
                  valueStyle: T.mono(12, weight: 600, color: C.red),
                ),
                KeyValueRow(
                  name: 'Налоги',
                  value: fmtMoney(snapshot.pnl.taxes),
                  showDivider: false,
                  valueStyle: T.mono(12, weight: 600, color: C.red),
                ),
              ],
            ),
          ),
          _CurrenciesCard(state: state),
          _ContoursCard(state: state),
        ]),
      ],
    );
  }
}

class _CurrenciesCard extends StatelessWidget {
  const _CurrenciesCard({required this.state});

  final CapitalState state;

  @override
  Widget build(BuildContext context) {
    final foreign = state.snapshot.foreignCash();
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('По валютам'),
          const SizedBox(height: 6),
          KeyValueRow(
            name: state.base.code,
            value: fmtMoney(state.snapshot.cashInBase()),
            showDivider: foreign.isNotEmpty,
            valueStyle: T.mono(12, weight: 600),
          ),
          for (final entry in foreign.entries)
            KeyValueRow(
              name: entry.key,
              value: fmtMoney(entry.value),
              showDivider: entry.key != foreign.keys.last,
              valueStyle: T.mono(12, weight: 600),
            ),
          if (foreign.isNotEmpty) ...[
            const SizedBox(height: 6),
            Text(
              'Валюты, отличные от базовой, показаны как есть: пересчёт идёт '
              'по курсу, сохранённому вместе с операцией, а не по сегодняшнему.',
              style: T.body(10, color: C.faint, height: 1.4),
            ),
          ],
        ],
      ),
    );
  }
}

class _ContoursCard extends StatelessWidget {
  const _ContoursCard({required this.state});

  final CapitalState state;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('По контурам'),
            const SizedBox(height: 6),
            for (final slice in state.allocation())
              KeyValueRow(
                name: slice.contour.label,
                value: '${fmtMoney(slice.value)} · '
                    '${slice.actualPercent.round()}% из ${slice.targetPercent.round()}%',
                showDivider: slice.contour != Contour.values.last,
                valueStyle: T.mono(11.5, weight: 600),
              ),
          ],
        ),
      );
}

/// Счета: где лежат деньги и какие права у ключа.
class _Accounts extends StatelessWidget {
  const _Accounts({required this.state, required this.controller});

  final CapitalState state;
  final AppController controller;

  /// К какой площадке относится счёт книги. Ручные счета площадки не имеют.
  static String? _venueOf(Account account) {
    if (account.id.startsWith('tinvest')) return 'tinvest';
    if (account.id.startsWith('bybit')) return 'bybit';
    return null;
  }

  /// Рыночная стоимость позиций каждого счёта.
  ///
  /// Карточка счёта обязана показывать не только кэш: счёт с пустым остатком
  /// и позициями на миллион — не пустой счёт.
  static Map<String, Money> _positionsByAccount(CapitalState state) {
    final result = <String, Money>{};
    for (final position in state.snapshot.positions) {
      final mark = state.marks[position.instrument];
      final value = mark == null
          ? position.costBasis.abs
          : mark.multiplyBy(position.quantity.abs);
      final current = result[position.accountId];
      if (current != null && current.currency != value.currency) continue;
      result[position.accountId] = current == null ? value : current + value;
    }
    return result;
  }

  @override
  Widget build(BuildContext context) {
    final cash = state.snapshot.cash;
    final positions = _positionsByAccount(state);
    final manual = [
      for (final account in state.accounts)
        if (_venueOf(account) == null) account,
    ];

    return ListView(
      padding: _pad,
      children: [
        // Сначала площадки — все, включая те, что молчат. Раньше здесь были
        // только счета, которые ответили, и Bybit без ключей просто исчезал.
        for (final venue in controller.venues) ...[
          _VenueCard(
            venue: venue,
            accounts: [
              for (final account in state.accounts)
                if (_venueOf(account) == venue.id.name) account,
            ],
            cash: cash,
            positions: positions,
          ),
          const SizedBox(height: 12),
        ],
        if (controller.venues.isEmpty)
          SectionCard(
            child: Text(
              'Площадки в этом режиме недоступны: учёт работает по книге, '
              'которую наполняют руками.',
              style: T.body(11.5, color: C.muted, height: 1.5),
            ),
          ),
        if (manual.isNotEmpty) ...[
          const SectionLabel('Счета вне площадок'),
          const SizedBox(height: 8),
          CardGrid(children: [
            for (final account in manual)
              _AccountCard(
                account: account,
                balances: cash[account.id] ?? const {},
                positions: positions[account.id],
              ),
          ]),
          const SizedBox(height: 12),
        ],
        ActionButton(
          label: controller.capitalLoading ? 'Сверяем…' : 'Сверить с площадками',
          primary: true,
          onTap: controller.capitalLoading
              ? null
              : () async {
                  await controller.refreshCapital(sync: true);
                  await controller.refreshVenues();
                },
        ),
        if (controller.capitalNote != null) ...[
          const SizedBox(height: 8),
          Text(controller.capitalNote!,
              style: T.mono(10, color: C.muted, height: 1.4)),
        ],
        const SizedBox(height: 8),
        ActionButton(
          label: 'Добавить счёт вручную',
          dense: true,
          onTap: () => showAddAccountSheet(context, controller),
        ),
        const SizedBox(height: 12),
        // Одно правило конфигурации — одна строка внизу экрана. Раньше этот
        // абзац повторялся на каждой карточке счёта: объяснение, одинаковое
        // для всех строк, не является свойством строки.
        Text(
          'Право на вывод средств у ключей не запрашивается ни на одной '
          'площадке: приложению оно не нужно, а ключ с выводом делает взлом '
          'устройства кражей денег.',
          style: T.body(10, color: C.faint, height: 1.4),
        ),
      ],
    );
  }
}

/// Площадка целиком: режим, ключи, её счета либо причина, почему их нет.
class _VenueCard extends StatelessWidget {
  const _VenueCard({
    required this.venue,
    required this.accounts,
    required this.cash,
    required this.positions,
  });

  final VenueStatus venue;
  final List<Account> accounts;
  final Map<String, Map<String, Money>> cash;
  final Map<String, Money> positions;

  @override
  Widget build(BuildContext context) {
    final problem = venue.problem;
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                width: 8,
                height: 8,
                decoration: BoxDecoration(
                  color: problem == null
                      ? C.green
                      : (venue.hasKeys ? C.warning : C.faint),
                  shape: BoxShape.circle,
                ),
              ),
              const SizedBox(width: 8),
              Expanded(child: Text(venue.title, style: T.body(13.5, weight: 700))),
              Text(
                accounts.isEmpty ? 'счетов нет' : 'счетов ${accounts.length}',
                style: T.mono(10.5, color: C.muted),
              ),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            'режим ${venue.mode.name} · ключи: '
            '${venue.hasKeys ? venue.keyModes.map((m) => m.name).join(', ') : 'нет'}',
            style: T.mono(10.5, color: C.faint),
          ),
          if (problem != null) ...[
            const SizedBox(height: 8),
            InsetBox(
              child: Text(problem,
                  style: T.body(11, color: C.warning, height: 1.4)),
            ),
          ],
          if (accounts.isNotEmpty) ...[
            const SizedBox(height: 10),
            for (final account in accounts) ...[
              _AccountCard(
                account: account,
                balances: cash[account.id] ?? const {},
                positions: positions[account.id],
                nested: true,
              ),
              if (account != accounts.last) const SizedBox(height: 8),
            ],
          ],
        ],
      ),
    );
  }
}

class _AccountCard extends StatelessWidget {
  const _AccountCard({
    required this.account,
    required this.balances,
    this.positions,
    this.nested = false,
  });

  final Account account;
  final Map<String, Money> balances;

  /// Рыночная стоимость позиций этого счёта. null — позиций нет.
  final Money? positions;

  /// Карточка внутри карточки площадки: без второй рамки поверх первой.
  final bool nested;

  @override
  Widget build(BuildContext context) {
    final status = switch (account.reconcile) {
      ReconcileStatus.matched => C.green,
      ReconcileStatus.mismatch => C.red,
      ReconcileStatus.stale => C.warning,
      ReconcileStatus.pending => C.info,
      ReconcileStatus.manual => C.muted,
    };
    final body = _body(status);
    return nested
        ? InsetBox(child: body)
        : SectionCard(child: body);
  }

  // Карточка счёта по ТЗ v3 §5: аватар, название, тип, equity справа и три
  // ячейки — кэш, позиции, итого. Раньше здесь вместо чисел стоял абзац про
  // право на вывод, и он повторялся на каждом счёте. Объяснение, одинаковое
  // для всех строк, — это не свойство строки: оно ушло одной строкой в низ
  // экрана.
  Widget _body(Color status) {
    final cash = balances.isEmpty ? null : balances.values.first;
    final total = cash == null
        ? positions
        : positions == null || positions!.currency != cash.currency
            ? cash
            : cash + positions!;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Container(
              width: 36,
              height: 36,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                color: C.inset,
                borderRadius: BorderRadius.circular(R.inset),
              ),
              child: Text(
                account.title.characters.first.toUpperCase(),
                style: T.jost(15, color: C.accent),
              ),
            ),
            const SizedBox(width: 11),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(account.title,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: T.body(13, weight: 700)),
                  Text('${account.kind.label} · ${account.currency.code}',
                      style: T.body(10.5, color: C.muted)),
                ],
              ),
            ),
            const SizedBox(width: 8),
            Text(
              total == null ? '—' : fmtMoney(total),
              style: T.mono(13.5, weight: 700),
            ),
          ],
        ),
        const SizedBox(height: 10),
        Row(
          children: [
            _Cell(label: 'кэш', value: cash),
            _Cell(label: 'позиции', value: positions),
            _Cell(label: 'итого', value: total, strong: true),
          ],
        ),
        const SizedBox(height: 9),
        Row(
          children: [
            Container(
              width: 7,
              height: 7,
              decoration: BoxDecoration(color: status, shape: BoxShape.circle),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                [
                  account.reconcile.label,
                  if (account.syncedAt != null) _time(account.syncedAt!),
                  account.permissions.label,
                ].join(' · '),
                style: T.mono(10.5, color: C.muted),
              ),
            ),
          ],
        ),
        if (account.note != null) ...[
          const SizedBox(height: 5),
          Text(account.note!, style: T.mono(10, color: C.faint, height: 1.35)),
        ],
      ],
    );
  }

  static String _time(DateTime at) {
    final local = at.toLocal();
    return '${local.hour.toString().padLeft(2, '0')}:'
        '${local.minute.toString().padLeft(2, '0')}';
  }
}

/// Ячейка карточки счёта: подпись сверху, число снизу.
class _Cell extends StatelessWidget {
  const _Cell({required this.label, required this.value, this.strong = false});

  final String label;
  final Money? value;
  final bool strong;

  @override
  Widget build(BuildContext context) => Expanded(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(label.toUpperCase(), style: T.microLabel()),
            const SizedBox(height: 2),
            Text(
              value == null ? '—' : fmtMoney(value!),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: T.mono(
                11.5,
                weight: strong ? 700 : 500,
                color: value == null ? C.faint : C.text,
              ),
            ),
          ],
        ),
      );
}

/// Пакеты: целевые корзины поверх книги.
class _Packages extends StatelessWidget {
  const _Packages({required this.state});

  final CapitalState state;

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.of(context);
    return ListView(
      padding: _pad,
      children: [
        SectionCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const SectionLabel('Пакеты капитала'),
              const SizedBox(height: 6),
              Text(
                'Пакет — целевая корзина поверх книги, а не отдельный счёт: '
                'горизонт, состав с полосами допуска и правило, при котором '
                'замысел считается сломанным. Фактические веса считаются из '
                'книги, доходность — историческая симуляция на реальных '
                'данных, а не прогноз.',
                style: T.body(11.5, color: C.muted, height: 1.5),
              ),
            ],
          ),
        ),
        const SizedBox(height: 12),
        for (final plan in controller.packagePlans) ...[
          _PackagePlanCard(plan: plan, controller: controller),
          const SizedBox(height: 12),
        ],
      ],
    );
  }
}

class _PackagePlanCard extends StatelessWidget {
  const _PackagePlanCard({required this.plan, required this.controller});

  final PackagePlan plan;
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final history = controller.packageHistory(plan.id);

    // Карточка — это анонс, а не сам пакет. Разбор до штук и денег живёт на
    // отдельном экране: раньше карточка не открывалась вовсе, и состав
    // пакета негде было увидеть.
    return Pressable(
      onTap: () => Navigator.of(context).push(
        PageRouteBuilder<void>(
          pageBuilder: (_, _, _) => PackageDetailScreen(plan: plan),
          transitionsBuilder: (_, animation, _, child) =>
              FadeTransition(opacity: animation, child: child),
        ),
      ),
      child: SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(child: Text(plan.title, style: T.jost(17))),
                OutlineBadge(
                  label: '${plan.horizonYears} ЛЕТ',
                  color: C.info,
                  borderColor: C.infoBorder,
                  background: C.infoFaint,
                  fontWeight: 800,
                ),
              ],
            ),
            const SizedBox(height: 6),
            Text(plan.thesis,
                maxLines: 3,
                overflow: TextOverflow.ellipsis,
                style: T.body(11.5, color: C.muted, height: 1.5)),
            const SizedBox(height: 12),
            CompositionBar(plan: plan),
            const SizedBox(height: 10),
            for (final target in plan.targets)
              Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Row(
                  children: [
                    Container(
                      width: 8,
                      height: 8,
                      decoration: BoxDecoration(
                        color: classColor(target.assetClass),
                        borderRadius: BorderRadius.circular(2),
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        '${target.assetClass.label} · ${target.assetClass.proxy}',
                        style: T.body(11.5, color: C.textSecondary),
                      ),
                    ),
                    Text(
                      '${target.weightPercent.round()}% '
                      '±${target.bandPercent.round()}',
                      style: T.mono(11, weight: 600),
                    ),
                  ],
                ),
              ),
            const SizedBox(height: 4),
            Row(
              children: [
                Expanded(
                  child: Text(
                    history == null || history.isEmpty
                        ? 'Открыть: состав в штуках и деньгах, история, заявки'
                        : 'Годовых ${history.cagr >= 0 ? '+' : '−'}'
                            '${history.cagr.abs().toStringAsFixed(1).replaceAll('.', ',')}%'
                            ' · просадка ${history.maxDrawdown.toStringAsFixed(0)}%',
                    style: T.body(11, color: C.accent, height: 1.4),
                  ),
                ),
                const VectorIcon(Icons.chevronRight, size: 14, color: C.accent),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

/// Книга операций: список с фильтрами и ручной ввод.
class _Book extends StatefulWidget {
  const _Book({required this.state, required this.controller});

  final CapitalState state;
  final AppController controller;

  @override
  State<_Book> createState() => _BookState();
}

class _BookState extends State<_Book> {
  int _filter = 0;
  List<LedgerEvent> _events = const [];
  bool _loaded = false;

  static const _filters = ['Все', 'Сделки', 'Деньги', 'Доходы', 'Издержки', 'Правки'];

  @override
  void initState() {
    super.initState();
    _reload();
  }

  Future<void> _reload() async {
    final desk = widget.controller.capitalDesk;
    if (desk == null) return;
    final events = await desk.events();
    if (!mounted) return;
    setState(() {
      _events = events.reversed.toList();
      _loaded = true;
    });
  }

  bool _matches(LedgerEvent event) => switch (_filter) {
        1 => event.kind == LedgerKind.trade,
        2 => event.kind == LedgerKind.cashflow || event.kind == LedgerKind.transfer,
        3 => event.kind == LedgerKind.income,
        4 => event.kind == LedgerKind.cost,
        5 => event.kind == LedgerKind.correction,
        _ => true,
      };

  @override
  Widget build(BuildContext context) {
    final visible = [for (final e in _events) if (_matches(e)) e];
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(S.screen, 10, S.screen, 6),
          child: SegmentedControl(
            items: _filters,
            index: _filter,
            onSelect: (i) => setState(() => _filter = i),
          ),
        ),
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(S.screen, 6, S.screen, 90),
            children: [
              if (!_loaded)
                Text('Читаем книгу…', style: T.body(11.5, color: C.muted))
              else if (visible.isEmpty)
                SectionCard(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        _events.isEmpty ? 'Книга пуста' : 'В этом срезе операций нет',
                        style: T.body(13, weight: 700),
                      ),
                      const SizedBox(height: 6),
                      Text(
                        _events.isEmpty
                            ? 'Заведите начальный остаток счёта — дальше сделки '
                                'и комиссии подтянутся с площадок, а переводы и '
                                'банковский резерв вносятся руками.'
                            : 'Смените фильтр или заведите операцию.',
                        style: T.body(11.5, color: C.muted, height: 1.5),
                      ),
                    ],
                  ),
                )
              else
                SectionCard(
                  clip: true,
                  padding: EdgeInsets.zero,
                  child: Column(
                    children: [
                      for (final event in visible)
                        _OperationRow(
                          event: event,
                          onTap: () => showOperationDetail(context, event),
                        ),
                    ],
                  ),
                ),
              const SizedBox(height: 12),
              Pressable(
                onTap: () async {
                  final added = await showOperationSheet(context, widget.controller);
                  if (added) await _reload();
                },
                child: Container(
                  padding: const EdgeInsets.symmetric(vertical: 12),
                  decoration: BoxDecoration(
                    color: C.accent,
                    borderRadius: BorderRadius.circular(R.button),
                  ),
                  child: Center(
                    child: Text('Записать операцию',
                        style: T.body(13, weight: 800, color: C.onAccent)),
                  ),
                ),
              ),
              const SizedBox(height: 10),
              Text(
                'Операции не удаляются. Ошибка исправляется компенсирующей '
                'записью — так история остаётся проверяемой, а не переписанной.',
                style: T.body(10, color: C.faint, height: 1.4),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

/// Строка книги: статус сверки, дата, тип, инструмент, влияние на деньги.
class _OperationRow extends StatelessWidget {
  const _OperationRow({required this.event, required this.onTap});

  final LedgerEvent event;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final color = switch (event.kind) {
      LedgerKind.trade => C.text,
      LedgerKind.cashflow => C.info,
      LedgerKind.transfer => C.tactical,
      LedgerKind.income => C.green,
      LedgerKind.cost => C.red,
      LedgerKind.correction => C.warning,
    };
    final status = switch (event.reconcile) {
      ReconcileStatus.matched => C.green,
      ReconcileStatus.mismatch => C.red,
      ReconcileStatus.stale => C.warning,
      ReconcileStatus.pending => C.info,
      ReconcileStatus.manual => C.muted,
    };
    final local = event.effectiveAt.toLocal();
    return Pressable(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        decoration: const BoxDecoration(
          border: Border(bottom: BorderSide(color: C.divider)),
        ),
        child: Row(
          children: [
            Container(
              width: 6,
              height: 6,
              decoration: BoxDecoration(color: status, shape: BoxShape.circle),
            ),
            const SizedBox(width: 10),
            SizedBox(
              width: 42,
              child: Text(
                '${local.day.toString().padLeft(2, '0')}.'
                '${local.month.toString().padLeft(2, '0')}',
                style: T.mono(10.5, color: C.muted),
              ),
            ),
            const SizedBox(width: 6),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(event.type.label,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: T.body(11.5, weight: 700, color: color)),
                  if (event.instrument != null)
                    Text(
                      '${event.instrument}'
                      '${event.quantity == null ? '' : ' · ${fmtQuantity(event.quantity!)}'}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: T.mono(10, color: C.muted),
                    ),
                ],
              ),
            ),
            const SizedBox(width: 8),
            Text(
              fmtMoney(event.cashImpact, sign: true),
              style: T.mono(11.5,
                  weight: 600,
                  color: event.cashImpact.isNegative ? C.red : C.green),
            ),
          ],
        ),
      ),
    );
  }
}

/// Аналитика капитала.
class _Analytics extends StatelessWidget {
  const _Analytics({required this.state});

  final CapitalState state;

  @override
  Widget build(BuildContext context) {
    final contributed = state.snapshot.netContributed;
    final result = state.investmentResult;
    // Простая доходность на внесённый капитал. TWR и XIRR требуют истории
    // стоимости по датам — она появится, когда в книге накопятся операции за
    // несколько периодов; до тех пор показывать их значило бы выдумывать.
    final simple = contributed.isZero
        ? null
        : result.minor / contributed.minor * 100;

    return ListView(
      padding: _pad,
      children: [
        MetricRow(tiles: [
          MetricTile(
            label: 'Внесено',
            value: fmtMoney(contributed),
            hint: 'чистый вклад владельца',
          ),
          MetricTile(
            label: 'Результат',
            value: fmtMoney(result, sign: true),
            color: result.isNegative ? C.red : C.green,
            hint: simple == null
                ? 'без базы для процента'
                : '${simple >= 0 ? '+' : '−'}${simple.abs().toStringAsFixed(1).replaceAll('.', ',')}%',
          ),
        ]),
        const SizedBox(height: 12),
        SectionCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const SectionLabel('Доходность'),
              const SizedBox(height: 6),
              Text(
                'TWR и XIRR считаются по стоимости капитала на даты потоков. '
                'Пока в книге ${state.snapshot.eventCount} операций и история '
                'короче периода расчёта, поэтому показан простой процент на '
                'внесённые деньги — с оговоркой, что он занижает результат при '
                'поздних пополнениях и завышает при ранних выводах.',
                style: T.body(11.5, color: C.muted, height: 1.5),
              ),
            ],
          ),
        ),
        const SizedBox(height: 12),
        SectionCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const SectionLabel('Издержки во времени'),
              const SizedBox(height: 6),
              KeyValueRow(
                name: 'Комиссии и фандинг',
                value: fmtMoney(state.snapshot.pnl.fees),
                valueStyle: T.mono(12, weight: 600, color: C.red),
              ),
              KeyValueRow(
                name: 'Налоги',
                value: fmtMoney(state.snapshot.pnl.taxes),
                showDivider: false,
                valueStyle: T.mono(12, weight: 600, color: C.red),
              ),
              const SizedBox(height: 8),
              Text(
                'Издержки — единственная часть результата, на которую можно '
                'влиять напрямую: их видно отдельно, а не спрятанными внутри '
                'прибыли.',
                style: T.body(10, color: C.faint, height: 1.4),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _Unavailable extends StatelessWidget {
  const _Unavailable({required this.loading});

  final bool loading;

  @override
  Widget build(BuildContext context) => ListView(
        padding: _pad,
        children: [
          SectionCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(loading ? 'Читаем книгу…' : 'Учёт недоступен',
                    style: T.jost(17)),
                const SizedBox(height: 6),
                Text(
                  loading
                      ? 'Считаем остатки и позиции по операциям книги.'
                      : 'Книга капитала живёт только в автономном расчёте на '
                          'устройстве. В этом режиме её нет.',
                  style: T.body(11.5, color: C.muted, height: 1.5),
                ),
              ],
            ),
          ),
        ],
      );
}
