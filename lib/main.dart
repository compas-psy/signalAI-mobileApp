import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'core/app_mode.dart';
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
  SystemChrome.setPreferredOrientations([
    DeviceOrientation.portraitUp,
    DeviceOrientation.portraitDown,
    DeviceOrientation.landscapeLeft,
    DeviceOrientation.landscapeRight,
  ]);

  // Режим production — `thin`: LocalAnalysisRepository временно остаётся
  // хранилищем настроек, снимка T-Invest и старого журнала, но controller не
  // запускает его scanner/optimizer и не смешивает ledger с server paper.
  const mode = AppMode.name;
  if (mode == 'server') {
    throw StateError(
      'SIGNALAI_MODE=server больше не поддерживается: RestRepository ходит по '
      'адресам старого контракта /v1/*, которых у движка §18 нет. '
      'Для production собирайте thin; demo предназначен только для fixture, '
      'а local — для legacy-разработки.',
    );
  }
  final SignalAiRepository repository = switch (mode) {
    'demo' => DemoRepository(),
    'thin' || 'local' => LocalAnalysisRepository(),
    _ => throw StateError(
        'Неизвестный SIGNALAI_MODE=$mode. Разрешены thin, local и demo; '
        'опечатка не должна включать legacy-анализатор.',
      ),
  };

  runApp(SignalAiApp(repository: repository, thinMode: mode == 'thin'));
}

class SignalAiApp extends StatefulWidget {
  const SignalAiApp({
    super.key,
    required this.repository,
    this.thinMode = AppMode.thin,
  });

  final SignalAiRepository repository;
  final bool thinMode;

  @override
  State<SignalAiApp> createState() => _SignalAiAppState();
}

class _SignalAiAppState extends State<SignalAiApp> with WidgetsBindingObserver {
  late final AppController _controller = AppController(
    widget.repository,
    thinMode: widget.thinMode,
  );

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

  // AppScope stays above MaterialApp so pushed routes and the root back-scope
  // see the same controller.
  @override
  Widget build(BuildContext context) => AppScope(
        controller: _controller,
        child: MaterialApp(
          title: 'SignalAI',
          debugShowCheckedModeBanner: false,
          theme: _theme,
          builder: (context, child) => Material(
            type: MaterialType.transparency,
            child: child ?? const SizedBox.shrink(),
          ),
          home: const Scaffold(
            backgroundColor: C.bg,
            body: _RootBackScope(),
          ),
        ),
      );
}

/// Maps Android's system/predictive back gesture to SignalAI's controller
/// state before allowing the root Navigator route to leave the application.
///
/// Idea details are controller state, not Navigator.push routes.  Without this
/// scope Android sees only the root route and correctly exits the task, while
/// the owner expects to return to the list/journal that opened the detail.
class _RootBackScope extends StatelessWidget {
  const _RootBackScope();

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.of(context);
    final intercept = controller.sheetOpen || controller.isDetailOpen;
    return PopScope(
      canPop: !intercept,
      onPopInvokedWithResult: (didPop, result) {
        if (didPop) return;
        if (controller.sheetOpen) {
          controller.closeSheet();
        } else if (controller.isDetailOpen) {
          controller.back();
        }
      },
      child: const AppShell(),
    );
  }
}
