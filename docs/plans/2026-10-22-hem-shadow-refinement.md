# Conservative hem shadow refinement

## Scope

Implement the measurement plan's next pipeline item in the active `tools/vision_pipeline.py`: detect a strong, near-bottom horizontal image edge that indicates the garment hem is above a connected soft shadow. Only trim the measured length mask when edge evidence is clear; otherwise preserve the current endpoint.

## Compatibility

- Keep width measurement, API response shape, manual correction, and fallback behavior unchanged.
- Clamp any correction to a small band near the current bottom.
- Leave the current endpoint untouched for weak/no edge evidence.

## Checks

- Synthetic high-contrast hem plus connected shadow moves the endpoint up to the hem.
- Low-contrast image keeps the original endpoint.
- Rerun the 20-image dataset and require all rows to complete with no regression against existing width/length MAE limits.
- Run focused vision/server tests and `git diff --check`.
