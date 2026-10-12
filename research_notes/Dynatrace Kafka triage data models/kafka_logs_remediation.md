# Apache Kafka broker/client log lines for production incidents and standard remediation (Kafka 3.x)

Conventions used below:
- **VERBATIM-SRC** = string template copied from Apache Kafka source on GitHub (branch `3.7`), placeholders (`{}`, `$var`) kept as in source. Level is the log level in code.
- **VERBATIM-LOG** = copied from a real pasted log (JIRA etc.), including timestamp/class.
- **PARAPHRASED / UNVERIFIED** = from memory or reconstructed; not confirmed against a source during this research. Fixtures should treat these as "near-verbatim, unconfirmed".
- Rendering: the Kafka log4j default layout is `[%d] %p %m (%c)%n`, which yields lines like `[2020-02-06 13:32:49,058] INFO Stopping serving logs in dir /data/... (kafka.log.LogManager)` — confirmed by real pasted logs in KAFKA-9604 (see below). The `logIdent` prefix (e.g. `[Partition t-0 broker=1] `) is prepended to the message.

---

## Q1. Broker-side log lines (ISR, URP, leader/controller, ZK/KRaft, disk/log-dir, fetcher, auth, shutdown)

### Takeaway
Exact broker strings for ISR shrink, ISR update, log-dir failure, replica-fetcher errors, KRaft controller leadership change, and auth failures were confirmed from 3.7 source or real JIRA logs. There is **no literal "Expanding ISR from" string in 3.x** (ISR expansion is logged as `ISR updated to ...`). Lines for OOM, "Too many open files", log-cleaner death, corrupted index, and `[KafkaServer id=1] started` were not verified this session.

### Cited Findings

**Partition / ISR (`kafka.cluster.Partition`)**
- logIdent: `[Partition $topicPartition broker=$localBrokerId] ` — VERBATIM-SRC — [Partition.scala 3.7](https://raw.githubusercontent.com/apache/kafka/3.7/core/src/main/scala/kafka/cluster/Partition.scala)
- INFO `Shrinking ISR from ${partitionState.isr.mkString(",")} to $newIsrLog. ` + `Leader: (highWatermark: ...` + `Out of sync replicas: $outOfSyncReplicaLog.` — VERBATIM-SRC (concatenated pieces) — [Partition.scala](https://raw.githubusercontent.com/apache/kafka/3.7/core/src/main/scala/kafka/cluster/Partition.scala)
  - Rendered fixture example (PARAPHRASED assembly of the pieces above; the inner HW/LEO detail is reconstructed): `[2024-05-01 10:00:00,123] INFO [Partition orders-0 broker=1] Shrinking ISR from 1,2,3 to 1,3. Leader: (highWatermark: 18234, endOffset: 18240). Out of sync replicas: (brokerId: 2, endOffset: 18100, lastCaughtUpTimeMs: 1714557570000). (kafka.cluster.Partition)`
- INFO `ISR updated to ${partitionState.isr.mkString(",")} ${if (isUnderMinIsr) "(under-min-isr)" else ""} ` — this is the 3.x success message for both shrink and expand (no "Expanding ISR from" literal exists in 3.7) — VERBATIM-SRC — [Partition.scala](https://raw.githubusercontent.com/apache/kafka/3.7/core/src/main/scala/kafka/cluster/Partition.scala)
- WARN `Failed to update ISR to $proposedIsrState due to unexpected $error. Retrying.` — VERBATIM-SRC — same
- INFO `Failed to alter partition to $proposedIsrState since the controller rejected the request with $error. ` — VERBATIM-SRC — same
- stateChangeLogger INFO `Leader $topicPartition with topic id $topicId starts at ...` / `Follower $topicPartition starts at leader epoch ${partitionState.leaderEpoch} from ...` — VERBATIM-SRC (prefixes) — same
- INFO `The topic partition $topicPartition was marked as RECOVERING. ` / `Marking the topic partition as RECOVERED.` (KIP-704 leader recovery after unclean election) — VERBATIM-SRC — same
- ISR shrink scheduling: `scheduler.schedule("isr-expiration", () => maybeShrinkIsr(), 0L, config.replicaLagTimeMaxMs / 2)`; source comment: "A follower can lag behind leader for up to config.replicaLagTimeMaxMs x 1.5 before it is removed from ISR" — VERBATIM-SRC — [ReplicaManager.scala 3.7](https://raw.githubusercontent.com/apache/kafka/3.7/core/src/main/scala/kafka/server/ReplicaManager.scala)

**ReplicaManager (`kafka.server.ReplicaManager`)**
- logIdent `[ReplicaManager broker=$localBrokerId] ` — VERBATIM-SRC — [ReplicaManager.scala](https://raw.githubusercontent.com/apache/kafka/3.7/core/src/main/scala/kafka/server/ReplicaManager.scala)
- FATAL `Halting broker because dir $newOfflineLogDir is offline` — VERBATIM-SRC — same
- ERROR `Error processing append operation on partition $topicPartition` — VERBATIM-SRC — same
- ERROR `Error processing fetch with max size $adjustedMaxBytes from $fetchSource on partition $tp: $fetchInfo` — VERBATIM-SRC — same
- NotLeaderOrFollower in the fetch path is mapped to `Errors.NOT_LEADER_OR_FOLLOWER` **without logging** on the broker — VERBATIM-SRC observation — same

**Log directory failure / disk (real pasted log, Kafka 2.2.1 but same strings/classes persist in 3.x for ZK mode)** — VERBATIM-LOG — [KAFKA-9604](https://issues.apache.org/jira/browse/KAFKA-9604)
```
[2020-02-06 13:32:48,979] ERROR Error while deleting segments for ocs.account-balances-12 in dir /data/ocswf/kafka_broker/kafka-data (kafka.server.LogDirFailureChannel)
[2020-02-06 13:32:48,982] INFO [ReplicaManager broker=3] Stopping serving replicas in dir /data/ocswf/kafka_broker/kafka-data (kafka.server.ReplicaManager)
org.apache.kafka.common.errors.KafkaStorageException: Error while deleting segments for ocs.account-balances-12
Caused by: java.nio.file.NoSuchFileException: /data/ocswf/kafka_broker/kafka-data/ocs.account-balances-12/00000000000007607076.log
[2020-02-06 13:32:49,058] INFO Stopping serving logs in dir /data/ocswf/kafka_broker/kafka-data (kafka.log.LogManager)
[2020-02-06 13:32:49,078] ERROR Shutdown broker because all log dirs in /data/ocswf/kafka_broker/kafka-data have failed (kafka.log.LogManager)
[2020-02-06 13:32:53,620] ERROR [ReplicaManager broker=1] Error while making broker the leader for partition ... (kafka.server.ReplicaManager)
org.apache.kafka.common.errors.KafkaStorageException: Error while writing to checkpoint file ...
```
(The JIRA rendering split each line from its class; reassembled here. The "Stopping serving replicas in dir" line continues with the dir path in practice — inferred.)
- A full log dir throws IOException "No space left on device", marking the dir failed and all its partitions offline; if all dirs fail, the broker shuts down — [KIP-928](https://cwiki.apache.org/confluence/spaces/KAFKA/pages/255070128/KIP-928+Making+Kafka+resilient+to+log+directories+becoming+full); [Netdata guide](https://www.netdata.cloud/guides/kafka/kafka-broker-out-of-disk/)

**Replica fetcher (`kafka.server.ReplicaFetcherThread`, defined in AbstractFetcherThread)** — [AbstractFetcherThread.scala 3.7](https://raw.githubusercontent.com/apache/kafka/3.7/core/src/main/scala/kafka/server/AbstractFetcherThread.scala)
- WARN `Error in response for fetch request $fetchRequest` (with exception) — VERBATIM-SRC
- WARN `Partition $topicPartition marked as failed` — VERBATIM-SRC
- ERROR `Error for partition $topicPartition at offset ${currentFetchState.fetchOffset}` — VERBATIM-SRC
- DEBUG `Remote broker is not the leader for partition $topicPartition, which could indicate ...` — VERBATIM-SRC (note: DEBUG, so not normally visible)
- INFO `Current offset ${fetchState.fetchOffset} for partition $topicPartition is out of range, which typically implies a leader change. Reset fetch offset to ...` — VERBATIM-SRC
- INFO `Truncating partition $tp with $truncationState due to local high watermark $highWatermark` / `... due to leader epoch and offset $leaderEpochOffset` — VERBATIM-SRC
- The logIdent `[ReplicaFetcher replicaId=%d, leaderId=%d, fetcherId=%d] ` and thread name `ReplicaFetcherThread-0-1` are NOT defined in this file — PARAPHRASED/UNVERIFIED (widely seen form; not confirmed this session).

**KRaft controller (`org.apache.kafka.controller.QuorumController`)** — [QuorumController.java 3.7](https://raw.githubusercontent.com/apache/kafka/3.7/metadata/src/main/java/org/apache/kafka/controller/QuorumController.java)
- LogContext prefix `[QuorumController id=%d] `; thread prefix `quorum-controller-%d-` — VERBATIM-SRC
- INFO `Becoming the active controller at epoch {}, next write offset {}.` — VERBATIM-SRC
- INFO `In the new epoch {}, the leader is {}.` — VERBATIM-SRC
- WARN `Renouncing the leadership due to a metadata log event.` — VERBATIM-SRC
- WARN `We were the leader at epoch {}, but in the new epoch {}, the leader is {}. Reverting to last stable offset {}.` — VERBATIM-SRC (this is the KRaft "lost leadership" signal; no literal "Lost leadership" string was found)
- ERROR `Unexpected exception while executing deferred write event {}. Rescheduling for a minute from now.` — VERBATIM-SRC
- Fault-handler texts: `Unable to apply %s record at offset %d on active controller, from the batch with baseOffset %d`; `Error while loading snapshot ...` — VERBATIM-SRC
- ZK→KRaft migration: INFO `Starting migration of ZooKeeper metadata to KRaft.` — VERBATIM-SRC
- Real fatal fault during renounce (stack through `QuorumController.renounce`, process-terminating fault handler; cause: no in-memory snapshot for last stable offset) — [KAFKA-17608](https://issues.apache.org/jira/browse/KAFKA-17608)

**ZooKeeper session expiry (ZK mode only; removed in 4.0)**
- Broker logs "Session expired" from `kafka.zookeeper.ZooKeeperClient` after the ZK connection fails to recover; re-registration of the broker znode can fail with NODEEXISTS when the old ephemeral node survives (ZOOKEEPER-2985), worked around in Kafka by deleting/recreating a znode owned by the old session — [KAFKA-7165](https://issues.apache.org/jira/browse/KAFKA-7165); [PR #5575](https://github.com/apache/kafka/pull/5575); [KAFKA-4277](https://issues.apache.org/jira/browse/KAFKA-4277)
- Exact 3.x ZK line text not confirmed — see Gaps.

**SSL / SASL authentication (broker side, `org.apache.kafka.common.network.Selector`)**
- Broker logs `Failed authentication with /10.x.x.x (SSL handshake failed)` at INFO from `org.apache.kafka.common.network.Selector`; Yandex attributes it to client-side protocol/cert/secret misconfiguration — [Yandex Cloud](https://docs.yandex.cloud/int/en/troubleshooting/managed-kafka/known-issues/error-failed-authentification.md); [IBM Support](https://www.ibm.com/support/pages/kafka-pod-errors-failed-authentication-ipaddress)
- Cause examples: certificate SAN not covering IP (Broadcom) — [Broadcom KB](https://knowledge.broadcom.com/external/article/441132/spectrum-alarms-not-appearing-on-netops.html); bad cert exchange between servers — [Avaya](https://documentation.avaya.com/en-us/home/bundle/proactive-outreach-manager/TroubleshootingAvayaProactiveOutreachManager_r41/troubleshooting-eventsdkkafkaserver/SSL_handshake_failed_Exception.html); unresolved case where broker's own IP appears (health checks/LB probes) — [Confluent forum](https://forum.confluent.io/t/diagnose-failed-authentication/3728)
- AWS MSK IAM variants: `Failed authentication ... Too many connects` (broker self-protecting; raise `reconnect.backoff.ms`) and `Failed authentication ... Session too short` (IAM creds near expiry) — [AWS MSK troubleshooting](https://docs.aws.amazon.com/msk/latest/developerguide/troubleshooting.html)

### Inferences
- For fixtures, a "transient ISR shrink during rolling restart" should show `Shrinking ISR from 1,2,3 to 1,3` followed within ~1 min by `ISR updated to 1,2,3` on the same partition; a persistent shrink without the re-expand indicates a real fault.
- `(under-min-isr)` suffix on `ISR updated to` is the broker-side marker that acks=all producers will start receiving NotEnoughReplicas.
- KRaft fixtures should use `[QuorumController id=3000]`-style prefixes and the `Becoming the active controller at epoch ...` / `In the new epoch ..., the leader is ...` pair for failover; ZK fixtures use `[Controller id=1]` (unverified prefix).

### Gaps
- Not verified this session (use as PARAPHRASED only): `[KafkaServer id=1] started (kafka.server.KafkaServer)` (ZK) and `[KafkaRaftServer nodeId=1] Kafka Server started` / `[BrokerServer id=1] Transition from STARTING to STARTED` (KRaft); `[KafkaServer id=1] shutting down` / `Starting controlled shutdown`; `[Controller id=1] ...` ZK controller lines and `Resigning`; `Uncaught exception in thread 'kafka-log-cleaner-thread-0':` (kafka.log.LogCleaner); `Found a corrupted index file ... deleting and rebuilding index` (kafka.log.LogLoader); `java.lang.OutOfMemoryError: Java heap space` / `Direct buffer memory`; `java.io.IOException: Too many open files` from `kafka.network.Acceptor` ("Error while accepting connection"); unclean-election warning text from the controller; KRaft `RaftClient`/`KafkaRaftClient` fetch-timeout messages (e.g., "Become candidate due to fetch timeout"); SASL failure text (e.g., `Failed authentication with /x.x.x.x (Authentication failed: Invalid username or password)`).
- No real pasted 3.x KRaft log excerpt for fetch-timeout/quorum loss was found.
- JSON log formats for Strimzi / CFK / Bitnami were not researched in the fetched sources (Strimzi uses log4j text by default; JSON requires a custom layout — unverified).

---

## Q2. Client-side lines (consumer, producer, Streams, Connect)

### Takeaway
Consumer group, producer timeout, NetworkClient connectivity, and Connect task-failure strings were confirmed from 3.7 source. Note an important wording change: 3.7's NetworkClient says "**Node** may not be available", not "Broker may not be available" (older versions/StackOverflow posts show "Broker").

### Cited Findings

**Consumer group (`org.apache.kafka.clients.consumer.internals.AbstractCoordinator` / `ConsumerCoordinator`)** — [AbstractCoordinator.java 3.7](https://raw.githubusercontent.com/apache/kafka/3.7/clients/src/main/java/org/apache/kafka/clients/consumer/internals/AbstractCoordinator.java)
- INFO `Attempt to heartbeat failed since coordinator {} is either not started or not valid` — VERBATIM-SRC
- INFO `Attempt to heartbeat with {} and group instance id {} failed due to {}, resetting generation` — VERBATIM-SRC
- WARN (poll timeout, VERBATIM-SRC, concatenated): `consumer poll timeout has expired. This means the time between subsequent calls to poll() was longer than the configured max.poll.interval.ms, which typically implies that the poll loop is spending too much time processing messages. You can address this either by increasing max.poll.interval.ms or by reducing the maximum size of batches returned in poll() with max.poll.records.`
- INFO `Member {} sending LeaveGroup request to coordinator {} due to {}` — VERBATIM-SRC (reason is e.g. "consumer poll timeout has expired." — inference)
- INFO `(Re-)joining group`; `Successfully joined group with generation {}`; `Successfully synced group in generation {}`; `Request joining group due to: {}`; `Resetting generation {}due to: {}` — VERBATIM-SRC
- INFO `SyncGroup failed: The group began another rebalance. Need to re-join the group. ` — VERBATIM-SRC
- INFO `JoinGroup failed due to non-fatal error: REBALANCE_IN_PROGRESS, which could indicate a replication timeout on the broker. Will retry.` — VERBATIM-SRC
- INFO `Discovered group coordinator {}`; `Group coordinator {} is unavailable or invalid due to cause: {}. isDisconnected: {}. Rediscovery will be attempted.` — VERBATIM-SRC
- CommitFailedException default message begins: `Commit cannot be completed since the group has already rebalanced and assigned the partitions to another member.` and continues to explain poll() gap exceeded max.poll.interval.ms and suggests raising it or lowering max.poll.records — VERBATIM-SRC (first sentence) — [CommitFailedException.java](https://raw.githubusercontent.com/apache/kafka/3.7/clients/src/main/java/org/apache/kafka/clients/consumer/CommitFailedException.java)

**Producer (`org.apache.kafka.clients.producer.internals.Sender`)** — [Sender.java 3.7](https://raw.githubusercontent.com/apache/kafka/3.7/clients/src/main/java/org/apache/kafka/clients/producer/internals/Sender.java)
- TimeoutException message: `"Expiring " + recordCount + " record(s) for " + topicPartition + ":" + (now - createdMs) + " ms has passed since batch creation"` → renders `Expiring 12 record(s) for orders-0:120001 ms has passed since batch creation` — VERBATIM-SRC (exception message, not itself logged by Sender; surfaces via callback/app logs). Note: in 3.x the threshold is `delivery.timeout.ms` (default 120000), so "30001 ms" examples are from older (pre-2.1) clients — inference from `hasReachedDeliveryTimeout` in [ProducerBatch.java](https://raw.githubusercontent.com/apache/kafka/3.7/clients/src/main/java/org/apache/kafka/clients/producer/internals/ProducerBatch.java)
- WARN `Got error produce response with correlation id {} on topic-partition {}, retrying ({} attempts left)` — VERBATIM-SRC (followed by `. Error: NOT_ENOUGH_REPLICAS` etc. — inferred tail)
- WARN `Got error produce response in correlation id {} on topic-partition {}, splitting and retrying` (MESSAGE_TOO_LARGE split path) — VERBATIM-SRC
- WARN `Received invalid metadata error in produce request on partition {} due to {}. Going to request metadata update now` — VERBATIM-SRC (typical with NOT_LEADER_OR_FOLLOWER during leader moves)
- ERROR `Uncaught error in kafka producer I/O thread` — VERBATIM-SRC

**NetworkClient (`org.apache.kafka.clients.NetworkClient`)** — [NetworkClient.java 3.7](https://raw.githubusercontent.com/apache/kafka/3.7/clients/src/main/java/org/apache/kafka/clients/NetworkClient.java)
- WARN `Connection to node {} ({}) could not be established. Node may not be available.` — VERBATIM-SRC (3.7). The commonly-quoted "Broker may not be available." is the older wording — conflict noted.
- WARN `Bootstrap broker {} disconnected` — VERBATIM-SRC (no "(id: rack:)" suffix in 3.7's literal; the `{}` node toString may include it)
- INFO `Disconnecting from node {} due to request timeout.`; INFO `Node {} disconnected.` — VERBATIM-SRC
- ERROR `Connection to node {} ({}) failed authentication due to: {}`; WARN `Connection to node {} ({}) terminated during authentication. ...` — VERBATIM-SRC
- Client log prefix such as `[Producer clientId=producer-1] ` / `[Consumer clientId=..., groupId=...] ` — PARAPHRASED/UNVERIFIED (standard LogContext form, not fetched).

**Kafka Connect (`org.apache.kafka.connect.runtime.WorkerTask`)** — [WorkerTask.java 3.7](https://raw.githubusercontent.com/apache/kafka/3.7/connect/runtime/src/main/java/org/apache/kafka/connect/runtime/WorkerTask.java)
- ERROR `{} Task threw an uncaught and unrecoverable exception. Task is being killed and will not recover until manually restarted` — VERBATIM-SRC (first `{}` is the task, e.g. `WorkerSinkTask{id=my-connector-0}` — format UNVERIFIED)
- ERROR `Task threw an uncaught and unrecoverable exception during shutdown` — VERBATIM-SRC
- Thread name `"task-thread-" + id` — VERBATIM-SRC

### Inferences
- A single `Task threw an uncaught and unrecoverable exception` is the canonical Connect case where restarting the **task** (REST `POST /connectors/<name>/tasks/<id>/restart`) — not the container — is the standard first action, because the message itself states it won't recover until manually restarted; repeated failure after restart → ticket (usually data/converter/config error).
- Poll-timeout WARN + LeaveGroup + CommitFailedException is the signature of a slow/stuck consumer processing loop; a restart clears it only temporarily if the processing is genuinely slow.

### Gaps
- Not verified: `Topic X not present in metadata after 60000 ms.`; `RecordTooLargeException: The message is N bytes when serialized which is larger than ...`; `NotEnoughReplicasException: Messages are rejected since there are fewer in-sync replicas than required.`; `Fetch position FetchPosition{...} is out of range for partition ..., resetting offset`; Kafka Streams `TaskMigratedException`, `StreamsException: ... task [0_1] ...`, `State store ... is not available / LockException`, and `stream-thread [..] State transition from RUNNING to PENDING_ERROR`. Treat these as PARAPHRASED.

---

## Q3. Log format (log4j and Kubernetes JSON variants)

### Takeaway
Real broker logs confirm the `[yyyy-MM-dd HH:mm:ss,SSS] LEVEL message (logger.class)` layout; JSON variants were not confirmed.

### Cited Findings
- Real lines: `[2020-02-06 13:32:49,058] INFO Stopping serving logs in dir /data/ocswf/kafka_broker/kafka-data (kafka.log.LogManager)` — VERBATIM-LOG — [KAFKA-9604](https://issues.apache.org/jira/browse/KAFKA-9604)
- Broker logIdents: `[Partition $tp broker=$id] `, `[ReplicaManager broker=$id] `, `[QuorumController id=%d] ` — VERBATIM-SRC — [Partition.scala](https://raw.githubusercontent.com/apache/kafka/3.7/core/src/main/scala/kafka/cluster/Partition.scala); [ReplicaManager.scala](https://raw.githubusercontent.com/apache/kafka/3.7/core/src/main/scala/kafka/server/ReplicaManager.scala); [QuorumController.java](https://raw.githubusercontent.com/apache/kafka/3.7/metadata/src/main/java/org/apache/kafka/controller/QuorumController.java)
- State-change lines go through `stateChangeLogger` (separate `state.change.logger`/`state-change.log` in default config — inference; file name unverified).

### Inferences
- Java-client (app-side) logs use whatever the app's logback/log4j2 layout is; logger names are the fully-qualified class (e.g. `org.apache.kafka.clients.consumer.internals.ConsumerCoordinator`).
- A JSON fixture can reasonably map to `{"@timestamp":..., "level":"INFO", "logger":"kafka.cluster.Partition", "message":"[Partition orders-0 broker=1] Shrinking ISR from ..."}` — PARAPHRASED shape, not sourced.

### Gaps
- Strimzi, Confluent for Kubernetes, and Bitnami default layouts/JSON options not verified this session.

---

## Q4. Root causes and operationally correct action (restart / escalate / ticket / ignore)

### Takeaway
Sources agree: URP during a controlled rolling restart is expected noise; persistent URP and offline partitions need investigation not blind restarts; disk-full needs space freed (never delete active segments while running) before restart; dead log-cleaner thread is fixed only by broker restart; Connect tasks need explicit task restart; stuck consumer groups can be fixed by static membership or restarting the coordinator (MSK).

### Cited Findings
- Rolling restart: use `controlled.shutdown.enable=true`; "Do not send a `kill -9` command."; "The active controller should be the last broker you restart."; URP "increases because data will not be replicated" during restart and returns to 0 after catch-up; "Wait until that broker completely restarts and is caught up before proceeding to restart the next broker"; "If there are under replicated partitions, investigate why before doing a rolling restart." — [Confluent post-deployment](https://docs.confluent.io/platform/current/kafka/post-deployment.html)
- "The Kafka cluster will automatically detect any broker shutdown or failure and elect new leaders for the partitions on that machine." — [Confluent post-deployment](https://docs.confluent.io/platform/current/kafka/post-deployment.html)
- MSK: URP spiky → cluster undersized; URP consistently >0 even at low traffic → possibly restrictive ACLs preventing replication (brokers need READ/DESCRIBE); offline partitions / out-of-sync replicas can be symptoms of low disk — [AWS MSK troubleshooting](https://docs.aws.amazon.com/msk/latest/developerguide/troubleshooting.html)
- MSK: `NotLeaderForPartitionException` "is often a transient error" — raise producer `retries` — [AWS MSK](https://docs.aws.amazon.com/msk/latest/developerguide/troubleshooting.html)
- MSK: high `MemoryUsed`/low `MemoryFree` "doesn't mean there's a problem" (page cache) — [AWS MSK](https://docs.aws.amazon.com/msk/latest/developerguide/troubleshooting.html)
- MSK: consumer group stuck in `PreparingRebalance` → static membership (`group.instance.id`) or identify and reboot the group coordinator broker — [AWS MSK](https://docs.aws.amazon.com/msk/latest/developerguide/troubleshooting.html)
- MSK: volume replacement can saturate disk throughput during re-replication; lower `num.replica.fetchers`, provision throughput — [AWS MSK](https://docs.aws.amazon.com/msk/latest/developerguide/troubleshooting.html)
- Disk: one mount can be 100% while others are 50%; ticket at >75% disk, page at >90% or <4h runway; page if `OfflineLogDirectoryCount` > 0; reduce retention via `kafka-configs.sh` (no restart); dead log cleaner → restart broker (no way to restart cleaner thread alone); all log.dirs failed → free space/expand volume then restart, expect long log recovery; one dir full → reassign partitions, don't hand-delete segments; avoid restarting mid-reassignment — [Netdata](https://www.netdata.cloud/guides/kafka/kafka-broker-out-of-disk/)
- Stop the broker before deleting segment files; never delete the active segment; deleting while running causes corruption/crashes — [Conduktor](https://www.conduktor.io/blog/disk-full-emergency-recovery)
- KAFKA-9604: one broker's disk/segment issue cascaded: other brokers hit `KafkaStorageException: Error while writing to checkpoint file` during leader election and also shut down — evidence that disk incidents can go cluster-wide — [KAFKA-9604](https://issues.apache.org/jira/browse/KAFKA-9604)
- SSL handshake failures: client-side cause (protocol/cert/secret); check application on the source IP — [Yandex Cloud](https://docs.yandex.cloud/int/en/troubleshooting/managed-kafka/known-issues/error-failed-authentification.md)
- KRaft: controller quorum leader is the active controller; followers are hot standbys — [Confluent KRaft overview](https://docs.confluent.io/platform/current/zookeeper/kraft.html)
- Connect task failure message states the task "will not recover until manually restarted" — [WorkerTask.java](https://raw.githubusercontent.com/apache/kafka/3.7/connect/runtime/src/main/java/org/apache/kafka/connect/runtime/WorkerTask.java)

### Inferences — proposed label mapping for eval cases (derived from the cited guidance; labels are the app's)
| Incident signature | Suggested label | Rationale |
|---|---|---|
| ISR shrink + re-expand within minutes, coincident `controlled shutdown`/rolling restart, URP returns to 0 | `suppress` | Expected during restart (Confluent) |
| Expected consumer rebalance (`(Re-)joining group`, `Successfully joined group`) during deployment | `suppress` | Normal group protocol |
| Producer `Received invalid metadata error ... NOT_LEADER_OR_FOLLOWER` single burst | `suppress` / `notify_only` | Transient per MSK |
| Single broker down/not restarting, URP > 0, OfflinePartitions = 0, ActiveController = 1 | `restart_container` (the down broker) then verify | Cluster self-heals leadership; bring broker back |
| Persistent URP > 0 outside maintenance, no broker down | `open_ticket` (or escalate if growing) | Capacity/ACL/network cause; restart won't fix (MSK) |
| `OfflinePartitionsCount` > 0, or ActiveControllerCount sum != 1, or KRaft quorum loss | `escalate_human` | Data unavailability; restarts risky |
| Unclean leader election / `marked as RECOVERING` | `escalate_human` | Data-loss risk |
| `No space left on device` / `Stopping serving logs in dir` / `Shutdown broker because all log dirs ... have failed` | `escalate_human` (do NOT restart first) | Must free space first; restart useless; can cascade (KAFKA-9604) |
| Disk trending >75%, not yet failed | `open_ticket` | Netdata thresholds |
| Log cleaner thread dead (`DeadThreadCount` > 0) | `open_ticket` → planned broker restart | Only fix is restart, but not urgent unless disk climbing |
| Consumer poll-timeout WARN + LeaveGroup + CommitFailedException, single instance, lag growing | `restart_container` (consumer) if hung; `open_ticket` if recurring | Slow processing; config fix needed |
| Gradual consumer lag growth, no errors | `open_ticket` | Capacity |
| Connect `Task threw an uncaught and unrecoverable exception` (first occurrence) | `restart_container`/task restart; repeat → `open_ticket` | Message says manual restart needed |
| Client `SSL handshake failed` from one IP | `open_ticket` (client team) ; from all clients after cert rotation → `escalate_human` | Client config/cert issue |
| Broker JVM OOM / hung, single broker, cluster healthy | `restart_container` | Low-risk single broker restart via controlled shutdown (inferred) |
| Lone log line with no metrics context (e.g. one `Node disconnected.`) | `cannot_tell` / `notify_only` | Insufficient evidence |

### Gaps
- Strimzi, Red Hat AMQ Streams, Aiven, Instaclustr, Uber/LinkedIn runbooks were not fetched (tool budget); the mapping above rests on Confluent, AWS MSK, Netdata, Conduktor, and source-code semantics.
- No source fetched explicitly on unclean leader election policy (`unclean.leader.election.enable`) remediation; "escalate" for it is an inference.

---

## Q5. Metrics to pair with each incident

### Takeaway
UnderReplicatedPartitions, OfflinePartitionsCount, OfflineLogDirectoryCount, LogCleaner DeadThreadCount, disk %, and BytesInPerSec are the key fixture metrics confirmed by sources.

### Cited Findings
- `UnderReplicatedPartitions` should be 0 in a healthy cluster; rises during broker restart and returns to original value after catch-up — [Confluent](https://docs.confluent.io/platform/current/kafka/post-deployment.html); [AWS MSK](https://docs.aws.amazon.com/msk/latest/developerguide/troubleshooting.html)
- `kafka.log:type=LogManager,name=OfflineLogDirectoryCount` (page if > 0); `kafka.log:type=LogCleaner,name=DeadThreadCount` (investigate if > 0); log cleaner dirty ratio > 50% investigate; `OfflinePartitionsCount` sustained nonzero investigate; disk % per log.dir with 75%/90% thresholds; `BytesInPerSec` vs steady state — [Netdata](https://www.netdata.cloud/guides/kafka/kafka-broker-out-of-disk/)
- MSK `MemoryUsed`/`MemoryFree` are not alert signals on their own — [AWS MSK](https://docs.aws.amazon.com/msk/latest/developerguide/troubleshooting.html)

### Inferences
- Fixture consistency rules: ISR-shrink logs ⇒ `IsrShrinksPerSec` > 0 and URP ≥ 1; matching `ISR updated to` (expand) ⇒ `IsrExpandsPerSec` > 0 and URP back to 0. Offline-dir logs ⇒ `OfflineLogDirectoryCount` ≥ 1 and usually `OfflinePartitionsCount` > 0 if RF=1 or all replicas affected. KRaft failover logs ⇒ brief period where the per-node `ActiveControllerCount` moves from old to new node (cluster sum stays 1 after election). Consumer poll-timeout/CommitFailed ⇒ consumer lag (`records-lag-max`) rising for that group.

### Gaps
- Exact MBean names for `ActiveControllerCount` (`kafka.controller:type=KafkaController,name=ActiveControllerCount`), `IsrShrinksPerSec` (`kafka.server:type=ReplicaManager,name=IsrShrinksPerSec`), and KRaft-specific metrics (e.g., `kafka.server:type=raft-metrics`) are standard but were not re-verified this session.
