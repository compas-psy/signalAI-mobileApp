import 'package:flutter/widgets.dart';

import '../domain/idea/idea_funnel.dart';
import '../state/app_controller.dart';
import '../state/app_scope.dart';
import '../state/execution_mode_controller.dart';
import '../theme/tokens.dart';
import '../theme/typography.dart';
import '../state/navigation.dart';
import 'screens/idea_detail_screen.dart';
import 'screens/ideas_screen.dart';
import 'screens/journal_screen.dart';
import 'screens/portfolio_screen.dart';
import 'screens/diagnostics_screen.dart';
import 'screens/server_data_screen.dart';
import 'screens/server_ideas_screen.dart';
import 'screens/server_integrations_screen.dart';
import 'screens/server_journal_screen.dart';
import 'screens/server_risk_screen.dart';
import 'screens/server_today_screen.dart';
import 'screens/settings_screen.dart';
import 'screens/strategies_screen.dart';
import 'screens/today_screen.dart';
import 'widgets/capacity_status_panel.dart';
import 'widgets/engine_address_sheet.dart';
import 'widgets/engine_setup_gate.dart';
import 'widgets/risk_boost_confirm_flow.dart';
import 'widgets/toast.dart';
import 'widgets/vector_icon.dart';
import 'widgets/bottom_nav.dart';
import 'widgets/section_header.dart';
import 'widgets/side_nav.dart';
import 'widgets/strategy_comparison_panel.dart';
import 'layout.dart';

class AppShell extends StatelessWidget {
  const AppShell({super.key});

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.of(context);
    if (controller.error != null && controller.digest == null) {
      return _ErrorState(onRetry: controller.load);
    }
    if (controller.isLoading) return const _LoadingState();

    if (controller.thinMode && controller.engineAuthIssue != null) {
      return EngineSetupGate(
        issue: controller.engineAuthIssue!,
        baseUrl: controller.engineBaseUrl,
        onConfigure: () => showEngineAddressSheet(
          context,
          current: controller.engineBaseUrl,
          currentToken: '',
          onSubmit: (url, token) => controller.setEngineBaseUrl(url, token: token),
        ),
      );
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
                if (controller.sheetOpen) _sheet(context, controller, pane),
                if (controller.toast != null)
                  Positioned(
                    top: 10,
                    left: 14,
                    right: 14,
                    child: Center(
                      child: ConstrainedBox(
                        constraints: const BoxConstraints(maxWidth: 440),
                        child: AppToast(
                          message: controller.toast!,
                          tone: controller.toastTone,
                        ),
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

  Widget _body(AppController controller, Pane pane) {
    if (!pane.usesSideNav) {
      return Column(
        children: [
          if (controller.demoData) const DemoBanner(),
          Expanded(child: _content(controller, pane)),
          if (!controller.sheetOpen)
            BottomNav(current: controller.section, onSelect: controller.goSection),
        ],
      );
    }
    return Column(
      children: [
        if (controller.demoData) const DemoBanner(),
        Expanded(child: _sideNavBody(controller, pane)),
      ],
    );
  }

  Widget _sideNavBody(AppController controller, Pane pane) => Row(
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

  Widget _content(AppController controller, Pane pane) {
    if (pane.usesTwoPane && controller.section == AppSection.ideas) {
      final signal = controller.currentSignal;
      final risk = controller.risk;
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _header(controller),
          Expanded(
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                SizedBox(
                  width: 400,
                  child: controller.thinMode
                      ? ServerIdeasScreen(controller: controller, pill: controller.pill)
                      : IdeasScreen(pill: _legacyIdeasPill(controller.pill)),
                ),
                const VerticalDivider(),
                Expanded(
                  child: signal == null || risk == null
                      ? const _PickIdea()
                      : IdeaDetailScreen(signal: signal, risk: risk, showBack: false),
                ),
              ],
            ),
          ),
        ],
      );
    }

    final screen = Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (!controller.isDetailOpen) _header(controller),
        Expanded(child: _screen(controller)),
      ],
    );
    return pane.isCompact
        ? screen
        : ReadableColumn(maxWidth: pane.contentWidth, child: screen);
  }

  Widget _header(AppController controller) => SectionHeader(
        section: controller.section,
        pill: controller.pill,
        onPill: controller.goPill,
        mode: controller.riskMode,
        dataAt: controller.dataFreshness,
        health: controller.dataHealth,
        healthDetail: controller.dataHealthDetail,
        pillCounts: _ideaPillCounts(controller),
        onHealth: () => controller.showToast(
          controller.dataHealthDetail,
          tone: ToastTone.warning,
        ),
      );

  List<int>? _ideaPillCounts(AppController controller) {
    if (controller.section != AppSection.ideas) return null;
    final funnel = IdeaFunnelSnapshot.from(
      ideas: controller.ideas,
      trades: controller.paperPositions,
    );
    return [
      funnel.total,
      funnel.decisions.length,
      funnel.forming.length,
      funnel.pending.length,
      funnel.open.length,
    ];
  }

  /// Legacy local/demo feed still uses the historical four-state filter. The
  /// visible navigation belongs to the owner-facing five-stage funnel, so the
  /// adapter stays here at the UI boundary rather than leaking into domain
  /// logic. Production thin mode never uses this mapping.
  int _legacyIdeasPill(int visibleIndex) {
    final visible = IdeaFunnelPill
        .values[visibleIndex.clamp(0, IdeaFunnelPill.values.length - 1)];
    return switch (visible) {
      IdeaFunnelPill.all => IdeasPill.all.index,
      IdeaFunnelPill.decisions => IdeasPill.decisions.index,
      IdeaFunnelPill.forming => IdeasPill.watch.index,
      IdeaFunnelPill.pending || IdeaFunnelPill.open => IdeasPill.active.index,
    };
  }

  Widget _screen(AppController controller) {
    if (controller.isDetailOpen) {
      final signal = controller.currentSignal;
      final risk = controller.risk;
      if (signal != null && risk != null) {
        return IdeaDetailScreen(signal: signal, risk: risk);
      }
    }

    if (controller.section == AppSection.settings) {
      // На экране настроек всегда один и тот же видимый порядок из пяти
      // разделов. SettingsPill.values хранит legacy mode/security ради
      // совместимости, поэтому видимый индекс нельзя трактовать как enum.index.
      final selected = SettingsPill.thinAt(controller.pill);
      if (controller.thinMode) {
        return switch (selected) {
          SettingsPill.risk => const ServerRiskScreen(),
          SettingsPill.connections => const ServerIntegrationsScreen(),
          SettingsPill.strategies => StrategyComparisonPanel(
              child: StrategiesScreen(
                snapshot: controller.strategies!,
                backtestRunning: controller.backtestRunning,
                optimizing: controller.optimizing,
                backtestStage: controller.analysisStage,
              ),
            ),
          // Старый экран уже содержит реальные тумблеры уведомлений; передаём
          // исторический enum-index, а не видимый индекс.
          SettingsPill.notifications => SettingsScreen(
              snapshot: controller.settings!,
              pill: SettingsPill.notifications.index,
            ),
          SettingsPill.data => const CapacityStatusPanel(
              child: ServerDataScreen(),
            ),
          _ => const ServerRiskScreen(),
        };
      }

      return switch (selected) {
        SettingsPill.strategies => StrategiesScreen(
            snapshot: controller.strategies!,
            backtestRunning: controller.backtestRunning,
            optimizing: controller.optimizing,
            backtestStage: controller.analysisStage,
          ),
        SettingsPill.data => const DiagnosticsScreen(),
        _ => SettingsScreen(
            snapshot: controller.settings!,
            pill: selected.index,
          ),
      };
    }

    return switch (controller.section) {
      AppSection.today => controller.thinMode
          ? const ServerTodayScreen()
          : const TodayScreen(),
      AppSection.portfolio => PortfolioScreen(pill: controller.pill),
      AppSection.ideas => controller.thinMode
          ? ServerIdeasScreen(controller: controller, pill: controller.pill)
          : IdeasScreen(pill: _legacyIdeasPill(controller.pill)),
      AppSection.journal => controller.thinMode
          ? ServerJournalScreen(pill: controller.pill)
          : JournalScreen(pill: controller.pill, summary: controller.trades!),
      AppSection.settings => throw StateError('settings handled above'),
    };
  }

  Widget _sheet(BuildContext context, AppController controller, Pane pane) {
    final signal = controller.currentSignal;
    final risk = controller.risk;
    final idea = controller.currentIdea;
    if (signal == null || risk == null) return const SizedBox.shrink();

    // Manual risk is enabled only when the server-owned execution lifecycle is
    // known to be PAPER. Missing scope/mode fails closed instead of guessing.
    final serverMode = ExecutionModeScope.maybeOf(context)?.mode.label ?? 'НЕИЗВЕСТЕН';
    final sheet = RiskBoostConfirmFlow(
      ideaId: idea?.id ?? '',
      currentMode: serverMode,
      signal: signal,
      plan: idea?.plan,
      risk: risk,
      impact: controller.currentImpact,
      busy: controller.confirming,
      paperOnly: idea != null && !controller.demoData,
      onExecute: controller.confirmCurrentSignal,
      onClose: controller.closeSheet,
    );
    return Positioned.fill(
      child: TweenAnimationBuilder<double>(
        tween: Tween(begin: 0, end: 1),
        duration: const Duration(milliseconds: 250),
        curve: Curves.easeOut,
        builder: (context, t, child) => Opacity(
          opacity: t,
          child: Transform.translate(
            offset: Offset(0, 40 * (1 - t)),
            child: child,
          ),
        ),
        child: pane.isCompact
            ? sheet
            : Center(
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 440),
                  child: sheet,
                ),
              ),
      ),
    );
  }
}

class DemoBanner extends StatelessWidget {
  const DemoBanner({super.key});
  @override
  Widget build(BuildContext context) => Container(
        width: double.infinity,
        padding: const EdgeInsets.symmetric(horizontal: S.screen, vertical: 7),
        color: C.warningFaint,
        child: Text(
          'ДЕМО · данные макета, а не рынок. Уровни, риск и результаты выдуманы — исполнять их нельзя.',
          textAlign: TextAlign.center,
          style: T.body(10.5, weight: 700, color: C.warning, height: 1.35),
        ),
      );
}

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
              Text.rich(TextSpan(
                text: 'Signal',
                style: T.jost(22),
                children: [
                  TextSpan(text: 'AI', style: T.jost(22, color: C.accent)),
                ],
              )),
            ],
          ),
        ),
      );

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
                    'Не получилось загрузить данные приложения. Проверьте подключение к интернету и повторите попытку.',
                    textAlign: TextAlign.center,
                    style: T.body(12, color: C.muted, height: 1.5),
                  ),
                  const SizedBox(height: 18),
                  GestureDetector(
                    onTap: onRetry,
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 22,
                        vertical: 13,
                      ),
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