import '../../domain/idea/idea.dart';
import '../../domain/idea/paper_position.dart';

String paperPositionOrigin(
  PaperPosition trade, {
  required Idea? idea,
  required bool canOpenIdea,
}) {
  if (idea != null) return 'из идеи ${idea.strategy.label}';

  if (trade.fromServer) {
    if (trade.ideaId.isEmpty) return 'серверный PAPER';
    return canOpenIdea
        ? 'серверный PAPER · разбор открыт'
        : 'серверный PAPER · идея вне текущей выдачи';
  }

  if (trade.ideaId.isEmpty) {
    return 'заведена расчётом на устройстве · идеи за ней не записано';
  }
  return canOpenIdea
      ? 'из расчёта на устройстве · разбор открыт'
      : 'идеи за ней нет в текущей выдаче · открывать нечего';
}
