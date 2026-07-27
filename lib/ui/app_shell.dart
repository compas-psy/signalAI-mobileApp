import 'package:flutter/widgets.dart';

import '../state/app_controller.dart';
import '../state/app_scope.dart';
import '../theme/tokens.dart';
import '../theme/typography.dart';
import 'screens/idea_detail_screen.dart';
import 'screens/ideas_screen.dart';
import 'screens/invest_screen.dart';
import 'screens/settings_screen.dart';
import 'screens/strategies_screen.dart';
import 'screens/trades_screen.dart';
import 'widgets/confirm_sheet.dart';
import 'widgets/toast.dart';
import 'widgets/bottom_nav.dart';
import 'widgets/side_nav.dart';
import 'layout.dart';

/// Каркас приложения: активный экран, нижняя навигация, шит подтверждения
/// и тост — ровно та же композиция, что в макете.
class AppShell extends StatelessWidget {
  const AppShell({super.key});

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.of(context);

    if (controller.error != null && controller.digest == null) {
      return _ErrorState(onRetry: controller.load);
    }
    if (controller.isLoading) {
      return const _LoadingState();
    }

    return ColoredBox(
      color: C.bg,
      child: SafeArea(
        child: LayoutBuilder(
          builder: (context, constraints) {
            final pane = Pane.of(constraints.maxWidth);
            return Stack(
              children: [
                _body(controller, pane),
                if (controller.sheetOpen) _sheet(controller, pane),
                if (controller.toast != null)
                  Positioned(
                    top: 10,
                    left: 14,
                    right: 14,
                    child: Center(
                      child: ConstrainedBox(
                        constraints: const BoxConstraints(maxWidth: 560),
                        child: AppToast(message: controller.toast!),
                      ),
                    ),
                  ),
              ],
            );
          },
        ),
      ),
    );
  }

  /// Каркас под ширину экрана: телефон — нижняя панель, планшет — боковая
  /// колонка, широкий планшет — ещё и две колонки содержимого.
  Widget _body(AppController controller, Pane pane) {
    if (!pane.usesSideNav) {
      return Column(
        children: [
          Expanded(child: _content(controller, pane)),
          if (!controller.sheetOpen)
            BottomNav(
              current: controller.tab,
              detailOpen: controller.isDetailOpen,
              onSelect: controller.goTab,
            ),
        ],
      );
    }
    return Row(
      children: [
        SideNav(
          current: controller.tab,
          detailOpen: controller.isDetailOpen,
          onSelect: controller.goTab,
          extended: pane == Pane.expanded,
        ),
        Expanded(child: _content(controller, pane)),
      ],
    );
  }

  /// Содержимое: на широком экране «Идеи» и разбор стоят рядом, остальные
  /// разделы — колонкой ограниченной ширины, чтобы строки оставались читаемыми.
  Widget _content(AppController controller, Pane pane) {
    if (pane.usesTwoPane && controller.tab == AppTab.ideas) {
      final signal = controller.currentSignal;
      final risk = controller.risk;
      return Row(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          SizedBox(
            width: 400,
            child: controller.digest == null
                ? _DigestPending(controller: controller)
                : IdeasScreen(digest: controller.digest!),
          ),
          const VerticalDivider(),
          Expanded(
            child: signal == null || risk == null
                ? const _PickIdea()
                : IdeaDetailScreen(
                    signal: signal,
                    risk: risk,
                    showBack: false,
                  ),
          ),
        ],
      );
    }
    final screen = _screen(controller);
    return pane.isCompact ? screen : ReadableColumn(child: screen);
  }

  Widget _screen(AppController controller) {
    if (controller.isDetailOpen) {
      final signal = controller.currentSignal;
      final risk = controller.risk;
      if (signal != null && risk != null) {
        return IdeaDetailScreen(signal: signal, risk: risk);
      }
    }
    return switch (controller.tab) {
      AppTab.ideas => controller.digest == null
          ? _DigestPending(controller: controller)
          : IdeasScreen(digest: controller.digest!),
      AppTab.trades => TradesScreen(summary: controller.trades!),
      AppTab.strategies => StrategiesScreen(
          snapshot: controller.strategies!,
          backtestRunning: controller.backtestRunning,
          optimizing: controller.optimizing,
          backtestStage: controller.analysisStage,
        ),
      AppTab.invest => const InvestScreen(),
      AppTab.settings => SettingsScreen(snapshot: controller.settings!),
    };
  }

  Widget _sheet(AppController controller, Pane pane) {
    final signal = controller.currentSignal;
    final risk = controller.risk;
    if (signal == null || risk == null) return const SizedBox.shrink();
    return Positioned.fill(
      // @keyframes sheetUp: подъём на 40px с проявлением, 250 мс
      child: TweenAnimationBuilder<double>(
        tween: Tween(begin: 0, end: 1),
        duration: const Duration(milliseconds: 250),
        curve: Curves.easeOut,
        builder: (context, t, child) => Opacity(
          opacity: t,
          child: Transform.translate(offset: Offset(0, 40 * (1 - t)), child: child),
        ),
        child: pane.isCompact
            ? ConfirmSheet(
                signal: signal,
                risk: risk,
                busy: controller.confirming,
                onExecute: controller.confirmCurrentSignal,
                onClose: controller.closeSheet,
              )
            // На планшете шит во всю ширину читался бы как строка длиной в
            // экран: ограничиваем и центрируем, поведение прежнее.
            : Center(
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 560),
                  child: ConfirmSheet(
                    signal: signal,
                    risk: risk,
                    busy: controller.confirming,
                    onExecute: controller.confirmCurrentSignal,
                    onClose: controller.closeSheet,
                  ),
                ),
              ),
      ),
    );
  }
}

/// Правая колонка планшета, пока идея не выбрана.
class _PickIdea extends StatelessWidget {
  const _PickIdea();

  @override
  Widget build(BuildContext context) => Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Text(
            'Выберите идею слева — разбор откроется здесь.',
            textAlign: TextAlign.center,
            style: T.body(13, color: C.muted, height: 1.5),
          ),
        ),
      );
}

/// Разделитель колонок планшета.
class VerticalDivider extends StatelessWidget {
  const VerticalDivider({super.key});

  @override
  Widget build(BuildContext context) =>
      Container(width: 1, color: C.dividerSoft);
}

/// Вкладка «Идеи», пока дайджест ещё считается или расчёт упал.
///
/// Это не заставка: оболочка уже работает, вкладки переключаются, а здесь
/// виден живой прогресс реального расчёта по данным бирж.
class _DigestPending extends StatelessWidget {
  const _DigestPending({required this.controller});

  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final failed = controller.digestError != null && !controller.digestLoading;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(28),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              failed ? 'Данные бирж недоступны' : 'Считаем идеи…',
              style: T.jost(20),
            ),
            const SizedBox(height: 10),
            if (failed) ...[
              Text(
                controller.digestErrorText,
                textAlign: TextAlign.center,
                style: T.body(12, color: C.muted, height: 1.5),
              ),
              const SizedBox(height: 18),
              GestureDetector(
                onTap: controller.refreshDigest,
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 13),
                  decoration: BoxDecoration(
                    color: C.accent,
                    borderRadius: BorderRadius.circular(R.button),
                  ),
                  child: Text('Повторить', style: T.body(14, weight: 800, color: C.onAccent)),
                ),
              ),
            ] else ...[
              // Стадия обновляется репозиторием: видно, какой инструмент
              // анализируется прямо сейчас.
              Text(
                controller.analysisStage ?? 'Подключаемся к биржам…',
                textAlign: TextAlign.center,
                style: T.mono(12, color: C.accent),
              ),
              const SizedBox(height: 10),
              Text(
                'Идёт реальный расчёт по котировкам MOEX ISS и Bybit: десятки '
                'запросов к биржам, обычно от полуминуты до пары минут. '
                'Расчёт не прерывается, если уйти на другую вкладку — '
                'остальные разделы уже работают.',
                textAlign: TextAlign.center,
                style: T.body(11, color: C.muted, height: 1.5),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _LoadingState extends StatelessWidget {
  const _LoadingState();

  @override
  Widget build(BuildContext context) => ColoredBox(
        color: C.bg,
        child: Center(
          child: Text.rich(
            TextSpan(
              text: 'Signal',
              style: T.jost(24),
              children: [TextSpan(text: 'AI', style: T.jost(24, color: C.accent))],
            ),
          ),
        ),
      );
}

class _ErrorState extends StatelessWidget {
  const _ErrorState({required this.onRetry});

  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) => ColoredBox(
        color: C.bg,
        child: SafeArea(
          child: Center(
            child: Padding(
              padding: const EdgeInsets.all(24),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text('Не удалось запуститься', style: T.jost(20)),
                  const SizedBox(height: 8),
                  Text(
                    'Не получилось загрузить данные приложения. Проверьте '
                    'подключение к интернету и повторите попытку.',
                    textAlign: TextAlign.center,
                    style: T.body(12, color: C.muted, height: 1.5),
                  ),
                  const SizedBox(height: 18),
                  GestureDetector(
                    onTap: onRetry,
                    child: Container(
                      padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 13),
                      decoration: BoxDecoration(
                        color: C.accent,
                        borderRadius: BorderRadius.circular(R.button),
                      ),
                      child: Text(
                        'Повторить',
                        style: T.body(14, weight: 800, color: C.onAccent),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      );
}
