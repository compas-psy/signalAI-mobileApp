import '../data/local_analysis_repository.dart';
import '../data/repository.dart';
import '../data/state_lock.dart';
import 'app_controller.dart';

/// Coordinates foreground recovery without racing a resumed deep link against
/// the fresh server summary.
///
/// In thin mode a notification can return to an already-running process. The
/// full idea detail must be hydrated only after the current `/ideas/today`
/// snapshot is installed; otherwise a slower summary response can replace the
/// hydrated TradePlan with its summary-only copy.
Future<void> resumeApp({
  required AppController controller,
  required SignalAiRepository repository,
  required bool thinMode,
}) async {
  if (!thinMode) {
    await controller.onAppResumed();
    return;
  }

  if (repository is LocalAnalysisRepository) {
    await repository.stateLock.heartbeat(StateLock.ui);
  }

  await controller.refreshIdeas();
  await controller.openFromNotification();
  await controller.refreshCapital();
}
