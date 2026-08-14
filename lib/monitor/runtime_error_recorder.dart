import 'dart:async';

import '../data/local_store.dart';

enum RuntimeErrorKind { flutter, async, ideaHydration, chartLoad }

class RuntimeBuildIdentity {
  const RuntimeBuildIdentity({
    required this.appVersion,
    required this.sourceSha,
  });

  static const current = RuntimeBuildIdentity(
    appVersion: String.fromEnvironment(
      'SIGNALAI_APP_VERSION',
      defaultValue: 'dev',
    ),
    sourceSha: String.fromEnvironment(
      'SIGNALAI_SOURCE_SHA',
      defaultValue: 'unknown',
    ),
  );

  final String appVersion;
  final String sourceSha;
}

class RuntimeErrorEvent {
  const RuntimeErrorEvent({
    required this.timestamp,
    required this.kind,
    required this.error,
    required this.appVersion,
    required this.sourceSha,
    this.stackTrace,
  });

  final DateTime timestamp;
  final RuntimeErrorKind kind;
  final String error;
  final String? stackTrace;
  final String appVersion;
  final String sourceSha;

  Map<String, dynamic> toJson() => {
        'timestamp': timestamp.toUtc().toIso8601String(),
        'kind': kind.name,
        'error': error,
        if (stackTrace != null) 'stack_trace': stackTrace,
        'app_version': appVersion,
        'source_sha': sourceSha,
      };

  static RuntimeErrorEvent? fromJson(Map<String, dynamic> json) {
    try {
      final kindName = json['kind'] as String;
      final kind = RuntimeErrorKind.values.firstWhere(
        (value) => value.name == kindName,
      );
      return RuntimeErrorEvent(
        timestamp: DateTime.parse(json['timestamp'] as String).toUtc(),
        kind: kind,
        error: json['error'] as String,
        stackTrace: json['stack_trace'] as String?,
        appVersion: json['app_version'] as String,
        sourceSha: json['source_sha'] as String,
      );
    } on Object {
      return null;
    }
  }
}

class RuntimeErrorRecorder {
  RuntimeErrorRecorder({
    required LocalStore store,
    RuntimeBuildIdentity identity = RuntimeBuildIdentity.current,
    this.maxEvents = 50,
    DateTime Function()? clock,
  })  : assert(maxEvents > 0),
        _store = store,
        _identity = identity,
        _clock = clock ?? DateTime.now;

  static const _storeName = 'runtime_error_history';
  static const _schemaVersion = 1;

  final LocalStore _store;
  final RuntimeBuildIdentity _identity;
  final int maxEvents;
  final DateTime Function() _clock;

  Future<void> _tail = Future<void>.value();

  /// Resolves the private application directory before a crash happens.
  /// Failure is harmless: LocalStore will keep best-effort in-memory history.
  Future<void> initialize() async {
    try {
      await _store.persistent;
    } on Object {
      // Diagnostics must never affect application startup.
    }
  }

  Future<void> record({
    required RuntimeErrorKind kind,
    required Object error,
    StackTrace? stackTrace,
  }) async {
    final previous = _tail;
    final completion = Completer<void>();
    _tail = completion.future;

    await previous;
    try {
      final current = await events();
      final event = RuntimeErrorEvent(
        timestamp: _clock().toUtc(),
        kind: kind,
        error: _redact(error.toString()),
        stackTrace: stackTrace == null ? null : _redact(stackTrace.toString()),
        appVersion: _identity.appVersion,
        sourceSha: _identity.sourceSha,
      );
      final next = <RuntimeErrorEvent>[...current, event];
      if (next.length > maxEvents) {
        next.removeRange(0, next.length - maxEvents);
      }
      await _store.write(_storeName, {
        'schema_version': _schemaVersion,
        'events': next.map((item) => item.toJson()).toList(),
      });
    } on Object {
      // Error reporting must not create a second application failure.
    } finally {
      completion.complete();
    }
  }

  Future<List<RuntimeErrorEvent>> events() async {
    try {
      final document = await _store.read(_storeName);
      final rawEvents = document?['events'];
      if (rawEvents is! List) return const <RuntimeErrorEvent>[];
      final result = <RuntimeErrorEvent>[];
      for (final raw in rawEvents) {
        if (raw is! Map) continue;
        final event = RuntimeErrorEvent.fromJson(
          Map<String, dynamic>.from(raw),
        );
        if (event != null) result.add(event);
      }
      return result;
    } on Object {
      return const <RuntimeErrorEvent>[];
    }
  }
}

String _redact(String input) {
  var output = input;

  output = output.replaceAllMapped(
    RegExp(r'authorization\s*[:=]\s*(?:bearer|basic)?\s*[^\s,;]+', caseSensitive: false),
    (match) => 'Authorization: [REDACTED]',
  );

  output = output.replaceAllMapped(
    RegExp(r'\b(bearer|basic)\s+[^\s,;]+', caseSensitive: false),
    (match) => '${match.group(1)} [REDACTED]',
  );

  output = output.replaceAllMapped(
    RegExp(
      r'("?(?:access[_-]?token|refresh[_-]?token|api[_-]?key|token|secret|password)"?\s*:\s*)"[^"]*"',
      caseSensitive: false,
    ),
    (match) => '${match.group(1)}"[REDACTED]"',
  );

  output = output.replaceAllMapped(
    RegExp(
      r'\b(access[_-]?token|refresh[_-]?token|api[_-]?key|token|secret|password)\b(\s*[:=]\s*)[^\s&;,]+',
      caseSensitive: false,
    ),
    (match) => '${match.group(1)}${match.group(2)}[REDACTED]',
  );

  return output;
}
