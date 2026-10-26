# ChatGPT Sites -> GitHub recovery status (2026-10-26)

## Scope and source-of-truth

- Live product: https://www.simulareloto.ro/
- ChatGPT Sites project: `sites-project://appgprj_6a92dc568b0081919783742cd70bc1f8`
- GitHub repo: `cpiq/simulatorloto`; `main` last changed 2026-08-29.
- Most recent known Sites publication: 2026-10-04, version 19 (responsive/mobile fixes). The preceding site version uses a *single* 6/49 engine `649-coverage-v1`.
- This branch recovers only the Python *equivalent* of the new 6/49 engine from a saved project file. It **does not** contain the full deployed Next.js/TypeScript Sites code, assets, or database state.
- Do **not** deploy the old Flask frontend from this repository as a replacement for the Sites app. Do **not** infer that the legacy `server.py` serves the live site.

## Changes captured

- Replaced the old `perplexitymix.py` engine (scored frequency / delay / pairs) with the recovered `649-coverage-v1` Python port.
- Defaults aligned to the later published Sites configuration: **6 tickets**, each with **6 primary numbers**, and **1 additional number** for a 7-number extension.
- Generates variants from all 49 numbers; seeks high distinct-number coverage and limits pairwise overlap to 2 primary numbers.
- Historical frequency and pair influences are included **only** when their chronological validation passes the engine's significance gate; the delay and profile filter are not treated as default predictors.
- Added an offline regression test suite for basic generation invariants and 3/6 match counting.

## Validation

Run from the repository root:

```shell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Local result on 2026-10-26: 6 tests passed. These tests cover reproducibility, 6+1 output, overlap, no duplicate tickets at several seeds, coverage accounting, and exactly 3 winning numbers in an intentionally constructed example.

**They do not test the deployed Sites UI/API**, saved user tickets, automatic draw loading, or historical predictions made before the draw.

## Open investigation: why no 3/6 lately?

1. Obtain the actual deployed Sites repository/build and verify its engine version and defaults against this port.
2. Verify that historical draws are current and do not include future data for historical backtests.
3. Compare **saved, immutable predictions created before draw time** against official draw results (not fresh predictions generated after the draw).
4. Compare the 6-number primary ticket separately from the 7-number extension. A system of 7 numbers comprises 7 six-number combinations and must be compared with an equivalent number of paid simple variants.
5. Verify number normalization (number versus numeric string) and draw date/timezone matching in the real frontend/API.
6. Run a walk-forward 500-draw test at 6 tickets per draw, compared with the same-cost uniform baseline. No predictive gain should be claimed without held-out confirmation.

## Limitations

This branch is a *partial recovery*, not a full backup of ChatGPT Sites. The public page exposed to the available browser tooling is an older indexed version and is **not** authoritative for current source files. GitHub is not yet fully synchronized to the latest deployed site.

Do not merge into `main` as a complete Sites sync without recovering the missing source.
