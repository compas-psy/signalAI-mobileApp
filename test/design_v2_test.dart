import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/theme/tokens.dart';
import 'package:signalai/ui/layout.dart';
import 'package:signalai/ui/widgets/level_strip.dart';
import 'package:signalai/ui/widgets/segmented.dart';
import 'package:signalai/ui/widgets/sparkline.dart';

/// Обёртка: компоненты нарисованы на `widgets`, без Material.
Widget _host(Widget child, {double width = 380}) => Directionality(
      textDirection: TextDirection.ltr,
      child: Center(
        child: SizedBox(width: width, child: child),
      ),
    );

void main() {
  group('Блок уровней', () {
    testWidgets('показывает подписи и все цели', (tester) async {
      await tester.pumpWidget(_host(const LevelStrip(
        entry: '121,40',
        stop: '118,90',
        targets: ['124,10', '126,80', '130,00'],
        riskReward: '2,3 R:R',
      )));

      expect(find.text('ВХОД'), findsOneWidget);
      expect(find.text('СТОП'), findsOneWidget);
      expect(find.text('ЦЕЛИ'), findsOneWidget);
      expect(find.text('121,40'), findsOneWidget);
      expect(find.text('118,90'), findsOneWidget);
      // Цели склеены разделителем: три числа — одна строка.
      expect(find.text('124,10 · 126,80 · 130,00'), findsOneWidget);
      expect(find.text('2,3 R:R'), findsOneWidget);
    });

    testWidgets('без R:R бейдж не рисуется', (tester) async {
      await tester.pumpWidget(_host(const LevelStrip(
        entry: '100',
        stop: '95',
        targets: ['110'],
      )));

      expect(find.byType(RrBadge), findsNothing);
    });
  });

  group('Сегмент-контрол', () {
    testWidgets('нажатие переключает активный сегмент', (tester) async {
      var picked = 0;
      await tester.pumpWidget(_host(StatefulBuilder(
        builder: (context, setState) => SegmentedControl(
          items: const ['План', 'Факторы', 'События'],
          index: picked,
          onSelect: (i) => setState(() => picked = i),
        ),
      )));

      await tester.tap(find.text('Факторы'));
      await tester.pump();
      expect(picked, 1);
    });
  });

  group('Плитки метрик', () {
    testWidgets('живут внутри ленты без ограничения высоты', (tester) async {
      // Регрессия: ряд с CrossAxisAlignment.stretch внутри ListView роняет
      // раскладку — высота сверху не ограничена.
      await tester.pumpWidget(_host(
        ListView(
          children: const [
            MetricRow(tiles: [
              MetricTile(label: 'Винрейт', value: '61%'),
              MetricTile(label: 'Профит-фактор', value: '2,1', hint: '38 сделок'),
            ]),
          ],
        ),
      ));

      expect(tester.takeException(), isNull);
      expect(find.text('ВИНРЕЙТ'), findsOneWidget);
      expect(find.text('2,1'), findsOneWidget);
    });
  });

  group('Сетка карточек', () {
    testWidgets('на телефоне — одна колонка', (tester) async {
      await tester.pumpWidget(_host(
        width: 380,
        const CardGrid(children: [Text('a'), Text('b'), Text('c')]),
      ));

      // Одна колонка: строки идут одна под другой, Row не появляется.
      expect(find.byType(Row), findsNothing);
      expect(find.text('c'), findsOneWidget);
    });

    testWidgets('на планшете — две колонки, порядок змейкой', (tester) async {
      await tester.pumpWidget(_host(
        width: 900,
        const CardGrid(children: [Text('a'), Text('b'), Text('c')]),
      ));

      expect(find.byType(Row), findsOneWidget);
      // Нечётные уезжают вправо: 'b' правее 'a' и 'c'.
      final ax = tester.getTopLeft(find.text('a')).dx;
      final bx = tester.getTopLeft(find.text('b')).dx;
      final cx = tester.getTopLeft(find.text('c')).dx;
      expect(bx, greaterThan(ax));
      expect(cx, ax, reason: 'третья карточка возвращается в левую колонку');
    });
  });

  group('Спарклайн', () {
    testWidgets('одна точка объясняет себя, а не остаётся пустой коробкой',
        (tester) async {
      // Кривая по одной точке не строится, и раньше здесь оставался пустой
      // прямоугольник 64 px — неотличимый от поломки. Владелец так его и
      // прочитал: «серое окно».
      await tester.pumpWidget(_host(const Sparkline(
        values: [1],
        height: 64,
        color: C.green,
        emptyLabel: 'кривая появится со второй сделки',
      )));

      expect(find.text('кривая появится со второй сделки'), findsOneWidget);
    });

    testWidgets('с двумя точками рисуется график, а не подпись',
        (tester) async {
      await tester.pumpWidget(_host(const Sparkline(
        values: [1, 2],
        height: 64,
        color: C.green,
        emptyLabel: 'не должно появиться',
      )));

      expect(find.text('не должно появиться'), findsNothing);
      expect(find.byType(CustomPaint), findsWidgets);
    });
  });

  group('Ширина содержимого', () {
    test('растёт по классам экрана', () {
      expect(Pane.compact.contentWidth, double.infinity);
      expect(Pane.medium.contentWidth, 760);
      expect(Pane.expanded.contentWidth, 1160);
    });
  });

  group('Токены v3', () {
    test('поверхности и рамки — из макета', () {
      expect(C.card, const Color(0xFF141419));
      expect(C.inset, const Color(0xFF101014));
      expect(C.sheet, const Color(0xFF17171C));
      expect(C.border, const Color(0xFF24242B));
      expect(C.muted, const Color(0xFF9696A1));
      expect(C.faint, const Color(0xFF666670));
      expect(R.card, 18);
      expect(R.sheet, 22);
    });

    test('семантические акценты заданы отдельными токенами', () {
      // Жёлтый больше не значит «важно», «нажми» и «хорошо» одновременно:
      // у учёта, тактики и неполных данных свои цвета.
      expect(C.info, const Color(0xFF63A5FF));
      expect(C.tactical, const Color(0xFFB38CFF));
      expect(C.warning, const Color(0xFFFFB74D));
      expect(C.accent, const Color(0xFFFFD400));
    });
  });
}
