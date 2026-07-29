import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/idea/evidence.dart';

ChartAnnotation annotation({
  AnnotationType type = AnnotationType.levelEntry,
  double? priceLow = 100,
  double? priceHigh = 105,
}) =>
    ChartAnnotation(
      id: 'a1',
      type: type,
      timeframe: 'H4',
      startTime: DateTime.utc(2026, 7, 20, 10),
      endTime: DateTime.utc(2026, 7, 21, 18, 30),
      priceLow: priceLow,
      priceHigh: priceHigh,
      confidence: 0.72,
      evidenceId: 'e1',
      detectorVersion: 'zone-1.2.0',
      label: 'Зона входа 100–105',
      displayPriority: 90,
    );

void main() {
  group('Слои графика (ТЗ §10.1)', () {
    test('свечи выключить нельзя, остальное — можно', () {
      // Без свечей график перестаёт быть графиком: разметка повисает в
      // пустоте и проверить её глазами уже нечем.
      for (final layer in ChartLayer.values) {
        expect(layer.alwaysOn, layer == ChartLayer.candles,
            reason: layer.label);
      }
    });

    test('каждый слой, кроме свечей, имеет чем себя наполнить', () {
      // Слой без единого типа аннотации — это переключатель, который
      // ничего не делает. Пользователь жмёт, и ничего не происходит.
      final used = {for (final t in AnnotationType.values) t.layer};
      final orphans =
          ChartLayer.values.where((l) => !l.alwaysOn && !used.contains(l));
      expect(orphans, isEmpty,
          reason: 'слои без аннотаций: ${orphans.map((l) => l.label)}');
    });
  });

  group('Аннотация на графике (ТЗ §10.6)', () {
    test('тип однозначно определяет слой', () {
      expect(AnnotationType.wyckoffSpring.layer, ChartLayer.wyckoff);
      expect(AnnotationType.smcFvg.layer, ChartLayer.smc);
      expect(AnnotationType.paPinBar.layer, ChartLayer.priceAction);
      expect(AnnotationType.levelStop.layer, ChartLayer.levels);
      expect(AnnotationType.oiBuildup.layer, ChartLayer.openInterest);
      expect(annotation().layer, ChartLayer.levels);
    });

    test('сериализация не теряет ни одного поля', () {
      // Разметку надо уметь поднять из журнала через месяц и понять, чем
      // она была обоснована и какой версией детектора нарисована.
      final source = annotation();
      final back = ChartAnnotation.fromJson(source.toJson());

      expect(back.id, source.id);
      expect(back.type, source.type);
      expect(back.timeframe, source.timeframe);
      expect(back.startTime, source.startTime);
      expect(back.endTime, source.endTime);
      expect(back.priceLow, source.priceLow);
      expect(back.priceHigh, source.priceHigh);
      expect(back.confidence, source.confidence);
      expect(back.evidenceId, source.evidenceId);
      expect(back.detectorVersion, source.detectorVersion);
      expect(back.label, source.label);
      expect(back.displayPriority, source.displayPriority);
    });

    test('метка без ценового диапазона переживает круг', () {
      // Событие привязано ко времени, а не к цене: null обязан остаться
      // null, а не превратиться в ноль на оси.
      final event = annotation(
        type: AnnotationType.eventMarker,
        priceLow: null,
        priceHigh: null,
      );
      final json = event.toJson();
      expect(json.containsKey('priceLow'), isFalse);

      final back = ChartAnnotation.fromJson(json);
      expect(back.priceLow, isNull);
      expect(back.priceHigh, isNull);
      expect(back.layer, ChartLayer.events);
    });

    test('все типы переживают сериализацию', () {
      for (final type in AnnotationType.values) {
        final back = ChartAnnotation.fromJson(annotation(type: type).toJson());
        expect(back.type, type, reason: type.label);
      }
    });

    test('каждая метка называет доказательство и версию детектора', () {
      // ТЗ §9.1: слой, не участвовавший в оценке, не рисуется. Пустой
      // evidenceId означает разметку без обоснования — её быть не должно.
      final a = annotation();
      expect(a.evidenceId, isNotEmpty);
      expect(a.detectorVersion, isNotEmpty);
    });
  });

  group('Доказательство (ТЗ §9.1)', () {
    const evidence = Evidence(
      id: 'e1',
      kind: 'wyckoff_phase',
      summary: 'Фаза C, Spring на объёме',
      detail: 'Пробой минимума диапазона с возвратом внутрь за две свечи.',
      confidence: 0.8,
      detectorVersion: 'wyckoff-2.0.1',
      annotationIds: ['a1', 'a2'],
    );

    test('доказательство ведёт к своей разметке', () {
      // Тап по тезису подсвечивает метки — связь обязана быть в данных, а
      // не восстанавливаться экраном по совпадению текста.
      expect(evidence.annotationIds, contains('a1'));
    });

    test('конфликт детекторов виден, а не спрятан', () {
      // «Wyckoff bullish, event risk bearish» — это то, что владелец
      // обязан увидеть до входа, а не после.
      expect(evidence.hasConflict, isFalse);

      final conflicted = Evidence(
        id: evidence.id,
        kind: evidence.kind,
        summary: evidence.summary,
        detail: evidence.detail,
        confidence: evidence.confidence,
        detectorVersion: evidence.detectorVersion,
        conflictsWith: const ['e_event_risk'],
      );
      expect(conflicted.hasConflict, isTrue);
      expect(conflicted.conflictsWith, contains('e_event_risk'));
    });
  });
}
