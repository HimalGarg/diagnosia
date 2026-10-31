---
id: extraction-templates
title: Document extraction formats
category: extraction_templates
version: 1.0
source: backend/lablens/extract.py and backend/prescription/analyzer.py
review_status: technical_reviewed
clinical_content: none
---

# Document extraction formats

How documents are read into structured fields. This covers format only.

## Lab reports

Each result has a test name, value, unit, reference range, printed flag and legibility. Copy what is printed. Keep the decimal point. Don't calculate, convert units or fill in a usual range. Each value must stay on its own row: tilted photos can make rows drift, so mark a result unsure if a row is in doubt.

## Prescriptions

Each medicine has a name, dose, frequency, day pattern, duration and notes, and each field has a legibility of clear, unsure or unreadable. Copy the letters actually written. A bracket or line beside several medicines with one duration applies to each of them.

## Day patterns

Marks such as 1-0-1 stand for morning, afternoon and night. Copy them exactly. Decoding is done by plain rules in code, not by an AI model.
