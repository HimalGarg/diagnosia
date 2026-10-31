# Third-party notices

Diagnosia is licensed under the Apache License 2.0 (see `LICENSE`). Parts of the
add-on tools are adapted from the works below, whose notices are kept here as
their licences require.

## OpenScribe

Adapted in `backend/clinscribe/verifier.py` (note verifier, ported from TypeScript
to Python and extended) and `backend/clinscribe/drafting.py` (core note-writing
rules). Source: https://github.com/sammargolis/OpenScribe at commit `f70a8d6`.

```
MIT License

Copyright (c) 2026 Sam Margolis

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## FHIR-XTract

Report-header patterns adapted in `backend/lablens/parser.py`. Source:
https://github.com/KIET7UKE/FHIR-XTract at commit `8e3d434`.

```
MIT License

Copyright (c) 2026 SAHIL SHARMA

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Healthtech Referral Management Toolkit

The stages in `backend/referralloop/routes.py` and the scenarios in
`backend/tests/test_referralloop.py` are adapted from this toolkit. Changes: prior
authorization left out, response times set per referral rather than fixed, and
further states added.

"Healthtech Referral Management Toolkit" © 2026 Sofeso, licensed under Creative
Commons Attribution 4.0 International (https://creativecommons.org/licenses/by/4.0/).
Source: https://github.com/Sofeso/healthtech-referral-management-toolkit at commit `e5fc18a`.

## LOINC

**Notice pending.** Diagnosia uses LOINC codes, but the required notice has not been added,
because the licence page could not be read automatically and wording must not be written from
memory. A person must copy the current notice from https://loinc.org/kb/license here and into
the terms of use, then record the `loinc_notice` signoff. Until then, LOINC-coded content
should not be distributed.

## RxNorm

MedGuard can look up drug names in RxNorm through the RxNav APIs. This product
uses publicly available data from the U.S. National Library of Medicine (NLM),
National Institutes of Health, Department of Health and Human Services. NLM is not
responsible for the product and does not endorse or recommend this or any other
product.

## openFDA

MedGuard can search US drug-label text through openFDA. Data is provided as is by
the U.S. Food and Drug Administration, which does not endorse this product.

## Runtime libraries

| Library | Used by | Licence |
|---|---|---|
| RapidFuzz 3 | Prescription Analyzer, medicine name check | MIT |
| pdfplumber 0.11 | LabLens | MIT |
| pdfminer.six | LabLens, through pdfplumber | MIT |
| pypdfium2 5 | LabLens, through pdfplumber | BSD-3-Clause and Apache-2.0, plus bundled PDFium licences |
| ReportLab 5 | Tests only, not shipped | BSD |
