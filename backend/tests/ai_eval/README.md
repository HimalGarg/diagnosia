# Model comparison for the reading tools

Synthetic photos of handwritten prescriptions and lab reports, with known answers. They are
**easier than real ones**, so they can show that a model is bad but not that it is good.

```bash
.venv/bin/python backend/tests/ai_eval/make_set.py /tmp/ai-set
PYTHONPATH=. .venv/bin/python backend/tests/ai_eval/run_eval.py /tmp/ai-set
```

Needs real keys in `.env`, and the macOS handwriting fonts. Run it again whenever a model changes.
It isn't part of the normal test run.

## Result on 31 October 2026

Five prescriptions (23 medicines) and three lab photos (36 results). The hardest lab photo is tilted
4.5 degrees with glare and low resolution, so rows and columns drift apart.

| Model | Medicines named | Day pattern right | Lab values right | Hard lab photo |
|---|---|---|---|---|
| gemini-3.5-flash | 23 of 23 | 23 of 23 | 36 of 36 | **11 of 11** |
| gemini-3.5-flash-lite | 23 of 23 | 23 of 23 | 30 of 36 | 5 of 11 |
| gemini-2.5-flash | 23 of 23 | 22 of 23 | 24 of 36 | 5 of 11 |
| Groq qwen/qwen3.8-27b | 23 of 23 | 21 of 23 | 30 of 36 | 5 of 11 |
| gemini-3.1-pro-preview | not answered | | | |

The wrong lab values were mostly a value attached to the wrong row, which is the dangerous kind.
Only the larger model avoided it. (Groq was measured at 2600 pixels, because its 1568-pixel run got no answer.) Bigger images (2600 pixels) didn't help. The larger models are often
busy, so each reading tool falls back to the smaller Gemini model, then to Groq.
