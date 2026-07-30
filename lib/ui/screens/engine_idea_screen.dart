import 'package:flutter/widgets.dart';

import '../../data/api/api_client.dart';
import '../../domain/idea/idea.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../tone.dart';
import '../widgets/common.dart';

/// Открывает именно ту серверную идею, по которой нажал пользователь.
void openEngineIdea(BuildContext context, Idea summary) {
  Navigator.of(context).push(
    PageRouteBuilder<void>(
      pageBuilder: (context, animation, secondaryAnimation) =>
          EngineIdeaScreen(summary: summary),
      transitionsBuilder: (context, animation, secondaryAnimation, child) =>
          FadeTransition(opacity: animation, child: child),
      transitionDuration: const Duration(milliseconds: 180),
    ),
  );
}

/// Разбор идеи из Engine API.
///
/// Здесь нет кнопки реальной заявки: серверное paper-исполнение пока отвечает
/// 503. Лучше честно оставить экран аналитическим, чем отправить пользователя
/// в старый локальный контур и подменить выбранный инструмент другим.
class EngineIdeaScreen extends StatefulWidget {
  const EngineIdeaScreen({super.key, required this.summary});

  final Idea summary;

  @override
  State<EngineIdeaScreen> createState() => _EngineIdeaScreenState();
}

class _EngineIdeaScreenState extends State<EngineIdeaScreen> {
  late final ApiClient _api = ApiClient();
  late Future<Map<String, dynamic>> _future = _load();

  Future<Map<String, dynamic>> _load() {
    return _api.get('/api/v1/ideas/${widget.summary.id}');
  }

  void _retry() {
    setState(() => _future = _load());
  }

  @override
  void dispose() {
    _api.close();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return ColoredBox(
      color: C.bg,
      child: SafeArea(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            _Header(summary: widget.summary),
            Expanded(
              child: FutureBuilder<Map<String, dynamic>>(
                future: _future,
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
                    return _LoadError(
                      text: snapshot.error.toString(),
                      onRetry: _retry,
                    );
                  }
                  return _IdeaBody(
                    summary: widget.summary,
                    json: snapshot.data!,
                  );
                },
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _Header extends StatelessWidget {
  const _Header({required this.summary});

  final Idea summary;

  @override
  Widget build(BuildContext context) {
    final title = summary.instrumentName.isEmpty
        ? summary.instrumentId
        : summary.instrumentName;
    return Container(
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
                  title,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: T.jost(19),
                ),
                const SizedBox(height: 3),
                Text(
                  '${summary.strategy.label} · '
                  '${summary.timeframes.join(" / ")}',
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
}

class _IdeaBody extends StatelessWidget {
  const _IdeaBody({required this.summary, required this.json});

  final Idea summary;
  final Map<String, dynamic> json;

  @override
  Widget build(BuildContext context) {
    final plan = _asMap(json['plan']);
    final probability = _asMap(json['probability']);
    final sizing = _asMap(json['sizing']);
    final explanation = _asMap(json['explanation']);
    final support = _asStrings(explanation['supporting_factors']);
    final counter = _asStrings(explanation['counter_factors']);
    final warnings = _asStrings(explanation['data_warnings']);
    final hasPlan = plan.isNotEmpty;

    return ListView(
      padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 28),
      children: [
        _DecisionCard(
          summary: summary,
          probability: probability,
          hasPlan: hasPlan,
        ),
        const SizedBox(height: 12),
        _Metrics(probability: probability, json: json),
        const SizedBox(height: 12),
        _TradePlan(plan: plan, sizing: sizing),
        const SizedBox(height: 12),
        _Explanation(
          headline: explanation['headline']?.toString() ?? '',
          thesis: explanation['thesis']?.toString() ?? summary.thesis,
          support: support,
          counter: counter,
          invalidation: plan['invalidation']?.toString() ?? '',
        ),
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
  const _DecisionCard({
    required this.summary,
    required this.probability,
    required this.hasPlan,
  });

  final Idea summary;
  final Map<String, dynamic> probability;
  final bool hasPlan;

  @override
  Widget build(BuildContext context) {
    final ready = summary.state.needsAttention && hasPlan;
    final p = _ratio(probability['p_tp1_before_sl']);
    final title = ready ? 'План сформирован' : 'Ждём подтверждения сетапа';
    final note = ready
        ? 'Уровни и риск рассчитаны сервером. Проверьте факторы против и срок.'
        : 'Это наблюдение, а не готовая сделка. До триггера входа нет.';

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
                borderColor: ready ? C.accentBorder : C.borderHover,
                background: ready ? C.accentFaint : C.inset,
                fontWeight: 800,
              ),
            ],
          ),
          const SizedBox(height: 7),
          Text(
            note,
            style: T.body(12, color: C.textSecondary, height: 1.45),
          ),
          if (p != null) ...[
            const SizedBox(height: 9),
            Text(
              'TP1 раньше стопа: ${_percent(p)}',
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
    final ev = _toDouble(probability['expected_r']);
    final rr = _toDouble(json['rr_tp2']);
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      children: [
        _Metric(label: 'TP1 раньше SL', value: _percentOrDash(p)),
        _Metric(label: 'Надёжность', value: _percentOrDash(confidence)),
        _Metric(
          label: 'Матожидание',
          value: ev == null ? '—' : '${ev >= 0 ? '+' : ''}${_decimal(ev)}R',
        ),
        _Metric(label: 'R/R до TP2', value: rr == null ? '—' : _decimal(rr)),
      ],
    );
  }
}

class _Metric extends StatelessWidget {
  const _Metric({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: 158,
      child: SectionCard(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 11),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(value, style: T.mono(13, weight: 800, color: C.accent)),
            const SizedBox(height: 4),
            Text(label, style: T.body(9.5, color: C.muted)),
          ],
        ),
      ),
    );
  }
}

class _TradePlan extends StatelessWidget {
  const _TradePlan({required this.plan, required this.sizing});

  final Map<String, dynamic> plan;
  final Map<String, dynamic> sizing;

  @override
  Widget build(BuildContext context) {
    if (plan.isEmpty) {
      return SectionCard(
        child: Text(
          'Торговый план ещё не сформирован. Входить по наблюдению рано.',
          style: T.body(12, color: C.muted, height: 1.45),
        ),
      );
    }
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('Торговый план'),
          const SizedBox(height: 8),
          _ValueRow(
            label: 'Вход',
            value: _entry(plan['entry_low'], plan['entry_high']),
          ),
          _ValueRow(label: 'Стоп', value: _price(plan['stop'])),
          _ValueRow(label: 'TP1', value: _price(plan['tp1'])),
          _ValueRow(label: 'TP2', value: _price(plan['tp2'])),
          if (plan['tp3'] != null)
            _ValueRow(label: 'TP3', value: _price(plan['tp3'])),
          const SizedBox(height: 4),
          _ValueRow(label: 'Размер', value: _numberText(sizing['quantity'])),
          _ValueRow(label: 'Риск', value: _rubles(sizing['risk_amount'])),
          _ValueRow(
            label: 'Риск счёта',
            value: _percentOrDash(_ratio(sizing['risk_pct'])),
          ),
        ],
      ),
    );
  }
}

class _Explanation extends StatelessWidget {
  const _Explanation({
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
  Widget build(BuildContext context) {
    return SectionCard(
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
            Text(
              thesis,
              style: T.body(12, color: C.textSecondary, height: 1.5),
            ),
          ],
          if (support.isNotEmpty) ...[
            const SizedBox(height: 12),
            const SectionLabel('За', color: C.green),
            const SizedBox(height: 5),
            for (final item in support)
              _Bullet(text: item, color: C.green),
          ],
          if (counter.isNotEmpty) ...[
            const SizedBox(height: 12),
            const SectionLabel('Против', color: C.warning),
            const SizedBox(height: 5),
            for (final item in counter)
              _Bullet(text: item, color: C.warning),
          ],
          if (invalidation.isNotEmpty) ...[
            const SizedBox(height: 12),
            const SectionLabel('Когда идея сломана', color: C.red),
            const SizedBox(height: 5),
            Text(
              invalidation,
              style: T.body(12, color: C.textSecondary, height: 1.45),
            ),
          ],
        ],
      ),
    );
  }
}

class _Warnings extends StatelessWidget {
  const _Warnings({required this.items});

  final List<String> items;

  @override
  Widget build(BuildContext context) {
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('Ограничения данных', color: C.warning),
          const SizedBox(height: 6),
          for (final item in items)
            _Bullet(text: item, color: C.warning),
        ],
      ),
    );
  }
}

class _ExecutionNotice extends StatelessWidget {
  const _ExecutionNotice();

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(13),
      decoration: BoxDecoration(
        color: C.inset,
        border: Border.all(color: C.warningBorder),
        borderRadius: BorderRadius.circular(R.button),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Заявка отсюда не отправляется',
            style: T.body(12.5, weight: 800, color: C.warning),
          ),
          const SizedBox(height: 5),
          Text(
            'Серверное paper-исполнение ещё не завершено. Экран показывает '
            'анализ и план без декоративной неработающей кнопки.',
            style: T.body(11, color: C.muted, height: 1.45),
          ),
        ],
      ),
    );
  }
}

class _ValueRow extends StatelessWidget {
  const _ValueRow({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        children: [
          Expanded(child: Text(label, style: T.body(11.5, color: C.muted))),
          const SizedBox(width: 12),
          Flexible(
            child: Text(
              value,
              textAlign: TextAlign.right,
              style: T.mono(11.5, weight: 700),
            ),
          ),
        ],
      ),
    );
  }
}

class _Bullet extends StatelessWidget {
  const _Bullet({required this.text, required this.color});

  final String text;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 5),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('•', style: T.body(12, color: color)),
          const SizedBox(width: 7),
          Expanded(
            child: Text(
              text,
              style: T.body(11.5, color: C.textSecondary, height: 1.4),
            ),
          ),
        ],
      ),
    );
  }
}

class _LoadError extends StatelessWidget {
  const _LoadError({required this.text, required this.onRetry});

  final String text;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(28),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text('Разбор не загрузился', style: T.jost(18)),
            const SizedBox(height: 8),
            Text(
              text,
              textAlign: TextAlign.center,
              style: T.body(11.5, color: C.warning, height: 1.45),
            ),
            const SizedBox(height: 16),
            ActionButton(label: 'Повторить', onTap: onRetry, primary: true),
          ],
        ),
      ),
    );
  }
}

Map<String, dynamic> _asMap(Object? value) {
  if (value is Map<String, dynamic>) return value;
  return <String, dynamic>{};
}

List<String> _asStrings(Object? value) {
  if (value is! List) return <String>[];
  return <String>[
    for (final item in value)
      if (item != null && item.toString().isNotEmpty) item.toString(),
  ];
}

double? _toDouble(Object? value) {
  if (value is num) return value.toDouble();
  if (value is String) return double.tryParse(value.replaceAll(',', '.'));
  return null;
}

double? _ratio(Object? value) {
  final number = _toDouble(value);
  if (number == null) return null;
  return number > 1 ? number / 100 : number;
}

String _decimal(double value) {
  var text = value.toStringAsFixed(2).replaceAll('.', ',');
  while (text.contains(',') && text.endsWith('0')) {
    text = text.substring(0, text.length - 1);
  }
  if (text.endsWith(',')) text = text.substring(0, text.length - 1);
  return text;
}

String _percent(double value) {
  return '${(value * 100).toStringAsFixed(0)}%';
}

String _percentOrDash(double? value) {
  return value == null ? '—' : _percent(value);
}

String _price(Object? value) {
  final number = _toDouble(value);
  if (number == null) return '—';
  final decimals = number.abs() < 10 ? 4 : (number.abs() < 1000 ? 2 : 0);
  final raw = number.toStringAsFixed(decimals).replaceAll('.', ',');
  final parts = raw.split(',');
  final integer = parts.first.replaceAllMapped(
    RegExp(r'\B(?=(\d{3})+(?!\d))'),
    (match) => ' ',
  );
  return parts.length == 1 ? integer : '$integer,${parts.last}';
}

String _entry(Object? lowValue, Object? highValue) {
  final low = _toDouble(lowValue);
  final high = _toDouble(highValue);
  if (low == null && high == null) return '—';
  if (low == null) return _price(high);
  if (high == null || (high - low).abs() < 1e-12) return _price(low);
  return '${_price(low)}–${_price(high)}';
}

String _numberText(Object? value) {
  final number = _toDouble(value);
  return number == null ? '—' : _decimal(number);
}

String _rubles(Object? value) {
  final number = _toDouble(value);
  if (number == null) return '—';
  final rounded = number.round().toString().replaceAllMapped(
    RegExp(r'\B(?=(\d{3})+(?!\d))'),
    (match) => ' ',
  );
  return '$rounded ₽';
}
