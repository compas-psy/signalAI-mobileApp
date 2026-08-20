import 'package:flutter_test/flutter_test.dart';

import 'package:signalai/data/api/api_client.dart';
import 'package:signalai/data/api/execution_timeline_client.dart';

class _RecordingApi extends ApiClient {
  _RecordingApi()
      : super(baseUrl: 'https://engine.test', deviceToken: 'device');

  String? path;
  Map<String, dynamic> response = <String, dynamic>{};

  @override
  Future<Map<String, dynamic>> get(String path) async {
    this.path = path;
    return response;
  }
}

Map<String, dynamic> _timelineJson() => <String, dynamic>{
      'idea_id': 'idea-51',
      'intent_ids': <String>['intent-51'],
      'events': <Map<String, dynamic>>[
        <String, dynamic>{
          'source': 'fill',
          'kind': 'FILL_RECORDED',
          'occurred_at': '2026-08-20T14:15:00+00:00',
          'facts': <String, dynamic>{
            'fill_id': 'fill-1',
            'intent_id': 'intent-51',
            'quantity': '1.250000000000',
            'price': '90110.000000000000',
          },
        },
        <String, dynamic>{
          'source': 'manual_control',
          'kind': 'MANUAL_CLOSE_REQUESTED',
          'occurred_at': '2026-08-20T14:16:00+00:00',
          'facts': <String, dynamic>{
            'command_id': 'command-51',
            'intent_id': 'intent-51',
            'status': 'REQUESTED',
            'reduce_only': true,
          },
        },
      ],
    };

void main() {
  test('SAI-051 client fetches timeline by idea id and preserves exact facts',
      () async {
    final api = _RecordingApi()..response = _timelineJson();
    final client = ExecutionTimelineClient(api: api);

    final timeline = await client.fetch(ideaId: 'idea-51');

    expect(api.path, '/api/v1/execution/ideas/idea-51/timeline');
    expect(timeline.ideaId, 'idea-51');
    expect(timeline.intentIds, <String>['intent-51']);
    expect(timeline.events, hasLength(2));
    expect(timeline.events.first.kind, 'FILL_RECORDED');
    expect(timeline.events.first.facts['quantity'], '1.250000000000');
    expect(timeline.events.first.facts['price'], '90110.000000000000');
    expect(timeline.events.last.facts['status'], 'REQUESTED');
  });

  test('SAI-051 client fails closed on malformed forensic event', () {
    final malformed = _timelineJson();
    (malformed['events'] as List<dynamic>)[0] = <String, dynamic>{
      'source': 'fill',
      'kind': 'FILL_RECORDED',
      'occurred_at': 'not-a-time',
      'facts': <String, dynamic>{'quantity': '1.25'},
    };

    expect(
      () => ExecutionTimeline.fromJson(malformed),
      throwsA(isA<ApiException>()),
    );
  });

  test('SAI-051 client rejects response whose idea identity changed', () async {
    final api = _RecordingApi()..response = _timelineJson();
    api.response['idea_id'] = 'other-idea';
    final client = ExecutionTimelineClient(api: api);

    await expectLater(
      client.fetch(ideaId: 'idea-51'),
      throwsA(isA<ApiException>()),
    );
  });
}
