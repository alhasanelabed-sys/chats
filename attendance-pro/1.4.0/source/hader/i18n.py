"""Language of the current request, and names shown in it.

Pages send ``X-Lang: en|ar`` (downloads: ``?lang=``). Names typed in Arabic get an English
form: the English name when one is stored, otherwise an automatic transliteration.
"""
from __future__ import annotations

import contextvars
import functools
import re

LANG: contextvars.ContextVar[str] = contextvars.ContextVar("lang", default="ar")


def lang() -> str:
    return LANG.get()


def pick(ar: str | None, en: str | None = None) -> str:
    """The Arabic or English form for the current request."""
    ar = ar or ""
    if LANG.get() != "en":
        return ar
    if en:
        return en
    return transliterate(ar) if _ARABIC.search(ar) else ar


def split_bilingual(text: str | None) -> tuple[str, str]:
    """'الإدارة العامة / Head Office' -> ('الإدارة العامة', 'Head Office')."""
    text = text or ""
    if " / " in text:
        a, b = (x.strip() for x in text.split(" / ", 1))
        if _ARABIC.search(a) and not _ARABIC.search(b):
            return a, b
        if _ARABIC.search(b) and not _ARABIC.search(a):
            return b, a
    return text, ""


_ARABIC = re.compile(r"[؀-ۿ]")

# Common names, written the way people spell them in English.
_NAMES = {
    "محمد": "Mohammed", "أحمد": "Ahmed", "احمد": "Ahmed", "محمود": "Mahmoud", "علي": "Ali", "عمر": "Omar",
    "عمرو": "Amr", "خالد": "Khaled", "حسن": "Hassan", "حسين": "Hussein", "إبراهيم": "Ibrahim", "ابراهيم": "Ibrahim",
    "يوسف": "Yousef", "مصطفى": "Mustafa", "سامي": "Sami", "سمير": "Samir", "ياسر": "Yasser", "وليد": "Walid",
    "زكريا": "Zakaria", "يحيى": "Yahya", "موسى": "Mousa", "عيسى": "Issa", "إسماعيل": "Ismail", "اسماعيل": "Ismail",
    "سعيد": "Saeed", "رامي": "Rami", "نضال": "Nidal", "جمال": "Jamal", "كمال": "Kamal", "طارق": "Tariq",
    "فادي": "Fadi", "هاني": "Hani", "باسم": "Basem", "بشير": "Bashir", "بلال": "Bilal", "أيمن": "Ayman",
    "ايمن": "Ayman", "أنس": "Anas", "انس": "Anas", "أسامة": "Osama", "اسامة": "Osama", "حمزة": "Hamza",
    "سليمان": "Suleiman", "صالح": "Saleh", "فاطمة": "Fatima", "مريم": "Maryam", "عائشة": "Aisha", "آمنة": "Amna",
    "سارة": "Sara", "هدى": "Huda", "نور": "Nour", "رنا": "Rana", "إيمان": "Eman", "ايمان": "Eman", "أسماء": "Asmaa",
    "اسماء": "Asmaa", "هبة": "Heba", "منى": "Mona", "عبير": "Abeer", "دعاء": "Doaa", "آلاء": "Alaa", "الاء": "Alaa",
    "علاء": "Alaa", "مازن": "Mazen", "ماهر": "Maher", "جبر": "Jaber", "عيد": "Eid", "قاسم": "Qasem", "حمدي": "Hamdi",
    "رزق": "Rizq", "عبده": "Abdo", "نشأت": "Nashaat", "عوض": "Awad", "سالم": "Salem", "ناصر": "Nasser",
    "منصور": "Mansour", "سفيان": "Sufyan", "توفيق": "Tawfiq", "إياد": "Iyad", "اياد": "Iyad", "خضر": "Khader",
    "منير": "Munir", "أشرف": "Ashraf", "اشرف": "Ashraf", "أمجد": "Amjad", "امجد": "Amjad", "جواد": "Jawad",
    "يونس": "Younis", "غزة": "Gaza", "الوسطى": "Middle Area", "الجنوب": "South", "الشمال": "North",
    "خانيونس": "Khan Younis", "رفح": "Rafah", "قسم": "Dept", "جهاز": "Terminal", "موظف": "Employee",
    "اختبار": "Test", "رحمن": "Rahman", "قادر": "Qader", "كريم": "Karim", "رحيم": "Rahim", "عزيز": "Aziz",
    "مجيد": "Majid", "حميد": "Hamid", "سلام": "Salam", "فتاح": "Fattah", "رؤوف": "Raouf", "لطيف": "Latif",
    "حكيم": "Hakim", "جليل": "Jalil", "هادي": "Hadi", "باري": "Bari", "رازق": "Razeq", "منعم": "Monem",
    "أبو": "Abu", "ابو": "Abu", "بن": "bin", "بنت": "bint", "درويش": "Darwish", "الإدارة": "Administration", "العامة": "General", "المقر": "Head", "الرئيسي": "Office",
}
_LETTERS = {
    "ا": "a", "أ": "a", "إ": "i", "آ": "aa", "ب": "b", "ت": "t", "ث": "th", "ج": "j", "ح": "h", "خ": "kh",
    "د": "d", "ذ": "dh", "ر": "r", "ز": "z", "س": "s", "ش": "sh", "ص": "s", "ض": "d", "ط": "t", "ظ": "z",
    "ع": "a", "غ": "gh", "ف": "f", "ق": "q", "ك": "k", "ل": "l", "م": "m", "ن": "n", "ه": "h", "ة": "a",
    "و": "o", "ي": "i", "ى": "a", "ء": "", "ئ": "e", "ؤ": "o", "ـ": "", "ً": "", "ٌ": "",
    "ٍ": "", "َ": "a", "ُ": "u", "ِ": "i", "ّ": "", "ْ": "",
    "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4", "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
}


def _abdul(rest: str) -> str:
    if rest in ("الله", "لله", "اللة"):
        return "Abdullah"
    core = rest[2:] if rest.startswith("ال") else rest
    return "Abdul" + (_NAMES.get(core) or _word(core)).lower()


def _word(w: str) -> str:
    if w in _NAMES:
        return _NAMES[w]
    if w.startswith("عبد") and len(w) > 3:          # عبدالله -> Abdullah, عبدالرحمن -> Abdulrahman
        return _abdul(w[3:])
    prefix = ""
    if w.startswith("ال") and len(w) > 3:
        prefix, w = "Al-", w[2:]
        if w in _NAMES:
            return prefix + _NAMES[w]
    out = []
    for i, ch in enumerate(w):
        if ch == "و":
            out.append("w" if i == 0 else "o")
        elif ch == "ي":
            out.append("y" if i == 0 else "i")
        elif ch == "ه" and i == len(w) - 1 and i > 1:
            out.append("a")                          # ورده -> Warda
        else:
            out.append(_LETTERS.get(ch, ch))
    s = "".join(out)
    # Arabic leaves short vowels out: open the first two consonants (فرحات -> Farhat)
    if len(s) > 2 and s[0] not in "aeiou" and s[1] not in "aeiouh" and s[:2] not in ("sh", "kh", "th", "gh", "dh"):
        s = s[0] + "a" + s[1:]
    return prefix + (s[:1].upper() + s[1:] if s else s)


@functools.lru_cache(maxsize=20000)
def transliterate(text: str) -> str:
    words = (text or "").split()
    out, i = [], 0
    while i < len(words):
        w = words[i]
        if w == "عبد" and i + 1 < len(words):   # عبد الرحمن -> Abdulrahman
            out.append(_abdul(words[i + 1]))
            i += 2
            continue
        out.append(_word(w))
        i += 1
    return " ".join(out)
