import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/engine_client.dart';
import 'package:signalai/data/api/engine_contract.dart';
import 'package:signalai/domain/idea/idea.dart';
import 'package:signalai/domain/idea/paper_position.dart';
import 'package:signalai/monitor/server_background_cycle.dart';

Map<String, dynamic> _ideaJson(String id) => {
      'id': id,
      'instrument_id': 'CRYPTO:PERP:${id.toUpperCase()}',
      'symbol': id.toUpperCase(),
      'strategy': 'TREND_PULLBACK',
      'direction': 'LONG',
      'status': 'TRIGGERED',
      'quality_status': 'ACTIVE',
      'score': '82',
      'signal_time': '2026-08-06T09:00:00Z',
      'expires_at': '2026-08-07T09:00:00Z',
    };

Idea _idea(
  String id, {
  IdeaReadiness readiness = IdeaReadiness.active,
}) =>
    EngineContract.idea(
      _ideaJson(id),
      readiness: readiness,
      actionable: readiness.canAct,
    );

PaperPosition _trade({
  PaperPositionStatus status = PaperPositionStatus.pending,
  int tps = 0,
  double stop = 90,
  bool breakeven = false,
  double resultR = 0,
  String outcome = '',
  String closeReason = '',
}) =>
    PaperPosition(
      id: 'paper-1',
      ideaId: 'btc',
      symbol: 'BTCUSDT',
      long: true,
      pending: status == PaperPositionStatus.pending,
      status: status,
      entry: 100,
      initialStop: 90,
      currentStop: stop,
      tpPrices: const [110, 120, 130],
      tpsTaken: tps,
      resultR: resultR,
      resultRealized: true,
      atBreakeven: breakeven,
      outcome: outcome,
      closeReason: closeReason,
      fromServer: true,
    );

void main() {
  final now = DateTime.utc(2026, 8, 6, 12);

  test('закрытое приложение получает только новую actionable-идею', () async {
    final state = ServerMonitorState();
    final cycle = ServerBackgroundCycle(
      loadIdeas: () async => EngineIdeas(ideas: [
        _idea('btc'),
        _idea('eth', readiness: IdeaReadiness.waiting),
      ]),
      loadPaperTrades: () async => [_trade()],
    );

    final report = await cycle.run(state: state, now: now);

    expect(report.complete, isTrue);
    expect(report.notices, hasLength(1));
    expect(report.notices.single.title, contains('BTC'));
    expect(report.notices.single.title, contains('можно действовать'));
    expect(report.notices.single.title, isNot(contains('ETH')));
    expect(state.snapshot?.ideas.keys, ['btc']);
    expect(state.lastFingerprint, isNotEmpty);
  });

  test('повтор и рестарт не дублируют snapshot-события', () async {
    final cycle = ServerBackgroundCycle(
      loadIdeas: () async => EngineIdeas(ideas: [_idea('btc')]),
      loadPaperTrades: () async => [_trade()],
    );
    final state = ServerMonitorState();
    await cycle.run(state: state, now: now);

    final restored = ServerMonitorState.fromJson(state.toJson());
    final repeat = await cycle.run(
      state: restored,
      now: now.add(const Duration(minutes: 15)),
    );

    expect(repeat.notices, isEmpty);
    expect(restored.lastFingerprint, state.lastFingerprint);
    expect(restored.notifiedIdeas, {'btc'});
  });

  test('paper сообщает open, TP с безубытком и close по одному разу',
      () async {
    var trades = [_trade()];
    final state = ServerMonitorState();
    final cycle = ServerBackgroundCycle(
      loadIdeas: () async => const EngineIdeas(ideas: []),
      loadPaperTrades: () async => trades,
    );
    await cycle.run(state: state, now: now);

    trades = [_trade(status: PaperPositionStatus.open)];
    var report = await cycle.run(
      state: state,
      now: now.add(const Duration(minutes: 15)),
    );
    expect(report.notices.single.title, contains('открыта'));

    trades = [
      _trade(
        status: PaperPositionStatus.open,
        tps: 1,
        stop: 100,
        breakeven: true,
        resultR: 0.4,
      ),
    ];
    report = await cycle.run(
      state: state,
      now: now.add(const Duration(minutes: 30)),
    );
    expect(report.notices, hasLength(1));
    expect(report.notices.single.title, contains('тейк 1'));
    expect(report.notices.single.body, contains('безубыток'));

    trades = [
      _trade(
        status: PaperPositionStatus.closed,
        tps: 1,
        stop: 100,
        breakeven: true,
        resultR: 1.2,
        outcome: 'TP2',
        closeReason: 'цель достигнута',
      ),
    ];
    report = await cycle.run(
      state: state,
      now: now.add(const Duration(minutes: 45)),
    );
    expect(report.notices.single.title, contains('закрыта'));
    expect(report.notices.single.body, contains('+1,20R'));

    final restored = ServerMonitorState.fromJson(state.toJson());
    expect(
      (await cycle.run(
        state: restored,
        now: now.add(const Duration(hours: 1)),
      ))
          .notices,
      isEmpty,
    );
  });

  test('новая server paper-заявка уведомляет один раз', () async {
    var trades = <PaperPosition>[];
    final state = ServerMonitorState();
    final cycle = ServerBackgroundCycle(
      loadIdeas: () async => const EngineIdeas(ideas: []),
      loadPaperTrades: () async => trades,
    );
    await cycle.run(state: state, now: now);

    trades = [_trade()];
    final appeared = await cycle.run(
      state: state,
      now: now.add(const Duration(minutes: 15)),
    );
    expect(appeared.notices.single.title, contains('заявка принята'));

    final repeat = await cycle.run(
      state: state,
      now: now.add(const Duration(minutes: 30)),
    );
    expect(repeat.notices, isEmpty);
  });

  test('неполный poll не затирает snapshot и безопасно повторяется',
      () async {
    var unavailable = false;
    var trades = [_trade(status: PaperPositionStatus.open)];
    final state = ServerMonitorState();
    final cycle = ServerBackgroundCycle(
      loadIdeas: () async => unavailable
          ? const EngineIdeas.unavailable('нет сети')
          : const EngineIdeas(ideas: []),
      loadPaperTrades: () async => trades,
    );
    await cycle.run(state: state, now: now);
    final before = state.lastFingerprint;

    unavailable = true;
    trades = [
      _trade(
        status: PaperPositionStatus.closed,
        resultR: -1,
        outcome: 'SL',
      ),
    ];
    final failed = await cycle.run(
      state: state,
      now: now.add(const Duration(minutes: 15)),
    );
    expect(failed.complete, isFalse);
    expect(failed.notices, isEmpty);
    expect(state.lastFingerprint, before);
    expect(state.snapshot?.trades['paper-1']?.status, PaperPositionStatus.open);

    unavailable = false;
    final retry = await cycle.run(
      state: state,
      now: now.add(const Duration(minutes: 30)),
    );
    expect(retry.complete, isTrue);
    expect(retry.notices.single.title, contains('закрыта'));
  });

  test('null paper response тоже не объявляет позиции закрытыми', () async {
    var paperAvailable = true;
    final state = ServerMonitorState();
    final cycle = ServerBackgroundCycle(
      loadIdeas: () async => const EngineIdeas(ideas: []),
      loadPaperTrades: () async =>
          paperAvailable ? [_trade(status: PaperPositionStatus.open)] : null,
    );
    await cycle.run(state: state, now: now);
    final before = state.lastFingerprint;
    paperAvailable = false;

    final report = await cycle.run(
      state: state,
      now: now.add(const Duration(minutes: 15)),
    );

    expect(report.complete, isFalse);
    expect(report.notices, isEmpty);
    expect(state.lastFingerprint, before);
  });
}
