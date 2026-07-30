import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/market/idea_chart_source.dart';
import 'package:signalai/domain/analysis/candle.dart';
import 'package:signalai/domain/enums.dart';
import 'package:signalai/domain/idea/idea.dart';
import 'package:signalai/domain/idea/idea_state.dart';
import 'package:signalai/domain/idea/quality_score.dart';

Idea _idea(List<String> timeframes, {String id = 'MOEX:FUT:SIU6'}) => Idea(
      id: 'x',
      instrumentId: id,
      instrumentName: 'демо',
      market: Market.forts,
      direction: Direction.long,
      strategy: SetupStrategy.trendPullback,
      strategyVersion: 'v1',
      state: IdeaState.watch,
      score: const QualityScore.empty(),
      createdAt: DateTime(2026, 7, 29),
      validUntil: DateTime(2026, 7, 30),
      thesis: '',
      plan: null,
      timeframes: timeframes,
    );

/// Свечи графика прямо с биржи — второй источник после движка.
///
/// Решение владельца: подключённые источники данных (MOEX ISS, публичный
/// Bybit) годятся не только для расчёта, но и для картинки, и ключа не
/// требуют. Проверяется отбор, а не сеть: до биржи в тестах не ходим.
void main() {
  test('тикер вынимается из канонического идентификатора', () {
    // Движок называет инструмент площадкой, классом и кодом; биржа знает
    // только код.
    expect(IdeaChartSource.symbolOf('MOEX:FUT:SIU6'), 'SIU6');
    expect(IdeaChartSource.symbolOf('BYBIT:PERP:BTCUSDT'), 'BTCUSDT');
    // Без префиксов идентификатор и есть тикер.
    expect(IdeaChartSource.symbolOf('SiU6'), 'SiU6');
  });

  test('берётся таймфрейм сетапа, а не контекста и не триггера', () {
    // Контекстный слишком крупен для зоны входа, триггерный слишком мелок
    // для структуры; разметка §10.6 живёт на сетапном.
    expect(IdeaChartSource.timeframeOf(_idea(['1d', '4h', '1h'])), Timeframe.h4);
    // Один таймфрейм — он и есть сетапный.
    expect(IdeaChartSource.timeframeOf(_idea(['1h'])), Timeframe.h1);
    // Пустой список: часовик как наименее вредное умолчание.
    expect(IdeaChartSource.timeframeOf(_idea(const [])), Timeframe.h1);
  });

  test('подпись графика называет тот таймфрейм, который нарисован', () {
    // Смысл всей проверки: у ISS нет четырёх часов, у Bybit нет десяти
    // минут. Заменять их молча нельзя — «4H» поверх часовых свечей это
    // картинка, по которой принимают решение.
    expect(IdeaChartSource.labelOf(Timeframe.h1), '1h');
    expect(IdeaChartSource.labelOf(Timeframe.h4), '4h');
    expect(IdeaChartSource.labelOf(Timeframe.d1), '1d');
    expect(IdeaChartSource.labelOf(Timeframe.m10), '10m');
  });

  test('таймфрейм контракта разбирается в обоих написаниях', () {
    expect(IdeaChartSource.parse('1h'), Timeframe.h1);
    expect(IdeaChartSource.parse('H1'), Timeframe.h1);
    expect(IdeaChartSource.parse('4H'), Timeframe.h4);
    expect(IdeaChartSource.parse('1d'), Timeframe.d1);
    // Недельные и месячные для картинки сетапа бесполезны — сводятся к дневкам.
    expect(IdeaChartSource.parse('1w'), Timeframe.d1);
  });
}
