/// Гипотезы ранних сигналов на устройстве.
///
/// Раздел живёт в «Портфеле», а не в «Идеях», и главная опасность здесь не
/// техническая: если карточка выглядит как идея, первым движением владельца
/// будет попытка по ней торговать. Поэтому проверяется и разбор контракта, и
/// то, что в модели нечем исполнить сделку.
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/research/hypothesis.dart';
import 'package:signalai/state/navigation.dart';

Map<String, dynamic> hypothesisJson({
  String state = 'early_candidate',
  Map<String, dynamic>? gate,
  Object? marketContext,
}) =>
    {
      'id': 'h-1',
      'version': 2,
      'market': 'russia_equity',
      'entity_id': 'issuer-1',
      'instrument_id': 'MOEX:EQ:TEST',
      'symbol': 'TEST',
      'title': 'Исполнение контрактов ускоряется — возможен рост выручки',
      'direction': 'positive',
      'state': state,
      'expected_lag': 'P2Q',
      'evidence_score': '0.71',
      'economic_score': '0.66',
      'market_context_score': marketContext,
      'market_context_state': 'unknown',
      'research_priority': '61.4',
      'three_two_one': gate ??
          {
            'unique_lineage_roots': 2,
            'independence_groups': 1,
            'confirmation_periods': 1,
            'causal_path_valid': false,
          },
      'target_kpis': [
        {
          'metric': 'segment_revenue',
          'expected_direction': 'up',
          'measurement_window': {'from': 'P2Q', 'to': 'P6Q'},
        },
      ],
      'falsifiers': [
        {'description': 'Исполнение контрактов снизится два месяца подряд'},
      ],
      'evidence': [
        {'role': 'support', 'claim': 'контракт исполнен', 'lineage_root_id': 'eis'},
        {'role': 'contradict', 'claim': 'выручка сегмента не выросла',
         'lineage_root_id': 'ir'},
      ],
      'risk_flags': ['ликвидность'],
      'missing_evidence': ['нет данных о марже сегмента'],
      'state_reason': 'ждём второго подтверждения',
    };

void main() {
  group('Разбор гипотезы', () {
    test('контракт движка разбирается целиком', () {
      final h = Hypothesis.fromJson(hypothesisJson());

      expect(h.symbol, 'TEST');
      expect(h.state, HypothesisState.earlyCandidate);
      expect(h.direction, HypothesisDirection.positive);
      expect(h.researchPriority, closeTo(61.4, 1e-9));
      expect(h.targetKpis.single.metric, 'segment_revenue');
      expect(h.targetKpis.single.window, 'P2Q–P6Q');
      expect(h.falsifiers.single, contains('снизится'));
    });

    test('числа приходят строками и не теряют точность', () {
      // Сервер отдаёт денежные величины строками намеренно: JSON-число это
      // double, и 0.71 на другом конце становится 0.7100000000000001.
      final h = Hypothesis.fromJson(hypothesisJson());
      expect(h.evidenceScore, closeTo(0.71, 1e-12));
      expect(h.economicScore, closeTo(0.66, 1e-12));
    });

    test('неизмеренный рыночный контекст остаётся неизмеренным', () {
      // Подставить сюда среднее значило бы выдумать знание о том, учтено ли
      // изменение в цене.
      final absent = Hypothesis.fromJson(hypothesisJson());
      expect(absent.marketContextScore, isNull);

      final measured = Hypothesis.fromJson(hypothesisJson(marketContext: '0.4'));
      expect(measured.marketContextScore, closeTo(0.4, 1e-12));
    });

    test('противоречащее доказательство не теряется', () {
      // Гипотеза, из которой вычистили неудобные факты, перестаёт быть
      // проверяемой.
      final h = Hypothesis.fromJson(hypothesisJson());
      final against = h.evidence.where((e) => e.contradicts).toList();
      expect(against, hasLength(1));
      expect(against.single.claim, contains('не выросла'));
    });

    test('незнакомое состояние читается как отклонённое, а не как готовое', () {
      // Ошибка чтения не должна давать больше, чем было.
      final h = Hypothesis.fromJson(hypothesisJson(state: 'что-то новое'));
      expect(h.state, HypothesisState.rejected);
      expect(h.state.live, isFalse);
    });
  });

  group('Чего не хватает до подтверждения', () {
    test('пересчитывается на устройстве и называется словами', () {
      final h = Hypothesis.fromJson(hypothesisJson());
      expect(h.shortfall, hasLength(4));
      expect(h.shortfall.first, contains('независимых источников 2 из 3'));
    });

    test('у подтверждённой гипотезы список пуст', () {
      final h = Hypothesis.fromJson(hypothesisJson(
        state: 'confirmed_hypothesis',
        gate: {
          'unique_lineage_roots': 3,
          'independence_groups': 2,
          'confirmation_periods': 2,
          'causal_path_valid': true,
        },
      ));
      expect(h.shortfall, isEmpty);
    });
  });

  group('Гипотеза — не сделка', () {
    test('в модели нечем исполнить ордер', () {
      // ТЗ §16.4 запрещает выдавать BUY, SELL, гарантированную доходность и
      // размер позиции. Запрет выражен отсутствием полей: то, чего нет,
      // невозможно заполнить по недосмотру.
      final h = Hypothesis.fromJson(hypothesisJson());
      final fields = h.toString();
      expect(fields, isNot(contains('entry')));
      expect(fields, isNot(contains('stopLoss')));
      expect(fields, isNot(contains('quantity')));
    });

    test('направление говорит о показателе, а не о стороне сделки', () {
      expect(HypothesisDirection.positive.label, 'улучшение');
      expect(HypothesisDirection.negative.label, 'ухудшение');
    });

    test('максимум системы — «изучить вручную», и дальше состояний нет', () {
      const last = HypothesisState.diligenceReady;
      expect(last.live, isTrue);
      // Все состояния после него — уже закрытые.
      for (final s in HypothesisState.values.where((s) => s.index > last.index)) {
        expect(s.live, isFalse, reason: s.name);
      }
    });
  });

  group('Место раздела', () {
    test('сигналы живут в «Портфеле», а не в «Идеях»', () {
      // Идея говорит, когда входить; гипотеза — что вообще стоит держать.
      // Это вопрос портфеля, и соседство с пакетами не случайно.
      expect(AppSection.portfolio.pills, contains('Сигналы'));
      expect(AppSection.ideas.pills, isNot(contains('Сигналы')));
      expect(PortfolioPill.values, contains(PortfolioPill.signals));
    });
  });

  group('Состояние контура', () {
    test('пустая выдача несёт причину и состояние источников', () {
      final state = ResearchState.fromJson({
        'status': {
          'total_sources': 25,
          'free_route': 17,
          'awaiting_terms_check': ['cbr_data', 'rosstat'],
          'manual_only': ['issuer_ir'],
          'unavailable': [
            {
              'source_id': 'hh_ru',
              'name': 'hh.ru',
              'status': 'prohibited',
              'note': 'условия ограничивают тематику найма',
            },
          ],
          'connect_order': ['cbr_data', 'fns_open_data'],
          'reason': 'источники ждут проверки условий',
        },
        'hypotheses': const [],
      });

      expect(state.isAvailable, isTrue);
      expect(state.hypotheses, isEmpty);
      expect(state.reason, isNotEmpty);
      expect(state.freeRoute, 17);
      expect(state.awaitingTermsCheck, hasLength(2));
      expect(state.unavailable.single.statusLabel, 'запрещён условиями');
    });

    test('недоступный движок отличим от пустой выдачи', () {
      const state = ResearchState.unavailable('сеть недоступна');
      expect(state.isAvailable, isFalse);
      expect(state.unavailableReason, 'сеть недоступна');
    });
  });

  group('Готовность движков', () {
    test('«не написан» и «нечем кормить» — разные состояния', () {
      // Пустой экран выглядит одинаково в обоих случаях, а требует
      // разного: первое — работы, второе — проверки условий источника.
      final state = ResearchState.fromJson({
        'status': {
          'engines': [
            {'key': 'WCQ', 'version': '1.0.0', 'ready': true,
             'feeds_from': ['fns_open_data'], 'blocked_by': ['fns_open_data']},
            {'key': 'DEMAND', 'version': '1.0.0', 'ready': true,
             'feeds_from': ['rosstat'], 'blocked_by': const []},
          ],
          'engines_ready': 2,
          'engines_fed': 1,
        },
        'hypotheses': const [],
      });

      expect(state.enginesReady, 2);
      expect(state.enginesFed, 1);
      expect(state.engines.first.fed, isFalse);
      expect(state.engines.first.blockedBy, ['fns_open_data']);
      expect(state.engines.last.fed, isTrue);
    });

    test('у каждого движка есть человеческое название', () {
      const keys = [
        'WCQ', 'DEMAND', 'HIRING', 'SPREAD', 'SUPPLIER',
        'BUDGET', 'CAPACITY', 'CRYPTO_ECON', 'CRYPTO_SUPPLY',
      ];
      for (final key in keys) {
        final engine = EngineState(key: key);
        expect(engine.title, isNot(key), reason: key);
      }
    });
  });
}
