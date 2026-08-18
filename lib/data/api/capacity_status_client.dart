import '../../domain/models/capacity_status.dart';
import 'api_client.dart';

/// GET-only client for the read-only SAI-022 capacity endpoint.
class CapacityStatusClient {
  CapacityStatusClient([ApiClient? api]) : _api = api ?? ApiClient();

  final ApiClient _api;

  Future<CapacityStatus> latest() async {
    final json = await _api.get('/api/v1/capacity');
    return CapacityStatus.fromJson(json);
  }
}
