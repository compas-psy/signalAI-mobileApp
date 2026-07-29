import 'package:flutter/widgets.dart';

import '../state/app_controller.dart';
import '../state/app_scope.dart';
import '../theme/tokens.dart';
import '../theme/typography.dart';
import '../state/navigation.dart';
import 'screens/idea_detail_screen.dart';
import 'screens/ideas_screen.dart';
import 'screens/journal_screen.dart';
import 'screens/portfolio_screen.dart';
import 'screens/diagnostics_screen.dart';
import 'screens/settings_screen.dart';
import 'screens/strategies_screen.dart';
import 'screens/today_screen.dart';
import 'widgets/confirm_sheet.dart';
import 'widgets/toast.dart';
import 'widgets/vector_icon.dart';
import 'widgets/bottom_nav.dart';
import 'widgets/section_header.dart';
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
                        constraints: const BoxConstraints(maxWidth: 440),
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
              current: controller.section,
              onSelect: controller.goSection,
            ),
        ],
      );
    }
    return Row(
      children: [
        SideNav(
          current: controller.section,
          onSelect: controller.goSection,
          extended: pane == Pane.expanded,
          onKillSwitch: controller.toggleKillSwitch,
          killSwitchOn: controller.killSwitchOn,
        ),
        Expanded(child: _content(controller, pane)),
      ],
    );
  }

  /// Содержимое: на широком экране «Идеи» и разбор стоят рядом, остальные
  /// разделы — колонкой ограниченной ширины, чтобы строки оставались читаемыми.
  Widget _content(AppController controller, Pane pane) {
    if (pane.usesTwoPane && controller.section == AppSection.ideas) {
      final signal = controller.currentSignal;
      final risk = controller.risk;
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          SectionHeader(
            section: controller.section,
            pill: controller.pill,
            onPill: controller.goPill,
            mode: controller.riskMode,
            dataAt: controller.dataFreshness,
          ),
          Expanded(
            child: Row(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          SizedBox(width: 400, child: IdeasScreen(pill: controller.pill)),
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
            ),
          ),
        ],
      );
    }
    // Разбор идеи закрывает раздел целиком, вместе с шапкой и пилюлями.
    // Пилюли — это разрез **ленты** («Решения · Наблюдение · В работе»), и
    // над открытой карточкой они управляют тем, чего на экране нет: нажатие
    // меняло фильтр списка, спрятанного под разбором.
    final screen = Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (!controller.isDetailOpen)
          SectionHeader(
            section: controller.section,
            pill: controller.pill,
            onPill: controller.goPill,
            mode: controller.riskMode,
            dataAt: controller.dataFreshness,
          ),
        Expanded(child: _screen(controller)),
      ],
    );
    return pane.isCompact
        ? screen
        : ReadableColumn(maxWidth: pane.contentWidth, child: screen);
  }

  Widget _screen(AppController controller) {
    if (controller.isDetailOpen) {
      final signal = controller.currentSignal;
      final risk = controller.risk;
      if (signal != null && risk != null) {
        return IdeaDetailScreen(signal: signal, risk: risk);
      }
    }
    return switch (controller.section) {
      AppSection.today => const TodayScreen(),
      AppSection.portfolio => PortfolioScreen(pill: controller.pill),
      AppSection.ideas => IdeasScreen(pill: controller.pill),
      AppSection.journal => JournalScreen(
          pill: controller.pill,
          summary: controller.trades!,
        ),
      // Стратегии и диагностика данных — подразделы настроек по ТЗ §13, но
      // экраны у них свои: набивать их в общую ленту настроек значило бы
      // прятать бэктест и живую проверку источников под скроллом.
      AppSection.settings => switch (SettingsPill
            .values[controller.pill.clamp(0, SettingsPill.values.length - 1)]) {
          SettingsPill.strategies => StrategiesScreen(
              snapshot: controller.strategies!,
              backtestRunning: controller.backtestRunning,
              optimizing: controller.optimizing,
              backtestStage: controller.analysisStage,
            ),
          SettingsPill.data => const DiagnosticsScreen(),
          _ => SettingsScreen(
              snapshot: controller.settings!,
              pill: controller.pill,
            ),
        },
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
                impact: controller.currentImpact,
                busy: controller.confirming,
                onExecute: controller.confirmCurrentSignal,
                onClose: controller.closeSheet,
              )
            // На планшете шит во всю ширину читался бы как строка длиной в
            // экран: ограничиваем и центрируем, поведение прежнее.
            : Center(
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 440),
                  child: ConfirmSheet(
                    signal: signal,
                    risk: risk,
                    impact: controller.currentImpact,
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

class _LoadingState extends StatelessWidget {
  const _LoadingState();

  @override
  Widget build(BuildContext context) => ColoredBox(
        color: C.bg,
        child: Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const BrandMark(size: 56),
              const SizedBox(height: 14),
              Text.rich(
                TextSpan(
                  text: 'Signal',
                  style: T.jost(22),
                  children: [
                    TextSpan(text: 'AI', style: T.jost(22, color: C.accent)),
                  ],
                ),
              ),
            ],
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
