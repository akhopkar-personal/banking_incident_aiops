# ADP-001: v1 vs v2 (RCA guideline) on golden-v3, before and after

Generated 2026-10-19 14:03 UTC from saved results in `data/evaluation/`. Reference: run A.

**Test set (27 cases, identical in every run):** GC-SC01-REPLAY, GC-SC01-FREETEXT, GC-SC01-NARROW, GC-SC01-WIDE, GC-SC01-MISSING, GC-SC02-REPLAY, GC-SC02-FREETEXT, GC-SC02-NARROW, GC-SC02-WIDE, GC-SC02-MISSING, GC-SC03-REPLAY, GC-SC03-FREETEXT, GC-SC03-NARROW, GC-SC03-WIDE, GC-SC03-MISSING, GC-SC04-REPLAY, GC-SC04-FREETEXT, GC-SC04-NARROW, GC-SC04-WIDE, GC-SC04-MISSING, GC-SC05-REPLAY, GC-SC05-FREETEXT, GC-SC05-NARROW, GC-SC05-WIDE, GC-SC05-MISSING, GC-SC05-FB001, GC-SC05-FB002

## Configurations

| Run | Label | Prompt | Retrieval | Judge | Eval ID | Total cost |
|---|---|---|---|---|---|---|
| A | active prompts | Active prompts: v1 | on | on | EVAL-20261019T130818577 | $0.1100 |
| B | v2 | rca_agent v2 | on | on | EVAL-20261019T132325374 | $0.1131 |

## Metrics

| Metric | Target | A | B |
|---|---|---|---|
| Root cause correct (top 1) | ≥ 0.6 | 0.55 | 0.36 (-0.18 worse) |
| Root cause in top 3 | ≥ 0.8 | 0.59 | 0.50 (-0.09 worse) |
| Red herrings rejected |  | 0.89 | 0.89 |
| Causal change found | ≥ 0.8 | 0.25 | 0.25 |
| Issue class correct | ≥ 0.8 | 0.85 | 0.85 |
| Severity correct | ≥ 0.7 | 0.85 | 0.89 (+0.04 better) |
| Abstained when it should | ≥ 0.8 | 0.81 | 0.81 |
| Citations valid | ≥ 0.95 | 1.00 | 1.00 |
| Right runbook retrieved (top 5) | ≥ 0.85 | 0.96 | 0.96 |
| Faithfulness (judge) | ≥ 0.8 | 0.88 | 0.93 (+0.04 better) |
| Hallucination rate (judge) | ≤ 0.1 | 0.07 | 0.11 (+0.04 worse) |
| Dispatch routed correctly | ≥ 1.0 | 0.89 | 0.89 |
| Latency per case (s) |  | 65.2 | 61.6 (-3.6 better) |
| Cost per case (USD) |  | $0.0041 | $0.0042 (+0.0001 worse) |

## Prompt → Output → What improved or worsened, per case

### GC-SC01-REPLAY

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S1 · class release_regression · top cause (0.90): Database timeouts in payments-service due to high load or inefficient queries. · changes blamed: DEP-0007 | reference |
| B | PENDING_REVIEW · severity S1 · class release_regression · top cause (0.90): DB_TIMEOUT errors in payments-service due to high load or inefficient queries. · changes blamed: DEP-0007 | improved: faithfulness (judge) |

### GC-SC01-FREETEXT

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S1 · class database_capacity · top cause (0.90): Database saturation leading to timeouts in payments-service. · no change blamed | reference |
| B | PENDING_REVIEW · severity S1 · class database_capacity · top cause (0.90): Database saturation leading to timeouts in the payments-service. · no change blamed | improved: root cause in top 3; worsened: faithfulness (judge) |

### GC-SC01-NARROW

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S1 · class release_regression · top cause (0.90): Database timeouts due to a regression in the payments-service after a release. · no change blamed | reference |
| B | PENDING_REVIEW · severity S1 · class release_regression · top cause (0.90): Recent release introduced a regression causing high database timeout errors during payment processing. · changes blamed: DEP-0007 | improved: causal change found, faithfulness (judge) |

### GC-SC01-WIDE

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S1 · class release_regression · top cause (0.90): The payments-service is experiencing DB_TIMEOUT errors due to high load or inefficient queries, leading to a high error rate and complaints about fund transfers… · changes blamed: DEP-0007 | reference |
| B | PENDING_REVIEW · severity S1 · class release_regression · top cause (0.90): DB_TIMEOUT errors in payments-service due to query timeouts on core-banking-db, leading to high error rates and customer complaints about fund transfers failing… · changes blamed: DEP-0007 | improved: faithfulness (judge) |

### GC-SC01-MISSING

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S1 · class release_regression · top cause (0.90): Database timeouts in payments-service due to high load or inefficient queries. · no change blamed | reference |
| B | PENDING_REVIEW · severity S1 · class release_regression · top cause (0.90): Recent release introduced a bug causing database timeouts during payment processing, leading to high error rates and customer complaints. · no change blamed | worsened: faithfulness (judge) |

### GC-SC02-REPLAY

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S2 · class eventhub_broker · top cause (0.90): The broker down event caused a disruption in the Kafka platform, leading to under-replicated partitions and increased consumer lag in downstream services. · changes blamed: DEP-0012 (red herring) | reference |
| B | PENDING_REVIEW · severity S2 · class eventhub_broker · top cause (0.90): Broker 2 became unreachable, leading to a broker down event and causing partition leadership changes, which resulted in under-replicated partitions and signific… · changes blamed: DEP-0012 (red herring) | worsened: faithfulness (judge) |

### GC-SC02-FREETEXT

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S2 · class eventhub_broker · top cause (0.90): The Kafka broker went down, leading to under-replicated partitions and significant consumer lag. · no change blamed | reference |
| B | PENDING_REVIEW · severity S2 · class eventhub_broker · top cause (0.90): A broker failure caused by Broker 2 being unreachable led to under-replicated partitions and significant consumer lag. · no change blamed | improved: faithfulness (judge); worsened: root cause correct (top 1), root cause in top 3 |

### GC-SC02-NARROW

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S2 · class eventhub_broker · top cause (0.90): Broker downtime led to under-replicated partitions, causing downstream consumer lag. · no change blamed | reference |
| B | PENDING_REVIEW · severity S2 · class eventhub_broker · top cause (0.90): Broker 2 became unreachable, leading to a broker down event and partition leadership changes. · changes blamed: DEP-0012 (red herring) | worsened: red herrings rejected, faithfulness (judge), hallucination rate (judge) |

### GC-SC02-WIDE

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S2 · class eventhub_broker · top cause (0.90): Broker down event caused under-replicated partitions, leading to significant consumer lag in downstream services. · changes blamed: DEP-0012 (red herring) | reference |
| B | PENDING_REVIEW · severity S2 · class eventhub_broker · top cause (0.90): A broker failure in the kafka-platform led to under-replicated partitions and significant consumer lag in downstream services. · changes blamed: DEP-0012 (red herring) | worsened: faithfulness (judge) |

### GC-SC02-MISSING

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S2 · class release_regression · top cause (0.90): A release in the notification-service introduced a regression causing increased error rates and latency in the /notify/v1/send endpoint, leading to customer com… · changes blamed: DEP-0012 (red herring) | reference |
| B | PENDING_REVIEW · severity S2 · class release_regression · top cause (0.90): A recent release of the notification-service introduced performance regressions, causing increased error rates and latency in the /notify/v1/send endpoint. · no change blamed | improved: red herrings rejected, faithfulness (judge) |

### GC-SC03-REPLAY

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S1 · class database_capacity · top cause (0.90): Core-banking-db reached maximum connection capacity, causing connection pool exhaustion in downstream services. · no change blamed | reference |
| B | PENDING_REVIEW · severity S1 · class database_capacity · top cause (0.90): Core-banking-db reached maximum connection capacity leading to connection pool exhaustion in downstream services. · no change blamed | worsened: faithfulness (judge) |

### GC-SC03-FREETEXT

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S1 · class database_capacity · top cause (0.90): Core-banking-db reached high connection usage and CPU load, leading to connection pool exhaustion in downstream services. · no change blamed | reference |
| B | PENDING_REVIEW · severity S1 · class database_capacity · top cause (0.90): Database connection pool exhaustion due to high connection usage and CPU load. · changes blamed: DEP-0021 | improved: causal change found, faithfulness (judge); worsened: root cause correct (top 1), root cause in top 3 |

### GC-SC03-NARROW

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S3 · class database_capacity · top cause (0.90): The core-banking-db reached its connection limit, causing the accounts-service to exhaust its connection pool. · no change blamed | reference |
| B | PENDING_REVIEW · severity S3 · class database_capacity · top cause (0.90): The core-banking-db reached high connection usage (98% of max connections) and CPU load (93.4%), leading to connection pool exhaustion in the accounts-service. · no change blamed | improved: root cause in top 3, faithfulness (judge); worsened: hallucination rate (judge) |

### GC-SC03-WIDE

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S1 · class database_capacity · top cause (0.90): Core-banking-db reached high connection usage and CPU load, leading to downstream service failures. · no change blamed | reference |
| B | PENDING_REVIEW · severity S1 · class database_capacity · top cause (0.90): Core-banking-db reached high connection usage and CPU load, leading to connection pool exhaustion in downstream services. · no change blamed | improved: faithfulness (judge), hallucination rate (judge); worsened: root cause correct (top 1), root cause in top 3 |

### GC-SC03-MISSING

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S1 · class database_capacity · top cause (0.90): Connection pool exhaustion in core-banking-db leading to high latency and error rates in auth-service and downstream services. · no change blamed | reference |
| B | PENDING_REVIEW · severity S1 · class database_capacity · top cause (0.90): Connection pool exhaustion in accounts-service due to saturation of core-banking-db, leading to high error rates and latency across dependent services. · no change blamed | improved: faithfulness (judge) |

### GC-SC04-REPLAY

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S1 · class network_tls · top cause (1.00): The TLS certificate for the card payment route expired, causing TLS handshake failures and resulting in a complete failure of card payment transactions. · no change blamed | reference |
| B | PENDING_REVIEW · severity S1 · class network_tls · top cause (1.00): The TLS certificate for the card payment route expired, leading to failed TLS handshakes and subsequent transaction failures. · changes blamed: DEP-0031 | worsened: faithfulness (judge) |

### GC-SC04-FREETEXT

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S1 · class network_tls · top cause (1.00): Expired TLS certificate for the card payment route. · no change blamed | reference |
| B | PENDING_REVIEW · severity S1 · class network_tls · top cause (1.00): Expired TLS certificate for the card payment route. · no change blamed | no change |

### GC-SC04-NARROW

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S1 · class network_tls · top cause (1.00): Expired TLS certificate for the card payment route. · no change blamed | reference |
| B | PENDING_REVIEW · severity S1 · class network_tls · top cause (1.00): Expired TLS certificate for the card payment route. · no change blamed | no change |

### GC-SC04-WIDE

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S1 · class network_tls · top cause (1.00): Expired TLS certificate for the card payment route. · no change blamed | reference |
| B | PENDING_REVIEW · severity S1 · class network_tls · top cause (1.00): The TLS certificate for the card payment route expired, leading to failed TLS handshakes and subsequent transaction failures. · changes blamed: DEP-0031 | worsened: faithfulness (judge) |

### GC-SC04-MISSING

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S1 · class network_tls · top cause (1.00): Expired TLS certificate for the card payment route. · changes blamed: DEP-0031 | reference |
| B | PENDING_REVIEW · severity S1 · class network_tls · top cause (1.00): The TLS certificate for the card payment route expired, causing all card payment transactions to fail due to handshake errors. · no change blamed | improved: hallucination rate (judge); worsened: faithfulness (judge) |

### GC-SC05-REPLAY

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S2 · class data_integrity · top cause (0.90): Duplicate transactions were produced due to a failure in transaction handling logic. · changes blamed: DEP-0041 | reference |
| B | PENDING_REVIEW · severity S2 · class data_integrity · top cause (0.90): Duplicate transactions were generated due to a processing error in the payments-service. · no change blamed | worsened: causal change found |

### GC-SC05-FREETEXT

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S1 · class data_integrity · top cause (0.90): Duplicate transactions were processed due to a system error in the payments-service. · no change blamed | reference |
| B | PENDING_REVIEW · severity S2 · class data_integrity · top cause (0.90): Duplicate transactions were produced by the payments-service due to a processing error. · no change blamed | improved: severity correct |

### GC-SC05-NARROW

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S2 · class data_integrity · top cause (0.90): Duplicate transactions were produced by the payments-service, leading to customer complaints about double charges. · no change blamed | reference |
| B | PENDING_REVIEW · severity S2 · class data_integrity · top cause (0.90): Duplicate transactions were produced due to a failure in transaction handling logic, leading to multiple charges for the same payment. · no change blamed | worsened: root cause correct (top 1) |

### GC-SC05-WIDE

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S2 · class data_integrity · top cause (0.90): Duplicate transactions were produced due to a failure in transaction handling logic in the payments-service. · no change blamed | reference |
| B | PENDING_REVIEW · severity S2 · class data_integrity · top cause (0.90): Duplicate transactions produced due to a processing error in the payments-service. · no change blamed | worsened: root cause in top 3 |

### GC-SC05-MISSING

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S2 · class data_integrity · top cause (0.90): Data integrity issue leading to double charges in card payments. · changes blamed: DEP-0041 | reference |
| B | PENDING_REVIEW · severity S2 · class data_integrity · top cause (0.90): Data integrity issue leading to duplicate transactions in the payments-service. · no change blamed | worsened: causal change found, hallucination rate (judge) |

### GC-SC05-FB001

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S2 · class data_integrity · top cause (0.90): Duplicate transactions were produced due to a malfunction in the payments service, causing customers to be charged multiple times. · no change blamed | reference |
| B | PENDING_REVIEW · severity S2 · class data_integrity · top cause (0.90): Duplicate transaction IDs were produced by the payments-service, leading to customer complaints about double charges. · no change blamed | improved: faithfulness (judge) |

### GC-SC05-FB002

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S2 · class data_integrity · top cause (0.90): Duplicate transactions were produced due to a processing error in the payments-service. · no change blamed | reference |
| B | PENDING_REVIEW · severity S2 · class data_integrity · top cause (0.90): Duplicate transactions were processed due to a failure in transaction handling logic in the payments-service. · no change blamed | no change |

## Summary against A

- **B (v2)**: improved Severity correct, Faithfulness (judge); worsened Root cause correct (top 1), Root cause in top 3, Hallucination rate (judge).

## Insights and choice of default

_To be written after reviewing the outputs above: what changed in the answers, new failure modes, and which configuration becomes the default and why._
