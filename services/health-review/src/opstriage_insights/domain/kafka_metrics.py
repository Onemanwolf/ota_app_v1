"""Metric keys the review reads (OTA-APP-002 5.1). Apache Kafka extension keys from the research report's
Appendix A; DISK_USED and GC_SUSPENSION are not extension metrics and their Grail keys are INFERRED."""

URP = "kafka.server.ReplicaManager.UnderReplicatedPartitions"
UNDER_MIN_ISR = "kafka.server.ReplicaManager.UnderMinIsrPartitionCount"
OFFLINE = "kafka.controller.KafkaController.OfflinePartitionsCount"
ACTIVE_CONTROLLER = "kafka.controller.KafkaController.ActiveControllerCount.Value"
LEADER_ELECTIONS = "kafka.controller.ControllerStats.LeaderElectionRateAndTimeMs.OneMinuteRate"
UNCLEAN = "kafka.controller.ControllerStats.UncleanLeaderElectionsPerSec.OneMinuteRate"
HANDLER_IDLE = "kafka.server.handler.average-idle-percent.rate"
NETWORK_IDLE = "kafka.network.SocketServer.NetworkProcessorAvgIdlePercent"
REQUEST_QUEUE = "kafka.network.RequestChannel.RequestQueueSize.Value"
FAILED_PRODUCE = "kafka.server.BrokerTopicMetrics.FailedProduceRequestsPerSec.OneMinuteRate"
BYTES_IN = "kafka.server.BrokerTopicMetrics.BytesInPerSec.OneMinuteRate"
LAG_MAX = "kafka.consumer.consumer-metrics.records-lag-max"
DISK_USED = "dt.host.disk.used.percent"  # INFERRED Grail key
GC_SUSPENSION = "builtin:tech.jvm.memory.gc.suspensionTime"  # classic key; Grail equivalent INFERRED

REVIEW_METRICS = (
    URP,
    UNDER_MIN_ISR,
    OFFLINE,
    ACTIVE_CONTROLLER,
    LEADER_ELECTIONS,
    UNCLEAN,
    HANDLER_IDLE,
    NETWORK_IDLE,
    REQUEST_QUEUE,
    FAILED_PRODUCE,
    BYTES_IN,
    LAG_MAX,
    DISK_USED,
    GC_SUSPENSION,
)
