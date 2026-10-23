# Measurement reference definitions

## Scope

Record the two planned length reference points for tops and dresses while keeping the existing shoulder/neck-junction-to-hem measurement as `referenceLengthCm` for backward compatibility and current accuracy calculations. Clarify the pants width point as 5 cm below the waistband.

## Contract

- Keep `referenceLengthCm` required for every garment.
- Add optional `referenceCollarToHemCm`; the UI requires it for tops and dresses only.
- Persist the optional value through the input draft, experiment API, result card, and CSV.
- Old experiments and API clients without the optional value remain valid.
- Keep accuracy/error calculations based on `referenceLengthCm`.

## Files and checks

- `js/measurement/garmentDraft.js`, `js/measurement/app.js`, `js/measurement/results.js`
- `server.py`
- `tests/measurement-garment-draft.js`, `tests/test_server.py`
- `measure/index.html` cache version

Run the browser draft fixture, focused server tests, then the full Python suite and `git diff --check`.
