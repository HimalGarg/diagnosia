"""The eight lesion types the model separates, in the order it outputs them."""

# (code, name, type). The codes match the ISIC 2019 ground-truth columns.
CONDITIONS = [
    ("MEL", "Melanoma", "malignant"),
    ("BCC", "Basal cell carcinoma", "malignant"),
    ("SCC", "Squamous cell carcinoma", "malignant"),
    ("AK", "Actinic keratosis", "precancerous"),
    ("BKL", "Benign keratosis-like lesion", "benign"),
    ("DF", "Dermatofibroma", "benign"),
    ("NV", "Melanocytic nevus (mole)", "benign"),
    ("VASC", "Vascular lesion", "usually benign"),
]
CODES = [code for code, _, _ in CONDITIONS]

# Lesions that need a doctor's attention soon. The referral score adds these up.
CONCERNING = {code for code, _, kind in CONDITIONS if kind in ("malignant", "precancerous")}
