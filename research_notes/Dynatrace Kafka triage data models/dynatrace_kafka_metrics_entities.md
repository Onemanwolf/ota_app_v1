# Dynatrace Kafka Monitoring: Metric Keys, Entity Model, and How Problems Surface

Labels used below: **DOCUMENTED** means the value or key is quoted from the cited URL. **INFERRED** means it is my reasoning and is not verified in a source.

Sources used most:
- Dynatrace built-in (classic) metrics list: https://docs.dynatrace.com/docs/analyze-explore-automate/metrics-classic/built-in-metrics. The metric IDs, display names and units were pulled from the page's embedded JSON.
- Apache Kafka extension docs: https://docs.dynatrace.com/docs/ingest-from/extensions/supported-extensions/apache-kafka (alias https://docs.dynatrace.com/docs/observe/infrastructure-observability/extensions/apache-kafka). The page says "Updated on Jun 15, 2026".
- Dynatrace Hub page for the extension, with release notes: https://www.dynatrace.com/hub/detail/apache-kafka/
- Apache Kafka monitoring docs (MBeans and normal values): https://kafka.apache.org/41/operations/monitoring/
- kafka_exporter README: https://github.com/danielqsj/kafka_exporter
- Dynatrace queue concepts: https://docs.dynatrace.com/docs/observe/infrastructure-observability/queues/queue-concepts

---

## Q1. OneAgent built-in (classic) Kafka metrics: builtin:tech.kafka.*

### Takeaway
Classic built-in Kafka monitoring exposes only **six** `builtin:tech.kafka.pg.*` metrics, all split by process group. Everything else (bytes in/out, ISR, request handler idle, purgatory and so on) is available only through the Extensions 2.0 "Apache Kafka" JMX extension. The classic metrics are listed under "Metrics Classic". Dynatrace's own Kafka monitoring pages now point users to the extension.

### Cited Findings
- **DOCUMENTED.** These six classic built-in Kafka metric keys are verbatim from the page JSON at https://docs.dynatrace.com/docs/analyze-explore-automate/metrics-classic/built-in-metrics. All are `billable:false`, and their aggregations are `auto, avg, count, max, min, sum`.

  | Metric key | Display name | Unit |
  |---|---|---|
  | `builtin:tech.kafka.pg.kafka.server.ReplicaManager.UnderReplicatedPartitions.Value` | Kafka broker - Under replicated partitions | Count |
  | `builtin:tech.kafka.pg.kafka.server.ReplicaManager.PartitionCount.Value` | Kafka broker - Partitions | Count |
  | `builtin:tech.kafka.pg.kafka.controller.ControllerStats.LeaderElectionRateAndTimeMs.OneMinuteRate` | Kafka broker - Leader election rate | MilliSecond (as listed) |
  | `builtin:tech.kafka.pg.kafka.controller.ControllerStats.UncleanLeaderElectionsPerSec.OneMinuteRate` | Kafka broker - Unclean election rate | PerSecond |
  | `builtin:tech.kafka.pg.kafka.controller.KafkaController.ActiveControllerCount.Value` | Kafka controller - Active cluster controllers | Count |
  | `builtin:tech.kafka.pg.kafka.controller.KafkaController.OfflinePartitionsCount.Value` | Kafka controller - Offline partitions | Count |

- **DOCUMENTED.** The built-in list has no `builtin:tech.kafka.*` keys for bytes in/out, messages in, ISR shrinks/expands, request handler idle, request queue, purgatory, log flush, or failed produce/fetch. A grep of the full page for `builtin:tech.kafka` returned only the six keys above. Source: same URL.
- **DOCUMENTED.** Dynatrace's older Kafka documentation says it "is now deprecated and will no longer be updated", recommends the new extension, and says Dynatrace "automatically recognizes Kafka processes and instantly gathers Kafka metrics on the process and cluster levels". This text appeared in a search snippet for https://docs.dynatrace.com/docs/shortlink/kafka. When I fetched that shortlink, it returned Confluent Cloud extension content instead.
- **DOCUMENTED.** These JVM metrics apply to Kafka broker processes, which are Java processes. All are split by process group instance. Source: https://docs.dynatrace.com/docs/analyze-explore-automate/metrics-classic/built-in-metrics
  - `builtin:tech.jvm.memory.pool.used` ("JVM heap memory pool used bytes")
  - `builtin:tech.jvm.memory.runtime.max`, `builtin:tech.jvm.memory.runtime.total`, `builtin:tech.jvm.memory.runtime.free`
  - `builtin:tech.jvm.memory.gc.collectionTime`, `builtin:tech.jvm.memory.gc.suspensionTime` ("Garbage collection suspension time", unit Percent)
  - `builtin:tech.jvm.memory.gc.activationCount`, `builtin:tech.jvm.threads.count`
- **DOCUMENTED.** These process metrics are relevant to brokers. Source: https://docs.dynatrace.com/docs/analyze-explore-automate/metrics-classic/built-in-metrics
  - Availability: `builtin:pgi.availability`, `builtin:pgi.availability.state`
  - CPU: `builtin:tech.generic.cpu.usage`
  - Memory: `builtin:tech.generic.mem.usage`
  - File descriptors: `builtin:tech.generic.handles.fileDescriptorsPercentUsed`, `builtin:tech.generic.handles.fileDescriptorsUsed`
  - Disk I/O: `builtin:tech.generic.io.bytesRead`, `builtin:tech.generic.io.bytesWritten`

### Inferences
- **INFERRED.** The `.pg.` segment in the keys means these metrics are recorded per Kafka **process group**, not per broker entity. In a classic environment, a "Kafka cluster" therefore shows up as a PROCESS_GROUP with technology "Apache Kafka", and each broker is a PROCESS_GROUP_INSTANCE.
- **INFERRED.** The "MilliSecond" unit on LeaderElectionRateAndTimeMs.OneMinuteRate is probably a labeling quirk. A OneMinuteRate is an events/sec rate. Treat the value as a rate when building synthetic data.
- **INFERRED.** Kafka's own docs describe ActiveControllerCount as "only one broker in the cluster should have 1". To use the classic metric for the cluster-wide check `== 1`, you would query it with `:splitBy():sum`.

### Gaps
- I found no explicit Dynatrace statement giving a removal date for the six `builtin:tech.kafka.pg.*` metrics. They are still listed under "Metrics Classic".
- Descriptions are empty in the page JSON for all six keys.

---

## Q2. Extensions 2.0 "Apache Kafka" (JMX) extension

### Takeaway
The extension is `com.dynatrace.extension.jmx-kafka`. The latest version on the Hub is 3.1.4. It is JMX-based and needs the OneAgent feature "Java Metric Extensions 2.0 (JMX)". It has 19 feature sets, and its metric keys mirror the MBean names (`kafka.server.*`, `kafka.controller.*`, `kafka.network.*`, `kafka.consumer.*`, `kafka.producer.*`, ...). It creates Broker, Topic, Producer and Consumer entities. Dynatrace documents that it ships alerts but does not publish their exact names or thresholds.

### Cited Findings
- **DOCUMENTED.** Extension identity and versions:
  - The extension ID `com.dynatrace.extension.jmx-kafka` appears in the Hub media URLs. The latest version is **3.1.4**.
  - Version history: 3.1.4, 3.1.2, 3.1.0, 2.5.0, 2.4.0, 2.3.5, 2.3.3, 2.3.1, 2.2.0, 2.1.1, 2.1.0, 2.0.0, 1.1.2, 1.0.8 ... 1.0.0.
  - Source: https://www.dynatrace.com/hub/detail/apache-kafka/
- **DOCUMENTED.** Release notes. Source: https://www.dynatrace.com/hub/detail/apache-kafka/
  - **3.1.0** requires Dynatrace and EEC version 1.343.0 or later. It is a breaking change for Grail: "any metrics ingested from the extension will be processed by a dedicated OpenPipeline pipeline". Field names, such as capitalization, may differ, and you opt in through the Settings API. It moved the lag metrics into the `consumer-records-lag` feature set, made consumed-rate metrics always split by topic, and added Smartscape on Grail plus the metrics `kafka.server.tenant-metrics.consumer-lag-offsets` and `kafka.log.Log.LogEndOffset`.
  - **3.1.4** files Kafka under `Messaging` instead of `Application`.
  - **2.4.0** added ALERT_TEMPLATES to the Davis Anomaly Detection app.
  - **2.0.0** was a breaking change: consumer and producer entities are now built from the process group instance plus host, not `client-id`, so entity IDs changed. Charts show metrics per `client-id`.
  - **1.1.2** removed the ZooKeeper entities.
  - **1.0.8** renamed the out-of-the-box alerts so their titles include "Kafka".
- **DOCUMENTED.** Requirements: the OneAgent feature "Java Metric Extensions 2.0 (JMX)" must be on. Brokers, producers and consumers must run on Linux or Windows. The only variable is "Topic name filter" (ObjectName wildcards `*` and `?`), which applies to every metric with a topic dimension. Source: https://docs.dynatrace.com/docs/ingest-from/extensions/supported-extensions/apache-kafka
- **DOCUMENTED.** Entities: Kafka Topic, Kafka Producer, Kafka Consumer, Kafka Broker, and the relationships between them. Screens: an "Apache Kafka Overview" dashboard and Unified Analysis pages for Topics, Producers, Consumers and Brokers. The extension also injects tiles into Host and Process Group Instance pages. Sources: https://docs.dynatrace.com/docs/ingest-from/extensions/supported-extensions/apache-kafka and https://www.dynatrace.com/hub/detail/apache-kafka/
- **DOCUMENTED.** Shipped alerts cover "broker availability, cluster controllers, offline partitions, leader elections, and replicated partitions". The docs list no exact names or thresholds. Source: https://docs.dynatrace.com/docs/ingest-from/extensions/supported-extensions/apache-kafka
- **DOCUMENTED.** Dimensions:
  - `topic`, `partition`, `host`, `process_group_instance`, `node` (producer node metrics), and `client-id`.
  - For `kafka.server.tenant-metrics.consumer-lag-offsets`: consumer group name, client ID, member ID, partition ID, topic name, group protocol.
  - `status` on connector metrics.
  - Sources: https://www.dynatrace.com/hub/detail/apache-kafka/ and https://docs.dynatrace.com/docs/ingest-from/extensions/supported-extensions/apache-kafka
- **DOCUMENTED.** Cost: the extension polls every minute. The classic license charges 0.001 DDU per data point. In the example of 1 broker, 1 producer and 1 consumer with all feature sets, that is 64 data points/min, about 33.6M data points/year, or about 33,638 DDU. Source: https://docs.dynatrace.com/docs/ingest-from/extensions/supported-extensions/apache-kafka

#### Extension metric keys by feature set (all DOCUMENTED)
Source for every key below: https://docs.dynatrace.com/docs/ingest-from/extensions/supported-extensions/apache-kafka. Units are not given as a field on the page; they come from the description text.

**broker-metrics**
- Byte and message throughput:
  - `kafka.server.BrokerTopicMetrics.BytesInPerSec.OneMinuteRate`
  - `kafka.server.BrokerTopicMetrics.BytesOutPerSec.OneMinuteRate`
  - `kafka.server.BrokerTopicMetrics.MirrorBytesInPerSec.OneMinuteRate`
  - `kafka.server.BrokerTopicMetrics.MessagesInPerSec.OneMinuteRate`
- Request rates:
  - `kafka.server.BrokerTopicMetrics.TotalProduceRequestsPerSec.OneMinuteRate`
  - `kafka.server.BrokerTopicMetrics.TotalFetchRequestsPerSec.OneMinuteRate`
  - `kafka.server.BrokerTopicMetrics.TotalFollowerFetchRequestsPerSec.OneMinuteRate`
- Failed requests:
  - `kafka.server.BrokerTopicMetrics.FailedProduceRequestsPerSec.OneMinuteRate`
  - `kafka.server.BrokerTopicMetrics.FailedFetchRequestsPerSec.OneMinuteRate`
- Message conversions:
  - `kafka.server.BrokerTopicMetrics.ProduceMessageConversionsPerSec.OneMinuteRate`
  - `kafka.server.FetchConversionsRate.OneMinuteRate`
  - `kafka.server.ProduceConversionsRate.OneMinuteRate`
- Partitions and replication:
  - `kafka.server.ReplicaManager.PartitionCount`
  - `kafka.server.ReplicaManager.UnderReplicatedPartitions`
  - `kafka.server.ReplicaManager.UnderMinIsrPartitionCount`
- Follower lag:
  - `kafka.server.ReplicaFetcherManager.MaxLag.Replica.Value` (max follower lag)
  - `kafka.server.FetcherLagMetrics.ConsumerLag.Value` (per-follower replica lag)

**controller-metrics**
- `kafka.controller.KafkaController.OfflinePartitionsCount` (partitions without an active leader)
- `kafka.controller.KafkaController.ActiveControllerCount.Value` (whether this broker is the controller)

**leader-metrics**
- `kafka.controller.ControllerStats.LeaderElectionRateAndTimeMs.OneMinuteRate` (non-zero after broker failures)
- `kafka.controller.ControllerStats.UncleanLeaderElectionsPerSec.OneMinuteRate` (should be zero)
- `kafka.server.ReplicaManager.LeaderCount.Value`

**network-metrics**
- Request rates:
  - `kafka.network.RequestMetrics.RequestsPerSec.Produce.OneMinuteRate`
  - `kafka.network.RequestMetrics.RequestsPerSec.FetchConsumer.OneMinuteRate`
  - `kafka.network.RequestMetrics.RequestsPerSec.FetchFollower.OneMinuteRate`
- Total request time (ms):
  - `kafka.network.RequestMetrics.TotalTimeMs.Produce.Count`
  - `kafka.network.RequestMetrics.TotalTimeMs.FetchConsumer.Count`
  - `kafka.network.RequestMetrics.TotalTimeMs.FetchFollower.Count`
- Queues and network threads:
  - `kafka.network.RequestChannel.RequestQueueSize.Value`
  - `kafka.network.RequestChannel.ResponseQueueSize.Value`
  - `kafka.network.SocketServer.NetworkProcessorAvgIdlePercent` (fraction of time idle)

**handler-metrics**
- `kafka.server.handler.average-idle-percent.rate` ("Handler Pool Idle Percent Rate", the fraction of time request handler threads are idle)

**purgatory-metrics**
- `kafka.server.purgatory.produce-delay-size`
- `kafka.server.purgatory.fetch-delay-size`

**log-metrics**
- `kafka.log.LogFlushStats.LogFlushRateAndTimeMs.Percentile95th`
- `kafka.log.LogFlushStats.LogFlushRateAndTimeMs.Mean`
- `kafka.log.Log.LogEndOffset` (per topic and partition)

**disk-metrics** (Linux only)
- `kafka.server.disk.read-bytes`
- `kafka.server.disk.write-bytes`

**zookeeper-metrics**
- `kafka.server.SessionExpireListener.ZooKeeperDisconnectsPerSec.OneMinuteRate`
- `kafka.server.SessionExpireListener.ZooKeeperExpiresPerSec.OneMinuteRate`
- `kafka.server.active-connections`

**Raft Broker** (KRaft)
- `kafka.raft.last-applied-record-offset`
- `kafka.raft.last-applied-record-timestamp`
- `kafka.raft.last-applied-record-lag-ms`
- `kafka.raft.metadata-load-error-count`
- `kafka.raft.metadata-apply-error-count`

**Raft Quorum**
- `kafka.raft.append-records-rate`
- `kafka.raft.commit-latency-avg`
- `kafka.raft.commit-latency-max`
- `kafka.raft.fetch-records-rate`
- `kafka.raft.log-end-epoch`
- `kafka.raft.log-end-offset`
- `kafka.raft.current-leader` (-1 means unknown)

**consumer-metrics**
- Requests and bytes:
  - `kafka.consumer.consumer-metrics.request-rate`
  - `kafka.consumer.consumer-metrics.request-size-avg`
  - `kafka.consumer.consumer-metrics.incoming-byte-rate`
  - `kafka.consumer.consumer-metrics.outgoing-byte-rate`
  - `kafka.consumer.consumer-metrics.request-latency-avg` (ms)
- Assignment:
  - `kafka.consumer.consumer-metrics.assigned-partitions`
- Consumption rates:
  - `kafka.consumer.consumer-metrics.records-consumed-rate`
  - `kafka.consumer.consumer-metrics.bytes-consumed-rate`
  - `kafka.consumer.consumer-metrics.records-consumed-rate-total`
  - `kafka.consumer.consumer-metrics.bytes-consumed-rate-total`
- Fetch latency and throttling:
  - `kafka.consumer.consumer-metrics.fetch-latency-avg`
  - `kafka.consumer.consumer-fetch-manager-metrics.fetch-throttle-time-avg`

**consumer-records-lag**
- `kafka.consumer.consumer-metrics.records-lag`
- `kafka.consumer.consumer-metrics.records-lag-avg`
- `kafka.consumer.consumer-metrics.records-lag-max`

**confluent-consumer-lag** (Confluent Platform only)
- `kafka.server.tenant-metrics.consumer-lag-offsets`

**producer-metrics**
- Bytes and responses:
  - `kafka.producer.producer-metrics.incoming-byte-rate`
  - `kafka.producer.producer-metrics.outgoing-byte-rate`
  - `kafka.producer.producer-metrics.response-rate`
- Latency and I/O wait:
  - `kafka.producer.producer-metrics.io-wait-time-ns-avg` (ns)
  - `kafka.producer.producer-metrics.request-latency-avg` (ms)
- Requests and compression:
  - `kafka.producer.producer-metrics.compression-rate-avg`
  - `kafka.producer.producer-metrics.request-size-avg`
  - `kafka.producer.producer-metrics.request-rate`
  - `kafka.producer.producer-metrics.produce-throttle-time-avg`
- Per-topic metrics:
  - `kafka.producer.producer-topic-metrics.byte-rate`
  - `kafka.producer.producer-topic-metrics.compression-rate`
  - `kafka.producer.producer-topic-metrics.record-error-rate` ("Failed Requests Rate")
  - `kafka.producer.producer-topic-metrics.record-send-rate`

**connect-metrics**
- `kafka.connect.connect-metrics.request-rate`
- `kafka.connect.connect-metrics.outgoing-byte-rate`
- `kafka.connect.connect-metrics.request-size-avg`
- `kafka.connect.connect-metrics.incoming-byte-rate`

**connector-metrics**
- Status (1 = running, 0 = otherwise):
  - `kafka.connector.status`
  - `kafka.connector.task.status`
- Task state ratios:
  - `kafka.connector.task.pause-ratio`
  - `kafka.connector.task.running-ratio`
- Offset commits:
  - `kafka.connector.task.offset-commit-success-percentage`
  - `kafka.connector.task.offset-commit-failure-percentage`
  - `kafka.connector.task.offset-commit-max-time-ms`
  - `kafka.connector.task.offset-commit-avg-time-ms`
- Batch sizes:
  - `kafka.connector.task.batch-size-max`
  - `kafka.connector.task.batch-size-avg`

**cluster-link** (Confluent)
- Link and mirror state:
  - `kafka.cluster-link.link-count`
  - `kafka.cluster-link.mirror-topic-count`
  - `kafka.cluster-link.link-task-count`
  - `kafka.cluster-link.unavailable-link-count`
  - `kafka.cluster-link.authentication-problems`
- Lag and throughput:
  - `kafka.cluster-link.max-lag`
  - `kafka.cluster-link.bytes-per-sec.count`
  - `kafka.cluster-link.fetch-throttle-time-avg`
- Connections and admin:
  - `kafka.cluster-link.reverse-connection-failed-total`
  - `kafka.cluster-link.controller-reverse-connection-count`
  - `kafka.cluster-link.remote-admin-request-time`
- Threads:
  - `kafka.cluster-link.background-thread-usage`

**multi-region-kafka** (Confluent)
- `kafka.cluster.Partition.ObserverReplicasCount`
- `kafka.cluster.Partition.CaughtUpReplicasCount`
- `kafka.cluster.Partition.IsNotCaughtUp`
- `kafka.cluster.Partition.ObserversInIsrCount`

#### Keys that do NOT exist in the extension (DOCUMENTED by absence)
The full feature-set list at https://docs.dynatrace.com/docs/ingest-from/extensions/supported-extensions/apache-kafka has no key for any of the following:
- **IsrShrinksPerSec / IsrExpandsPerSec**. Use UnderReplicatedPartitions or UnderMinIsrPartitionCount as the proxy.
- **RequestHandlerAvgIdlePercent** under that MBean-style name. The equivalent key is `kafka.server.handler.average-idle-percent.rate`.
- **Purgatory under the MBean name** (`DelayedOperationPurgatory.PurgatorySize`). Use the `kafka.server.purgatory.*-delay-size` keys.
- **Log dir disk usage percentage.**

### Inferences
- **INFERRED.** In Grail, metrics from 3.1.x go through a dedicated OpenPipeline pipeline. DQL keys are expected to be the same `kafka.*` strings, for example `timeseries max(kafka.server.ReplicaManager.UnderReplicatedPartitions), by:{dt.entity.process_group_instance}`. Dimension names, including their capitalization, may differ between pre-3.1 routing and post-3.1 routing, so check them in Notebooks.
- **INFERRED.** In environments that ingest JMX MBeans through the old EF1 plugin, metric keys can appear with a `legacy.` prefix. This comes from a search snippet of a Dynatrace community thread (https://community.dynatrace.com/t5/Extensions/Legacy-metric-event-though-EF2/td-p/302474) that I could not fetch directly.

### Gaps
- **Exact Grail/Smartscape entity type IDs** for the extension's generic types (for example `kafka:broker` / `dt.entity.kafka:broker`, or `kafka:topic`) were **not found** in any accessible source. The GitHub API needed authentication and the community page returned 403. Do not hard-code these IDs without checking them in a tenant (`fetch dt.entity.` autocomplete).
- I found no published names or thresholds for the shipped alerts or metric events.
- The docs page gives no per-metric unit field and no full dimension list for each metric.

---

## Q3. Consumer lag in Dynatrace

### Takeaway
There is no `builtin:` consumer-lag metric. Lag comes from one of four places:
1. Consumer-side JMX via the extension (`kafka.consumer.consumer-metrics.records-lag*`), which needs OneAgent with JMX on the consumer JVMs.
2. Broker-side Confluent Platform tenant metrics (`kafka.server.tenant-metrics.consumer-lag-offsets`).
3. Confluent Cloud extension metrics.
4. Prometheus kafka_exporter or Kafka Lag Exporter ingestion.

### Cited Findings
- **DOCUMENTED.** The extension provides `kafka.consumer.consumer-metrics.records-lag` (latest partition lag), `kafka.consumer.consumer-metrics.records-lag-avg` and `kafka.consumer.consumer-metrics.records-lag-max`, all in the `consumer-records-lag` feature set. Source: https://docs.dynatrace.com/docs/ingest-from/extensions/supported-extensions/apache-kafka
- **DOCUMENTED.** `kafka.server.tenant-metrics.consumer-lag-offsets` is the "Gap between the broker's last offset and the group's committed offset". It is split by consumer group, client, member, partition, topic and protocol, and works on Confluent Platform only. Sources: https://docs.dynatrace.com/docs/ingest-from/extensions/supported-extensions/apache-kafka and https://www.dynatrace.com/hub/detail/apache-kafka/
- **DOCUMENTED.** Kafka MBean source: `kafka.consumer:type=consumer-fetch-manager-metrics,client-id={client-id}` with attribute `records-lag-max`. Kafka gives no normal-value guidance for it. Source: https://kafka.apache.org/41/operations/monitoring/
- **DOCUMENTED.** Confluent Cloud extension lag metrics:
  - `confluent_kafka_server_consumer_lag_offsets` ("Kafka Server Consumer Lag Offsets")
  - `kafka_consumergroup_group_lag` (needs a separately run Kafka Lag Exporter, which Dynatrace does not support)
  - `kafka_partition_earliest_offset` and `kafka_partition_latest_offset`
  - Confluent's API has a fixed 5-minute timestamp offset.
  - Source: https://docs.dynatrace.com/docs/ingest-from/technology-support/dynatrace-extensions/supported-out-of-the-box/kafka. That URL served Confluent Cloud extension content when fetched on 2026-10-12.

### Inferences
- **INFERRED.** For an ops-triage app, the most realistic Dynatrace-native lag signal is `kafka.consumer.consumer-metrics.records-lag-max`, split by `client-id` / `topic` / `partition`, on consumer process group instances. A healthy value hovers near 0 to a few hundred records and does not grow. The unhealthy pattern is a monotonic increase over 10 to 15 minutes while `records-consumed-rate` falls.

### Gaps
- I found no Dynatrace-published lag threshold.

---

## Q4. Prometheus, OpenTelemetry and Confluent Cloud ingestion

### Takeaway
kafka_exporter metric names are well documented, and they keep their Prometheus names when ingested through the Dynatrace Prometheus or OTel paths. The Confluent Cloud extension uses `confluent_kafka_*` keys. Dynatrace also documents an OTel Collector "kafka receiver" use case.

### Cited Findings
- **DOCUMENTED.** kafka_exporter metrics. Source: https://github.com/danielqsj/kafka_exporter
  - Brokers: `kafka_brokers`, `kafka_broker_info{address,id}`
  - Topic partitions: `kafka_topic_partitions{topic}`, plus these with `{topic,partition}`:
    - `kafka_topic_partition_current_offset`
    - `kafka_topic_partition_oldest_offset`
    - `kafka_topic_partition_in_sync_replica`
    - `kafka_topic_partition_leader`
    - `kafka_topic_partition_leader_is_preferred`
    - `kafka_topic_partition_replicas`
    - `kafka_topic_partition_under_replicated_partition` (1 if under-replicated)
  - Consumer groups:
    - `kafka_consumergroup_current_offset{consumergroup,topic,partition}`
    - `kafka_consumergroup_current_offset_sum{consumergroup,topic}`
    - `kafka_consumergroup_lag{consumergroup,topic,partition}`
    - `kafka_consumergroup_lag_sum{consumergroup,topic}`
    - `kafka_consumergroup_members{consumergroup}`
    - `kafka_consumergroupzookeeper_lag_zookeeper`
- **DOCUMENTED.** Confluent Cloud extension examples:
  - `confluent_kafka_server_received_bytes`
  - `confluent_kafka_server_partition_count.gauge`
  - `confluent_kafka_server_request_count.gauge`
  - `confluent_kafka_server_producer_latency_avg_milliseconds`
  - `confluent_kafka_server_consumer_lag_offsets`
  - Authentication uses a Cloud/Cluster API key with the MetricsViewer role. Resource IDs go in the endpoint URL (`resource.kafka.id=lkc-XXXXX`).
  - Source: https://docs.dynatrace.com/docs/ingest-from/technology-support/dynatrace-extensions/supported-out-of-the-box/kafka
- **DOCUMENTED (existence only).** Dynatrace has an OTel Collector Kafka receiver use-case page at https://docs.dynatrace.com/docs/ingest-from/opentelemetry/collector/use-cases/kafka/receiver (found in search results; I did not fetch it).

### Inferences
- **INFERRED.** Ingesting through Prometheus scraping (ActiveGate/EEC Prometheus extension or OTel Collector) keeps the metric names as-is. Label names become dimensions.

### Gaps
- I did not research JMX-exporter metric naming (`kafka_server_replicamanager_underreplicatedpartitions`-style). It depends on the user's rules config and is not Dynatrace-specific.
- I did not fetch the OTel kafkametrics receiver metric names (for example `kafka.consumer_group.lag`), so they are not verified here.

---

## Q5. Kafka client monitoring and tracing in OneAgent; queue entities

### Takeaway
OneAgent in Full-Stack mode instruments Kafka clients and creates **Queue** entities for topics seen in traces. It also creates producer, consumer and listener services, and adds `builtin:queue.incoming_requests` and `builtin:queue.outgoing_requests` message counts.

### Cited Findings
- **DOCUMENTED.** OneAgent "automatically detects queues and topics when monitored applications interact with them by instrumenting compatible messaging clients". Dynatrace "creates Queue entities for all detected queues and topics that are part of distributed traces". This requires Full-Stack Monitoring mode. Topic entities are named `<topic-name>`. Source: https://docs.dynatrace.com/docs/observe/infrastructure-observability/queues/queue-concepts
- **DOCUMENTED.** There are three service roles:
  - **Producer service**: sends messages to a queue or topic.
  - **Consumer service**: retrieves messages via a listener, or is subscribed to a topic.
  - **Listener service**: counts dequeued messages and "is always followed by a consumer service".
  - Trace context travels via `traceparent`/`tracestate`, `x-dynatrace`, `dtdTraceTagInfo`, or a key built from message properties.
  - Source: https://docs.dynatrace.com/docs/observe/infrastructure-observability/queues/queue-concepts
- **DOCUMENTED.** Queue metrics: `builtin:queue.incoming_requests` ("Incoming messages", Count) and `builtin:queue.outgoing_requests` ("Outgoing messages", Count). Source: https://docs.dynatrace.com/docs/analyze-explore-automate/metrics-classic/built-in-metrics
- **DOCUMENTED.** For Java, features can be enabled to instrument Kafka listeners automatically, so you do not have to define a custom messaging service. Source: https://docs.dynatrace.com/docs/platform-modules/applications-and-microservices/services/service-detection-and-naming/service-types/define-messaging-services (search snippet)

### Inferences
- **INFERRED.** The classic entity type for queues is `QUEUE` (Entities API v2), with IDs like `QUEUE-1A2B3C4D5E6F7A8B`. The docs page only says "Queue". `QUEUE_INSTANCE` also exists in classic topology, but I did not verify it in a source.
- **INFERRED.** Spans follow OTel semantic conventions (`messaging.system="kafka"`, `messaging.destination.name=<topic>`, `messaging.operation`). I found no Dynatrace page confirming the exact attributes.

### Gaps
- I found no Dynatrace doc stating the `messaging.system` value or the Grail span field names for Kafka.

---

## Q6. Entity types and ID formats

### Takeaway
Classic Kafka brokers are PROCESS_GROUP_INSTANCEs (technology "Apache Kafka") running on HOSTs, and the cluster is a PROCESS_GROUP. The extension adds generic Kafka Broker, Topic, Producer and Consumer entities on top. OneAgent tracing adds Queue entities and messaging SERVICEs.

### Cited Findings
- **DOCUMENTED.** The classic metrics are keyed `builtin:tech.kafka.pg.*`, which means they are per process group. Source: https://docs.dynatrace.com/docs/analyze-explore-automate/metrics-classic/built-in-metrics
- **DOCUMENTED.** The extension creates entities for brokers, topics, producers and consumers, and injects tiles into Host and Process Group Instance pages. Since 2.0.0, producer and consumer entities derive from the PGI plus host. Source: https://www.dynatrace.com/hub/detail/apache-kafka/
- **DOCUMENTED.** Each classic entity type has a `dt.entity.*` view in Grail, and Dynatrace recommends Smartscape on Grail going forward. Source: https://docs.dynatrace.com/docs/semantic-dictionary/model/dt-entities (search snippet)

### Inferences
- **INFERRED.** These are standard Dynatrace ID formats (16 hex characters after the type prefix):
  - `HOST-0123456789ABCDEF`
  - `PROCESS_GROUP-…`
  - `PROCESS_GROUP_INSTANCE-…`
  - `SERVICE-…`
  - `QUEUE-…`
  - `CONTAINER_GROUP_INSTANCE-…`
  - `CLOUD_APPLICATION-…` (Kubernetes workload)
  - `CLOUD_APPLICATION_NAMESPACE-…`
  - `KUBERNETES_CLUSTER-…`
- **INFERRED.** Generic extension entities use an uppercase form of the type name as the ID prefix (for example `KAFKA:BROKER-…` style). I could not verify the exact type string. Treat it as unknown.

### Gaps
- The exact extension entity type IDs are unverified (see Q2 Gaps).

---

## Q7. Alert thresholds: healthy vs unhealthy

### Takeaway
Apache Kafka's own docs give clear "normal values". Dynatrace ships alerts for the same signals but does not publish their thresholds.

### Cited Findings (all DOCUMENTED from https://kafka.apache.org/41/operations/monitoring/)

| Signal | Normal value or guidance from Kafka docs |
|---|---|
| UnderReplicatedPartitions | "0" |
| UnderMinIsrPartitionCount | "0" |
| ActiveControllerCount | "only one broker in the cluster should have 1" |
| UncleanLeaderElectionsPerSec | "0" |
| IsrShrinksPerSec / IsrExpandsPerSec | "the expected value for both ISR shrink rate and expansion rate is 0", except around a broker going down or coming back up |
| RequestHandlerAvgIdlePercent | "between 0 and 1, ideally > 0.3" |
| NetworkProcessorAvgIdlePercent | "between 0 and 1, ideally > 0.3" |
| LeaderElectionRateAndTimeMs | "non-zero when there are broker failures" |
| PurgatorySize (Produce) | "non-zero if ack=-1 is used" |
| PurgatorySize (Fetch) | "size depends on fetch.wait.max.ms in the consumer" |
| MaxLag (replica) | "lag should be proportional to the maximum batch size of a produce request" |
| TotalTimeMs | Broken into queue, local, remote and response-send time |

- **DOCUMENTED.** Dynatrace describes UncleanLeaderElections as "should be zero" and LeaderElectionRate as "non-zero after broker failures". Source: https://docs.dynatrace.com/docs/ingest-from/extensions/supported-extensions/apache-kafka

### Inferences (INFERRED thresholds for eval cases)
These are standard ops practice and are not verified in a Dynatrace source.

| Condition | Healthy | Unhealthy |
|---|---|---|
| OfflinePartitionsCount | 0 | > 0 (critical) |
| ActiveControllerCount, summed per cluster | == 1 | ≠ 1 |
| Log dir disk usage | — | warn > 80%, critical > 85–90% |
| JVM heap used / max | — | > 85% sustained |
| GC suspension time | — | > 10–20% |
| Consumer lag | Flat | Growing for > 10–15 min |
| Request handler idle | ~0.6–0.9 | < 0.3 (saturation) |
| Failed produce/fetch rate | 0 | > 0 |

- **INFERRED.** Example values for a healthy 3-broker cluster:
  - BytesInPerSec: 1–50 MB/s per broker
  - MessagesInPerSec: 1k–100k/s
  - TotalTimeMs Produce: p99 of 5–50 ms
  - RequestQueueSize: 0 to single digits
- **INFERRED.** Typical incident pattern: a broker process dies, so `builtin:pgi.availability` drops. Then UnderReplicatedPartitions rises on the surviving brokers (for example 0 → 120) and LeaderElectionRate spikes. OfflinePartitionsCount goes above 0 if RF=1 topics existed. Consumer lag grows on the affected topics.

### Gaps
- Kafka docs give no numeric normal values for OfflinePartitionsCount, RequestQueueSize or records-lag-max.
- I found no Dynatrace page listing the default thresholds of the shipped Kafka alerts.

---

## Q8. Typical DQL and metric selector queries

### Takeaway
The DQL `timeseries` syntax is documented. The Kafka-specific queries below are INFERRED compositions of documented keys and documented syntax.

### Cited Findings
- **DOCUMENTED.** General DQL syntax: `timeseries [column =] aggregation(metricKey [, filter:] ...) ... [, by:] [, filter:]`. Source: https://docs.dynatrace.com/docs/platform/grail/dynatrace-query-language/commands/metric-commands

### Inferences (INFERRED queries; keys are DOCUMENTED above)
- Classic metric selector, under-replicated partitions per broker:
  `builtin:tech.kafka.pg.kafka.server.ReplicaManager.UnderReplicatedPartitions.Value:splitBy("dt.entity.process_group_instance"):max`
- Classic metric selector, controllers per cluster:
  `builtin:tech.kafka.pg.kafka.controller.KafkaController.ActiveControllerCount.Value:splitBy("dt.entity.process_group"):sum`
- DQL, extension, under-replicated partitions:
  `timeseries urp = max(kafka.server.ReplicaManager.UnderReplicatedPartitions), by:{dt.entity.process_group_instance}`
- DQL, extension, offline partitions:
  `timeseries off = max(kafka.controller.KafkaController.OfflinePartitionsCount) | filter arrayMax(off) > 0`
- DQL, extension, consumer lag:
  `timeseries lag = max(kafka.consumer.consumer-metrics.records-lag-max), by:{topic, ` + "`client-id`" + `}`
- DQL, extension, request handler idle:
  `timeseries idle = avg(kafka.server.handler.average-idle-percent.rate), by:{dt.entity.process_group_instance} | filter arrayAvg(idle) < 0.3`

### Gaps
- Dimension field names in Grail (for example whether `client-id` keeps its hyphen, or `dt.entity.process_group_instance` versus a Smartscape node ID after 3.1.0's OpenPipeline change) are unverified.
