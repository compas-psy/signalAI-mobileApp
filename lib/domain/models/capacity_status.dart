class CapacityRemediation {
  const CapacityRemediation({
    required this.auditId,
    required this.occurredAt,
    required this.pressureState,
    required this.effectiveState,
    required this.newEntries,
    required this.reasons,
    required this.ollamaStatus,
    required this.retentionStatus,
    required this.retentionDeletedFiles,
    required this.retentionDeletedBytes,
    required this.fingerprint,
  });

  factory CapacityRemediation.fromJson(Map<String, dynamic> json) =>
      CapacityRemediation(
        auditId: '${json['audit_id'] ?? ''}',
        occurredAt: DateTime.parse('${json['occurred_at']}'),
        pressureState: '${json['pressure_state'] ?? 'UNKNOWN'}',
        effectiveState: '${json['effective_state'] ?? 'UNKNOWN'}',
        newEntries: '${json['new_entries'] ?? 'UNKNOWN'}',
        reasons: _strings(json['reasons']),
        ollamaStatus: '${json['ollama_status'] ?? 'UNKNOWN'}',
        retentionStatus: '${json['retention_status'] ?? 'UNKNOWN'}',
        retentionDeletedFiles:
            (json['retention_deleted_files'] as num?)?.toInt() ?? 0,
        retentionDeletedBytes:
            (json['retention_deleted_bytes'] as num?)?.toInt() ?? 0,
        fingerprint: '${json['fingerprint'] ?? ''}',
      );

  final String auditId;
  final DateTime occurredAt;
  final String pressureState;
  final String effectiveState;
  final String newEntries;
  final List<String> reasons;
  final String ollamaStatus;
  final String retentionStatus;
  final int retentionDeletedFiles;
  final int retentionDeletedBytes;
  final String fingerprint;
}

/// Live, read-only capacity snapshot plus the latest historical autopilot event.
class CapacityStatus {
  const CapacityStatus({
    required this.collectedAt,
    required this.memoryUsedBytes,
    required this.memoryLimitBytes,
    required this.memoryUsedRatio,
    required this.swapUsedBytes,
    required this.load1,
    required this.load5,
    required this.load15,
    required this.diskUsedBytes,
    required this.diskTotalBytes,
    required this.diskUsedRatio,
    required this.inodeUsed,
    required this.inodeTotal,
    required this.inodeUsedRatio,
    required this.oomEvents,
    required this.oomKills,
    required this.postgresConnections,
    required this.databaseSizeBytes,
    required this.schedulerLagSeconds,
    required this.ingestLagSeconds,
    required this.redisMemoryUsedBytes,
    required this.redisKeys,
    required this.executionQueueDepth,
    required this.executionQueueLagSeconds,
    required this.ollamaReachable,
    required this.ollamaLoadedModels,
    required this.ollamaConfiguredModelLoaded,
    required this.probeErrors,
    required this.latestRemediation,
  });

  factory CapacityStatus.fromJson(Map<String, dynamic> json) {
    final system = _map(json['system']);
    final memory = _map(system['memory']);
    final disk = _map(system['disk']);
    final inodes = _map(system['inodes']);
    final postgres = _map(json['postgres']);
    final redis = _map(json['redis']);
    final ollama = _map(json['ollama']);
    final remediationJson = json['latest_remediation'];

    return CapacityStatus(
      collectedAt: DateTime.parse('${json['collected_at']}'),
      memoryUsedBytes: _int(memory['used_bytes']),
      memoryLimitBytes: _int(memory['limit_bytes']),
      memoryUsedRatio: _doubleOrNull(memory['used_ratio']),
      swapUsedBytes: _int(system['swap_used_bytes']),
      load1: _double(system['load1']),
      load5: _double(system['load5']),
      load15: _double(system['load15']),
      diskUsedBytes: _int(disk['used_bytes']),
      diskTotalBytes: _int(disk['total_bytes']),
      diskUsedRatio: _doubleOrNull(disk['used_ratio']),
      inodeUsed: _int(inodes['used']),
      inodeTotal: _int(inodes['total']),
      inodeUsedRatio: _doubleOrNull(inodes['used_ratio']),
      oomEvents: _int(system['oom_events']),
      oomKills: _int(system['oom_kills']),
      postgresConnections: _int(postgres['connections']),
      databaseSizeBytes: _int(postgres['database_size_bytes']),
      schedulerLagSeconds: _double(postgres['scheduler_lag_seconds']),
      ingestLagSeconds: _double(postgres['ingest_lag_seconds']),
      redisMemoryUsedBytes: _int(redis['memory_used_bytes']),
      redisKeys: _int(redis['keys']),
      executionQueueDepth: _int(redis['execution_queue_depth']),
      executionQueueLagSeconds:
          _double(redis['execution_queue_lag_seconds']),
      ollamaReachable: ollama['reachable'] == true,
      ollamaLoadedModels: _int(ollama['loaded_models']),
      ollamaConfiguredModelLoaded: ollama['configured_model_loaded'] == true,
      probeErrors: _strings(json['probe_errors']),
      latestRemediation: remediationJson is Map<String, dynamic>
          ? CapacityRemediation.fromJson(remediationJson)
          : null,
    );
  }

  final DateTime collectedAt;
  final int memoryUsedBytes;
  final int memoryLimitBytes;
  final double? memoryUsedRatio;
  final int swapUsedBytes;
  final double load1;
  final double load5;
  final double load15;
  final int diskUsedBytes;
  final int diskTotalBytes;
  final double? diskUsedRatio;
  final int inodeUsed;
  final int inodeTotal;
  final double? inodeUsedRatio;
  final int oomEvents;
  final int oomKills;
  final int postgresConnections;
  final int databaseSizeBytes;
  final double schedulerLagSeconds;
  final double ingestLagSeconds;
  final int redisMemoryUsedBytes;
  final int redisKeys;
  final int executionQueueDepth;
  final double executionQueueLagSeconds;
  final bool ollamaReachable;
  final int ollamaLoadedModels;
  final bool ollamaConfiguredModelLoaded;
  final List<String> probeErrors;
  final CapacityRemediation? latestRemediation;
}

Map<String, dynamic> _map(Object? value) =>
    value is Map<String, dynamic> ? value : const <String, dynamic>{};

int _int(Object? value) => value is num ? value.toInt() : 0;

double _double(Object? value) => value is num ? value.toDouble() : 0.0;

double? _doubleOrNull(Object? value) => value is num ? value.toDouble() : null;

List<String> _strings(Object? value) => value is List
    ? value.whereType<Object>().map((item) => item.toString()).toList()
    : const <String>[];
