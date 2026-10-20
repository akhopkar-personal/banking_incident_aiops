# Adaptation cycle: click-by-click script (E-1 to E-10)

Follow these steps in the Streamlit UI on the laptop, so the cycle lands in the same logs as the other
evidence. The result is one adaptation with an evidence report showing E-1 to E-10, plus one
rejected adaptation, which the requirements also ask for.

## What this cycle fixes

In the full golden baseline (EVAL-20261015T144951477), the RCA Agent names the **symptom** instead of
the **cause** in SC-01, SC-03 and SC-05: root cause top-1 is 0.0 in 14 of those 15 cases. This script
uses SC-05 (duplicate debits). The correct answer, from the golden set:

> The retry-policy change DEP-0041 (max_attempts 1 to 3 on gateway timeout) resubmitted payments
> without an idempotency key, creating duplicate debits.

Typical v1 answer: "Duplicate transactions were produced due to a processing error in the payments-service."

## Before you start

- **Start the UI:** `.\.venv\Scripts\python -m streamlit run src/ui/app.py`, then open http://localhost:8501.
- **Sidebar:** every step below sets **Role** and **Reviewer ID** in the left sidebar. Use these
  IDs (short IDs, not names):

  | ID | Who |
  |---|---|
  | `rev-a` | first reviewer |
  | `rev-b` | second reviewer |
  | `sme-1` | the SME |
  | `ee-1` | the Evaluation Engineer |

  The two reviewer IDs must differ. With 2 signals, the reviewer cap allows only 1 per reviewer.
- **Evaluation mode stays OFF** on the Investigate page throughout. Evaluation runs don't appear on
  the Incident page and can't be reviewed.
- **Cost and time:**

  | Part | Cost | Time |
  |---|---|---|
  | A, B, C | about $0.01 | about 10 minutes of clicking |
  | E, all cases, judge on (**used in this script**) | about $0.45 to $0.55 | about 30 minutes |
  | F | about $0.04 | |
  | G | about $0.005 | |

  The whole cycle costs about $0.60. Step 11 lists cheaper Part E options for reference.
- **Check the budget first:** the Vocareum Assistant panel shows the GenAI allowance of the key's
  assignment. If the key runs out during a run, cases fail as `SYSTEM_ERROR` with "Insufficient
  budget". That distorts the before/after numbers, so stop and tell me; such a run is not evidence.

- **The outcome is measured, not guaranteed.** The engine keeps the new prompt only if the target
  metric (root cause top-1) improves and no other tracked metric drops by more than 5 points.
  Otherwise it reverts the change. A revert is also valid evidence; step 13 covers what to do then.
- **Be honest in the reviews.** If an output is actually right, approve it instead of rejecting it,
  and run another investigation. The evidence claims a human judged the output.
- **Screenshots** to take along the way are marked 📸.

---

## Part A: two real outputs to review

1. Open the **Investigate** page (the first page).
   1. **Data source:** choose `SC-05 · …duplicate debits…`.
   2. **Input:** leave it on **Detection replay**.
   3. Click **Investigate** and wait for "Finished in … s" (about 45 s).
2. Still on **Investigate**, with Data source SC-05:
   1. Set **Input** to **Free text**. The box is pre-filled with "Customers complaining about being
      charged twice for card payments since about 10:20 UTC."
   2. Click **Investigate** and wait for "Finished".

   Both runs belong to the same incident, because the second is a repeat of the first. Each run can be
   reviewed once.

## Part B: two reviewers reject (E-1 feedback, E-2 captured)

3. Sidebar: **Role** = *Incident Escalation Team*, **Reviewer ID** = `rev-a`.
4. Open the **Incident** page.
   1. **Incident:** choose the SC-05 incident (payments-service).
   2. **Investigation run (newest first):** choose the **second** run in the list (the replay from step 1).
   3. Read the **Hypotheses and evidence** tab. Check whether the top hypothesis names the retry-policy
      change, or only "duplicate transactions / a processing error".
   4. Open the **Review** tab:
      - **Decision:** *Reject*
      - **Ratings:**

        | Rating | Value |
        |---|---|
        | Root cause analysis | **2** |
        | Recommended actions | as you judge them |
        | Severity | as you judge them |
        | Stakeholder summary | as you judge them |

        Root cause analysis must be 2 or lower. That rating also creates the second pattern, the one you
        reject in step 9.
      - **Reason:** in your own words, for example: *"Names the symptom (duplicate transactions, a
        processing error) instead of the cause: the retry-policy configuration change on payments-service
        resubmitted payments without an idempotency key."*
      - **Issue type:** *root cause error*
   5. Click **Submit review**. Expect: "Review recorded: REJECTED. Feedback candidate FC-0001 created." 📸
5. Sidebar: **Reviewer ID** = `rev-b`. The role can stay the same.
6. On the **Incident** page, choose the **newest** run (the free-text run from step 2).
   1. Open the **Review** tab: **Reject**, **Root cause analysis 2**, a reason in different words,
      **Issue type** *root cause error*.
   2. Click **Submit review**. Expect: "… Feedback candidate FC-0002 created."

## Part C: the SME validates (E-3)

7. Sidebar: **Role** = *SME*, **Reviewer ID** = `sme-1`. Open the **Review Queue** page.
   1. **Pending candidate:** FC-0001. Compare "What the system said" with "What the reviewer said". 📸
   2. The promote form is pre-filled with the ground truth: expected root cause, severity S1, issue class,
      runbook RB-PAY-005 and causal change DEP-0041. Check it, then click **Promote**. Expect "FC-0001
      promoted. Golden dataset is now golden-v2."
   3. Choose FC-0002 and click **Promote**. Expect golden-v3.

## Part D: pattern found, one rejected, one approved (E-5, E-6)

8. Sidebar: **Role** = *Evaluation Engineer*, **Reviewer ID** = `ee-1`. Open the **Adaptation History** page.
   1. Check that "Active prompt versions" shows `v1` for every agent.
   2. Click **Scan feedback for patterns**. Expect a message like "2 proposed, … waiting".
   3. Under **Proposed**, find these two entries:
      - **(a)** `ADP-00x · PROPOSED · prompt rule for rca_agent · root cause error`. Source *feedback*, and
        "Pattern: 2 signals (threshold 2) from reviewers {'rev-a': 1, 'rev-b': 1}". **Approve this one.**
      - **(b)** `ADP-00y · PROPOSED · prompt rule for rca_agent · low rating rca`, source *preference*.
        **Reject this one.**
      - Entries with source *implicit* (repeated golden-set failures) may also appear. Leave them as
        they are.
9. Open entry **(b)**.
   1. **Reason (required to reject):** for example *"Same problem as ADP-00x, which addresses it from
      the reviewers' explicit feedback; one change at a time."*
   2. Click **Reject**. Expect "ADP-00y rejected." This is the "rejected adaptation" evidence. 📸
10. Open entry **(a)**.
    1. Read the reviewers' reasons and the **guideline** the LLM drafted from them.
    2. Just above the **Approve** and **Reject** buttons is a **text box** labelled *"Guideline to add
       (edit before approving if needed)"*, pre-filled with the drafted guideline. It appears only while
       the entry is *proposed* and only for the SME or Evaluation Engineer role; after Approve the text
       is locked. Edit it before approving if needed. It must stay general: no incident IDs, change
       IDs or dates. A good version: *"State the root cause as the mechanism that produced the symptoms,
       for example a configuration or release change that changed retries, limits or checks on the origin
       service, not the symptom itself (errors, timeouts, duplicates). Cite the log or event evidence that
       shows that mechanism."*
    3. Click **Approve**. Expect "ADP-00x approved. Apply it to measure the change." 📸

## Part E: measure and decide (E-4 baseline, E-7 changed, E-8 no regressions)

11. Still on **Adaptation History**, entry (a), now under **Approved**:
    1. **DeepEval judge on:** leave it on. The judge compares the root-cause text with the expected
       answer, which is what the guideline changes.
    2. **Golden cases:** leave it on **All cases** (27 cases after Part C; the specification asks for the
       full set).
    3. Click **Apply**. A progress panel shows "before: n/27 cases", then "after: n/27 cases". 📸 You can
       switch pages; start no other learning task until it ends. If the panel shows errors, stop and
       tell me.

    **Why all cases (options kept for reference).** The golden set runs twice, before and after:

    | Option | Runs | Approx. cost | Time | Trade-off |
    |---|---|---|---|---|
    | **All 27 cases, judge on** | 54 | **$0.45 to $0.55** | about 30 min | **Used here.** Full check; one noisy case is tolerated |
    | All 27 cases, judge off | 54 | about $0.22 | about 20 min | Root cause scored by the evidence rubric, not the judge |
    | Only SC-05 (7 cases), judge on | 14 | about $0.10 | about 10 min | Strict, see below |
    | Only SC-05 (7 cases), judge off | 14 | about $0.06 | about 8 min | Cheapest; rubric scoring, also strict |

    The engine reverts if any tracked metric drops by more than 5 points.
    - With 27 cases, one case changing moves a metric by about 4 points, which is tolerated.
    - With 7 cases, one case moves it by about 14 points, so one noisy run in an unrelated metric (such
      as severity or the blamed change) reverts the adaptation even if root cause improved.
    - The full set also shows whether the new guideline made other scenarios (SC-01 to SC-04) worse,
      which is what E-8 ("improved without regressions") has to prove.
    - The judge scores the root-cause wording, which is what the guideline changes; with the judge off,
      root cause is scored by an evidence-citation rubric instead.
12. When it ends, the entry moves to **Applied** or **Reverted**. Open it and read the explanation and the
    before/after metrics table. 📸
    - **Applied:** "Active prompt versions" now shows `rca_agent v2`. Continue with Part F.
    - **Reverted:** the page says why ("rca_top1 did not improve" or "regression in …"). Go to step 13.
13. Only if it was reverted: first read the reason, then choose one.
    1. **Reason is a regression in another metric** ("regression in severity_match …"): the guideline
       helped one thing and hurt another. To retry, click **Scan feedback for patterns** again. A new
       proposal for the same pattern appears, because the reverted one is closed. Approve it with a
       narrower guideline and **Apply** on *All cases*, judge on (about $0.50 more).
    2. **Reason is "rca_top1 did not improve":** the guideline did not help. Either retry once with a
       sharper guideline (scan, approve, apply as above, about $0.50 more), or stop.
    3. **Stop here:** the evidence report will then show E-7 to E-10 as MISSING, and the reverted
       adaptation still counts as evidence. Tell me, and I'll write it up honestly.

## Part F: do humans prefer the new version? (E-9)

14. Sidebar: **Role** = *Evaluation Engineer*, `ee-1`. Open **Feedback and RLHF**, then the **Sessions** tab.

    | Field | Value |
    |---|---|
    | Agent | Root Cause Analysis |
    | Version A | `v1` |
    | Version B | `v2` |
    | Golden cases | clear the defaults, then pick GC-SC05-REPLAY, GC-SC05-FREETEXT, GC-SC05-NARROW, GC-SC05-WIDE |
    | Held-out share | **1.0** |
    | For adaptation | ADP-00x |

    Click **Create session** and wait for the panel to finish (8 runs, a few minutes).
15. Sidebar: **Role** = *Incident Escalation Team*, **Reviewer ID** = `rev-a`. Open the **Label pairs** tab
    and choose the new session.
    1. For each pair, compare **Output 1** and **Output 2**. The versions are hidden and the order is random.
    2. Choose the better one (or *About the same*), type **Why?**, and click **Record my choice**. 📸
    3. Repeat until it says "Nothing left for rev-a to label".
16. Sidebar: **Reviewer ID** = `rev-b`. Label every pair again, judging independently.
17. If any pair is shown as **disputed**: Sidebar **Role** = *SME*, `sme-1`, then label the disputed pairs.
18. Sidebar: **Role** = *Evaluation Engineer*. On the **Sessions** tab, choose the session under "Log a
    session's result to eval.log" and click **Log result**. It shows the win rate of B (v2).

    For an adaptation driven by reviewer feedback, the golden set decides keep or revert; the win rate is
    the human-preference evidence.

## Part G: a new run uses v2 and is reviewed (E-10)

19. Open the **Investigate** page: SC-05, **Detection replay**, Evaluation mode **off**. Click **Investigate**.
20. Sidebar: **Role** = *Incident Escalation Team*, `rev-a`. On the **Incident** page, choose the SC-05
    incident and the **newest** run.
    1. Compare the top hypothesis with the one from Part A. 📸
    2. Open the **Review** tab, decide honestly (Approve, Edit or Reject) with ratings, and click
       **Submit review**.

       Approve and Edit ask you to confirm each high-risk action in two steps first.
21. Optional: on **Feedback and RLHF**, open the **Reward scores** tab and click **Log reward scores to
    eval.log and LangFuse**.

## Part H: the evidence report

22. Sidebar: **Role** = *SME* or *Evaluation Engineer*. On **Adaptation History**, open entry (a) and
    click **Generate the evidence report**.
    - Expect "every step found", or a list of the missing steps.
    - The report is written to `docs/evidence/ADP-00x.md`, and the **Evidence report** expander shows it. 📸
23. Tell me when you're done. I'll check the logs and update `EVIDENCE_MAP.md`:
    - the adaptation IDs, the before/after evaluation IDs and the flow logs
    - a before/after comparison table from the two golden runs
    - a final PII check

## If something goes wrong

| What you see | Why | What to do |
|---|---|---|
| The Review tab says to switch the role | Only the Incident Escalation Team, Production Support and the incident commander review | Change **Role** in the sidebar |
| "run … was already reviewed" | One review per run | Pick the other run, or run another investigation |
| The scan shows entry (a) as **waiting** with "reviewer cap exceeded" | Both rejections used the same Reviewer ID | Run one more SC-05 investigation and reject it as a third reviewer (`rev-c`), promote it, scan again |
| The scan shows (a) as **waiting**, count 1 | Only one candidate promoted, or the two rejections used different issue types | Promote both candidates; both must be *root cause error* |
| No "low rating rca" entry to reject | Root cause analysis was rated 3 or higher | Reject one of the *implicit* proposals instead, with a reason |
| "… is still running" when clicking Apply or Create session | One learning task runs at a time | Wait for the progress panel to finish |
| OpenAI rate-limit or budget errors in the progress panel | Gateway limits or budget | Rate limits are retried. "Insufficient budget" means the key ran out: stop and tell me |
