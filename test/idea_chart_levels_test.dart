import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/engine_contract.dart';
import 'package:signalai/domain/idea/evidence.dart';

/// График идеи не остаётся голыми свечами, даже если разметка не приехала.
///
/// Идея — снимок неизменяемый: посчитанная вчерашней версией движка, она
/// живёт свой срок как есть, и переписать её задним числом нельзя — по ней
/// принято решение. Но владельцу график нужен сейчас, а не через три дня.
/// Уровни плана достраиваются из самого плана: те же вход, стоп и цели уже
/// показаны текстом рядом, и §9.1 требует, чтобы картинка повторяла текст.
void main() {
  Map<String, dynamic> detail({List<Map<String, dynamic>> annotations = const []}) => {
        'id': 'i1',
        'instrument_id': 'CRYPTO:PERP:ETHUSDT',
        'symbol': 'ETHUSDT',
        'strategy': 'WYCKOFF_REVERSAL',
        'direction': 'LONG',
        'status': 'WATCH',
        'quality_status': 'OK',
        'horizon_days': 5,
        'score': '84',
        'signal_time': '2026-07-30T09:00:00Z',
        'expires_at': '2026-08-03T09:00:00Z',
        'context_timeframe': '1d',
        'setup_timeframe': '4h',
        'trigger_timeframe': '1h',
        'plan': {
          'order_intent': 'LIMIT_RETEST',
          'entry_low': '1858.12',
          'entry_high': '1874.02',
          'entry_reference': '1866.07',
          'stop': '1845.91',
          'tp1': '1896.94',
          'tp2': '1935.76',
          'tp3': '2013.40',
          'rr_tp1': '1.5',
          'rr_tp2': '3.5',
          'invalidation': 'закрытие ниже границы',
        },
        'annotations': annotations,
      };

  test('без разметки с сервера уровни плана всё равно на графике', () {
    final idea = EngineContract.idea(detail());
    // Сервер прислал пустую разметку — старая идея. График обязан показать
    // хотя бы саму сделку.
    expect(idea.annotations, isEmpty);
    final layers = idea.availableLayers;
    expect(layers, contains(ChartLayer.levels));

    final drawn = idea.annotationsFor({ChartLayer.levels});
    final types = drawn.map((a) => a.type).toSet();
    expect(types, contains(AnnotationType.levelEntry));
    expect(types, contains(AnnotationType.levelStop));
    expect(types, contains(AnnotationType.levelTarget));
    // Целей ровно столько, сколько в плане, — ни одной выдуманной.
    expect(
      drawn.where((a) => a.type == AnnotationType.levelTarget).length,
      3,
    );
    // Цены совпадают с планом до знака: это те же числа, что показаны текстом.
    final stop = drawn.firstWhere((a) => a.type == AnnotationType.levelStop);
    expect(stop.priceLow, closeTo(1845.91, 1e-9));
    final entry = drawn.firstWhere((a) => a.type == AnnotationType.levelEntry);
    expect(entry.priceLow, closeTo(1858.12, 1e-9));
    expect(entry.priceHigh, closeTo(1874.02, 1e-9));
  });

  test('разметка движка не дублируется достроенной', () {
    final idea = EngineContract.idea(detail(annotations: [
      {
        'id': 'plan-stop',
        'type': 'levelStop',
        'timeframe': '1h',
        'start_time': '2026-07-30T09:00:00Z',
        'end_time': '2026-08-03T09:00:00Z',
        'price_low': '1845.91',
        'price_high': '1845.91',
        'confidence': 1,
        'evidence_id': 'plan',
        'detector_version': 'plan-1.0.0',
        'label': 'Стоп',
        'display_priority': 96,
      },
    ]));
    final stops = idea
        .annotationsFor({ChartLayer.levels})
        .where((a) => a.type == AnnotationType.levelStop);
    expect(stops.length, 1, reason: 'стоп нарисован дважды');
    expect(stops.single.detectorVersion, 'plan-1.0.0', reason: 'подменён свой');
  });

  test('находки детекторов не выдумываются', () {
    final idea = EngineContract.idea(detail());
    // Вайкофф и смарт-мани восстановить из плана нельзя, и врать о них
    // нельзя тем более: их слоёв просто нет.
    expect(idea.availableLayers, isNot(contains(ChartLayer.wyckoff)));
    expect(idea.availableLayers, isNot(contains(ChartLayer.smc)));
  });

  test('один неокруглённый край не задаёт вид всем ценам', () {
    // Ровно случай с боевого сервера: граница зоны пришла неокруглённой.
    final raw = detail();
    (raw['plan'] as Map<String, dynamic>)['entry_high'] = '1874.023606';
    final signal = EngineContract.signalFrom(EngineContract.idea(raw));
    // Медиана, а не максимум: пять цен из шести — с двумя знаками.
    expect(signal.priceDecimals, 2);
  });

  test('по-настоящему дробные цены не огрубляются', () {
    final raw = detail();
    final plan = raw['plan'] as Map<String, dynamic>;
    plan['entry_low'] = '0.084512';
    plan['entry_high'] = '0.085134';
    plan['entry_reference'] = '0.084823';
    plan['stop'] = '0.083901';
    plan['tp1'] = '0.086742';
    plan['tp2'] = '0.088310';
    plan.remove('tp3');
    final signal = EngineContract.signalFrom(EngineContract.idea(raw));
    expect(signal.priceDecimals, 6);
  });
}
