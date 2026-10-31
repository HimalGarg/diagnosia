# External integration validation

Results from 31 October 2026. All data was synthetic.

## Services

| Service | Mocked failure tests | Live test | Result |
|---|---|---|---|
| RxNorm (RxNav) | Yes | **Run** | Passed: known drug, unknown name, route still served |
| openFDA | Yes | **Run** | Passed: reply shape, unknown name |
| Groq chat and vision | Yes | **Run** | Passed, with synthetic data. Strict output on both text models, an image, and Hindi interview turns |
| Groq speech | Yes | **Run** | Speech to text accepted audio. **The English voice returned 400** until the account owner accepts the model's terms in the Groq console |
| Gemini chat and images | Yes | **Run** | Passed, with synthetic data, including a wrong Groq key answered by Gemini |
| Browser microphone | Fake device | Chrome with a fake audio device | 30 of 30. **No real microphone was used** |

Mocked cases cover valid, invalid, empty, timeout, rate limit, authentication failure, outage,
malformed and partial replies, in `test_external_failures.py`.

Each client has explicit timeouts and bounded retries: Groq chat 120 seconds and 1 retry, speech
60 seconds and 1 retry, references 6 seconds and 1 retry. A rate limit with a long wait isn't retried. Failures give a clear message
and invent nothing. Authentication failures aren't retried.

## Stand-in Groq server

`backend/tests/browser/fake_groq.py` answers like Groq's API over real HTTP. In Chrome, against it,
a note was drafted, its invented dose and lost negation were flagged, it was signed, audio was
recorded and transcribed, and a scan went through vision reading. **This proves our wiring only. It
says nothing about how Groq's models behave or how accurate they are.** The DiagnoFast interview,
its report, and prescription reading and explanation were also run against it, in Chrome.

## Opt-in live tests

Marked `live`, excluded from the normal run, synthetic data only:

```bash
DIAGNOSIA_LIVE_TESTS=1 .venv/bin/pytest -m live backend/tests/live
```

Live tests skip themselves without real keys in `GROQ_API_KEY` and `GEMINI_API_KEY`. They are the
only proof that the chosen models accept our requests. They were run on 31 October 2026, and all
passed except the English voice. Re-run them after changing a model.

## Browser microphone

```bash
DIAGNOSIA_DB=/tmp/test-run/mic.db DIAGNOSIA_DOCUMENTS=/tmp/test-run/docs \
DIAGNOSIA_DOCUMENT_KEY_FILE=/path/outside/keys.json python -m backend.tests.browser.serve 8124
node backend/tests/browser/run_microphone.mjs <puppeteer node_modules> 8124 /tmp/test-run/mic.db .venv/bin/python
```

Speech is faked by the server. Covered in Chrome: nothing records before a click, permission
refused, no microphone, microphone busy, unsupported browser, empty recording, network failure,
transcription failure, empty transcript, repeated recording, leaving the tool, a long recording
across a chunk boundary, the final chunk being kept, microphone released, and revoked consent
never opening the microphone.

Faked in the page rather than real: permission refusal, missing and busy devices were simulated
by making `getUserMedia` reject, and the unsupported browser by removing `MediaRecorder`.

**Not tested:** a real microphone, real speech through Groq, Hindi through Whisper, the browser's
Hindi voice on a real device, which depends on the voices that device has installed, Safari, Firefox,
Android or iOS. Safari's recorder produces mp4, which the code labels correctly but nobody has tried.

## Indian lab reports

`backend/tests/lab_validation/` generates 12 synthetic layouts. Run with
`python -m backend.tests.lab_validation.score`.

| Field | Correct | Total |
|---|---|---|
| Test found | 37 | 37 |
| Result value | 37 | 37 |
| Unit | 35 | 36 |
| Reference low | 30 | 30 |
| Reference high | 34 | 34 |
| Patient name | 9 | 9 |
| Collection date | 9 | 9 |

Wrong values: 0. Silently missed results: 0. Covers text lines, bordered tables, range before
unit, colons and brackets, "Sr." and "S." prefixes, high and low marks, multiple pages, two
columns, unit variations, missing fields, Indian digit grouping and several date formats.

**These numbers come from layouts we wrote ourselves, and the parser was fixed against them.**
They show no regression. They are not an accuracy estimate for real reports. Real measures need
representative, consented, de-identified reports from real laboratories.

Found and fixed through this: two-column reports silently lost the right column, "Mrs." was read
as "Mr" plus "s.", and two-digit-year dates weren't parsed.

**Scans:** the two scanned cases contain no text, so they correctly aren't read locally. AI reading
of scans has only been tested with a fake reply, so its accuracy is unmeasured. Devanagari
reports, handwriting, and photographed pages are also unmeasured.

Extracted values never become verified on their own: a test confirms that nothing reaches the
record for any layout without a person's confirmation.

## Next steps

| Task | Owner |
|---|---|
| Live Groq tests, with a key | Engineering |
| Real microphone tests on target devices and browsers | Engineering, clinic |
| De-identified real lab reports, and field accuracy on them | Laboratory professional |
| Measure AI reading of scans | Laboratory professional |
