import 'api_client.dart';

/// Состояние одного серверного секрета интеграции.
/// Значения секретов сервер никогда не возвращает.
class ServerIntegration {
  const ServerIntegration({
    required this.slot,
    required this.venue,
    required this.title,
    required this.purpose,
    required this.environment,
    required this.fields,
    required this.configured,
    this.required = true,
    this.updatedAt,
  });

  final String slot;
  final String venue;
  final String title;
  final String purpose;
  final String environment;
  final List<String> fields;
  final bool required;
  final bool configured;
  final DateTime? updatedAt;

  factory ServerIntegration.fromJson(Map<String, dynamic> json) => ServerIntegration(
        slot: '${json['slot'] ?? ''}',
        venue: '${json['venue'] ?? ''}',
        title: '${json['title'] ?? ''}',
        purpose: '${json['purpose'] ?? ''}',
        environment: '${json['environment'] ?? ''}',
        fields: [
          for (final item in json['fields'] as List<dynamic>? ?? const []) '$item',
        ],
        required: json['required'] != false,
        configured: json['configured'] == true,
        updatedAt: DateTime.tryParse('${json['updated_at'] ?? ''}')?.toLocal(),
      );
}

class IntegrationsClient {
  IntegrationsClient({ApiClient? client}) : _api = client ?? ApiClient();

  final ApiClient _api;
  static const _base = '/api/v1/integrations';

  Future<List<ServerIntegration>> list() async {
    final raw = await _api.getList(_base);
    return [
      for (final item in raw)
        if (item is Map<String, dynamic>) ServerIntegration.fromJson(item),
    ];
  }

  Future<ServerIntegration> save(String slot, Map<String, String> values) async {
    final json = await _api.put('$_base/$slot', body: {'values': values});
    return ServerIntegration.fromJson(json);
  }

  Future<void> remove(String slot) async {
    await _api.delete('$_base/$slot');
  }
}
