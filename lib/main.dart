import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'data/api/api_config.dart';
import 'data/api/rest_repository.dart';
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

  // Режим сборки:
  //   server — тонкий клиент, всё считает сервер (ТЗ §2);
  //   local  — автономный анализ на устройстве по публичным данным бирж;
  //   demo   — данные макета, без сети.
  // Если адрес гейтвея задан, он всегда выигрывает.
  const mode = String.fromEnvironment('SIGNALAI_MODE', defaultValue: 'local');
  final SignalAiRepository repository = ApiConfig.isConfigured
      ? RestRepository()
      : switch (mode) {
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

  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'SignalAI',
        debugShowCheckedModeBanner: false,
        theme: ThemeData(
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
        ),
        home: AppScope(
          controller: _controller,
          child: const Scaffold(
            backgroundColor: C.bg,
            body: AppShell(),
          ),
        ),
      );
}
