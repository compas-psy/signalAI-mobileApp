import 'package:flutter/widgets.dart';

import '../state/app_controller.dart';
import '../state/app_scope.dart';
import '../theme/tokens.dart';
import '../theme/typography.dart';
import 'screens/idea_detail_screen.dart';
import 'screens/ideas_screen.dart';
import 'screens/settings_screen.dart';
import 'screens/strategies_screen.dart';
import 'screens/trades_screen.dart';
import 'widgets/confirm_sheet.dart';
import 'widgets/toast.dart';
import 'widgets/bottom_nav.dart';

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
        child: Stack(
          children: [
            Column(
              children: [
                Expanded(child: _screen(controller)),
                if (!controller.sheetOpen)
                  BottomNav(
                    current: controller.tab,
                    detailOpen: controller.isDetailOpen,
                    onSelect: controller.goTab,
                  ),
              ],
            ),
            if (controller.sheetOpen) _sheet(controller),
            if (controller.toast != null)
              Positioned(
                top: 10,
                left: 14,
                right: 14,
                child: AppToast(message: controller.toast!),
              ),
          ],
        ),
      ),
    );
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
      AppTab.ideas => IdeasScreen(digest: controller.digest!),
      AppTab.trades => TradesScreen(summary: controller.trades!),
      AppTab.strategies => StrategiesScreen(
          snapshot: controller.strategies!,
          backtestRunning: controller.backtestRunning,
        ),
      AppTab.settings => SettingsScreen(snapshot: controller.settings!),
    };
  }

  Widget _sheet(AppController controller) {
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
        child: ConfirmSheet(
          signal: signal,
          risk: risk,
          busy: controller.confirming,
          onExecute: controller.confirmCurrentSignal,
          onClose: controller.closeSheet,
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
                  Text('Нет связи с сервером', style: T.jost(20)),
                  const SizedBox(height: 8),
                  Text(
                    'Сигналы приходят с вашего сервера. Проверьте подключение '
                    'и повторите попытку.',
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
