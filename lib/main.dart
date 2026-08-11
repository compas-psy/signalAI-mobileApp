import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'core/app_mode.dart';
import 'data/api/sandbox_mirroring_engine_client.dart';
import 'data/local_analysis_repository.dart';
import 'data/local_store.dart';
import 'data/mock/demo_repository.dart';
import 'data/repository.dart';
import 'state/app_controller.dart';
import 'state/app_scope.dart';
import 'state/navigation.dart';
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
  late final AppController _controller;
  late final LocalStore _runtimeStore;

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
    _runtimeStore = LocalStore();

    final repository = widget.repository;
    final engine = widget.thinMode && repository is LocalAnalysisRepository
        ? SandboxMirroringEngineClient(
            repository: repository,
            instrumentStore: _runtimeStore,
            onResult: (result) {
              final tone = switch (result.tone) {
                SandboxMirrorTone.success => ToastTone.success,
                SandboxMirrorTone.warning => ToastTone.warning,
                SandboxMirrorTone.failure => ToastTone.failure,
              };
              _controller.showToast(result.message, tone: tone);
            },
          )
        : null;

    _controller = AppController(
      repository,
      thinMode: widget.thinMode,
      prefs: _runtimeStore,
      engine: engine,
    );

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

/// Maps Android's system/predictive back gesture to SignalAI's internal
/// navigation before allowing the root Navigator route to leave the app.
///
/// SignalAI keeps section/pill/detail navigation in [AppController] rather
/// than Navigator routes. Android therefore sees a single root route unless we
/// maintain a tiny UI history here. Back now unwinds, in order: modal sheet,
/// idea detail, previous section/pill. Only when that history is exhausted is
/// Android allowed to leave the task.
class _RootBackScope extends StatefulWidget {
  const _RootBackScope();

  @override
  State<_RootBackScope> createState() => _RootBackScopeState();
}

class _RootBackScopeState extends State<_RootBackScope> {
  static const _historyLimit = 24;

  final List<AppRoute> _history = <AppRoute>[];
  AppRoute? _seenRoute;
  bool _restoring = false;

  void _observeRoute(AppController controller) {
    final current = controller.route;
    final seen = _seenRoute;
    if (seen == null) {
      _seenRoute = current;
      return;
    }
    if (current == seen) return;

    if (_restoring) {
      _restoring = false;
      _seenRoute = current;
      return;
    }

    _history.add(seen);
    if (_history.length > _historyLimit) _history.removeAt(0);
    _seenRoute = current;
  }

  void _restorePreviousRoute(AppController controller) {
    if (_history.isEmpty) return;
    final previous = _history.removeLast();
    _restoring = true;
    controller.goSection(previous.section);
    controller.goPill(previous.pill);
  }

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.of(context);
    _observeRoute(controller);
    final intercept = controller.sheetOpen ||
        controller.isDetailOpen ||
        _history.isNotEmpty;
    return PopScope(
      canPop: !intercept,
      onPopInvokedWithResult: (didPop, result) {
        if (didPop) return;
        if (controller.sheetOpen) {
          controller.closeSheet();
        } else if (controller.isDetailOpen) {
          controller.back();
        } else {
          _restorePreviousRoute(controller);
        }
      },
      child: const AppShell(),
    );
  }
}
