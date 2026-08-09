# Banded Gig Recommendation Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make missing-part count the primary recommendation score criterion while retaining scarce-instrument utilization as the secondary criterion.

**Architecture:** Calculate a normalized instrument-fit value from the optimizer's existing inverse-quantity rewards. Map each missing-part count to a non-overlapping score band, then use instrument fit only within that band. Use the same missing-part priority in the optimizer so assignment selection and displayed scores agree.

**Tech Stack:** Python, Django TestCase, NumPy/SciPy MILP

## Global Constraints

- Fewer missing parts must always produce a higher score.
- Within the same missing-part count, assigning players to scarcer instruments must produce a higher score.
- Scores should remain in the 0–100 range for songs with up to the configured maximum missing-part count.

---

### Task 1: Specify scoring behavior

**Files:**
- Modify: `prsb/band/tests.py`

**Interfaces:**
- Consumes: `get_score(num_missing_parts: int, instrument_fit: float) -> float`
- Produces: regression tests for score-band separation, instrument-fit ordering, and boundaries

- [ ] Add tests proving every score with `n` missing parts exceeds every score with `n + 1` missing parts.
- [ ] Add tests proving higher instrument fit wins within one missing-part band.
- [ ] Add tests for 100-point perfect fit and bounded extreme inputs.
- [ ] Run the focused tests and verify the behavioral tests fail before implementation.

### Task 2: Implement banded scoring

**Files:**
- Modify: `prsb/scripts/gig_part_assignment.py`

**Interfaces:**
- Produces: `get_score(num_missing_parts: int, instrument_fit: float) -> float`
- Preserves: `get_gig_song_part_assignments(...) -> tuple[list[PartAssignment] | None, float]`

- [ ] Give each missing-part count a distinct score band.
- [ ] Normalize inverse-quantity instrument rewards into a 0–1 fit value.
- [ ] Calculate missing parts from ready assignments selected by the solver.
- [ ] Make the optimizer's missing-part coefficient dominate its total instrument reward.
- [ ] Run the focused scoring tests.

### Task 3: Verify integration

**Files:**
- Verify: `prsb/scripts/gig_part_assignment.py`
- Verify: `prsb/band/tests.py`

**Interfaces:**
- Consumes: gig 40 from the configured development database
- Produces: verified recommendation scores and order

- [ ] Run the complete band test suite.
- [ ] Recompute gig 40 and confirm song 76 outranks song 65 because it has fewer missing parts.
- [ ] Check edited files for lint diagnostics.
