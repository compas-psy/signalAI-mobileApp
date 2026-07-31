import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/engine_contract.dart';
import 'package:signalai/domain/idea/evidence.dart';
import 'package:signalai/domain/idea/idea_state.dart';

/// Два вопроса владельца об одной карточке BTCUSDT: когда идея появилась и
/// актуальна ли она ещё.
///
/// На экране не было ответа ни на один. График рисовал уровни от края до
/// края, и момент рождения идеи ничем не отличался от любой другой свечи;
/// состояние оставалось «Watch» неделю подряд, пока цена ходила к третьей
/// цели и обратно.
void main() {
  Map<String, dynamic> detail({
    String status = 'WATCH',
    String closingReason = '',
  }) =>
      {
        'id': 'i1',
        'instrument_id': 'CRYPTO:PERP:BTCUSDT',
        'symbol': 'BTCUSDT',
        'strategy': 'TREND_PULLBACK',
        'direction': 'SHORT',
        'status': status,
        'quality_status': 'OK',
        'horizon_days': 5,
        'score': '80',
        'signal_time': '2026-07-27T09:00:00Z',
        'expires_at': '2026-08-01T09:00:00Z',
        'context_timeframe': '1d',
        'setup_timeframe': '4h',
        'trigger_timeframe': '1h',
        'closing_reason': closingReason,
        'plan': {
          'order_intent': 'LIMIT_RETEST',
          'entry_low': '64739.6',
          'entry_high': '65009.7',
          'entry_reference': '64874.6',
          'stop': '65208.2',
          'tp1': '64469.4',
          'tp2': '64199.3',
          'tp3': '63929.1',
          'rr_tp1': '1.0',
          'rr_tp2': '2.0',
          'invalidation': 'закрытие выше границы',
        },
      };

  group('Когда идея появилась', () {
    test('на графике стоит метка момента, а не только уровни', () {
      final idea = EngineContract.idea(detail());
      final drawn = idea.annotationsFor({ChartLayer.levels});
      final mark = drawn.where((a) => a.type == AnnotationType.planCreated);
      expect(mark, hasLength(1));
      // Метка стоит ровно в момент расчёта идеи — по ней и видно свечу.
      expect(mark.first.startTime.toUtc(), DateTime.utc(2026, 7, 27, 9));
      expect(mark.first.label, 'Идея');
    });

    test('метка живёт в слое уровней, а не в событиях', () {
      // «События» — это риск календаря: отчёты, ставки. Момент рождения
      // идеи туда не относится, и выключаться вместе с ними не должен.
      expect(AnnotationType.planCreated.layer, ChartLayer.levels);
    });

    test('метка не дублирует то, что прислал сервер', () {
      final json = detail();
      json['annotations'] = [
        {
          'id': 'srv-1',
          'type': 'planCreated',
          'timeframe': '4h',
          'start_time': '2026-07-27T09:00:00Z',
          'end_time': '2026-07-27T09:00:00Z',
          'evidence_id': 'plan',
          'label': 'Идея',
        },
      ];
      final drawn = EngineContract.idea(json).annotationsFor({ChartLayer.levels});
      expect(
        drawn.where((a) => a.type == AnnotationType.planCreated),
        hasLength(1),
      );
    });
  });

  group('Актуальна ли идея', () {
    test('рынок ушёл без нас — своё состояние, а не «Watch»', () {
      // Раньше MISSED не разбирался вовсе и падал в watch: обогнанная
      // рынком идея неделю выглядела живой.
      final idea = EngineContract.idea(detail(status: 'MISSED'));
      expect(idea.state, IdeaState.missed);
      expect(idea.state.isTerminal, isTrue);
      expect(idea.state.canConfirm, isFalse);
    });

    test('тайм-аут движка — это истёкший срок, а не наблюдение', () {
      expect(
        EngineContract.idea(detail(status: 'TIMED_OUT')).state,
        IdeaState.expired,
      );
    });

    test('причина закрытия доезжает дословно', () {
      // Состояние отвечает «что», причина — «почему». Без второго «рынок
      // ушёл без нас» не отличить от «срок истёк».
      const reason = 'цена дошла до дальней цели 63929.1, ни разу не зайдя '
          'в зону входа 64739.6–65009.7: сетап отработал без нас';
      final idea = EngineContract.idea(
        detail(status: 'MISSED', closingReason: reason),
      );
      expect(idea.closingReason, reason);
    });

    test('живая идея причины закрытия не несёт', () {
      expect(EngineContract.idea(detail()).closingReason, isEmpty);
    });

    test('из терминального состояния никуда не уходят', () {
      expect(IdeaState.missed.allowedNext, isEmpty);
    });

    test('обогнать рынком можно и сработавший сигнал', () {
      expect(IdeaState.triggered.canMoveTo(IdeaState.missed), isTrue);
      expect(IdeaState.watch.canMoveTo(IdeaState.missed), isTrue);
    });
  });
}
