import 'package:flutter/services.dart';
import 'package:flutter/widgets.dart';

import '../../data/market/http_json.dart';
import '../../data/market/iss_client.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../widgets/common.dart';
import '../widgets/vector_icon.dart';

/// Сырой ответ биржи по выбранному адресу.
///
/// Этот экран существует не для красоты, а потому что схему выдачи MOEX
/// нельзя проверить оттуда, где собирается приложение: доступа к бирже там
/// нет, а состав колонок ISS меняет. Раньше расхождение схемы выглядело как
/// «раздел пустой», и разговор о причине шёл догадками. Здесь виден адрес,
/// код ответа, время, имена колонок каждого блока и первые строки как есть —
/// один скриншот отвечает на все вопросы сразу.
class RawProbeScreen extends StatefulWidget {
  const RawProbeScreen({super.key, this.assetCode = 'SI'});

  final String assetCode;

  @override
  State<RawProbeScreen> createState() => _RawProbeScreenState();
}

class _RawProbeScreenState extends State<RawProbeScreen> {
  late final IssClient _iss =
      IssClient(http: HttpJson(timeout: const Duration(seconds: 10)));

  late final List<(String, Uri)> _targets =
      IssClient.optionProbeUrls(widget.assetCode);

  int _index = 0;
  IssRawProbe? _probe;
  bool _running = false;

  @override
  void initState() {
    super.initState();
    _run();
  }

  Future<void> _run() async {
    if (_running) return;
    setState(() {
      _running = true;
      _probe = null;
    });
    final probe = await _iss.probeUrl(_targets[_index].$2);
    if (!mounted) return;
    setState(() {
      _probe = probe;
      _running = false;
    });
  }

  /// Весь разбор одной строкой в буфер: скриншот хорош, текст точнее.
  String _asText() {
    final probe = _probe;
    if (probe == null) return '';
    final buffer = StringBuffer()
      ..writeln(probe.report.url)
      ..writeln('время ответа: ${probe.report.elapsed.inMilliseconds} мс');
    if (!probe.report.ok) {
      buffer.writeln('ОШИБКА: ${probe.report.error}');
      return buffer.toString();
    }
    for (final entry in probe.report.blocks.entries) {
      buffer
        ..writeln()
        ..writeln('[${entry.key}] строк ${entry.value.rows}')
        ..writeln(entry.value.columns.join(', '));
      for (final row in probe.samples[entry.key] ?? const []) {
        buffer.writeln(row.toString());
      }
    }
    return buffer.toString();
  }

  @override
  Widget build(BuildContext context) {
    final probe = _probe;
    return ColoredBox(
      color: C.bg,
      child: SafeArea(
        child: Column(
          children: [
            _Header(
              title: 'Сырой ответ биржи',
              subtitle: _running
                  ? 'спрашиваем…'
                  : probe == null
                      ? 'выберите адрес'
                      : probe.report.ok
                          ? 'ответ за ${probe.report.elapsed.inMilliseconds} мс'
                          : 'биржа не ответила',
              onBack: () => Navigator.of(context).pop(),
            ),
            Expanded(
              child: ListView(
                padding: const EdgeInsets.all(S.screen),
                children: [
                  Text(
                    'Адреса, по которым приложение спрашивает биржу об опционах '
                    'на ${widget.assetCode}. Здесь ответ показан как есть: имена '
                    'колонок и первые строки. По ним видно, тот ли это адрес и '
                    'те ли поля, на которые рассчитывает разбор.',
                    style: T.body(11, color: C.muted, height: 1.5),
                  ),
                  const SizedBox(height: 12),
                  for (var i = 0; i < _targets.length; i++) ...[
                    _TargetRow(
                      title: _targets[i].$1,
                      url: _targets[i].$2,
                      selected: i == _index,
                      onTap: _running
                          ? null
                          : () {
                              setState(() => _index = i);
                              _run();
                            },
                    ),
                    const SizedBox(height: 6),
                  ],
                  const SizedBox(height: 8),
                  if (_running)
                    Center(
                      child: Padding(
                        padding: const EdgeInsets.all(20),
                        child: Text('запрос идёт…',
                            style: T.mono(12, color: C.accent)),
                      ),
                    )
                  else if (probe != null) ...[
                    _ProbeCard(probe: probe),
                    const SizedBox(height: 10),
                    ActionButton(
                      label: 'Скопировать разбор',
                      dense: true,
                      onTap: () {
                        Clipboard.setData(ClipboardData(text: _asText()));
                      },
                    ),
                    const SizedBox(height: 8),
                    ActionButton(
                      label: 'Спросить снова',
                      dense: true,
                      onTap: _run,
                    ),
                  ],
                  const SizedBox(height: 40),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _TargetRow extends StatelessWidget {
  const _TargetRow({
    required this.title,
    required this.url,
    required this.selected,
    required this.onTap,
  });

  final String title;
  final Uri url;
  final bool selected;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) => Pressable(
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.fromLTRB(11, 9, 11, 10),
          decoration: BoxDecoration(
            color: selected ? C.inset : null,
            border: Border.all(color: selected ? C.accent : C.border),
            borderRadius: BorderRadius.circular(R.inset),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(title,
                  style: T.body(12, weight: 700, color: selected ? C.accent : C.text)),
              const SizedBox(height: 3),
              Text('${url.path}?${url.query}',
                  style: T.mono(9.5, color: C.faint, height: 1.4)),
            ],
          ),
        ),
      );
}

class _ProbeCard extends StatelessWidget {
  const _ProbeCard({required this.probe});

  final IssRawProbe probe;

  @override
  Widget build(BuildContext context) {
    final report = probe.report;
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('Что ответила биржа'),
          const SizedBox(height: 8),
          Text(report.url.toString(),
              style: T.mono(9.5, color: C.muted, height: 1.4)),
          const SizedBox(height: 8),
          if (!report.ok)
            Text(report.error!,
                style: T.body(12, color: C.red, height: 1.4))
          else if (report.blocks.isEmpty)
            Text(
              'Ответ получен, но блоков данных в нём нет: адрес отвечает не тем, '
              'что ожидает разбор.',
              style: T.body(11.5, color: C.warning, height: 1.4),
            )
          else
            for (final entry in report.blocks.entries) ...[
              Row(
                children: [
                  Expanded(
                    child: Text('[${entry.key}]',
                        style: T.mono(12, weight: 700, color: C.info)),
                  ),
                  Text('строк ${entry.value.rows}',
                      style: T.mono(11, color: C.muted)),
                ],
              ),
              const SizedBox(height: 4),
              if (entry.value.columns.isEmpty)
                Text('колонок нет', style: T.body(11, color: C.faint))
              else
                Text(entry.value.columns.join(', '),
                    style: T.mono(10, color: C.textSecondary, height: 1.5)),
              const SizedBox(height: 6),
              for (final row in probe.samples[entry.key] ?? const [])
                Padding(
                  padding: const EdgeInsets.only(bottom: 5),
                  child: InsetBox(
                    child: Text(
                      row.entries
                          .map((e) => '${e.key}=${e.value}')
                          .join('  '),
                      style: T.mono(9.5, color: C.muted, height: 1.5),
                    ),
                  ),
                ),
              const SizedBox(height: 8),
            ],
        ],
      ),
    );
  }
}

/// Шапка модального экрана со стрелкой назад.
class _Header extends StatelessWidget {
  const _Header({
    required this.title,
    required this.subtitle,
    required this.onBack,
  });

  final String title;
  final String subtitle;
  final VoidCallback onBack;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        decoration: const BoxDecoration(
          color: C.headerBg,
          border: Border(bottom: BorderSide(color: C.dividerSoft)),
        ),
        child: Row(
          children: [
            Pressable(
              onTap: onBack,
              child: Container(
                width: 36,
                height: 36,
                decoration: BoxDecoration(
                  color: C.card,
                  shape: BoxShape.circle,
                  border: Border.all(color: C.border),
                ),
                child: const Center(
                  child: VectorIcon(Icons.chevronLeft, size: 18, color: C.text),
                ),
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(title, style: T.jost(18)),
                  Text(subtitle, style: T.body(11, color: C.muted)),
                ],
              ),
            ),
          ],
        ),
      );
}
