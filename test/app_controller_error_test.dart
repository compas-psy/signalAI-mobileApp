import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/api_client.dart';
import 'package:signalai/data/mock/demo_repository.dart';
import 'package:signalai/state/app_controller.dart';
import 'package:signalai/state/navigation.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('ошибка становится красным сообщением и не вызывает рекурсию', () {
    final controller = AppController(DemoRepository());
    addTearDown(controller.dispose);

    // До исправления этот единичный вызов повторно вызывал showError и
    // заканчивался stack overflow вместо пользовательского сообщения.
    controller.showError(ApiException('Движок временно недоступен'));

    expect(controller.toast, 'Движок временно недоступен');
    expect(controller.toastTone, ToastTone.failure);
  });
}
