"""Synthetic Indian lab report layouts, with the right answers alongside.

Every name, value and laboratory here is invented. These test how layouts are
handled. They are not real reports, and passing here does not validate LabLens
on real ones.
"""
import io
from dataclasses import dataclass, field
from typing import List, Optional

from PIL import Image, ImageDraw, ImageFilter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle


@dataclass
class Expected:
    code: Optional[str]
    name: str
    value: float
    unit: str = ""
    low: Optional[float] = None
    high: Optional[float] = None


@dataclass
class Case:
    name: str
    pdf: bytes
    patient: Optional[str]
    collected: Optional[str]
    results: List[Expected] = field(default_factory=list)
    readable_locally: bool = True


def lines_pdf(pages: List[List[str]]) -> bytes:
    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=A4)
    for lines in pages:
        height = 800
        for line in lines:
            page.drawString(40, height, line)
            height -= 16
        page.showPage()
    page.save()
    return buffer.getvalue()


def grid_pdf(header: List[str], rows: List[List[str]]) -> bytes:
    buffer = io.BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=A4, topMargin=20 * mm)
    table = Table(rows, hAlign="LEFT")
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black), ("FONTSIZE", (0, 0), (-1, -1), 9)]))

    def write_header(page, _):
        height = 810
        for line in header:
            page.drawString(40, height, line)
            height -= 14

    document.build([table], onFirstPage=write_header)
    return buffer.getvalue()


def two_column_pdf(left: List[str], right: List[str]) -> bytes:
    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=A4)
    page.drawString(40, 810, "Pioneer Pathology Centre")
    page.drawString(40, 794, "Patient Name : Mr. Arun Patil   Age : 61 Years")
    page.drawString(40, 778, "Collected On : 05/02/2026")
    height = 750
    for first, second in zip(left, right):
        page.drawString(40, height, first)
        page.drawString(320, height, second)
        height -= 16
    page.save()
    return buffer.getvalue()


def scanned_pdf(lines: List[str], blur: float = 0.0, noise: bool = False) -> bytes:
    """A page with no text layer, as a phone scan would give."""
    image = Image.new("L", (1240, 1754), 255)
    draw = ImageDraw.Draw(image)
    height = 80
    for line in lines:
        draw.text((80, height), line, fill=0)
        height += 40
    if blur:
        image = image.filter(ImageFilter.GaussianBlur(blur))
    if noise:
        image = image.rotate(1.5, fillcolor=255)
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PDF")
    return buffer.getvalue()


def cases() -> List[Case]:
    return [
        Case(
            "single_lines",
            lines_pdf([[
                "Sunrise Diagnostics Laboratory", "Patient Name : Mr. Ravi Kumar   Age : 56 Years", "Sample Collected On : 12/03/2026",
                "Test Result Unit Reference Range", "Haemoglobin 11.2 L g/dL 13.0 - 17.0", "Total Leucocyte Count 7,800 /cumm 4000 - 11000",
                "Platelet Count 2.5 lakh/cumm 1.5 - 4.5", "Creatinine 1.02 mg/dl < 1.3",
            ]]),
            "Ravi Kumar", "2026-03-12",
            [Expected("718-7", "Haemoglobin", 11.2, "g/dL", 13, 17), Expected("6690-2", "TLC", 7800, "/cumm", 4000, 11000),
             Expected("777-3", "Platelets", 2.5, "lakh/cumm", 1.5, 4.5), Expected("2160-0", "Creatinine", 1.02, "mg/dl", None, 1.3)],
        ),
        Case(
            "bordered_table",
            grid_pdf(
                ["City Care Labs", "Patient Name : Mrs. Sita Devi   Age : 44", "Report Date : 03-Jan-2026"],
                [["Test", "Result", "Unit", "Reference"], ["Hemoglobin", "12.4", "g/dL", "12.0 - 15.0"],
                 ["Glucose, Fasting", "104", "mg/dL", "70 - 100"], ["HbA1c", "6.4", "%", "4.0 - 5.6"], ["TSH", "3.20", "uIU/mL", "0.27 - 4.20"]],
            ),
            "Sita Devi", "2026-01-03",
            [Expected("718-7", "Hemoglobin", 12.4, "g/dL", 12, 15), Expected("1558-6", "Glucose fasting", 104, "mg/dL", 70, 100),
             Expected("4548-4", "HbA1c", 6.4, "%", 4, 5.6), Expected("3016-3", "TSH", 3.2, "uIU/mL", 0.27, 4.2)],
        ),
        Case(
            "range_before_unit",
            lines_pdf([[
                "Metro Lab", "Name : Mr. Imran Shaikh", "Collection Date : 15/06/2026", "Investigation Value Biological Ref. Interval Unit",
                "Hemoglobin 14.1 13.0-17.0 g/dL", "Total Cholesterol 212 < 200 mg/dL", "HDL Cholesterol 41 > 40 mg/dL", "Triglycerides 168 < 150 mg/dL",
            ]]),
            "Imran Shaikh", "2026-06-15",
            [Expected("718-7", "Hemoglobin", 14.1, "g/dL", 13, 17), Expected("2093-3", "Total cholesterol", 212, "mg/dL", None, 200),
             Expected("2085-9", "HDL", 41, "mg/dL", 40, None), Expected("2571-8", "Triglycerides", 168, "mg/dL", None, 150)],
        ),
        Case(
            "colons_and_brackets",
            lines_pdf([[
                "Shree Diagnostics Centre", "Patient : Mrs. Kavita Joshi", "Sample Date : 20.07.2026",
                "Hemoglobin : 10.9 gm/dl (12-15)", "Sr. Creatinine : 0.8 mg/dL (0.6-1.1)", "S. Bilirubin Total : 1.4 mg/dL (0.2-1.2)",
                "Serum Sodium (Na+) : 138 mmol/L (136-145)",
            ]]),
            "Kavita Joshi", "2026-07-20",
            [Expected("718-7", "Hemoglobin", 10.9, "gm/dl", 12, 15), Expected("2160-0", "Creatinine", 0.8, "mg/dL", 0.6, 1.1),
             Expected("1975-2", "Bilirubin", 1.4, "mg/dL", 0.2, 1.2), Expected("2951-2", "Sodium", 138, "mmol/L", 136, 145)],
        ),
        Case(
            "abnormal_marks",
            lines_pdf([[
                "Lifeline Pathology Lab", "Patient Name : Mr. Mohan Das", "Reported On : 09/09/2026",
                "Hemoglobin 7.1 Low g/dL 13.0 - 17.0", "Glucose, Fasting 312 H mg/dL 70 - 100", "Potassium 6.2 HH mmol/L 3.5 - 5.1", "ALT 188 * U/L 0 - 41",
            ]]),
            "Mohan Das", "2026-09-09",
            [Expected("718-7", "Hemoglobin", 7.1, "g/dL", 13, 17), Expected("1558-6", "Glucose fasting", 312, "mg/dL", 70, 100),
             Expected("2823-3", "Potassium", 6.2, "mmol/L", 3.5, 5.1), Expected("1742-6", "ALT", 188, "U/L", 0, 41)],
        ),
        Case(
            "multiple_pages",
            lines_pdf([
                ["Prime Labs", "Patient Name : Ms. Anjali Rao", "Sample Collected On : 11-Feb-26", "COMPLETE BLOOD COUNT",
                 "Hemoglobin 12.9 g/dL 12.0 - 15.0", "MCV 88 fL 83 - 101", "Page 1 of 2"],
                ["Prime Labs", "Patient Name : Ms. Anjali Rao", "LIPID PROFILE", "LDL Cholesterol 121 mg/dL < 100", "VLDL Cholesterol 28 mg/dL < 30", "Page 2 of 2"],
            ]),
            "Anjali Rao", "2026-02-11",
            [Expected("718-7", "Hemoglobin", 12.9, "g/dL", 12, 15), Expected("787-2", "MCV", 88, "fL", 83, 101),
             Expected("13457-7", "LDL", 121, "mg/dL", None, 100), Expected("13458-5", "VLDL", 28, "mg/dL", None, 30)],
        ),
        Case(
            "two_columns",
            two_column_pdf(
                ["Hemoglobin 13.2 g/dL 13.0 - 17.0", "MCV 90 fL 83 - 101"],
                ["Creatinine 1.1 mg/dL 0.7 - 1.3", "Urea 31 mg/dL 15 - 40"],
            ),
            "Arun Patil", "2026-02-05",
            [Expected("718-7", "Hemoglobin", 13.2, "g/dL", 13, 17), Expected("787-2", "MCV", 90, "fL", 83, 101),
             Expected("2160-0", "Creatinine", 1.1, "mg/dL", 0.7, 1.3), Expected("3091-6", "Urea", 31, "mg/dL", 15, 40)],
        ),
        Case(
            "unit_variations",
            lines_pdf([[
                "Apex Diagnostics", "Patient Name : Mrs. Rekha Iyer", "Collected On : 18 March 2026",
                "RBC Count 4.52 million/cumm 3.8 - 4.8", "Platelet Count 245 x10^3/uL 150 - 410", "Glucose, Random 96 mg% 70 - 140", "TSH 2.1 mIU/L 0.4 - 4.0",
            ]]),
            "Rekha Iyer", "2026-03-18",
            [Expected("789-8", "RBC", 4.52, "million/cumm", 3.8, 4.8), Expected("777-3", "Platelets", 245, "x10^3/uL", 150, 410),
             Expected("2345-7", "Random glucose", 96, "mg%", 70, 140), Expected("3016-3", "TSH", 2.1, "mIU/L", 0.4, 4)],
        ),
        Case(
            "missing_fields",
            lines_pdf([[
                "Community Health Lab", "Hemoglobin 11.8 g/dL", "Creatinine 0.9", "Uric Acid 6.1 mg/dL 3.5 - 7.2",
            ]]),
            None, None,
            [Expected("718-7", "Hemoglobin", 11.8, "g/dL"), Expected("2160-0", "Creatinine", 0.9), Expected("3084-1", "Uric acid", 6.1, "mg/dL", 3.5, 7.2)],
        ),
        Case(
            "indian_number_grouping",
            lines_pdf([[
                "Heritage Lab", "Patient Name : Mr. Vikram Singh", "Collected On : 01/10/2026",
                "Platelet Count 1,85,000 /cumm 1,50,000 - 4,10,000", "Total Leucocyte Count 11,240 /cumm 4,000 - 11,000",
            ]]),
            "Vikram Singh", "2026-10-01",
            [Expected("777-3", "Platelets", 185000, "/cumm", 150000, 410000), Expected("6690-2", "TLC", 11240, "/cumm", 4000, 11000)],
        ),
        Case(
            "scanned_clean",
            scanned_pdf(["Sunrise Diagnostics Laboratory", "Patient Name : Mr. Ravi Kumar", "Hemoglobin 11.2 g/dL 13.0 - 17.0"]),
            "Ravi Kumar", None, [Expected("718-7", "Hemoglobin", 11.2, "g/dL", 13, 17)], readable_locally=False,
        ),
        Case(
            "scanned_poor",
            scanned_pdf(["Sunrise Diagnostics Laboratory", "Hemoglobin 11.2 g/dL 13.0 - 17.0"], blur=1.6, noise=True),
            None, None, [Expected("718-7", "Hemoglobin", 11.2, "g/dL", 13, 17)], readable_locally=False,
        ),
    ]
