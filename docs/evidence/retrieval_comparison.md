# With and without retrieval on the same test set (FR-75)

Generated 2026-10-15 13:37 UTC from saved results in `data/evaluation/`. Reference: run A.

**Test set (6 cases, identical in every run):** GC-SC01-REPLAY, GC-SC01-FREETEXT, GC-SC02-REPLAY, GC-SC02-FREETEXT, GC-SC04-REPLAY, GC-SC04-FREETEXT

## Configurations

| Run | Label | Prompt | Retrieval | Judge | Eval ID | Total cost |
|---|---|---|---|---|---|---|
| A | v1 baseline | Active prompts: v1 | on | on | EVAL-20261015T132212386 | $0.0248 |
| B | v1, no retrieval | Active prompts: v1 | off | on | EVAL-20261015T133336971 | $0.0235 |

## Metrics

| Metric | Target | A | B |
|---|---|---|---|
| Root cause correct (top 1) | ≥ 0.6 | 0.50 | 0.50 |
| Root cause in top 3 | ≥ 0.8 | 0.50 | 0.50 |
| Red herrings rejected |  | 0.83 | 0.67 (-0.17 worse) |
| Causal change found | ≥ 0.8 | 0.00 | 0.00 |
| Issue class correct | ≥ 0.8 | 0.83 | 1.00 (+0.17 better) |
| Severity correct | ≥ 0.7 | 1.00 | 1.00 |
| Abstained when it should | ≥ 0.8 | 1.00 | 1.00 |
| Citations valid | ≥ 0.95 | 1.00 | 1.00 |
| Right runbook retrieved (top 5) | ≥ 0.85 | 1.00 | 0.00 (-1.00 worse) |
| Faithfulness (judge) | ≥ 0.8 | 0.92 | 0.87 (-0.05 worse) |
| Hallucination rate (judge) | ≤ 0.1 | 0.17 | 0.17 |
| Dispatch routed correctly | ≥ 1.0 | 1.00 | 1.00 |
| Latency per case (s) |  | 60.1 | 70.3 (+10.2 worse) |
| Cost per case (USD) |  | $0.0041 | $0.0039 (-0.0002 better) |

## Prompt → Output → What improved or worsened, per case

### GC-SC01-REPLAY

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S1 · class release_regression · top cause (0.90): DB_TIMEOUT errors in payments-service due to slow queries or database issues. · no change blamed | reference |
| B | PENDING_REVIEW · severity S1 · class release_regression · top cause (0.90): The payments-service is experiencing high error rates due to database timeouts when processing payment requests, leading to failed transactions and customer com… · no change blamed | improved: root cause correct (top 1), root cause in top 3; worsened: right runbook retrieved (top 5), faithfulness (judge) |

### GC-SC01-FREETEXT

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S1 · class database_capacity · top cause (0.90): Database timeouts due to high load on core-banking-db causing payment failures. · no change blamed | reference |
| B | PENDING_REVIEW · severity S1 · class release_regression · top cause (0.90): Database timeouts due to high load or inefficient queries in the core-banking-db. · no change blamed | improved: issue class correct; worsened: right runbook retrieved (top 5), faithfulness (judge) |

### GC-SC02-REPLAY

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S2 · class eventhub_broker · top cause (0.90): Disk I/O error on kafka-broker-2 caused the broker to shut down, leading to under-replicated partitions and consumer lag in downstream services. · changes blamed: DEP-0012 (red herring) | reference |
| B | PENDING_REVIEW · severity S2 · class eventhub_broker · top cause (0.90): Kafka broker downtime caused by Broker 2 being unreachable, leading to under-replication and consumer lag. · changes blamed: DEP-0012 (red herring) | worsened: root cause correct (top 1), root cause in top 3, right runbook retrieved (top 5) |

### GC-SC02-FREETEXT

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S2 · class eventhub_broker · top cause (0.90): A broker down event caused significant consumer lag and under-replicated partitions in the EventHub platform. · no change blamed | reference |
| B | PENDING_REVIEW · severity S2 · class eventhub_broker · top cause (0.90): Kafka broker down event caused significant consumer lag and errors in downstream services. · changes blamed: DEP-0012 (red herring) | improved: faithfulness (judge); worsened: red herrings rejected, right runbook retrieved (top 5), hallucination rate (judge) |

### GC-SC04-REPLAY

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S1 · class network_tls · top cause (1.00): Expired TLS certificate for the card payment route. · changes blamed: DEP-0031 | reference |
| B | PENDING_REVIEW · severity S1 · class network_tls · top cause (1.00): Expired TLS certificate for card route causing handshake failures and subsequent errors in payment processing. · no change blamed | worsened: right runbook retrieved (top 5), faithfulness (judge) |

### GC-SC04-FREETEXT

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S1 · class network_tls · top cause (1.00): Expired TLS certificate for the card payment route causing handshake failures and transaction errors. · no change blamed | reference |
| B | PENDING_REVIEW · severity S1 · class network_tls · top cause (1.00): The TLS handshake failures were caused by an expired certificate on the payment-network-gateway when connecting to the external switch. · changes blamed: DEP-0031 | improved: hallucination rate (judge); worsened: right runbook retrieved (top 5), faithfulness (judge) |

## Summary against A

- **B (v1, no retrieval)**: improved Issue class correct; worsened Red herrings rejected, Right runbook retrieved (top 5), Faithfulness (judge).

## Insights and choice of default

_To be written after reviewing the outputs above: what changed in the answers, new failure modes, and which configuration becomes the default and why._
