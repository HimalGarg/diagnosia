"""Synthetic test images with known answers. Handwriting-style fonts, noise and tilt stand in for real photos.

They are easier than a real doctor's handwriting, so a good score here proves little. A bad one proves a lot.
Run:  python backend/tests/ai_eval/make_set.py /some/folder"""
import io, json, random, sys
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import numpy as np
random.seed(7); np.random.seed(7)
OUT = sys.argv[1]
F = "/System/Library/Fonts/Supplemental/"

def photo(im, tilt=1.5, blur=0.7, noise=9, quality=62, shade=True):
    im = im.convert("RGB").rotate(tilt, expand=True, fillcolor=(236, 233, 226))
    im = im.filter(ImageFilter.GaussianBlur(blur))
    a = np.asarray(im).astype(np.float32)
    if shade:
        h, w = a.shape[:2]; gx = np.linspace(0.82, 1.0, w)[None, :, None]; gy = np.linspace(0.9, 1.0, h)[:, None, None]; a = a * gx * gy
    a += np.random.normal(0, noise, a.shape)
    out = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)); b = io.BytesIO(); out.save(b, "JPEG", quality=quality); return Image.open(io.BytesIO(b.getvalue()))

def wobble(d, xy, text, font, fill, amount=3):
    x, y = xy
    for ch in text:
        d.text((x, y + random.randint(-amount, amount)), ch, font=font, fill=fill); x += d.textlength(ch, font=font) + random.uniform(-1.5, 1.5)

def prescription(name, font, size, lines, header, notes, follow, tilt, messy=False, bracket=None):
    im = Image.new("RGB", (1100, 1500), (246, 244, 238)); d = ImageDraw.Draw(im)
    big = ImageFont.truetype(F + "Arial Bold.ttf", 34); hand = ImageFont.truetype(font[0], size, index=font[1]) if font[1] is not None else ImageFont.truetype(font[0], size)
    d.text((60, 50), header[0], font=big, fill=(20, 40, 110)); d.text((60, 100), header[1], font=ImageFont.truetype(F + "Arial.ttf", 22), fill=(60, 60, 60)); d.line((60, 150, 1040, 150), fill=(20, 40, 110), width=3)
    d.text((60, 190), "Rx", font=ImageFont.truetype(F + "Times New Roman Bold.ttf", 60), fill=(30, 30, 90))
    y = 280
    top = y
    for line in lines:
        if messy: wobble(d, (110, y), line, hand, (25, 30, 80)); d.line((105, y + size - 6, 105 + random.randint(0, 40), y + size - 4), fill=(25, 30, 80), width=1)
        else: d.text((110, y), line, font=hand, fill=(25, 30, 80))
        y += size + 38
    if bracket: d.arc((770, top, 830, y - 30), 270, 90, fill=(25, 30, 80), width=3); d.text((840, (top + y) // 2 - 30), bracket, font=hand, fill=(25, 30, 80))
    d.text((110, y + 20), notes, font=hand, fill=(25, 30, 80)); d.text((110, y + 90), "F/U: " + follow, font=hand, fill=(25, 30, 80))
    d.text((760, 1380), "Sign", font=hand, fill=(25, 30, 80))
    return photo(im, tilt=tilt)

SETS = [
 ("rx1", (F + "Bradley Hand Bold.ttf", None), 44, ["1) Tab. Azithral 500 mg  1-0-0  x 3 days", "2) Cap. Pan-D 40  1-0-0  BBF x 15 days", "3) Syp. Ascoril LS 5 ml  TDS  x 5 days", "4) Tab. Dolo 650  SOS"], ("Dr. A. Sharma, MBBS, MD", "Reg. No. 48213   Ph: 011-555-0142"), "Viral fever with cough", "12/11/2026", 1.2,
  [("azithral", "1-0-0", "3"), ("pan-d", "1-0-0", "15"), ("ascoril", "tds", "5"), ("dolo", "sos", "")]),
 ("rx2", (F + "Chalkboard.ttc", 0), 40, ["Tab Glycomet GP 1   1-0-1   x 30 d", "Tab Telma 40   OD   x 30 d", "Tab Ecosprin 75   HS   x 30 d", "Tab Atorva 10   HS   x 30 d", "Tab Shelcal 500  BD  x 30 d"], ("Dr. Meera Iyer, MBBS", "Reg. 77120   Diabetes and Heart Clinic"), "T2DM, HTN. Review labs", "30/11/2026", -2.0,
  [("glycomet", "1-0-1", "30"), ("telma", "od", "30"), ("ecosprin", "hs", "30"), ("atorva", "hs", "30"), ("shelcal", "bd", "30")]),
 ("rx3", (F + "Bradley Hand Bold.ttf", None), 38, ["Tab Montair LC  1-0-0  x 10 days", "Tab Levolin  1-1-1 x 5 d", "Cap Omez 20  1-0-0 AC x 10 d", "Syp Cremaffin 15 ml HS", "Tab Zerodol SP  1-0-1 PC x 3 d"], ("Dr. R. Khan MD (Paed)", "Child Care Clinic   Reg 33019"), "Allergic bronchitis", "05/11/2026", 3.0,
  [("montair", "1-0-0", "10"), ("levolin", "1-1-1", "5"), ("omez", "1-0-0", "10"), ("cremaffin", "hs", ""), ("zerodol", "1-0-1", "3")]),
]
SETS += [
 ("rx4", (F + "SnellRoundhand.ttc", 0), 52, ["T. Azee 500   1-0-0   x 3 d", "T. Pantop 40   1-0-0   BBF", "T. Calpol 500   SOS", "Syp. Grilinctus  10 ml  TDS"], ("Dr. S. Banerjee MBBS", "Reg 21877   Salt Lake Clinic"), "Acute bronchitis", "08/11/2026", 2.4,
  [("azee", "1-0-0", "3"), ("pantop", "1-0-0", ""), ("calpol", "sos", ""), ("grilinctus", "tds", "")], True, "x 5 days"),
 ("rx5", (F + "Bradley Hand Bold.ttf", None), 36, ["T. Metformin 500  1-0-1", "T. Amlodipine 5  1-0-0", "T. Rosuvas 10  0-0-1", "T. Cilacar 10   1-0-0", "T. Thyronorm 50 mcg  1-0-0 BBF"], ("Dr. P. Reddy MD (Med)", "Reg. 90113  Madhapur"), "DM + HTN + Hypothyroid", "20/11/2026", -3.2,
  [("metformin", "1-0-1", "30"), ("amlodipine", "1-0-0", "30"), ("rosuvas", "0-0-1", "30"), ("cilacar", "1-0-0", "30"), ("thyronorm", "1-0-0", "30")], True, "x 30 days"),
]
truth = {}
for entry in SETS:
    key, font, size, lines, header, notes, follow, tilt, meds, *extra = entry
    messy, bracket = (extra + [None])[:2] if extra else (False, None)
    prescription(key, font, size, lines, header, notes, follow, tilt, messy=messy, bracket=bracket).save(f"{OUT}/{key}.jpg", quality=70)
    truth[key] = meds

# Lab reports: printed, then photographed.
from reportlab.lib.pagesizes import A4; from reportlab.pdfgen import canvas; import pypdfium2
def lab(key, title, rows, tilt, blur, two_col=False):
    b = io.BytesIO(); c = canvas.Canvas(b, pagesize=A4); c.setFont("Helvetica-Bold", 16); c.drawString(40, 800, title)
    c.setFont("Helvetica", 10); c.drawString(40, 780, "Patient Name : Mrs. Sunita Yadav     Age/Sex : 29 Y / F     Sample Collected : 14/10/2026"); c.line(40, 770, 555, 770)
    c.setFont("Helvetica-Bold", 10); c.drawString(40, 755, "TEST"); c.drawString(250, 755, "RESULT"); c.drawString(330, 755, "UNIT"); c.drawString(410, 755, "REFERENCE RANGE"); y = 735
    c.setFont("Helvetica", 10)
    for name, val, unit, ref, flag in rows:
        c.drawString(40, y, name); c.drawString(250, y, f"{val} {flag}".strip()); c.drawString(330, y, unit); c.drawString(410, y, ref); y -= 19
    c.save(); pdf = pypdfium2.PdfDocument(b.getvalue()); img = pdf[0].render(scale=1.6).to_pil()
    photo(img, tilt=tilt, blur=blur, noise=7, quality=55).save(f"{OUT}/{key}.jpg", quality=70)
    return [{"name": n, "value": v, "unit": u, "ref": r, "flag": f} for n, v, u, r, f in rows]
rowsA = [("Haemoglobin", "10.4", "g/dL", "12.0 - 15.5", "L"), ("Total WBC Count", "8,900", "/cu.mm", "4,000 - 11,000", ""), ("Platelet Count", "1.62", "lakh/cu.mm", "1.5 - 4.1", ""), ("RBC Count", "3.86", "mill/cu.mm", "3.8 - 4.8", ""),
         ("PCV", "32.1", "%", "36 - 46", "L"), ("MCV", "83.2", "fL", "80 - 100", ""), ("MCH", "26.9", "pg", "27 - 33", "L"), ("Fasting Glucose", "92", "mg/dL", "70 - 100", ""), ("HbA1c", "5.4", "%", "4.0 - 5.6", ""),
         ("Serum Creatinine", "0.71", "mg/dL", "0.6 - 1.1", ""), ("TSH", "6.82", "uIU/mL", "0.4 - 4.0", "H"), ("Vitamin B12", "187", "pg/mL", "211 - 911", "L"), ("Serum Ferritin", "9.5", "ng/mL", "13 - 150", "L")]
rowsB = [("S. Cholesterol Total", "212", "mg/dL", "< 200", "H"), ("S. Triglycerides", "148", "mg/dL", "< 150", ""), ("HDL Cholesterol", "41", "mg/dL", "> 50", "L"), ("LDL Cholesterol", "141", "mg/dL", "< 100", "H"), ("SGPT (ALT)", "38", "U/L", "7 - 35", "H"),
         ("SGOT (AST)", "29", "U/L", "8 - 33", ""), ("Alkaline Phosphatase", "96", "U/L", "35 - 105", ""), ("Total Bilirubin", "0.8", "mg/dL", "0.2 - 1.2", ""), ("Blood Urea", "24", "mg/dL", "15 - 40", ""), ("Uric Acid", "5.9", "mg/dL", "2.6 - 6.0", ""), ("Sodium", "139", "mmol/L", "135 - 145", ""), ("Potassium", "4.6", "mmol/L", "3.5 - 5.1", "")]
labs = {"lab1": lab("lab1", "CITY DIAGNOSTICS - Haematology and Biochemistry", rowsA, 1.0, 0.8), "lab2": lab("lab2", "Sunrise Pathology Lab - Lipid, Liver and Kidney", rowsB, -1.8, 1.1)}
rowsC = [("Colour", "Pale Yellow", "", "Pale yellow", ""), ("Protein", "Trace", "", "Nil", "H"), ("Glucose", "Nil", "", "Nil", ""), ("Pus Cells", "6-8", "/hpf", "0 - 5", "H"), ("Epithelial Cells", "2-3", "/hpf", "0 - 5", ""),
         ("Total WBC Count", "11.2", "x10^3/uL", "4.0 - 11.0", "H"), ("Neutrophils", "74", "%", "40 - 75", ""), ("Eosinophils", "6", "%", "1 - 4", "H"), ("CRP", "14.8", "mg/L", "< 5.0", "H"), ("Serum Calcium", "8.2", "mg/dL", "8.6 - 10.2", "L"), ("Bilirubin Direct", "0.3", "mg/dL", "< 0.5", "")]
labs["lab3"] = lab("lab3", "Dr. Patel's Path Lab (NABL) - Urine, Blood and Chemistry", rowsC, 4.5, 1.6)
im3 = Image.open(f"{OUT}/lab3.jpg"); im3.thumbnail((1000, 1000)); a = np.asarray(im3.convert("RGB")).astype(np.float32); h, w = a.shape[:2]
yy, xx = np.mgrid[0:h, 0:w]; glare = 70 * np.exp(-(((xx - w * 0.7) / (w * 0.18)) ** 2 + ((yy - h * 0.3) / (h * 0.15)) ** 2)); a += glare[..., None]
Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).save(f"{OUT}/lab3.jpg", quality=50)
json.dump({"rx": truth, "lab": labs}, open(f"{OUT}/truth.json", "w"), indent=1)
print("made", sorted(truth) + sorted(labs))
