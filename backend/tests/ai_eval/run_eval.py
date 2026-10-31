"""Compares vision models on the synthetic set, using real keys from .env. Costs a few free-tier calls.

Run from the project folder:  PYTHONPATH=. python backend/tests/ai_eval/run_eval.py /some/folder [model ...]
"""
import base64, io, json, re, sys, time
from pathlib import Path
from dotenv import dotenv_values
from PIL import Image
env = dotenv_values(".env")
from backend.config import settings
settings.groq_api_key, settings.gemini_api_key = env["GROQ_API_KEY"], env["GEMINI_API_KEY"]
from backend import llm
from backend.prescription import analyzer
from backend.lablens import extract
D = Path(sys.argv[1]); truth = json.load(open(D / "truth.json"))
MODELS = [("groq", "qwen/qwen3.8-27b"), ("gemini", "gemini-3.5-flash-lite"), ("gemini", "gemini-3.5-flash"), ("gemini", "gemini-2.5-flash"), ("gemini", "gemini-3.1-pro-preview")]
only = sys.argv[2:] 

def image_content(path, side, text):
    im = Image.open(path); im = im.convert("RGB"); im.thumbnail((side, side)); b = io.BytesIO(); im.save(b, "JPEG", quality=90)
    return [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(b.getvalue()).decode()}}, {"type": "text", "text": text}]

def call(provider, model, system, content, schema, max_tokens=8000):
    key = env["GROQ_API_KEY"] if provider == "groq" else env["GEMINI_API_KEY"]
    base = settings.gemini_base_url if provider == "gemini" else None
    body = llm.build_request(system, content, schema, model, max_tokens)
    for attempt in range(4):
        try:
            r = llm._post(body, key, 180, base, 1)
        except (llm.AIUnavailable, llm.AIRateLimited):
            time.sleep(8); continue
        if r.status_code == 200: break
        time.sleep(6)
    else:
        raise RuntimeError("no answer after 4 tries")
    if r.status_code != 200: raise RuntimeError(f"status {r.status_code} {r.text[:80]}")
    choice = r.json()["choices"][0]
    return llm._parse(choice["message"]["content"], schema)

def norm(s): return re.sub(r"[^a-z0-9]", "", s.lower())
def score_rx(read, items):
    got = [(norm(m.name.value), norm(m.day_pattern.value), norm(m.frequency.value), re.sub(r"\D", "", m.duration.value)) for m in read.medicines]
    name_ok = pat_ok = dur_ok = 0
    for want_name, want_pat, want_dur in items:
        hit = next((g for g in got if norm(want_name) in g[0]), None)
        if not hit: continue
        name_ok += 1
        wp = norm(want_pat); pat_ok += (wp in (hit[1], hit[2])) or (wp in hit[1] + hit[2]); dur_ok += (want_dur == hit[3]) or (want_dur == "" )
    return name_ok, pat_ok, dur_ok, len(items), len(got)
def score_lab(read, rows):
    ok_v = ok_u = ok_r = found = 0
    for row in rows:
        keyname = norm(row["name"]).replace("serum", "").replace("s", "", 0)
        hit = next((r for r in read.results if keyname[:7] in norm(r.test_name) or norm(r.test_name)[:7] in norm(row["name"])), None)
        if not hit: continue
        found += 1; ok_v += norm(hit.value) == norm(row["value"]); ok_u += norm(hit.unit) == norm(row["unit"]); ok_r += norm(hit.reference_range).replace("to", "") == norm(row["ref"])
    return found, ok_v, ok_u, ok_r, len(rows), len(read.results)

for provider, model in MODELS:
    if only and model not in only: continue
    t0 = time.time(); line = [f"{provider}:{model}"]; detail = []
    try:
        tot = [0, 0, 0, 0, 0]
        for key, items in truth["rx"].items():
            read = call(provider, model, analyzer.READ_SYSTEM, image_content(D / f"{key}.jpg", 1568, "Read this prescription."), analyzer.ReadPrescription)
            s = score_rx(read, items); tot = [a + b for a, b in zip(tot, s)]
            detail.append(f"{key}:{s[0]}/{s[1]}/{s[3]}")
        line.append(f"RX names {tot[0]}/{tot[3]} patterns {tot[1]}/{tot[3]} durations {tot[2]}/{tot[3]} extras {tot[4]-tot[0]}")
    except Exception as e: line.append(f"RX FAILED {type(e).__name__} {str(e)[:60]}")
    rx_t = time.time() - t0; t1 = time.time()
    for side in (1568, 2600):
        try:
            tot = [0, 0, 0, 0, 0, 0]
            for key, rows in truth["lab"].items():
                read = call(provider, model, extract.READ_SYSTEM, image_content(D / f"{key}.jpg", side, "Read this laboratory report."), extract.ReadReport)
                s = score_lab(read, rows); tot = [a + b for a, b in zip(tot, s)]
                detail.append(f"{key}@{side}:{s[0]}/{s[1]}/{s[2]}/{s[3]} of {s[4]}")
            line.append(f"LAB@{side}px found {tot[0]}/{tot[4]} values {tot[1]} units {tot[2]} ranges {tot[3]} (of {tot[4]})")
        except Exception as e: line.append(f"LAB@{side} FAILED {type(e).__name__} {str(e)[:60]}")
    print(" | ".join(line), f"| rx {rx_t:.0f}s lab {time.time()-t1:.0f}s\n    detail (found/pattern-or-value ok): " + ", ".join(detail), flush=True)
