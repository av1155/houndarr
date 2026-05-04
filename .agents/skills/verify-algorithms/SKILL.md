---
name: verify-algorithms
description: Verify probabilistic, distributional, or random behaviour empirically before changing search-engine code. Loads when reading or editing src/houndarr/engine/. Use when a user, code review, or another AI surfaces a claim about bias, ordering, page selection, randomness, or "we are searching the same things over and over".
paths:
  - "src/houndarr/engine/**"
---

# Verifying claims about algorithms

Before modifying search-engine logic, scheduling, randomisation, ordering,
distribution, or any code where probability or stateful iteration governs
behaviour, verify the claim empirically and analytically first. Most
reported "bugs" in this class turn out to be sample noise, observation
bias, or misreadings of timing-dependent state, and shipping a fix for a
non-bug introduces real risk for no real gain.

## When this rule fires

Apply this workflow whenever a user, a code review, or another AI surfaces
a claim along the lines of:

- "X picks the wrong page / item / branch"
- "Y is biased / unfair / skewed toward Z"
- "Random does not feel random"
- "The cycle order is broken"
- "We are searching the same things over and over"

It does not apply to clear logic bugs, typos, or behaviour-change
requests. The trigger is specifically: claims about probabilistic or
distribution-shaped behaviour where the right answer is a measured
histogram, not a code reading.

## Required workflow

1. Reproduce the algorithm in isolation against `tests/mock_arr/`, not
   against the live test instances or short-window log dumps. The live
   test *arrs hold tens of records, which is far below the sample size
   needed to distinguish bias from variance, and live state (cooldowns,
   hourly caps, *arr-side sort orders) confounds the measurement.
2. Derive analytically what each page, item, or branch's probability
   should be under the current code. Read the loop, write the math
   down, and predict the distribution shape before running anything.
   "I think it should be uniform" is not a prediction; "uniform with
   chi-square below 16.92 at df=9" is.
3. Run hundreds of cycles through `tests/mock_arr/probe_distribution.py`
   or a similar probe modelled on it. Compute chi-square, max/min
   ratio, and per-bucket standard deviation. Compare against the
   analytical prediction and against the 5% chi-square critical value
   at `df = N - 1`.
4. Decide on evidence. If the empirical result agrees with the
   prediction and the chi-square lands below the critical value, the
   claim is wrong. Document the finding, reference the probe output,
   and close the investigation. If the result confirms real bias,
   scope the fix to the smallest change that closes the measured gap,
   then re-run the probe to prove the gap is gone.

## Tooling to use

- `just mock-arr port=PORT items=N seed=S` launches the seeded
  multi-app mock server with configurable item counts and a
  deterministic seed; identical seeds produce byte-identical responses.
- `.venv/bin/python -m tests.mock_arr.probe_distribution` boots the
  mock in-process, drives the production `run_instance_search` for
  many cycles across a sweep of library sizes, and reports per-cycle
  start-page distributions plus full visit histograms. Use it as the
  template for any new programmatic probe.
- The mock exposes `GET /__page_log__/{app}` and
  `GET /__commands__/{app}` for ground-truth request and dispatch
  records, plus `POST /__reset__/{app}` to clear them between
  configurations.
- For statistical-power-bound questions (100k+ trials), a short
  pure-Python simulation of just the algorithm beats running through
  HTTP. Use it when the measurement is about the math, not the
  integration.

## What not to do

- Do not treat a short-window dev-DB histogram (a few hours, dozens
  of cycles, a handful of items) as evidence of algorithmic bias.
  Cooldown phase, *arr-side sort order, and small-sample variance
  dominate that signal. The math you owe is a many-cycle distribution
  against a predicted shape.
- Do not adopt an external diagnostic write-up without re-deriving
  the math yourself. Direction (page 1 vs page N) and magnitude
  (1.5x vs 5x) routinely invert in second-hand summaries of
  probabilistic algorithms, and shipping a fix for an inverted claim
  ships a regression.
- Do not start coding because the claim is plausible. Plausibility is
  not evidence. The bar is a reproducible measurement that disagrees
  with the predicted distribution by more than chance.

## Closing the loop

When measurement contradicts the claim, the writeup is the engineering
contribution. Reference the probe output, state the measured statistics,
explain what the original observation was actually picking up
(cooldown saturation, recency effects, sort-order interaction, sample
noise), and close the discussion. A correct "no change required" is a
successful task, not a non-result.

## Known emergent behaviours (already measured)

These are real but minor effects that have been verified by probe and
deliberately left alone. Do not re-investigate them unless the
operating point changes or a user reports a concrete regression.

- Partial-last-page over-selection on missing/cutoff under random
  search order. When the engine's `page_size` does not divide
  `totalRecords` evenly, items on the (short) last page are drained
  every visit because the engine dispatches up to `batch_size` items
  per page. Measured at most 2x attention skew for the 1-9 items on
  the last page at default settings (batch=1, pageSize=10) and 4x in
  contrived configurations (batch=5, pageSize=20). Affects a small
  slice of the backlog; the only clean fix is a virtual flat-index
  draw which is a substantial redesign of `_run_search_pass`. Probe:
  `tests/mock_arr/probe_cooldown.py`.
- Sonarr / Whisparr v2 windowed-rotation coverage time. The upgrade
  pass visits 5 series per cycle; full-library coverage takes
  approximately `ceil(eligible_episodes * H / batch)` cycles where H
  is the harmonic-coverage factor. Measured 91% theoretical and
  85-89% empirical coverage at 60 cycles with batch=5 on 50 series.
  This is the intentional trade-off versus hammering one series with
  a single huge *arr fetch. Probe:
  `tests/mock_arr/probe_upgrade_coverage.py`.
