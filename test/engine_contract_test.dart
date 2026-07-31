import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/engine_contract.dart';
import 'package:signalai/domain/enums.dart';
import 'package:signalai/domain/idea/evidence.dart';
import 'package:signalai/domain/idea/idea.dart';
import 'package:signalai/domain/idea/idea_state.dart';
import 'package:signalai/domain/idea/quality_score.dart';

/// Разбор контракта §18.
///
/// Эти тесты существуют потому, что расхождение клиента и сервера нашлось
/// на телефоне: клиент читал `cards`, сервер отдавал `trade_now`. Ошибка в
/// имени поля не ловится ни компилятором, ни анализатором — только сверкой
/// с ответом, и вот она.
///
/// Фикстура — форма ответа движка, а не выдуманный удобный JSON.
Map<String, dynamic> ideaJson() => {
      'id': '3f2b7e4a-0000-0000-0000-000000000001',
      'instrument_id': 'MOEX:FUT:SiU6',
      'symbol': 'SiU6',
      'strategy': 'TREND_PULLBACK',
      'direction': 'LONG',
      'status': 'TRIGGERED',
      'quality_status': 'ACTIVE',
      'horizon_days': 5,
      'score': '78.5',
      'p_tp1_before_sl': '0.61',
      'confidence': '0.55',
      'expected_r': '0.42',
      'rr_tp2': '2.4',
      'risk_amount': '12000',
      'signal_time': '2026-07-29T12:00:00Z',
      'expires_at': '2026-08-03T12:00:00Z',
      'context_timeframe': '1d',
      'setup_timeframe': '4h',
      'trigger_timeframe': '1h',
      'engine_version': '0.1.0',
      'plan': {
        'order_intent': 'LIMIT_RETEST',
        'entry_low': '90000',
        'entry_high': '90200',
        'entry_reference': '90100',
        'stop': '89500',
        'tp1': '91000',
        'tp2': '92000',
        'tp3': '93000',
        'rr_tp1': '1.5',
        'rr_tp2': '3.2',
        'invalidation': 'закрытие ниже границы зоны',
      },
      'sizing': {
        'risk_pct': '0.0075',
        'risk_amount': '12000',
        'quantity': '20',
        'risk_per_unit': '600',
      },
      'score_breakdown': {
        'total': '78.5',
        'data_quality': '0.9',
        'components': [
          {
            'name': 'regime_fit',
            'label': 'Соответствие режиму рынка',
            'weight': '0.16',
            'value': '80',
            'points': '12.8',
            'detail': 'UPTREND',
            'measured': true,
            'evidence_id': 'regime',
          },
          {
            'name': 'derivatives_confirmation',
            'label': 'Подтверждение производными',
            'weight': '0',
            'value': '0',
            'points': '0',
            'detail': 'открытого интереса у этого инструмента нет',
            'measured': false,
          },
        ],
      },
      'explanation': {'thesis': 'откат в зону спроса', 'headline': 'откат'},
      'evidence': [
        {
          'id': 'smc',
          'kind': 'smc_context',
          'title': 'Структура и Smart Money',
          'summary': 'BOS вверх',
          'detail': 'слом структуры подтверждён закрытием',
          'confidence': 0.8,
          'detector_version': 'smc-1.0.0',
          'conflicts_with': ['структура 4H против направления'],
        },
      ],
      'annotations': [
        {
          'id': 'smc-0',
          'type': 'smcOrderBlock',
          'timeframe': '1h',
          'start_time': '2026-07-29T09:00:00Z',
          'end_time': '2026-07-29T10:00:00Z',
          'price_low': 90000.0,
          'price_high': 90200.0,
          'confidence': 0.8,
          'evidence_id': 'smc',
          'detector_version': 'smc-1.0.0',
          'label': 'Order block',
          'display_priority': 60,
        },
        {
          'id': 'unknown-0',
          'type': 'мозгоправ',
          'timeframe': '1h',
          'start_time': '2026-07-29T09:00:00Z',
          'end_time': '2026-07-29T10:00:00Z',
          'evidence_id': 'smc',
        },
      ],
      'data_warnings': ['OI_UNAVAILABLE'],
    };

void main() {
  group('Идея из ответа движка', () {
    test('поля плана и размера позиции переносятся без пересчёта', () {
      final idea = EngineContract.idea(ideaJson());
      final plan = idea.plan!;

      expect(idea.instrumentId, 'MOEX:FUT:SiU6');
      expect(idea.direction, Direction.long);
      expect(idea.strategy, SetupStrategy.trendPullback);
      expect(plan.entryLow, 90000);
      expect(plan.stop, 89500);
      expect(plan.targets.length, 3);
      // Доли §21.1 — часть подписываемого плана, а не настройка интерфейса.
      expect(plan.targets.map((t) => t.fraction), [0.4, 0.4, 0.2]);
      // risk_pct приходит долей — на экране это проценты.
      expect(plan.riskPercent, closeTo(0.75, 1e-9));
    });

    test('дробный номинал не округляется до целого', () {
      // «28 ETHUSDT» появилось из деления рублёвого бюджета на риск в USDT.
      // После пересчёта объём стал дробным — и округление до целого снова
      // превратило бы его в ноль либо в позицию в три раза крупнее.
      final json = ideaJson();
      json['sizing'] = {
        'risk_pct': '0.0075',
        'risk_amount': '7496',
        'quantity': '0.348',
        'quantity_step': '0.001',
        'quantity_unit': 'ETH',
        'risk_per_unit': '20.16',
        'quote_currency': 'USDT',
        'quote_rate_rub': '80.0',
        'quote_note': 'USDT по курсу 80.0000 ₽ (MOEX FORTS SiU6 / 1000)',
      };
      final plan = EngineContract.idea(json).plan!;
      expect(plan.quantity, closeTo(0.348, 1e-9));
      expect(plan.quantityStep, closeTo(0.001, 1e-9));
      expect(plan.quantityDecimals, 3);
      expect(plan.quantityUnit, 'ETH');
      // Цены в USDT, риск — в рублях. Обе единицы названы, иначе экран
      // подписывает рублями то, что рублями не является.
      expect(plan.quoteCurrency, 'USDT');
      expect(plan.quoteRateRub, closeTo(80, 1e-9));
      expect(plan.riskRubles, closeTo(7496, 1e-6));
      // Прибыль считается в R и умножается на рублёвый риск — значит она
      // тоже в рублях и пересчёта не требует.
      expect(plan.weightedR * plan.riskRubles, greaterThan(0));
    });

    test('отказ движка исполнять объём доезжает до идеи', () {
      // Сервер писал «идея информационная», клиент это поле не читал вовсе —
      // и кнопка входа оставалась активной под неисполнимым объёмом.
      final json = ideaJson();
      json['sizing'] = {
        ...(json['sizing']! as Map<String, dynamic>),
        'tradable': false,
        'not_tradable_reason': 'курс USDT к рублю неизвестен',
      };
      expect(EngineContract.idea(json).sizingBlocker, 'курс USDT к рублю неизвестен');
    });

    test('исполнимый объём блокировки не создаёт', () {
      expect(EngineContract.idea(ideaJson()).sizingBlocker, isEmpty);
    });

    test('рублёвый инструмент курса не требует', () {
      final plan = EngineContract.idea(ideaJson()).plan!;
      expect(plan.quoteCurrency, 'RUB');
      expect(plan.quoteRateRub, isNull);
      expect(plan.quantityStep, 1);
      expect(plan.quantityDecimals, 0);
    });

    test('стоимость пункта восстанавливается, а не зашивается единицей', () {
      // Зашитая единица — молчаливая ошибка в деньгах: у Si пункт стоит
      // рубль, у нефти семь с половиной, и объём разойдётся в разы.
      final idea = EngineContract.idea(ideaJson());
      final plan = idea.plan!;
      // risk_per_unit 600 при расстоянии до стопа 600 пунктов → 1 ₽ за пункт.
      expect(plan.valuePerPoint, closeTo(1.0, 1e-9));
      expect(plan.riskPerUnit, closeTo(600, 1e-6));
    });

    test('оценка приходит одиннадцатью компонентами с качеством данных', () {
      final score = EngineContract.idea(ideaJson()).score;
      expect(score.value, 79);
      expect(score.dataQuality, closeTo(0.9, 1e-9));

      final na = score.contributions.firstWhere(
          (c) => c.kind == ScoreComponentKind.derivativesConfirmation);
      // «Не измерено» и «неприменимо» обязаны различаться: первое портит
      // оценку и требует объяснения, второе — свойство инструмента.
      expect(na.status, MeasurementStatus.missing);
      expect(na.effectiveWeight, 0);
    });

    test('риск на сделку — ступень engine-ТЗ, а не UX-ТЗ', () {
      // 79 баллов — ещё не A-grade: ступень начинается с 80.
      expect(EngineContract.idea(ideaJson()).score.riskPercent, 0.50);

      final aGrade = ideaJson()
        ..['score_breakdown'] = {
          ...ideaJson()['score_breakdown'] as Map<String, dynamic>,
          'total': '84',
        };
      // Выше 0,75% ступеней нет: 1% из UX-ТЗ движок не использует.
      expect(EngineContract.idea(aGrade).score.riskPercent, 0.75);
    });

    test('разметка разбирается, неизвестный тип отбрасывается', () {
      // Метка неизвестного типа не рисуется вовсе. Подставить «какой-нибудь»
      // значит показать на графике не то, что нашёл детектор.
      final idea = EngineContract.idea(ideaJson());
      expect(idea.annotations.length, 1);
      final a = idea.annotations.single;
      expect(a.type, AnnotationType.smcOrderBlock);
      expect(a.layer, ChartLayer.smc);
      expect(a.evidenceId, 'smc');
      expect(a.priceLow, 90000);
      expect(a.displayPriority, 60);
    });

    test('конфликты доказательств доезжают до карточки', () {
      final idea = EngineContract.idea(ideaJson());
      final smc = idea.evidence.single;
      expect(smc.hasConflict, isTrue);
      expect(smc.conflictsWith.single, contains('против направления'));
    });

    test('флаги качества данных распознаются по коду', () {
      final idea = EngineContract.idea(ideaJson());
      expect(idea.dataFlags, contains(DataQualityFlag.oiUnavailable));
    });

    test('идея без плана не притворяется готовой сделкой', () {
      final json = ideaJson()..remove('plan');
      expect(EngineContract.idea(json).plan, isNull);
    });
  });
}
