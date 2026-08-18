import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/api_client.dart';
import 'package:signalai/data/api/capacity_status_client.dart';
import 'package:signalai/domain/models/capacity_status.dart';
import 'package:signalai/ui/widgets/capacity_status_panel.dart';

class _CapacityApi extends ApiClient {
  _CapacityApi(this.response)
      : super(baseUrl: 'https://engine.test', deviceToken: '');

  final Map<String, dynamic> response;
  final List<String> paths = [];

  @override
  Future<Map<String, dynamic>> get(String path) async {
    paths.add(path);
    return response;
  }
}

Map<String, dynamic> _json({
  bool withRemediation = true,
  List<String> probeErrors = const [],
}) =>
    {
      'collected_at': '2026-08-18T18:30:00+00:00',
      'system': {
        'memory': {
          'used_bytes': 6442450944,
          'limit_bytes': 8589934592,
          'used_ratio': 0.75,
        },
        'swap_used_bytes': 268435456,
        'load1': 1.25,
        'load5': 0.75,
        'load15': 0.5,
        'disk': {
          'used_bytes': 66571993088,
          'total_bytes': 107374182400,
          'used_ratio': 0.62,
        },
        'inodes': {
          'used': 300000,
          'total': 1000000,
          'used_ratio': 0.30,
        },
        'oom_events': 2,
        'oom_kills': 0,
      },
      'postgres': {
        'connections': 7,
        'database_size_bytes': 2147483648,
        'scheduler_lag_seconds': 12.0,
        'ingest_lag_seconds': 90.0,
      },
      'redis': {
        'memory_used_bytes': 67108864,
        'keys': 42,
        'execution_queue_depth': 3,
        'execution_queue_lag_seconds': 45.0,
      },
      'ollama': {
        'reachable': true,
        'loaded_models': 1,
        'configured_model_loaded': true,
      },
      'probe_errors': probeErrors,
      'latest_remediation': withRemediation
          ? {
              'audit_id': 'audit-1',
              'occurred_at': '2026-08-18T18:25:00+00:00',
              'pressure_state': 'PRESSURE',
              'effective_state': 'PRESSURE',
              'new_entries': 'ALLOW',
              'reasons': ['disk_headroom_pressure'],
              'ollama_status': 'UNLOADED',
              'retention_status': 'CLEANED',
              'retention_deleted_files': 4,
              'retention_deleted_bytes': 4096,
              'fingerprint': 'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff',
            }
          : null,
    };

CapacityStatus _status({
  bool withRemediation = true,
  List<String> probeErrors = const [],
}) =>
    CapacityStatus.fromJson(
      _json(withRemediation: withRemediation, probeErrors: probeErrors),
    );

void main() {
  group('CapacityStatusClient', () {
    test('loads exactly the read-only capacity endpoint', () async {
      final api = _CapacityApi(_json());

      final status = await CapacityStatusClient(api).latest();

      expect(api.paths, ['/api/v1/capacity']);
      expect(status.memoryUsedRatio, closeTo(0.75, 1e-9));
      expect(status.diskUsedRatio, closeTo(0.62, 1e-9));
      expect(status.inodeUsedRatio, closeTo(0.30, 1e-9));
      expect(status.executionQueueDepth, 3);
      expect(status.executionQueueLagSeconds, closeTo(45.0, 1e-9));
      expect(status.ollamaReachable, isTrue);
      expect(status.latestRemediation?.pressureState, 'PRESSURE');
      expect(status.latestRemediation?.retentionStatus, 'CLEANED');
    });

    test('preserves unknown ratios and probe degradation', () async {
      final json = _json(
        withRemediation: false,
        probeErrors: const ['redis:ConnectionError'],
      );
      (json['system']['memory'] as Map<String, dynamic>)['used_ratio'] = null;
      (json['system']['disk'] as Map<String, dynamic>)['used_ratio'] = null;
      (json['system']['inodes'] as Map<String, dynamic>)['used_ratio'] = null;

      final status = await CapacityStatusClient(_CapacityApi(json)).latest();

      expect(status.memoryUsedRatio, isNull);
      expect(status.diskUsedRatio, isNull);
      expect(status.inodeUsedRatio, isNull);
      expect(status.probeErrors, ['redis:ConnectionError']);
      expect(status.latestRemediation, isNull);
    });
  });

  group('CapacityStatusCard', () {
    Future<void> pumpCard(WidgetTester tester, CapacityStatus value) async {
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: CapacityStatusCard(status: value),
            ),
          ),
        ),
      );
    }

    testWidgets('shows live capacity separately from last autopilot event',
        (tester) async {
      await pumpCard(tester, _status());

      expect(find.text('РЕСУРСЫ СЕРВЕРА'), findsOneWidget);
      expect(find.textContaining('Память'), findsWidgets);
      expect(find.textContaining('75%'), findsWidgets);
      expect(find.textContaining('Диск'), findsWidgets);
      expect(find.textContaining('62%'), findsWidgets);
      expect(find.textContaining('Очередь'), findsWidgets);
      expect(find.textContaining('3'), findsWidgets);
      expect(find.textContaining('Последнее событие автопилота'), findsOneWidget);
      expect(find.textContaining('PRESSURE'), findsWidgets);
      expect(find.textContaining('Ollama: UNLOADED'), findsOneWidget);
      expect(find.textContaining('Retention: CLEANED'), findsOneWidget);
      expect(find.text('Очистить'), findsNothing);
      expect(find.text('Разгрузить Ollama'), findsNothing);
      expect(find.byType(Switch), findsNothing);
    });

    testWidgets('explicitly says when autopilot has no historical event',
        (tester) async {
      await pumpCard(tester, _status(withRemediation: false));

      expect(find.textContaining('Автопилот ещё не фиксировал давление'),
          findsOneWidget);
    });

    testWidgets('probe failures degrade the card without pretending zero is healthy',
        (tester) async {
      await pumpCard(
        tester,
        _status(
          withRemediation: false,
          probeErrors: const ['redis:ConnectionError', 'ollama:TimeoutError'],
        ),
      );

      expect(find.textContaining('Часть метрик недоступна'), findsOneWidget);
      expect(find.textContaining('redis:ConnectionError'), findsOneWidget);
      expect(find.textContaining('ollama:TimeoutError'), findsOneWidget);
    });
  });

  testWidgets('capacity panel loads once only when its route is built',
      (tester) async {
    var calls = 0;
    Future<CapacityStatus> loader() async {
      calls += 1;
      return _status(withRemediation: false);
    }

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: CapacityStatusPanel(
            loader: loader,
            child: const SizedBox.expand(child: Text('existing data screen')),
          ),
        ),
      ),
    );
    expect(calls, 1);

    await tester.pumpAndSettle();
    expect(find.text('РЕСУРСЫ СЕРВЕРА'), findsOneWidget);
    expect(find.text('existing data screen'), findsOneWidget);

    await tester.pump();
    expect(calls, 1);
  });
}
