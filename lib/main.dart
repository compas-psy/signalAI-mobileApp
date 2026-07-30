import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'data/local_analysis_repository.dart';
import 'data/mock/demo_repository.dart';
import 'data/repository.dart';
import 'state/app_controller.dart';
import 'state/app_scope.dart';
import 'theme/tokens.dart';
import 'ui/app_shell.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  SystemChrome.setSystemUIOverlayStyle(
    const SystemUiOverlayStyle(
      statusBarColor: Color(0x00000000),
      statusBarIconBrightness: Brightness.light,
      systemNavigationBarColor: C.navBg,
      systemNavigationBarIconBrightness: Brightness.light,
    ),
  );
  // Ориентация свободная: на планшете альбом — основной режим работы, и
  // блокировать его значит отдать половину экрана. Разметка сама выбирает
  // каркас по ширине (см. lib/ui/layout.dart).
  SystemChrome.setPreferredOrientations([
    DeviceOrientation.portraitUp,
    DeviceOrientation.portraitDown,
    DeviceOrientation.landscapeLeft,
    DeviceOrientation.landscapeRight,
  ]);

  // Режим сборки — это выбор источника **журнала, настроек и книги
  // операций**, и только его:
  //   local — считает устройство по публичным данным бирж;
  //   demo  — данные макета, без сети.
  //
  // Идеи и пакеты капитала берёт `EngineClient` по адресу движка §18 в любом
  // режиме, и `SIGNALAI_API_BASE_URL` на этот выбор не влияет.
  //
  // Режима «server» здесь больше нет. Он поднимал `RestRepository` — клиент
  // старого контракта (`/v1/settings`, `/v1/trades`, `/v1/strategies`),
  // адресов которого у движка §18 не существует: первый же запрос при старте
  // падал, и приложение показывало «Не удалось запуститься» при полностью
  // исправном сервере. Раньше на этом месте стояла оговорка словами — её не
  // хватило, 30.07 сборка с этим режимом уехала на устройство. Комментарий
  // не защищает от выбора, которого не должно существовать; выбор убран и из
  // сборки (`.github/workflows/android-sideload.yml`), и отсюда.
  const mode = String.fromEnvironment('SIGNALAI_MODE', defaultValue: 'local');
  if (mode == 'server') {
    // Отказ, а не тихая подмена на local: сборка, которая называет себя
    // серверной, а считает на устройстве, вводит в заблуждение сильнее, чем
    // падение с внятной причиной.
    throw StateError(
      'SIGNALAI_MODE=server больше не поддерживается: RestRepository ходит по '
      'адресам старого контракта /v1/*, которых у движка §18 нет. '
      'Собирайте local (идеи и пакеты всё равно берутся с движка) или demo.',
    );
  }
  final SignalAiRepository repository = switch (mode) {
    'demo' => DemoRepository(),
    _ => LocalAnalysisRepository(),
  };

  runApp(SignalAiApp(repository: repository));
}

class SignalAiApp extends StatefulWidget {
  const SignalAiApp({super.key, required this.repository});

  final SignalAiRepository repository;

  @override
  State<SignalAiApp> createState() => _SignalAiAppState();
}

class _SignalAiAppState extends State<SignalAiApp> with WidgetsBindingObserver {
  late final AppController _controller = AppController(widget.repository);

  /// Фоновый контур поднимается на уходе в фон и снимается на возврате.
  ///
  /// Так передний план и фон никогда не считают одновременно: пока экран
  /// открыт, состояние пишет интерфейс, и это единственный писатель.
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    switch (state) {
      case AppLifecycleState.resumed:
        _controller.onAppResumed();
      case AppLifecycleState.paused:
      case AppLifecycleState.detached:
      case AppLifecycleState.hidden:
        _controller.onAppPaused();
      case AppLifecycleState.inactive:
        // Шторка уведомлений или входящий звонок — приложение ещё живо.
        break;
    }
  }

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _controller.load();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _controller.dispose();
    super.dispose();
  }

  /// Тема собирается один раз.
  ///
  /// [AppScope] стоит над [MaterialApp], то есть каждое уведомление
  /// контроллера пересобирает приложение целиком. Готовая тема снимает
  /// главную аллокацию этой пересборки.
  static final _theme = ThemeData(
    brightness: Brightness.dark,
    scaffoldBackgroundColor: C.bg,
    fontFamily: 'Manrope',
    colorScheme: const ColorScheme.dark(
      primary: C.accent,
      surface: C.card,
      onPrimary: C.onAccent,
    ),
    textSelectionTheme: const TextSelectionThemeData(
      cursorColor: C.accent,
      selectionColor: Color(0x40FFD400),
    ),
  );

  // AppScope стоит НАД MaterialApp сознательно.
  //
  // Раньше он жил внутри `home:`, то есть под корневым Navigator. Маршрут,
  // открытый через Navigator.push, встаёт рядом с `home`, а не под ним —
  // и контроллера оттуда не видно. В релизе это не ошибка на экране, а
  // серый прямоугольник: assert вырезан, срабатывает null-check, Flutter
  // рисует ErrorWidget. Ровно так «пропал» разбор пакета.
  @override
  Widget build(BuildContext context) => AppScope(
        controller: _controller,
        child: MaterialApp(
          title: 'SignalAI',
          debugShowCheckedModeBanner: false,
          theme: _theme,
          // builder оборачивает сам Navigator, поэтому предок Material
          // появляется у всех маршрутов сразу — и у нынешних, и у будущих.
          // Без него MaterialApp подставляет тексту вне Material свой
          // «ошибочный» стиль, от которого наследуется жёлтое двойное
          // подчёркивание: наши стили задают цвет и размер, но не decoration.
          // MaterialType.transparency не рисует ни фона, ни чернил.
          builder: (context, child) => Material(
            type: MaterialType.transparency,
            child: child ?? const SizedBox.shrink(),
          ),
          home: const Scaffold(
            backgroundColor: C.bg,
            body: AppShell(),
          ),
        ),
      );
}
