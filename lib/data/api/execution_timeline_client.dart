import 'api_client.dart';

/// One durable forensic fact from the server execution history.
///
/// Facts intentionally stay dynamically typed at this generic boundary. In
/// particular, NUMERIC values arrive as exact strings and must not be coerced
/// through `double` by the mobile client.
class ExecutionTimelineEvent {
  const ExecutionTimelineEvent({
    required this.source,
    required this.kind,
    required this.occurredAt,
    required this.facts,
  });

  final String source;
  final String kind;
  final DateTime occurredAt;
  final Map<String, dynamic> facts;

  factory ExecutionTimelineEvent.fromJson(Map<String, dynamic> json) {
    String requiredString(String key) {
      final value = json[key];
      if (value is! String || value.trim().isEmpty) {
        throw ApiException('Сервер вернул timeline-событие без поля $key.');
      }
      return value;
    }

    final occurredRaw = requiredString('occurred_at');
    final occurredAt = DateTime.tryParse(occurredRaw);
    if (occurredAt == null) {
      throw ApiException('Сервер вернул некорректное время timeline-события.');
    }
    final rawFacts = json['facts'];
    if (rawFacts is! Map<String, dynamic>) {
      throw ApiException('Сервер вернул timeline-событие без фактов.');
    }

    return ExecutionTimelineEvent(
      source: requiredString('source'),
      kind: requiredString('kind'),
      occurredAt: occurredAt.toUtc(),
      facts: Map<String, dynamic>.unmodifiable(rawFacts),
    );
  }
}

/// Read-only forensic history for one trade idea.
class ExecutionTimeline {
  const ExecutionTimeline({
    required this.ideaId,
    required this.intentIds,
    required this.events,
  });

  final String ideaId;
  final List<String> intentIds;
  final List<ExecutionTimelineEvent> events;

  factory ExecutionTimeline.fromJson(Map<String, dynamic> json) {
    final idea = json['idea_id'];
    if (idea is! String || idea.trim().isEmpty) {
      throw ApiException('Сервер вернул timeline без идентификатора идеи.');
    }

    final rawIntentIds = json['intent_ids'];
    if (rawIntentIds is! List) {
      throw ApiException('Сервер вернул timeline без списка исполнений.');
    }
    final intentIds = <String>[];
    for (final value in rawIntentIds) {
      if (value is! String || value.trim().isEmpty) {
        throw ApiException('Сервер вернул некорректный идентификатор исполнения.');
      }
      intentIds.add(value);
    }

    final rawEvents = json['events'];
    if (rawEvents is! List) {
      throw ApiException('Сервер вернул timeline без списка событий.');
    }
    final events = <ExecutionTimelineEvent>[];
    DateTime? previous;
    for (final raw in rawEvents) {
      if (raw is! Map<String, dynamic>) {
        throw ApiException('Сервер вернул некорректное timeline-событие.');
      }
      final event = ExecutionTimelineEvent.fromJson(raw);
      if (previous != null && event.occurredAt.isBefore(previous)) {
        throw ApiException('Сервер вернул timeline в некорректном порядке.');
      }
      previous = event.occurredAt;
      events.add(event);
    }

    return ExecutionTimeline(
      ideaId: idea,
      intentIds: List<String>.unmodifiable(intentIds),
      events: List<ExecutionTimelineEvent>.unmodifiable(events),
    );
  }
}

/// Thin read-only client for SAI-051.
class ExecutionTimelineClient {
  ExecutionTimelineClient({ApiClient? api}) : _api = api ?? ApiClient();

  final ApiClient _api;

  Future<ExecutionTimeline> fetch({required String ideaId}) async {
    final idea = ideaId.trim();
    if (idea.isEmpty) {
      throw ApiException('Не указана идея для истории исполнения.');
    }
    final json = await _api.get(
      '/api/v1/execution/ideas/${Uri.encodeComponent(idea)}/timeline',
    );
    final timeline = ExecutionTimeline.fromJson(json);
    if (timeline.ideaId != idea) {
      throw ApiException('Сервер вернул историю другого исполнения.');
    }
    return timeline;
  }
}
