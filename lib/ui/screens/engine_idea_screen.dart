import 'package:flutter/widgets.dart';

import '../../data/api/api_client.dart';
import '../../domain/idea/idea.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../tone.dart';
import '../widgets/common.dart';

/// Открывает разбор именно той серверной идеи, по которой нажал пользователь.
///
/// Прежний путь сохранял ID серверной идеи, а старый экран искал этот ID в
/// локальном дайджесте. Если совпадения не было, он молча показывал первый
/// локальный сигнал. Для торгового приложения это недопустимо: нажатие на один
/// инструмент могло открыть план другого.
void openEngineIdea(BuildContext context, Idea summary) {
  Navigator.of(context).push(
    PageRouteBuilder<void>(
      pageBuilder: (_, __, ___) => EngineIdeaScreen(summary: summary),
      transitionsBuilder: (_, animation, __, child) => FadeTransition(
        opacity: animation,
        child: child,
      ),
      transitionDuration: const Duration(milliseconds: 180),
    ),
  );
}

/// Пользовательский разбор идеи из единственного источника истины — Engine API.
///
/// Экран намеренно не показывает кнопку отправки заявки. Серверный endpoint
/// paper-исполнения в текущей версии отвечает 503, а подменять его локальным
/// брокерским сигналом опасно. Пока исполнение не реализовано end-to-end,
/// приложение остаётся честным аналитическим терминалом.
class EngineIdeaScreen extends StatefulWidget {
  const EngineIdeaScreen({super.key, required this.summary});

  final Idea summary;

  @override
  State<EngineIdeaScreen> createState() => _EngineIdeaScreenState();
}

class _EngineIdeaScreenState extends State<EngineIdeaScreen> {
  late final ApiClient _api = ApiClient();
  late Future<Map<String, dynamic>> _detail = _load();

  Future<Map<String, dynamic>> _load() =>
      _api.get('/api/v1/ideas/${widget.summary.id}');

  void _retry() => setState(() => _detail = _load());

  @override
  void dispose() {
    _api.close();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => ColoredBox(
        color: C.bg,
        child: SafeArea(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              _Header(summary: widget.summary),
              Expanded(
                child: FutureBuilder<Map<String, dynamic>>(
                  future: _detail,
                  builder: (context, snapshot) {
                    if (snapshot.connectionState != ConnectionState.done) {
                      return const Center(
                        child: Padding(
                          padding: EdgeInsets.all(28),
                          child: BusyLine(
                            label: 'Загружаем план и расчёты этой идеи…',
                          ),
                        ),
                      );
                    }
                    if (snapshot.hasError || snapshot.data == null) {
                      return _Error(
                        text: snapshot.error.toString(),
                        onRetry: _retry,
                      );
                    }
                    return _Body(summary: widget.summary, json: snapshot.data!);
                  },
                ),
              ),
            ],
          ),
        ),
      );
}

class _Header extends StatelessWidget {
  const _Header({required this.summary});

  final Idea summary;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 11),
        decoration: const BoxDecoration(
          color: C.headerBg,
          border: Border(bottom: BorderSide(color: C.dividerSoft)),
        ),
        child: Row(
          children: [
            Pressable(
              onTap: () => Navigator.of(context).pop(),
              child: Padding(
                padding: const EdgeInsets.fromLTRB(0, 8, 14, 8),
                child: Text('←', style: T.jost(22, color: C.accent)),
              ),
            ),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    summary.instrumentName.isEmpty
                        ? summary.instrumentId
                        : summary.instrumentName,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: T.jost(19),
                  ),
                  const SizedBox(height: 3),
                  Text(
                    '${summary.strategy.label} · ${summary.timeframes.join(" / ")}',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: T.body(11, color: C.muted),
                  ),
                ],
              ),
            ),
            const SizedBox(width: 8),
            DirectionBadge(
              label: summary.direction.label,
              color: directionColor(summary.direction),
              background: directionBackground(summary.direction),
            ),
          ],
        ),
      );
}

class _Body extends StatelessWidget {
  const _Body({required this.summary, required this.json});

  final Idea summary;
  final Map<String, dynamic> json;

  @override
  Widget build(BuildContext context) {
    final plan = _map(json['plan']);
    final probability = _map(json['probability']);
    final sizing = _map(json['sizing']);
    final explanation = _map(json['explanation']);
    final support = _strings(explanation['supporting_factors']);
    final counter = _strings(explanation['counter_factors']);
    final warnings = _strings(explanation['data_warnings']);
    final evidence = _maps(json['evidence']);

    return ListView(
      padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 28),
      children: [
        _DecisionCard(summary: summary, probability: probability),
        const SizedBox(height: 12),
        _Metrics(probability: probability, json: json),
        const SizedBox(height: 12),
        _Plan(plan: plan, sizing: sizing),
        const SizedBox(height: 12),
        _Narrative(
          headline: explanation['headline']?.toString() ?? '',
          thesis: explanation['thesis']?.toString() ?? summary.thesis,
          support: support,
          counter: counter,
          invalidation: plan['invalidation']?.toString() ?? '',
        ),
        if (evidence.isNotEmpty) ...[
          const SizedBox(height: 12),
          _Evidence(items: evidence),
        ],
        if (warnings.isNotEmpty) ...[
          const SizedBox(height: 12),
          _Warnings(items: warnings),
        ],
        const SizedBox(height: 12),
        const _ExecutionNotice(),
      ],
    );
  }
}

class _DecisionCard extends StatelessWidget {
  const _DecisionCard({required this.summary, required this.probability});

  final Idea summary;
  final Map<String, dynamic> probability;

  @override
  Widget build(BuildContext context) {
    final ready = summary.state.needsAttention && summary.plan != null;
    final title = ready ? 'План сформирован' : 'Ждём подтверждения сетапа';
    final note = ready
        ? 'Уровни и риск рассчитаны сервером. Проверьте факторы против и срок идеи.'
        : 'Это наблюдение, а не готовая сделка. Вход до появления триггера не предусмотрен.';
    final p = _ratio(probability['p_tp1_before_sl']);

    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(child: Text(title, style: T.jost(17))),
              OutlineBadge(
                label: summary.state.label,
                color: ready ? C.accent : C.muted,
                borderColor: ready ? C.accent : C.borderHover,
                background: ready ? C.accentFaint : C.inset,
                fontWeight: 800,
              ),
            ],
          ),
          const SizedBox(height: 7),
          Text(note, style: T.body(12, color: C.textSecondary, height: 1.45)),
          if (p != null) ...[
            const SizedBox(height: 9),
            Text(
              'Вероятность TP1 раньше стопа: ${_percent(p)}',
              style: T.body(12, weight: 800, color: C.accent),
            ),
          ],
        ],
      ),
    );
  }
}

class _Metrics extends StatelessWidget {
  const _Metrics({required this.probability, required this.json});

  final Map<String, dynamic> probability;
  final Map<String, dynamic> json;

  @override
  Widget build(BuildContext context) {
    final p = _ratio(probability['p_tp1_before_sl']);
    final confidence = _ratio(probability['confidence']);
    final ev = _number(probability['expected_r']);
    final rr = _number(json['rr_tp2']);

    return Row(
      children: [
        Expanded(child: _Metric(label: 'TP1 раньше SL', value: _percentOrDash(p))),
        const SizedBox(width: 8),
        Expanded(child: _Metric(label: 'Надёжность', value: _percentOrDash(confidence))),
        const SizedBox(width: 8),
        Expanded(child: _Metric(label: 'Матожидание', value: ev == null ? '—' : '${ev >= 0 ? '+' : ''}${_decimal(ev)}R')),
        const SizedBox(width: 8),
        Expanded(child: _Metric(label: 'R/R до TP2', value: rr == null ? '—' : _decimal(rr))),
      ],
    );
  }
}

class _Metric extends StatelessWidget {
  const _Metric({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) => SectionCard(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 11),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(value, maxLines: 1, style: T.mono(13, weight: 800, color: C.accent)),
            const SizedBox(height: 4),
            Text(label, maxLines: 2, style: T.body(9.5, color: C.muted, height: 1.25)),
          ],
        ),
      );
}

class _Plan extends StatelessWidget {
  const _Plan({required this.plan, required this.sizing});

  final Map<String, dynamic> plan;
  final Map<String, dynamic> sizing;

  @override
  Widget build(BuildContext context) {
    if (plan.isEmpty) {
      return SectionCard(
        child: Text(
          'Торговый план ещё не сформирован. Это идея наблюдения, входить по ней рано.',
          style: T.body(12, color: C.muted, height: 1.45),
        ),
      );
    }
    final entryLow = _number(plan['entry_low']);
    final entryHigh = _number(plan['entry_high']);
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('Торговый план'),
          const SizedBox(height: 9),
          _Row('Вход', _entry(entryLow, entryHigh)),
          _Row('Стоп', _price(plan['stop'])),
          _Row('TP1', _price(plan['tp1'])),
          _Row('TP2', _price(plan['tp2'])),
          if (plan['tp3'] != null) _Row('TP3', _price(plan['tp3'])),
          const SizedBox(height: 5),
          _Row('Размер', _quantity(sizing['quantity'])),
          _Row('Риск', _rub(sizing['risk_amount'])),
          _Row('Риск счёта', _percentOrDash(_ratio(sizing['risk_pct']))),
        ],
      ),
    );
  }
}

class _Narrative extends StatelessWidget {
  const _Narrative({
    required this.headline,
    required this.thesis,
    required this.support,
    required this.counter,
    required this.invalidation,
  });

  final String headline;
  final String thesis;
  final List<String> support;
  final List<String> counter;
  final String invalidation;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('Почему эта идея существует'),
            if (headline.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text(headline, style: T.jost(16)),
            ],
            if (thesis.isNotEmpty) ...[
              const SizedBox(height: 6),
              Text(thesis, style: T.body(12, color: C.textSecondary, height: 1.5)),
            ],
            if (support.isNotEmpty) ...[
              const SizedBox(height: 12),
              const SectionLabel('За', color: C.green),
              const SizedBox(height: 5),
              for (final item in support) _Bullet(text: item, color: C.green),
            ],
            if (counter.isNotEmpty) ...[
              const SizedBox(height: 12),
              const SectionLabel('Против', color: C.warning),
              const SizedBox(height: 5),
              for (final item in counter) _Bullet(text: item, color: C.warning),
            ],
            if (invalidation.isNotEmpty) ...[
              const SizedBox(height: 12),
              const SectionLabel('Когда идея сломана', color: C.red),
              const SizedBox(height: 5),
              Text(invalidation, style: T.body(12, color: C.textSecondary, height: 1.45)),
            ],
          ],
        ),
      );
}

class _Evidence extends StatelessWidget {
  const _Evidence({required this.items});

  final List<Map<String, dynamic>> items;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('Подтверждения'),
            const SizedBox(height: 8),
            for (var i = 0; i < items.length; i++) ...[
              Text(
                items[i]['summary']?.toString().isNotEmpty == true
                    ? items[i]['summary'].toString()
                    : items[i]['kind']?.toString() ?? 'Фактор',
                style: T.body(12, weight: 700),
              ),
              if ((items[i]['detail']?.toString() ?? '').isNotEmpty) ...[
                const SizedBox(height: 3),
                Text(
                  items[i]['detail'].toString(),
                  style: T.body(11, color: C.muted, height: 1.4),
                ),
              ],
              if (i != items.length - 1)
                const Padding(
                  padding: EdgeInsets.symmetric(vertical: 9),
                  child: ColoredBox(color: C.dividerSoft, child: SizedBox(height: 1)),
                ),
            ],
          ],
        ),
      );
}

class _Warnings extends StatelessWidget {
  const _Warnings({required this.items});

  final List<String> items;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('Ограничения данных', color: C.warning),
            const SizedBox(height: 6),
            for (final item in items) _Bullet(text: item, color: C.warning),
          ],
        ),
      );
}

class _ExecutionNotice extends StatelessWidget {
  const _ExecutionNotice();

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.all(13),
        decoration: BoxDecoration(
          color: C.inset,
          border: Border.all(color: C.warning.withValues(alpha: 0.45)),
          borderRadius: BorderRadius.circular(R.button),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Заявка отсюда не отправляется', style: T.body(12.5, weight: 800, color: C.warning)),
            const SizedBox(height: 5),
            Text(
              'Серверное paper-исполнение ещё не завершено. Это безопаснее, чем показывать рабочую кнопку, которая возвращает ошибку или подменяет серверную идею локальным сигналом.',
              style: T.body(11, color: C.muted, height: 1.45),
            ),
          ],
        ),
      );
}

class _Row extends StatelessWidget {
  const _Row(this.label, this.value);

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 4),
        child: Row(
          children: [
            Expanded(child: Text(label, style: T.body(11.5, color: C.muted))),
            const SizedBox(width: 12),
            Flexible(child: Text(value, textAlign: TextAlign.right, style: T.mono(11.5, weight: 700))),
          ],
        ),
      );
}

class _Bullet extends StatelessWidget {
  const _Bullet({required this.text, required this.color});

  final String text;
  final Color color;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 5),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('•', style: T.body(12, color: color)),
            const SizedBox(width: 7),
            Expanded(child: Text(text, style: T.body(11.5, color: C.textSecondary, height: 1.4))),
          ],
        ),
      );
}

class _Error extends StatelessWidget {
  const _Error({required this.text, required this.onRetry});

  final String text;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) => Center(
        child: Padding(
          padding: const EdgeInsets.all(28),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text('Разбор не загрузился', style: T.jost(18)),
              const SizedBox(height: 8),
              Text(text, textAlign: TextAlign.center, style: T.body(11.5, color: C.warning, height: 1.45)),
              const SizedBox(height: 16),
              ActionButton(label: 'Повторить', onTap: onRetry, primary: true),
            ],
          ),
        ),
      );

Map<String, dynamic> _map(Object? value) =>
    value is Map<String, dynamic> ? value : const {};

List<Map<String, dynamic>> _maps(Object? value) => value is List
    ? [for (final item in value) if (item is Map<String, dynamic>) item]
    : const [];

List<String> _strings(Object? value) => value is List
    ? [for (final item in value) if (item != null && item.toString().isNotEmpty) item.toString()]
    : const [];

double? _number(Object? value) => switch (value) {
      num n => n.toDouble(),
      String s => double.tryParse(s.replaceAll(',', '.')),
      _ => null,
    };

double? _ratio(Object? value) {
  final number = _number(value);
  if (number == null) return null;
  return number > 1 ? number / 100 : number;
}

String _decimal(double value) =>
    value.toStringAsFixed(2).replaceAll('.', ',').replaceFirst(RegExp(r',?0+$'), '');

String _percent(double value) => '${(value * 100).toStringAsFixed(0)}%';
String _percentOrDash(double? value) => value == null ? '—' : _percent(value);

String _price(Object? value) {
  final number = _number(value);
  if (number == null) return '—';
  final decimals = number.abs() < 10 ? 4 : number.abs() < 1000 ? 2 : 0;
  final raw = number.toStringAsFixed(decimals).replaceAll('.', ',');
  final parts = raw.split(',');
  final grouped = parts.first.replaceAllMapped(
    RegExp(r'\B(?=(\d{3})+(?!\d))'),
    (_) => ' ',
  );
  return parts.length == 1 ? grouped : '$grouped,${parts.last}';
}

String _entry(double? low, double? high) {
  if (low == null && high == null) return '—';
  if (low == null) return _price(high);
  if (high == null || (high - low).abs() < 1e-12) return _price(low);
  return '${_price(low)}–${_price(high)}';
}

String _quantity(Object? value) {
  final number = _number(value);
  return number == null ? '—' : _decimal(number);
}

String _rub(Object? value) {
  final number = _number(value);
  if (number == null) return '—';
  final rounded = number.round().toString().replaceAllMapped(
        RegExp(r'\B(?=(\d{3})+(?!\d))'),
        (_) => ' ',
      );
  return '$rounded ₽';
}
