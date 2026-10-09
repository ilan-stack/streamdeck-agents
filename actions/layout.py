"""layout.py < text -> the same keystrokes re-read on the other keyboard layout (Hebrew <-> English).

"zv gucs" -> "זה עובד" and "שלום" -> "akuo". If the text has Hebrew letters it is treated as
English typed on the Hebrew layout, otherwise as Hebrew typed on the English layout.
"""
import sys

EN = "qwertyuiop[]asdfghjkl;'zxcvbnm,./"
HE = "/'קראטוןםפ][שדגכעיחלךף,זסבהנמצתץ."
EN_TO_HE = dict(zip(EN, HE))
EN_TO_HE.update({c.upper(): h for c, h in zip(EN, HE) if c.isalpha()})
HE_TO_EN = dict(zip(HE, EN))

text = sys.stdin.read()
if any("א" <= ch <= "ת" for ch in text):
    out = "".join(HE_TO_EN.get(ch, ch) for ch in text)
else:
    out = "".join(EN_TO_HE.get(ch, ch) for ch in text)
sys.stdout.write(out)
