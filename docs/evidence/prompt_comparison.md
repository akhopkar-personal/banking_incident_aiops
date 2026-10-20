# Prompt strategies on the same test set (FR-78)

Generated 2026-10-15 13:33 UTC from saved results in `data/evaluation/`. Reference: run A.

**Test set (6 cases, identical in every run):** GC-SC01-REPLAY, GC-SC01-FREETEXT, GC-SC02-REPLAY, GC-SC02-FREETEXT, GC-SC04-REPLAY, GC-SC04-FREETEXT

## Configurations

| Run | Label | Prompt | Retrieval | Judge | Eval ID | Total cost |
|---|---|---|---|---|---|---|
| A | v1 baseline | Active prompts: v1 | on | on | EVAL-20261015T132212386 | $0.0248 |
| B | p2-checklist | change_correlation_agent p2-checklist; rca_agent p2-checklist. Prompt strategy 2: the base prompt plus an explicit step-by-step reasoning checklist (origin vs symptoms, contradicting evidence, three tests per change). | on | on | EVAL-20261015T132550004 | $0.0239 |
| C | p3-examples | change_correlation_agent p3-examples; rca_agent p3-examples. Prompt strategy 3: strategy 2 plus one worked example (few-shot) of rejecting a red herring. | on | on | EVAL-20261015T132931844 | $0.0268 |

## Metrics

| Metric | Target | A | B | C |
|---|---|---|---|---|
| Root cause correct (top 1) | ≥ 0.6 | 0.50 | 0.33 (-0.17 worse) | 0.20 (-0.30 worse) |
| Root cause in top 3 | ≥ 0.8 | 0.50 | 0.50 | 0.20 (-0.30 worse) |
| Red herrings rejected |  | 0.83 | 0.83 | 0.80 (-0.03 worse) |
| Causal change found | ≥ 0.8 | 0.00 | 0.50 (+0.50 better) | 1.00 (+1.00 better) |
| Issue class correct | ≥ 0.8 | 0.83 | 0.83 | 1.00 (+0.17 better) |
| Severity correct | ≥ 0.7 | 1.00 | 1.00 | 1.00 |
| Abstained when it should | ≥ 0.8 | 1.00 | 1.00 | 0.40 (-0.60 worse) |
| Citations valid | ≥ 0.95 | 1.00 | 1.00 | 1.00 |
| Right runbook retrieved (top 5) | ≥ 0.85 | 1.00 | 1.00 | 1.00 |
| Faithfulness (judge) | ≥ 0.8 | 0.92 | 0.79 (-0.13 worse) | 0.74 (-0.18 worse) |
| Hallucination rate (judge) | ≤ 0.1 | 0.17 | 0.00 (-0.17 better) | 0.00 (-0.17 better) |
| Dispatch routed correctly | ≥ 1.0 | 1.00 | 1.00 | 1.00 |
| Latency per case (s) |  | 60.1 | 63.4 (+3.3 worse) | 50.3 (-9.7 better) |
| Cost per case (USD) |  | $0.0041 | $0.0040 (-0.0001 better) | $0.0045 (+0.0004 worse) |

## Prompt → Output → What improved or worsened, per case

### GC-SC01-REPLAY

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S1 · class release_regression · top cause (0.90): DB_TIMEOUT errors in payments-service due to slow queries or database issues. · no change blamed | reference |
| B | PENDING_REVIEW · severity S1 · class release_regression · top cause (0.90): The payments-service is experiencing DB_TIMEOUT errors due to high load or performance issues with the core-banking-db, leading to a high error rate in payment … · changes blamed: DEP-0007 | improved: causal change found |
| C | INSUFFICIENT_EVIDENCE · severity S1 · class release_regression · changes blamed: DEP-0007 | improved: causal change found, faithfulness (judge); worsened: abstained when it should |

### GC-SC01-FREETEXT

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S1 · class database_capacity · top cause (0.90): Database timeouts due to high load on core-banking-db causing payment failures. · no change blamed | reference |
| B | PENDING_REVIEW · severity S1 · class database_capacity · top cause (0.90): Database capacity issues leading to DB_TIMEOUT errors in payments-service. · no change blamed | improved: root cause in top 3; worsened: faithfulness (judge) |
| C | SYSTEM_ERROR · severity S1 · class None · no change blamed | no change |

### GC-SC02-REPLAY

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S2 · class eventhub_broker · top cause (0.90): Disk I/O error on kafka-broker-2 caused the broker to shut down, leading to under-replicated partitions and consumer lag in downstream services. · changes blamed: DEP-0012 (red herring) | reference |
| B | PENDING_REVIEW · severity S2 · class eventhub_broker · top cause (0.90): Kafka broker down, causing under-replicated partitions and consumer lag. · changes blamed: DEP-0012 (red herring) | worsened: root cause correct (top 1), root cause in top 3, faithfulness (judge) |
| C | INSUFFICIENT_EVIDENCE · severity S2 · class eventhub_broker · changes blamed: DEP-0012 (red herring) | worsened: root cause correct (top 1), root cause in top 3, abstained when it should, faithfulness (judge) |

### GC-SC02-FREETEXT

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S2 · class eventhub_broker · top cause (0.90): A broker down event caused significant consumer lag and under-replicated partitions in the EventHub platform. · no change blamed | reference |
| B | PENDING_REVIEW · severity S2 · class eventhub_broker · top cause (0.90): Kafka broker down leading to under-replicated partitions and consumer lag. · no change blamed | worsened: faithfulness (judge) |
| C | PENDING_REVIEW · severity S2 · class eventhub_broker · top cause (0.90): Broker 2 failure caused under-replicated partitions, leading to significant consumer lag in both ledger-consumer and notification-service, resulting in complain… · no change blamed | improved: root cause correct (top 1), root cause in top 3; worsened: faithfulness (judge) |

### GC-SC04-REPLAY

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S1 · class network_tls · top cause (1.00): Expired TLS certificate for the card payment route. · changes blamed: DEP-0031 | reference |
| B | PENDING_REVIEW · severity S1 · class network_tls · top cause (1.00): Expired TLS certificate for the card payment route · no change blamed | worsened: faithfulness (judge) |
| C | INSUFFICIENT_EVIDENCE · severity S1 · class network_tls · no change blamed | worsened: root cause correct (top 1), root cause in top 3, abstained when it should, faithfulness (judge) |

### GC-SC04-FREETEXT

| Run | Output | What improved or worsened (vs A) |
|---|---|---|
| A | PENDING_REVIEW · severity S1 · class network_tls · top cause (1.00): Expired TLS certificate for the card payment route causing handshake failures and transaction errors. · no change blamed | reference |
| B | PENDING_REVIEW · severity S1 · class network_tls · top cause (1.00): Expired TLS certificate for the card payment route. · no change blamed | improved: hallucination rate (judge); worsened: faithfulness (judge) |
| C | PENDING_REVIEW · severity S1 · class network_tls · top cause (0.90): The TLS certificate for the card payment route expired, causing TLS handshake failures and resulting in a 100% error rate for card payments. · no change blamed | improved: hallucination rate (judge); worsened: root cause correct (top 1), root cause in top 3, faithfulness (judge) |

## Summary against A

- **B (p2-checklist)**: improved Causal change found, Hallucination rate (judge); worsened Root cause correct (top 1), Faithfulness (judge).
- **C (p3-examples)**: improved Causal change found, Issue class correct, Hallucination rate (judge); worsened Root cause correct (top 1), Root cause in top 3, Red herrings rejected, Abstained when it should, Faithfulness (judge).

## Insights and choice of default

_To be written after reviewing the outputs above: what changed in the answers, new failure modes, and which configuration becomes the default and why._
