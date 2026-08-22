import 'package:flutter/widgets.dart';

import '../../core/format.dart';
import '../../data/ledger/capital_desk.dart';
import '../../domain/ledger/account.dart';
import '../../domain/ledger/ledger_event.dart';
import '../../domain/ledger/money.dart';
import '../../domain/portfolio/package.dart';
import '../../domain/portfolio/package_absence.dart';
import '../../domain/portfolio/package_pick.dart';
import '../../domain/portfolio/package_plan.dart';
import '../../state/app_controller.dart';
import '../../state/app_scope.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../layout.dart';
import '../widgets/common.dart';
import '../widgets/engine_package_widgets.dart';
import '../widgets/operation_sheet.dart';
import '../widgets/segmented.dart';

/// Раздел «Капитал»: обзор, счета, пакеты, книга операций, аналитика.
///
/// Книга здесь не витрина, а рабочая поверхность: операцию можно завести
/// руками, и любое число раздела считается из тех же записей.
/// Подразделы прежнего «Капитала».
///
/// Живут здесь, а не в навигации: снаружи раздел называется «Портфель» и
/// имеет собственные пилюли по ТЗ §7. Это внутреннее устройство экрана,
/// которое уйдёт вместе с его переписыванием.
enum CapitalPill { overview, accounts, packages, book, analytics }

class CapitalScreen extends StatelessWidget {
  const CapitalScreen({super.key, required this.pill});

  final int pill;

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.of(context);
    final state = controller.capital;
    final section =
        CapitalPill.values[pill.clamp(0, CapitalPill.values.length - 1)];

    // Пакеты живут не в книге операций, а на сервере: их состав не зависит
    // ни от одной записи журнала. Пока эта ветка стояла после проверки
    // книги, пустой журнал прятал посчитанный движком состав.
    if (section == CapitalPill.packages) {
      return _Packages(controller: controller);
    }

    if (state == null) {
      return _Unavailable(loading: controller.capitalLoading);
    }

    return switch (section) {
      CapitalPill.overview => _Overview(state: state),
      CapitalPill.accounts => _Accounts(state: state, controller: controller),
      CapitalPill.packages => _Packages(controller: controller),
      CapitalPill.book => _Book(state: state, controller: controller),
      CapitalPill.analytics => _Analytics(state: state),
    };
  }
}

EdgeInsets get _pad => const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 90);

/// Шаг конвейера: сделан или нет. Прогресс виден точкой и цветом, а не
/// абзацем текста.
class _Stage extends StatelessWidget {
  const _Stage({required this.done, required this.name, this.detail = ''});

  final bool done;
  final String name;

  /// Чем шаг измеряется: «12 бумаг», «история есть у 9». Число рядом с
  /// отметкой отличает сделанный шаг от объявленного сделанным.
  final String detail;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 7),
        child: Row(
          children: [
            Container(
              width: 8,
              height: 8,
              decoration: BoxDecoration(
                color: done ? C.green : C.borderStrong,
                shape: BoxShape.circle,
              ),
            ),
            const SizedBox(width: 10),
            // Название шага важнее числа рядом с ним: без него строка не
            // читается вовсе. Раньше оба поля делили ширину поровну, и
            // длинная деталь ломала «Фундаментальный срез» на слоги.
            Expanded(
              flex: 3,
              child: Text(
                name,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: T.body(12, color: done ? C.textSoft : C.muted),
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              flex: 2,
              child: Text(
                detail.isNotEmpty ? detail : (done ? 'есть' : 'нет'),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                textAlign: TextAlign.right,
                style: T.mono(10.5, color: done ? C.green : C.faint),
              ),
            ),
          ],
        ),
      );
}

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

/// Пакеты капитала: состав, посчитанный движком (§6).
///
/// Шаблонов здесь нет по решению владельца от 30.07: пакет обязан собираться
/// после фундаментально-технического отбора и статистической проверки — на
/// данных, а не из заготовки. Всё, что показано ниже, приезжает с сервера:
/// доли, роли, тезисы, условия выхода и то, что состав уже переживал.
class _Packages extends StatefulWidget {
  const _Packages({required this.controller});

  final AppController controller;

  @override
  State<_Packages> createState() => _PackagesState();
}

/// Вариант пакета на экране: одна шкала от «сохранить» до «рискнуть».
///
/// Движок считает профили и размеры отдельными осями, но на экране это была
/// бы сетка из двенадцати вариантов с двумя переключателями — владелец
/// пришёл сюда не за настройкой оптимизатора. Оси сведены в одну.
///
/// Простой пакет — самый спокойный, максимальный — самый рисковый. В
/// падающий рынок спокойный состав собирается из денежного рынка и
/// облигаций: отдельного «доходного» варианта для этого не нужно, к нему
/// приводит сам оптимизатор — если такие бумаги дошли до отбора.
enum _Variant {
  simple('Простой', 'CONSERVATIVE', PackageSize.simple),
  balanced('Сбалансированный', 'OPTIMAL', PackageSize.balanced),
  maxPotential('Максимальный', 'AGGRESSIVE', PackageSize.maxPotential);

  const _Variant(this.label, this.profile, this.size);

  final String label;
  final String profile;
  final PackageSize size;
}

class _PackagesState extends State<_Packages> {
  /// Выбранный вариант пакета.
  _Variant _variant = _Variant.balanced;

  @override
  void initState() {
    super.initState();
    widget.controller.loadPortfolio();
  }

  @override
  Widget build(BuildContext context) {
    final controller = widget.controller;
    final portfolio = controller.portfolio;

    if (portfolio == null) {
      return _PackagesNote(
        title: 'Пакеты капитала',
        text: controller.portfolioLoading
            ? 'Спрашиваю движок…'
            : 'Состав считает сервер — запрос ещё не ушёл.',
        busy: controller.portfolioLoading,
      );
    }
    if (!portfolio.isAvailable) {
      return _PackagesNote(
        title: 'Движок не ответил',
        text: portfolio.unavailableReason!,
        onRetry: () => controller.loadPortfolio(force: true),
      );
    }

    final years = controller.packageHorizon.years;
    final onHorizon = portfolio.forHorizon(years);
    // Диагональ «профиль ↔ размер» — предпочтение, а не условие. Движок
    // считает девять сочетаний на горизонт, а вкладок три: требуя точного
    // совпадения, экран прятал шесть сочетаний из девяти. Так и вышло:
    // «прошли проверку 2» и пусто под ними.
    final pick = pickPackage(
      onHorizon,
      wantProfile: _variant.profile,
      wantSize: _variant.size,
    );
    final package = pick.package;
    // Какие варианты вообще собрались. Нужно, чтобы отличить «этот не прошёл
    // проверку» от «не посчитано ничего»: первое лечится другим вариантом,
    // второе — ничем, и владельцу нужна причина, а не совет.
    final ready = {
      for (final variant in _Variant.values)
        if (pickPackage(
              onHorizon,
              wantProfile: variant.profile,
              wantSize: variant.size,
            ).package !=
            null)
          variant,
    };
    // На какие горизонты составы всё-таки посчитаны.
    //
    // Без этого экран врал молчанием: конвейер зелёный, «составов 2», а под
    // ним пусто — и ни слова о том, что оба состава посчитаны на другой
    // горизонт. Владелец видел исправную машину, которая ничего не выдаёт,
    // и это худший вид пустого экрана: он не называет ни причины, ни
    // действия.
    final otherHorizons = {
      for (final p in portfolio.packages)
        if (p.horizonYears != years) p.horizonYears,
    }.toList()
      ..sort();

    return ListView(
      padding: _pad,
      children: [
        SegmentedControl(
          items: [for (final v in _Variant.values) v.label],
          index: _Variant.values.indexOf(_variant),
          onSelect: (i) => setState(() => _variant = _Variant.values[i]),
        ),
        const SizedBox(height: 12),
        if (package == null)
          _Progress(
            portfolio: portfolio,
            note: packageAbsenceNote(
              readyVariants: [for (final v in ready) v.label],
              otherHorizons: otherHorizons,
              horizonLabel: controller.packageHorizon.label,
              reason: portfolio.reason,
            ),
            onSwitchHorizon: ready.isEmpty && otherHorizons.isNotEmpty
                ? () => controller.setPackageHorizon(
                      controller.packageHorizon == PackageHorizon.oneYear
                          ? PackageHorizon.fivePlus
                          : PackageHorizon.oneYear,
                    )
                : null,
            onRetry: () => controller.loadPortfolio(force: true),
          )
        else ...[
          // Подмена не бывает молчаливой: «сбалансированный» и «простой» —
          // разные обещания по риску, и владелец обязан видеть, что смотрит
          // не на то, что выбрал.
          if (pick.note.isNotEmpty) ...[
            SectionCard(
              child: Text(
                pick.note,
                style: T.body(11.5, color: C.warning, height: 1.45),
              ),
            ),
            const SizedBox(height: 12),
          ],
          _PackageHead(package: package),
          const SizedBox(height: 12),
          _PackageComposition(package: package),
          const SizedBox(height: 12),
          _PackageEvidence(package: package),
          const SizedBox(height: 12),
          _SelectedPackageRebalance(
            controller: controller,
            package: package,
          ),
        ],
      ],
    );
  }
}

/// Ручные действия для приведения текущего счёта к выбранному пакету.
class _SelectedPackageRebalance extends StatefulWidget {
  const _SelectedPackageRebalance({
    required this.controller,
    required this.package,
  });

  final AppController controller;
  final EnginePackage package;

  @override
  State<_SelectedPackageRebalance> createState() =>
      _SelectedPackageRebalanceState();
}

class _SelectedPackageRebalanceState
    extends State<_SelectedPackageRebalance> {
  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void didUpdateWidget(covariant _SelectedPackageRebalance oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.package.id != widget.package.id) _load();
  }

  void _load() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      widget.controller.loadPortfolioRebalance(widget.package);
    });
  }

  @override
  Widget build(BuildContext context) {
    final controller = widget.controller;
    final modelId = widget.package.id;
    final active = controller.portfolioRebalanceModelId == modelId;
    final loading = controller.portfolioRebalanceLoadingModelId == modelId;
    final rebalance = active ? controller.portfolioRebalance : null;

    if (loading || rebalance == null) {
      return _PackagesNote(
        title: 'Мой счёт → этот пакет',
        text: 'Сверяю текущие позиции с выбранным составом…',
        busy: true,
      );
    }
    if (!rebalance.isAvailable) {
      return _PackagesNote(
        title: 'Ребаланс недоступен',
        text: rebalance.unavailableReason!,
        onRetry: () => controller.loadPortfolioRebalance(
          widget.package,
          force: true,
        ),
      );
    }
    final economicsBlocked = rebalance.needed && !rebalance.actionable;

    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Expanded(child: SectionLabel('МОЙ СЧЁТ → ЭТОТ ПАКЕТ')),
              OutlineBadge(
                label: economicsBlocked
                    ? 'экономика не готова'
                    : rebalance.urgent
                    ? 'срочно'
                    : (rebalance.needed ? 'нужно поправить' : 'в норме'),
                color: economicsBlocked
                    ? C.red
                    : (rebalance.needed ? C.warning : C.green),
                borderColor: economicsBlocked
                    ? C.red
                    : (rebalance.needed ? C.warningBorder : C.greenBorder),
                background: economicsBlocked
                    ? C.redFaint
                    : (rebalance.needed ? C.warningFaint : C.greenFaint),
                fontWeight: 700,
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            rebalance.reason.isNotEmpty
                ? rebalance.reason
                : (rebalance.needed
                    ? 'Есть отклонения от выбранного состава.'
                    : 'Отклонения ниже порога ребаланса.'),
            style: T.body(11.5, color: C.muted, height: 1.45),
          ),
          const SizedBox(height: 10),
          _RebalanceEconomicsSummary(rebalance: rebalance),
          if (rebalance.actions.isNotEmpty) ...[
            const SizedBox(height: 10),
            for (final action in rebalance.actions) ...[
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          action.symbol.isEmpty
                              ? action.instrumentId
                              : action.symbol,
                          style: T.body(12.5, weight: 700),
                        ),
                        const SizedBox(height: 2),
                        Text(
                          '${(action.actualWeight * 100).toStringAsFixed(1)}% → '
                          '${(action.targetWeight * 100).toStringAsFixed(1)}% · '
                          '${action.reason}',
                          style: T.mono(10.5, color: C.muted),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(width: 10),
                  Text(
                    rebalance.actionable && action.actionable
                        ? '${action.side == 'BUY' ? 'Купить' : 'Сократить'} '
                            '${(action.orderNotionalRub ?? action.amountRub).round()} ₽'
                        : 'Не готово к ручной операции',
                    style: T.mono(
                      11.5,
                      weight: 700,
                      color: rebalance.actionable && action.actionable
                          ? (action.side == 'BUY' ? C.green : C.warning)
                          : C.red,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 4),
              Text(
                _actionEconomicsText(action),
                style: T.mono(10, color: C.faint, height: 1.35),
              ),
              if (action.economicsBlockers.isNotEmpty) ...[
                const SizedBox(height: 3),
                Text(
                  'Блокировка: ${action.economicsBlockers.join(', ')}',
                  style: T.mono(10, color: C.red, height: 1.35),
                ),
              ],
              if (action.economicsProvenance.isNotEmpty) ...[
                const SizedBox(height: 3),
                Text(
                  'Источник: ${action.economicsProvenance.entries.map((item) => '${item.key}=${item.value}').join(' · ')}',
                  style: T.mono(9.5, color: C.faint, height: 1.35),
                ),
              ],
              const SizedBox(height: 8),
            ],
          ],
          const SizedBox(height: 2),
          Text(
            'Только подсказка для ручного исполнения — заявок этот экран не отправляет.',
            style: T.mono(9.5, color: C.faint, height: 1.35),
          ),
        ],
      ),
    );
  }
}

class _RebalanceEconomicsSummary extends StatelessWidget {
  const _RebalanceEconomicsSummary({required this.rebalance});

  final PortfolioRebalance rebalance;

  @override
  Widget build(BuildContext context) {
    final finalAmounts = rebalance.economicsStatus == 'BROKER_FINAL';
    final costs = finalAmounts
        ? rebalance.brokerFinalCostsRub
        : rebalance.estimatedCostsRub;
    final tax = finalAmounts
        ? rebalance.brokerFinalTaxRub
        : rebalance.estimatedTaxRub;
    final blocked = rebalance.needed && !rebalance.actionable;
    return InsetBox(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            !rebalance.needed
                ? 'ЭКОНОМИКА НЕ ТРЕБУЕТСЯ'
                : blocked
                ? 'ЭКОНОМИКА НЕ ГОТОВА — РУЧНОЕ ДЕЙСТВИЕ ЗАБЛОКИРОВАНО'
                : 'ЭКОНОМИКА ЧЕРНОВИКА',
            style: T.microLabel(color: blocked ? C.red : C.info),
          ),
          const SizedBox(height: 5),
          Text(
            'Статус: ${_economicsStatusLabel(rebalance.economicsStatus)} · '
            'комиссия: ${_rubOrUnknown(costs)} · налог: ${_rubOrUnknown(tax)}',
            style: T.mono(10.5, color: blocked ? C.red : C.muted, height: 1.4),
          ),
          if (blocked) ...[
            const SizedBox(height: 4),
            Text(
              'Суммы нулём не подставляются: проверьте blockers и provenance по каждому действию.',
              style: T.body(10, color: C.faint, height: 1.35),
            ),
          ],
        ],
      ),
    );
  }
}

String _actionEconomicsText(PortfolioRebalanceAction action) {
  final finalAmounts = action.economicsStatus == 'BROKER_FINAL';
  final costs =
      finalAmounts ? action.brokerFinalCostsRub : action.estimatedCostsRub;
  final tax = finalAmounts ? action.brokerFinalTaxRub : action.estimatedTaxRub;
  return 'Статус: ${_economicsStatusLabel(action.economicsStatus)} · '
      'кол-во: ${_numberOrUnknown(action.orderQuantity)} · '
      'notional: ${_rubOrUnknown(action.orderNotionalRub)} · '
      'комиссия: ${_rubOrUnknown(costs)} · налог: ${_rubOrUnknown(tax)}';
}

String _economicsStatusLabel(String status) => switch (status) {
      'BROKER_FINAL' => 'факт брокера',
      'ESTIMATED' => 'оценка',
      _ => 'неизвестно',
    };

String _numberOrUnknown(double? value) =>
    value == null ? 'неизвестно' : value.toStringAsFixed(4);

String _rubOrUnknown(double? value) =>
    value == null ? 'неизвестно' : '${value.toStringAsFixed(2)} ₽';

/// Шапка пакета: диапазон доходности, риск, полоса состава.
class _PackageHead extends StatelessWidget {
  const _PackageHead({required this.package});

  final EnginePackage package;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(child: SectionLabel(package.size.label.toUpperCase())),
                if (package.isStale)
                  OutlineBadge(
                    label: 'протух',
                    color: C.warning,
                    borderColor: C.warningBorder,
                    background: C.warningFaint,
                    fontWeight: 700,
                  )
                else if (!package.meetsTarget)
                  // Состав годен, но рискованнее, чем обещает профиль. Прятать
                  // его было бы хуже: числа риска нужны владельцу именно
                  // тогда, когда они больше ожидаемых.
                  OutlineBadge(
                    label: 'риск выше цели',
                    color: C.warning,
                    borderColor: C.warningBorder,
                    background: C.warningFaint,
                    fontWeight: 700,
                  )
                else
                  OutlineBadge(
                    label: '${package.positions.length} позиций',
                    color: C.info,
                    borderColor: C.infoBorder,
                    background: C.infoFaint,
                    fontWeight: 700,
                  ),
              ],
            ),
            const SizedBox(height: 10),
            ExpectedBand(low: package.expectedLow, high: package.expectedHigh),
            const SizedBox(height: 4),
            Text(
              'разброс по окнам проверки на истории',
              style: T.body(10.5, color: C.faint),
            ),
            const SizedBox(height: 14),
            MixBar(mix: package.mix),
            const SizedBox(height: 10),
            MixLegend(mix: package.mix),
          ],
        ),
      );
}

/// Состав: позиции полосами. Нажатие раскрывает тезис и условия выхода.
class _PackageComposition extends StatelessWidget {
  const _PackageComposition({required this.package});

  final EnginePackage package;

  @override
  Widget build(BuildContext context) {
    final maximum = package.positions.isEmpty
        ? 0.0
        : package.positions.map((p) => p.weight).reduce((a, b) => a > b ? a : b);
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('Состав'),
          const SizedBox(height: 6),
          for (final position in package.positions)
            PositionRow(position: position, maxWeight: maximum),
          const SizedBox(height: 4),
          Text(
            'Нажмите позицию — покажу, за что она здесь и когда её выкидывать.',
            style: T.body(10.5, color: C.faint),
          ),
        ],
      ),
    );
  }
}

String _day(DateTime at) {
  final local = at.toLocal();
  return '${local.day.toString().padLeft(2, '0')}.'
      '${local.month.toString().padLeft(2, '0')}.${local.year}';
}

/// Чем состав подтверждён: риск, стресс, способ счёта.
class _PackageEvidence extends StatelessWidget {
  const _PackageEvidence({required this.package});

  final EnginePackage package;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('Чем подтверждён'),
            const SizedBox(height: 8),
            TileGrid(
              minTileWidth: 118,
              tiles: [
                MetricTile(
                  label: 'Колебания',
                  value: sharePercent(package.volatility, decimals: 1),
                  hint: 'годовых',
                ),
                MetricTile(
                  label: 'Просадка',
                  value: sharePercent(package.drawdown, decimals: 1),
                  color: C.red,
                  hint: 'вне обучения',
                ),
                if (package.cvar95 != null)
                  MetricTile(
                    label: 'Хвост 5%',
                    value: sharePercent(package.cvar95!, decimals: 1),
                    color: C.red,
                    hint: 'средний убыток худших дней',
                  ),
              ],
            ),
            if (package.stress.isNotEmpty) ...[
              const SizedBox(height: 10),
              const SectionLabel('Что уже случалось', color: C.faint),
              const SizedBox(height: 6),
              StressTiles(stress: package.stress),
            ],
            if (package.warnings.isNotEmpty) ...[
              const SizedBox(height: 10),
              for (final warning in package.warnings)
                Padding(
                  padding: const EdgeInsets.only(bottom: 4),
                  child: Text(
                    '· $warning',
                    style: T.body(11, color: C.warning, height: 1.45),
                  ),
                ),
            ],
            if (package.rationale.isNotEmpty) ...[
              const SizedBox(height: 12),
              Text(
                package.rationale,
                style: T.body(11, color: C.faint, height: 1.45),
              ),
            ],
            const SizedBox(height: 8),
            Text(
              'Пересчитан ${_day(package.generatedAt)}, '
              'действует до ${_day(package.validUntil)}',
              style: T.mono(10, color: C.dim),
            ),
          ],
        ),
      );
}

/// Прогресс сборки: где именно стоит работа.
///
/// Владелец спросил «если собираются, то где прогресс?» — значит на экране
/// должен быть прогресс, а не обещание. Шаги приезжают с сервера и отмечены
/// по факту: «есть» означает, что результат шага лежит в базе.
class _Progress extends StatelessWidget {
  const _Progress({
    required this.portfolio,
    required this.note,
    required this.onRetry,
    this.onSwitchHorizon,
  });

  final PortfolioState portfolio;
  final String note;
  final VoidCallback onRetry;

  /// Переключить горизонт, если составы посчитаны на другой. null — некуда.
  final VoidCallback? onSwitchHorizon;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Expanded(child: SectionLabel('Сборка состава')),
                OutlineBadge(
                  label: 'считает движок',
                  color: C.info,
                  borderColor: C.infoBorder,
                  background: C.infoFaint,
                  fontWeight: 700,
                ),
              ],
            ),
            // Причина пустоты — первой строкой, а не подписью под таблицей
            // шагов. Владелец пришёл за составом и не нашёл его: он ищет
            // ответ на «почему», а не список зелёных галочек.
            if (note.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text(note, style: T.body(11.5, color: C.text, height: 1.45)),
              if (onSwitchHorizon != null) ...[
                const SizedBox(height: 10),
                ActionButton(
                  label: 'Показать посчитанный горизонт',
                  onTap: onSwitchHorizon!,
                  dense: true,
                ),
              ],
            ],
            const SizedBox(height: 10),
            for (final stage in portfolio.stages)
              _Stage(done: stage.done, name: stage.name, detail: stage.detail),
            if (portfolio.universeMix.isNotEmpty) ...[
              const SizedBox(height: 10),
              const SectionLabel('Из чего собирается', color: C.faint),
              const SizedBox(height: 6),
              // Состав вселенной по классам. «В отборе только акции и ОФЗ»
              // без этой таблицы — загадка: то ли фонды не пришли с биржи,
              // то ли не прошли срез, то ли у них нет истории. Три разные
              // поломки с одинаковым экраном.
              for (final slice in portfolio.universeMix)
                _Stage(
                  done: slice.withHistory > 0,
                  name: slice.label,
                  detail: '${slice.withHistory} из ${slice.total} с историей',
                ),
            ],
            if (portfolio.universeNotes.isNotEmpty) ...[
              const SizedBox(height: 8),
              // Целый класс активов не может пропадать беззвучно. Если доска
              // биржи не ответила или её отсёк порог оборота — это видно
              // здесь, а не выясняется разбором скриншотов.
              for (final note in portfolio.universeNotes)
                Padding(
                  padding: const EdgeInsets.only(bottom: 4),
                  child: Text(
                    '· $note',
                    style: T.body(10.5, color: C.warning, height: 1.4),
                  ),
                ),
            ],
            if (portfolio.jobs.isNotEmpty) ...[
              const SizedBox(height: 10),
              const SectionLabel('Чем занят движок', color: C.faint),
              const SizedBox(height: 6),
              // Пустой экран одинаково выглядит и когда расчёт идёт прямо
              // сейчас, и когда он не запускался третий день. Итоги задач
              // планировщика различают эти два случая.
              for (final job in portfolio.jobs)
                Padding(
                  padding: const EdgeInsets.only(bottom: 3),
                  child: Text(
                    job,
                    style: T.mono(10, color: C.dim, height: 1.4),
                  ),
                ),
            ],
            const SizedBox(height: 12),
            ActionButton(label: 'Спросить ещё раз', onTap: onRetry),
          ],
        ),
      );
}

/// Короткое состояние экрана: ждём ответа либо движок не ответил.
class _PackagesNote extends StatelessWidget {
  const _PackagesNote({
    required this.title,
    required this.text,
    this.busy = false,
    this.onRetry,
  });

  final String title;
  final String text;
  final bool busy;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) => ListView(
        padding: _pad,
        children: [
          SectionCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SectionLabel(title),
                const SizedBox(height: 8),
                if (busy)
                  const BusyLine(label: 'считаю')
                else
                  Text(text, style: T.body(12, color: C.textSecondary, height: 1.45)),
                if (onRetry != null) ...[
                  const SizedBox(height: 12),
                  ActionButton(label: 'Спросить ещё раз', onTap: onRetry!),
                ],
              ],
            ),
          ),
        ],
      );
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
