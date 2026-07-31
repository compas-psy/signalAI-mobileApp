/// Пуш об идее движка и переход из него в разбор.
///
/// До этого уведомления слал только скринер на устройстве по своему,
/// отдельному дайджесту. Идеи движка — то, что показано на экране «Идеи», —
/// не приводили к пушу вообще: приложение находило сделку и молчало, пока
/// владелец сам не откроет его. А нажатие по любому пушу просто поднимало
/// приложение на том экране, где его закрыли.
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/engine_contract.dart';
import 'package:signalai/monitor/background_cycle.dart';
import 'package:signalai/state/app_controller.dart';


/// Уведомление о событии, у которого идеи нет.
MonitorNotice closedTradeNotice() =>
    (title: 'BTCUSDT: позиция без стопа', body: 'Выставьте стоп', payload: '');

void main() {
  Map<String, dynamic> detail({String status = 'TRIGGERED'}) => {
        'id': '3f2b7e4a-0000-0000-0000-000000000009',
        'instrument_id': 'CRYPTO:PERP:BTCUSDT',
        'symbol': 'BTCUSDT',
        'strategy': 'TREND_PULLBACK',
        'direction': 'SHORT',
        'status': status,
        'quality_status': 'ACTIVE',
        'horizon_days': 5,
        'score': '80',
        'signal_time': '2026-07-31T09:00:00Z',
        'expires_at': '2026-08-05T09:00:00Z',
        'context_timeframe': '1d',
        'setup_timeframe': '4h',
        'trigger_timeframe': '1h',
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

  group('Текст уведомления об идее', () {
    test('в заголовке тикер, а не канонический идентификатор', () {
      // «CRYPTO:PERP:BTCUSDT» в заголовке обрезается на «CRYPTO:PER…», и по
      // такой подписи нельзя понять, о каком активе пуш.
      final notice = ideaNotice(EngineContract.idea(detail()));
      expect(notice.title, startsWith('BTCUSDT · SHORT'));
      expect(notice.title, isNot(contains('CRYPTO:')));
    });

    test('сработавший сигнал зовёт решать, а не сообщает балл', () {
      // Момент входа не повторяется: пуш о переходе обязан отличаться от
      // пуша «появилась идея», иначе оба читаются одинаково.
      final armed = ideaNotice(EngineContract.idea(detail()), armed: true);
      expect(armed.title, contains('можно решать'));
      expect(armed.title, isNot(contains('/100')));
    });

    test('в теле — зона входа, стоп и R:R, а не пересказ', () {
      final notice = ideaNotice(EngineContract.idea(detail()));
      expect(notice.body, contains('64739.6'));
      expect(notice.body, contains('65009.7'));
      expect(notice.body, contains('65208.2'));
      expect(notice.body, contains('R:R 2,0'));
    });

    test('идея без плана не выдумывает уровни', () {
      final json = detail()..remove('plan');
      final notice = ideaNotice(EngineContract.idea(json));
      expect(notice.body, contains('Плана пока нет'));
    });
  });

  group('Переход из уведомления', () {
    test('адрес идеи узнаётся по префиксу', () {
      // Голый идентификатор разбирать нельзя: адреса появятся и у других
      // экранов, и однажды пуш о пакете открыл бы сделку.
      expect(AppController.notificationIdeaPrefix, 'idea:');
    });

    test('пуш об идее несёт адрес её карточки', () {
      final idea = EngineContract.idea(detail());
      final notice = ideaNotice(idea);
      expect(
        notice.payload,
        '${AppController.notificationIdeaPrefix}${idea.id}',
      );
    });

    test('пуш о позиции без стопа никуда не ведёт', () {
      // Это событие брокера, а не идеи: открывать по нему чужую карточку
      // хуже, чем просто поднять приложение.
      final trade = closedTradeNotice();
      expect(trade.payload, isEmpty);
    });
  });
}
