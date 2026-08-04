/// Отказ не должен выглядеть успехом.
///
/// Владелец прислал экран, где отказ «Вход стоп-заявкой для Т-Инвестиций
/// ещё не реализован — идея ведётся на бумаге» показан зелёной галочкой.
/// Тона у тоста не было вовсе: галочка и зелёный цвет стояли в разметке
/// жёстко, а через тот же тост шли «Проверка не пропустила» и любая ошибка
/// пути к бирже. Приложение поздравляло владельца с тем, что заявка не
/// ушла.
library;

import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/state/navigation.dart';
import 'package:signalai/theme/tokens.dart';
import 'package:signalai/ui/widgets/toast.dart';
import 'package:signalai/ui/widgets/vector_icon.dart';

Widget _host(Widget child) => Directionality(
      textDirection: TextDirection.ltr,
      child: Center(child: SizedBox(width: 380, child: child)),
    );

VectorIcon _iconOf(WidgetTester tester) =>
    tester.widget<VectorIcon>(find.byType(VectorIcon));

void main() {
  testWidgets('успех — зелёная галочка', (tester) async {
    await tester.pumpWidget(_host(const AppToast(message: 'Ордер отправлен')));

    final icon = _iconOf(tester);
    expect(icon.spec, Icons.check);
    expect(icon.color, C.green);
  });

  testWidgets('отказ — не галочка и не зелёный', (tester) async {
    await tester.pumpWidget(_host(const AppToast(
      message: 'Вход стоп-заявкой для Т-Инвестиций ещё не реализован',
      tone: ToastTone.failure,
    )));

    final icon = _iconOf(tester);
    expect(icon.spec, isNot(Icons.check), reason: 'галочка на отказе');
    expect(icon.color, isNot(C.green), reason: 'зелёный на отказе');
    expect(icon.color, C.red);
  });

  testWidgets('предупреждение отличается и от успеха, и от отказа',
      (tester) async {
    await tester.pumpWidget(_host(const AppToast(
      message: 'Расчёт уже идёт',
      tone: ToastTone.warning,
    )));

    final icon = _iconOf(tester);
    expect(icon.color, C.warning);
    expect(icon.spec, isNot(Icons.check));
  });

  test('три тона, и ни один не совпадает с другим', () {
    // Тон отдельным перечислением, а не флагом «ошибка»: «действие не
    // состоялось» и «состоялось не полностью» — разные новости, и валить
    // их в одно значит снова показывать одинаково разное.
    expect(ToastTone.values, hasLength(3));
  });
}
