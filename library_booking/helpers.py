"""ตัวช่วยจัดรูปแบบข้อความภาษาไทย และสร้างภาพ QR Code เป็น SVG"""
from datetime import date as Date

from markupsafe import Markup

import qr

TH_DAY = ["จ.", "อ.", "พ.", "พฤ.", "ศ.", "ส.", "อา."]          # ตาม weekday() ของ Python
TH_DAY_FULL = ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์"]
TH_MON = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


def hhmm(minute):
    m = int(minute)
    return f"{m // 60:02d}:{m % 60:02d}"


def dur(minutes):
    m = max(0, round(minutes))
    h, mm = divmod(m, 60)
    if not h and not mm:
        return "0 นาที"
    parts = []
    if h:
        parts.append(f"{h} ชั่วโมง")
    if mm:
        parts.append(f"{mm} นาที")
    return " ".join(parts)


def dur_short(minutes):
    m = max(0, round(minutes))
    h, mm = divmod(m, 60)
    if h and mm:
        return f"{h} ชม. {mm} น."
    return f"{h} ชม." if h else f"{mm} น."


def thai_date(day, full=False, today=None):
    d = Date.fromisoformat(day)
    if full:
        return f"วัน{TH_DAY_FULL[d.weekday()]}ที่ {d.day} {TH_MON[d.month - 1]} {d.year + 543}"
    if today and day == today:
        return "วันนี้"
    return f"{TH_DAY[d.weekday()]} {d.day} {TH_MON[d.month - 1]}"


def day_chip(day):
    d = Date.fromisoformat(day)
    return {"dow": TH_DAY[d.weekday()], "day": d.day, "mon": TH_MON[d.month - 1]}


def qr_svg(data, label="QR Code"):
    """สร้าง QR Code จริง (สแกนด้วยกล้องมือถือได้) เป็น SVG"""
    return Markup(qr.to_svg(data, label=label))
