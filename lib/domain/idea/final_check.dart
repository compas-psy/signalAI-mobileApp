import 'idea.dart';
import 'quality_score.dart';
import 'trade_plan.dart';

/// Проверка из таблицы ТЗ §11.1.
enum CheckKind {
  ideaVersion('Версия идеи', 'План изменился с момента показа'),
  ttl('Срок сигнала', 'Сигнал живёт ограниченное время'),
  price('Цена', 'Рынок ушёл из зоны входа или проскальзывание больше допуска'),
  riskReward('R/R', 'Отношение до второй цели ниже порога исполнения'),
  riskLimits('Лимиты риска', 'День, неделя, месяц, открытый риск, кластер'),
  sizing('Объём и стоп', 'Без стопа и полного расчёта объёма подтверждать нечего'),
  margin('Маржа', 'Свободных средств не хватает на позицию'),
  correlation('Корреляция', 'Риск кластера после сделки выше 1,25%'),
  eventWindow('Окно события', 'Политика события запрещает вход'),
  marketState('Состояние рынка', 'Инструмент не торгуется или стакан деградировал'),
  protectiveOrders('Защитные заявки', 'Брокер не гарантирует постановку стопа'),
  state('Состояние идеи', 'Подтверждать можно только сработавший сигнал');

  const CheckKind(this.label, this.meaning);

  final String label;
  final String meaning;
}

/// Результат одной проверки.
class CheckResult {
  const CheckResult.pass(this.kind, [this.detail = ''])
      : passed = true,
        skipped = false;
  const CheckResult.fail(this.kind, this.detail)
      : passed = false,
        skipped = false;

  /// Проверка не выполнялась: нет данных, чтобы её выполнить.
  ///
  /// Это не «пройдено». ТЗ §27 запрещает выдавать отсутствие данных за
  /// норму, поэтому непроверенное блокирует подтверждение так же, как
  /// проваленное, но называется иначе.
  const CheckResult.unknown(this.kind, this.detail)
      : passed = false,
        skipped = true;

  final CheckKind kind;
  final bool passed;
  final bool skipped;
  final String detail;
}

/// Живое состояние рынка и брокера на момент подтверждения.
class ExecutionContext {
  const ExecutionContext({
    required this.now,
    required this.lastPrice,
    required this.budget,
    required this.freeMargin,
    required this.clusterRiskAfterPercent,
    required this.shownPlanHash,
    this.tradingOpen = true,
    this.orderBookHealthy = true,
    this.protectiveOrdersSupported = true,
    this.marketStateNote = '',
    this.paperMode = false,
  });

  final DateTime now;

  /// Текущая цена. null — котировки нет, проверить вход нечем.
  final double? lastPrice;

  final RiskBudget budget;

  /// Свободная маржа в рублях. null — брокер не ответил.
  final double? freeMargin;

  /// Риск коррелированного кластера после этой сделки, % капитала.
  /// null — состав кластера неизвестен.
  final double? clusterRiskAfterPercent;

  /// Отпечаток плана, который видел владелец на экране (ТЗ §11.1).
  final String shownPlanHash;

  final bool tradingOpen;
  final bool orderBookHealthy;
  final bool protectiveOrdersSupported;
  final String marketStateNote;

  /// Бумажный режим: заявки на биржу не уходят.
  ///
  /// Тогда маржа и защитные заявки — не «неизвестно», а «не применяется»:
  /// резервировать нечего и ставить стоп негде. Выдавать отсутствие брокера
  /// за отказ проверки значило бы заблокировать всё приложение навсегда.
  final bool paperMode;
}

/// Финальная проверка перед подтверждением плана (ТЗ §11.1).
///
/// Возвращает все результаты, а не первый отказ: владелец должен видеть
/// полную картину — иначе он чинит одно препятствие за другим вслепую.
abstract final class FinalCheck {
  static List<CheckResult> run(Idea idea, ExecutionContext ctx) {
    final results = <CheckResult>[];
    final plan = idea.plan;

    // Состояние. Подтверждать можно только Triggered (ТЗ §8).
    results.add(idea.state.canConfirm
        ? CheckResult.pass(CheckKind.state, idea.state.label)
        : CheckResult.fail(
            CheckKind.state, 'состояние ${idea.state.label} — подтверждать нечего'));

    if (plan == null) {
      results.add(const CheckResult.fail(
          CheckKind.sizing, 'плана нет: зона входа и стоп не определены'));
      return results;
    }

    // Версия идеи. Отпечаток обязан совпасть с показанным.
    results.add(plan.hash == ctx.shownPlanHash
        ? CheckResult.pass(CheckKind.ideaVersion, plan.hash)
        : CheckResult.fail(CheckKind.ideaVersion,
            'план пересчитан: показан ${ctx.shownPlanHash}, сейчас ${plan.hash}'));

    // Срок.
    final expired = idea.isExpiredAt(ctx.now) || plan.isExpired(ctx.now);
    results.add(expired
        ? const CheckResult.fail(CheckKind.ttl, 'срок сигнала истёк')
        : CheckResult.pass(
            CheckKind.ttl, 'осталось ${_left(idea.remaining(ctx.now))}'));

    // Цена и проскальзывание.
    final price = ctx.lastPrice;
    if (price == null) {
      results.add(const CheckResult.unknown(
          CheckKind.price, 'котировки нет — вход проверить нечем'));
    } else {
      final allowed = plan.entry * plan.maxSlippagePercent / 100;
      final inZone = price >= plan.entryLow - allowed && price <= plan.entryHigh + allowed;
      results.add(inZone
          ? CheckResult.pass(CheckKind.price, _num(price))
          : CheckResult.fail(CheckKind.price,
              'цена ${_num(price)} вне зоны ${_num(plan.entryLow)}–${_num(plan.entryHigh)} '
              'с допуском ${plan.maxSlippagePercent.toStringAsFixed(2).replaceAll('.', ',')}%'));
    }

    // Структура плана: стоп, доли, объём, R/R (ТЗ §13.1, §20).
    final structural = plan.structuralBlockers();
    final rrIssue = structural.where((s) => s.contains('R/R')).toList();
    final sizingIssues = structural.where((s) => !s.contains('R/R')).toList();

    results.add(rrIssue.isEmpty
        ? CheckResult.pass(CheckKind.riskReward,
            'до TP2 ${_r(plan.rrToSecondTarget)}R при пороге ${_r(TradePlan.executionFloorRr)}R')
        : CheckResult.fail(CheckKind.riskReward, rrIssue.first));

    results.add(sizingIssues.isEmpty
        ? CheckResult.pass(CheckKind.sizing,
            '${plan.quantity} ед., риск ${_money(plan.riskRubles)} ₽')
        : CheckResult.fail(CheckKind.sizing, sizingIssues.join('; ')));

    // Лимиты риска.
    final budget = ctx.budget;
    if (budget.blocked) {
      results.add(CheckResult.fail(
          CheckKind.riskLimits, '${budget.binding.name} исчерпан'));
    } else if (plan.riskPercent > budget.percent + 1e-9) {
      results.add(CheckResult.fail(
          CheckKind.riskLimits,
          'риск ${_r(plan.riskPercent)}% выше доступного ${_r(budget.percent)}% '
          '(${budget.binding.name})'));
    } else {
      results.add(CheckResult.pass(CheckKind.riskLimits,
          '${_r(plan.riskPercent)}% из ${_r(budget.percent)}% доступных'));
    }

    // Оценка ниже порога торговли не даёт риска вовсе (ТЗ §14.2).
    if (!idea.score.tradable) {
      results.add(CheckResult.fail(CheckKind.riskLimits,
          'оценка ${idea.score.value} ниже ${QualityScore.minimumToTrade} — только наблюдение'));
    }

    // Маржа.
    final margin = ctx.freeMargin;
    if (ctx.paperMode) {
      results.add(const CheckResult.pass(
          CheckKind.margin, 'бумажный режим: маржа не резервируется'));
    } else if (margin == null) {
      results.add(const CheckResult.unknown(
          CheckKind.margin, 'брокер не сообщил свободную маржу'));
    } else {
      results.add(margin >= plan.marginEstimate
          ? CheckResult.pass(CheckKind.margin,
              '${_money(margin)} ₽ при требуемых ${_money(plan.marginEstimate)} ₽')
          : CheckResult.fail(CheckKind.margin,
              'свободно ${_money(margin)} ₽, нужно ${_money(plan.marginEstimate)} ₽'));
    }

    // Корреляция.
    final cluster = ctx.clusterRiskAfterPercent;
    if (cluster == null) {
      results.add(const CheckResult.unknown(
          CheckKind.correlation, 'состав кластера неизвестен'));
    } else {
      results.add(cluster <= RiskBudget.clusterRiskPercent + 1e-9
          ? CheckResult.pass(CheckKind.correlation, '${_r(cluster)}% после сделки')
          : CheckResult.fail(CheckKind.correlation,
              'кластер ${_r(cluster)}% выше ${_r(RiskBudget.clusterRiskPercent)}%'));
    }

    // Окно события.
    final event = idea.eventRisk;
    if (event == null) {
      results.add(const CheckResult.pass(CheckKind.eventWindow, 'событий рядом нет'));
    } else if (event.blocksEntry) {
      results.add(CheckResult.fail(CheckKind.eventWindow,
          '${event.title}: ${event.policy}'));
    } else {
      results.add(CheckResult.pass(
          CheckKind.eventWindow, '${event.title} — ${event.policy}'));
    }

    // Состояние рынка.
    if (!ctx.tradingOpen) {
      results.add(CheckResult.fail(CheckKind.marketState,
          ctx.marketStateNote.isEmpty ? 'инструмент не торгуется' : ctx.marketStateNote));
    } else if (!ctx.orderBookHealthy) {
      results.add(CheckResult.fail(CheckKind.marketState,
          ctx.marketStateNote.isEmpty ? 'стакан деградировал' : ctx.marketStateNote));
    } else {
      results.add(const CheckResult.pass(CheckKind.marketState, 'торги идут'));
    }

    // Защитные заявки.
    results.add(ctx.paperMode
        ? const CheckResult.pass(
            CheckKind.protectiveOrders, 'бумажный режим: стоп ведётся расчётом')
        : ctx.protectiveOrdersSupported
        ? const CheckResult.pass(CheckKind.protectiveOrders, 'стоп ставится у брокера')
        : const CheckResult.fail(CheckKind.protectiveOrders,
            'брокер не гарантирует постановку стопа — вход без защиты запрещён'));

    return results;
  }

  /// Пропускает ли проверка к подтверждению.
  ///
  /// Непроверенное блокирует наравне с проваленным: «не знаю» — не «да».
  static bool passes(List<CheckResult> results) =>
      results.every((r) => r.passed);

  static List<CheckResult> blockers(List<CheckResult> results) =>
      results.where((r) => !r.passed).toList();

  static String _r(double v) => v.toStringAsFixed(2).replaceAll('.', ',');

  static String _num(double v) =>
      v.toStringAsFixed(2).replaceAll('.', ',');

  static String _money(double v) => v
      .round()
      .toString()
      .replaceAllMapped(RegExp(r'\B(?=(\d{3})+(?!\d))'), (_) => ' ');

  static String _left(Duration d) {
    if (d.isNegative) return '0 мин';
    if (d.inHours >= 1) return '${d.inHours} ч ${d.inMinutes % 60} мин';
    return '${d.inMinutes} мин';
  }
}
