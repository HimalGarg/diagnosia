# Archive

Everything here is out of the live product. Nothing in this folder is served by the site.

| Folder | What it is | Status |
|---|---|---|
| `visiontrack/` | Posture and eye-level tracker | **Sidelined.** No decision yet; it may come back. |
| `skin_cancer/` | Old Streamlit skin app and its Keras models | Replaced by the new skin tool in `backend/skin/`. |
| `talkie/` | Gemini chatbot | Removed. |
| `curl_counter/` | Exercise rep counter | Removed. |
| `old_site/` | BMI, DocFinder, Symptom Checker and Prescription Generator pages, with their CSS, JS and data | Removed or replaced. Needs `web/css/index.css`, `web/js/index.js` and `web/images/` if you ever restore a page. |
| `php/` | Old PHP login backend | Replaced by the FastAPI backend. It has SQL injection and MD5 passwords, so don't reuse it. |
| `old_requirements.txt` | Combined Python dependencies for the old apps | Kept for reference. |

Model files (`*.h5`) and `talkie/.env` are on disk here but are not in git.
