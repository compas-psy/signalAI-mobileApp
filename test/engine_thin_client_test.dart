import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/api_client.dart';
import 'package:signalai/data/api/engine_client.dart';
import 'package:signalai/data/api/engine_contract.dart';
import 'package:signalai/domain/enums.dart';
import 'package:signalai/domain/idea/idea.dart';

class _FakeApi extends ApiClient {
  _FakeApi({
    this.todayResponse = const <String, dynamic>{},
    this.postResponse = const <String, dynamic>{},
    this.barsByTimeframe = const <String, List<dynamic>>{},
  }) : super(baseUrl: 'https://engine.test', deviceToken: '');

  final Map<String, dynamic> todayResponse;
  final Map<String, dynamic> postResponse;
  final Map<String, List<dynamic>> barsByTimeframe;

  final List<String> paths = [];
  Map<String, dynamic>? lastBody;
  String? lastIdempotencyKey;

  @override
  Future<Map<String, dynamic>> get(String path) async {
    paths.add(path);
    return todayResponse;
  }

  @override
  Future<List<dynamic>> getList(String path) async {
    paths.add(path);
    final uri = Uri.parse('https://engine.test$path');
    return barsByTimeframe[uri.queryParameters['timeframe']] ?? const [];
  }

  @override
  Future<Map<String, dynamic>> post(
    String path, {
    Map<String, dynamic>? body,
    String? idempotencyKey,
    String? pairingSessionId,
  }) async {
    paths.add(path);
    lastBody = body;
    lastIdempotencyKey = idempotencyKey;
    return postResponse;
  }
}

Map<String, dynamic> _idea(String id) => {
      'id': id,
      'instrument_id': 'CRYPTO:PERP:BTCUSDT',
      'symbol': 'BTCUSDT',
      'strategy': 'TREND_PULLBACK',
      'direction': 'LONG',
      // Намеренно одинаковый lifecycle status в обеих корзинах: именно
      // bucket/readiness обязан не дать WAITING стать сделкой.
      'status': 'TRIGGERED',
      'quality_status': 'ACTIVE',
      'score': '82',
      'signal_time': '2026-08-06T09:00:00Z',
      'expires_at': '2026-08-07T09:00:00Z',
    };

Map<String, dynamic> _detail(String id) => {
      ..._idea(id),
      'context_timeframe': 'D1',
      'setup_timeframe': 'H4',
      'trigger_timeframe': 'H1',
      'plan': {
        'order_intent': 'LIMIT_RETEST',
        'entry_low': '60000',
        'entry_high': '60100',
        'stop': '59000',
        'tp1': '61000',
        'tp2': '62000',
        'tp3': '63000',
      },
      'sizing': {
        'risk_pct': '0.0075',
        'risk_amount': '1000',
        'quantity': '0.01',
        'risk_per_unit': '1000',
        'tradable': true,
      },
      'event_calendar': {
        'status': 'UNAVAILABLE',
        'reason_code': 'EVENT_SOURCE_UNAVAILABLE',
        'detail': 'календарь событий недоступен',
        'blocks_admission': true,
        'event': null,
      },
    };

Map<String, dynamic> _trade() => {
      'id': 'paper-1',
      'idea_id': 'active',
      'instrument_id': 'CRYPTO:PERP:BTCUSDT',
      'symbol': 'BTCUSDT',
      'direction': 'LONG',
      'status': 'PENDING',
      'entry': '60050',
      'initial_stop': '59000',
      'current_stop': '59000',
      'tp_prices': ['61000', '62000', '63000'],
      'tps_taken': 0,
      'realized_r': '0',
    };

void main() {
  group('readiness выдачи', () {
    test('неизвестный календарь остаётся блокирующим риском на карточке', () {
      final idea = EngineContract.idea(_detail('calendar-unavailable'));

      expect(idea.eventRisk?.blocksEntry, isTrue);
      expect(idea.eventRisk?.policy, 'EVENT_SOURCE_UNAVAILABLE');
    });

    test('wait_for_trigger не сливается с actionable', () async {
      final api = _FakeApi(todayResponse: {
        'trade_now': [_idea('active')],
        'wait_for_trigger': [_idea('waiting')],
      });

      final ideas = (await EngineClient(client: api).today()).ideas;
      final active = ideas.singleWhere((idea) => idea.id == 'active');
      final waiting = ideas.singleWhere((idea) => idea.id == 'waiting');

      expect(active.readiness, IdeaReadiness.active);
      expect(active.actionable, isTrue);
      expect(waiting.readiness, IdeaReadiness.waiting);
      expect(waiting.actionable, isFalse);
    });

    test('WAITING с полным планом всё равно не получает подтверждение', () {
      final waiting = EngineContract.idea(
        _detail('waiting'),
        readiness: IdeaReadiness.waiting,
        actionable: false,
      );
      final active = EngineContract.idea(
        _detail('active'),
        readiness: IdeaReadiness.active,
        actionable: true,
      );

      expect(waiting.canApprovePaper, isFalse);
      expect(EngineContract.signalFrom(waiting).status, SignalStatus.watch);
      expect(EngineContract.signalFrom(waiting).status.canConfirm, isFalse);
      expect(active.canApprovePaper, isTrue);
      expect(EngineContract.signalFrom(active).status.canConfirm, isTrue);
    });
  });

  group('paper decisions', () {
    test('фоновый poll может запросить CLOSED через live_only=false', () async {
      final api = _FakeApi();

      await EngineClient(client: api).paperTrades(liveOnly: false);

      expect(api.paths.single, contains('live_only=false'));
      expect(api.paths.single, contains('limit=100'));
    });

    test('approve читает envelope и прикреплённую сделку', () async {
      final api = _FakeApi(postResponse: {
        'idea_id': 'active',
        'decision': 'APPROVED_PAPER',
        'idea_status': 'ACTIVE',
        'paper_only': true,
        'idempotent_replay': false,
        'trade': _trade(),
      });

      final decision = await EngineClient(client: api).approvePaper('active');

      expect(decision.trade?.id, 'paper-1');
      expect(decision.paperOnly, isTrue);
      expect(api.paths.single, '/api/v1/ideas/active/approve-paper');
      expect(api.lastIdempotencyKey, 'approve-paper:active');
    });

    test('approve отклоняет bare trade без явного paper_only', () async {
      await expectLater(
        EngineClient(client: _FakeApi(postResponse: _trade()))
            .approvePaper('active'),
        throwsA(
          isA<ApiException>().having(
            (error) => error.message,
            'message',
            contains('paper-only'),
          ),
        ),
      );
    });

    test('approve отклоняет envelope с paper_only=false', () async {
      await expectLater(
        EngineClient(
          client: _FakeApi(postResponse: {
            'idea_id': 'active',
            'decision': 'APPROVED_PAPER',
            'idea_status': 'ACTIVE',
            'paper_only': false,
            'trade': _trade(),
          }),
        ).approvePaper('active'),
        throwsA(isA<ApiException>()),
      );
    });

    test('reject отправляет справочную причину и идемпотентный ключ', () async {
      final api = _FakeApi(postResponse: const {
        'idea_id': 'waiting',
        'decision': 'REJECTED',
        'idea_status': 'CANCELLED',
        'paper_only': true,
        'idempotent_replay': false,
      });

      final decision = await EngineClient(client: api).rejectIdea(
        'waiting',
        reason: 'NO_TRUST',
        comment: 'нет подтверждения объёмом',
      );

      expect(decision.decision, 'REJECTED');
      expect(api.lastBody, {
        'reason': 'NO_TRUST',
        'comment': 'нет подтверждения объёмом',
      });
      expect(api.lastIdempotencyKey, 'reject:waiting');
    });
  });

  group('fallback графика', () {
    test('H4 нормализуется и затем пробуется H1 без промежуточной ошибки',
        () async {
      final api = _FakeApi(barsByTimeframe: {
        '1h': [
          {
            'open_time': '2026-08-06T09:00:00Z',
            'open': '100',
            'high': '102',
            'low': '99',
            'close': '101',
          },
        ],
      });
      final failures = <String>[];

      final chart = await EngineClient(client: api).barsWithFallback(
        'CRYPTO:PERP:BTCUSDT',
        setupTimeframe: 'H4',
        onFailure: failures.add,
      );

      expect(chart?.timeframeLabel, '1h');
      expect(api.paths[0], contains('timeframe=4h'));
      expect(api.paths[1], contains('timeframe=1h'));
      expect(failures, isEmpty);
    });

    test('итоговая причина появляется только после setup, H4, H1 и D1',
        () async {
      final api = _FakeApi();
      final failures = <String>[];

      final chart = await EngineClient(client: api).barsWithFallback(
        'MOEX:FUT:SIU6',
        setupTimeframe: '15m',
        onFailure: failures.add,
      );

      expect(chart, isNull);
      expect(
        api.paths.map((path) => Uri.parse('https://x$path').queryParameters['timeframe']),
        ['15m', '4h', '1h', '1d'],
      );
      expect(failures, hasLength(1));
      expect(failures.single, contains('проверены 15m, 4h, 1h, 1d'));
    });
  });
}
