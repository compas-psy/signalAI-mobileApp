import 'package:flutter/widgets.dart';

import '../../domain/broker/trading_diagnostics.dart';
import '../../state/app_scope.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../widgets/common.dart';
import '../widgets/vector_icon.dart';

/// Диагностика торговли: работает ли контур на самом деле.
///
/// Настройки показывают состояние словами приложения — «ключи заданы»,
/// «ГОТОВ». Этот экран показывает состояние словами биржи: принят ли ключ,
/// что ответил счёт, какие позиции она за нами видит. Разница между этими
/// двумя картинами и есть то, из-за чего терминалу перестают доверять.
class TradingDiagnosticsScreen extends StatefulWidget {
  const TradingDiagnosticsScreen({super.key});

  @override
  State<TradingDiagnosticsScreen> createState() => _TradingDiagnosticsScreenState();
}

class _TradingDiagnosticsScreenState extends State<TradingDiagnosticsScreen> {
  final List<TradingCheck> _results = [];
  bool _running = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _run());
  }

  Future<void> _run() async {
    if (_running) return;
    final desk = AppScope.read(context).tradingDesk;
    if (desk == null) return;
    setState(() {
      _running = true;
      _results.clear();
    });
    // Проверки приходят потоком: обращение к бирже занимает секунды, и
    // держать экран пустым, пока идут все, незачем.
    await for (final check in desk.diagnoseTrading()) {
      if (!mounted) return;
      setState(() => _results.add(check));
    }
    if (mounted) setState(() => _running = false);
  }

  @override
  Widget build(BuildContext context) {
    final passed = _results.where((r) => r.ok).length;
    return ColoredBox(
      color: C.bg,
      child: SafeArea(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
              decoration: const BoxDecoration(
                color: C.headerBg,
                border: Border(bottom: BorderSide(color: C.dividerSoft)),
              ),
              child: Row(
                children: [
                  Pressable(
                    onTap: () => Navigator.of(context).pop(),
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
                        Text('Диагностика торговли', style: T.jost(18)),
                        Text(
                          _running
                              ? 'спрашиваем биржу…'
                              : 'пройдено $passed из ${_results.length}',
                          style: T.body(11, color: _running ? C.accent : C.muted),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
            Expanded(
              child: ListView(
                padding: const EdgeInsets.all(16),
                children: [
                  Text(
                    'Живой прогон торгового контура: хранилище ключей, способ '
                    'подтверждения, ответ каждой площадки на её ключ и то, '
                    'какие позиции и заявки она за нами видит. Ордера здесь не '
                    'отправляются — только запросы на чтение.',
                    style: T.body(11, color: C.muted, height: 1.5),
                  ),
                  const SizedBox(height: 12),
                  for (final result in _results) ...[
                    _CheckCard(result: result),
                    const SizedBox(height: 8),
                  ],
                  if (_running)
                    const Padding(
                      padding: EdgeInsets.symmetric(vertical: 16),
                      child: Center(
                        child: Text('…', style: TextStyle(color: C.accent)),
                      ),
                    ),
                  if (!_running) ...[
                    const SizedBox(height: 8),
                    Pressable(
                      onTap: _run,
                      child: Container(
                        padding: const EdgeInsets.all(12),
                        decoration: BoxDecoration(
                          border: Border.all(color: C.borderHover),
                          borderRadius: BorderRadius.circular(R.inner),
                        ),
                        child: Center(
                          child: Text('Запустить снова',
                              style: T.body(13, weight: 800, color: C.accent)),
                        ),
                      ),
                    ),
                  ],
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _CheckCard extends StatelessWidget {
  const _CheckCard({required this.result});

  final TradingCheck result;

  @override
  Widget build(BuildContext context) => SectionCard(
        margin: EdgeInsets.zero,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Container(
                  width: 8,
                  height: 8,
                  decoration: BoxDecoration(
                    color: result.ok ? C.green : C.red,
                    shape: BoxShape.circle,
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(child: Text(result.name, style: T.body(12.5, weight: 700))),
              ],
            ),
            const SizedBox(height: 6),
            for (final line in result.details)
              Padding(
                padding: const EdgeInsets.only(top: 2),
                child: Text('· $line', style: T.mono(10.5, color: C.muted, height: 1.4)),
              ),
          ],
        ),
      );
}
