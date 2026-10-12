"""ค่าตั้งต้นของระบบจองที่นั่งห้องสมุด (แก้ไขกฎต่าง ๆ ได้ที่ไฟล์นี้ที่เดียว)"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.environ.get("LIBRARY_DB", os.path.join(BASE_DIR, "instance", "library.db"))

# ---------------- กฎการจอง ----------------
DAY_LIMIT = 180          # โควตาสูงสุดต่อวัน (นาที) = 3 ชั่วโมง
CHECKIN_WINDOW = 15      # ต้องเช็คอินภายใน 15 นาทีนับจากเวลาเริ่ม
STEP = 30                # เลือกเวลาทีละ 30 นาที
BOOK_AHEAD_DAYS = 7      # จองล่วงหน้าได้ 7 วัน (รวมวันนี้)
MAX_DURATION = 240       # ปุ่มระยะเวลายาวสุดที่แสดง (เกิน 3 ชม. จะขึ้นเตือนโควตา)
MAX_NOSHOW = 3           # ไม่มาเช็คอินครบกี่ครั้ง (ใน 30 วัน) จึงถูกระงับ
BAN_DAYS = 7             # ระงับสิทธิ์กี่วัน

# ช่วงเวลาเปิดให้บริการ (นาทีนับจากเที่ยงคืน)
PERIODS = [
    {"id": "m", "label": "ช่วงเช้า", "start": 8 * 60, "end": 12 * 60},
    {"id": "a", "label": "ช่วงบ่าย", "start": 13 * 60, "end": 17 * 60},
    {"id": "e", "label": "ช่วงค่ำ", "start": 18 * 60, "end": 21 * 60},
]

FLOORS = [
    {"n": 2, "name": "โซนทั่วไป"},
    {"n": 3, "name": "โซนอ่านเงียบ"},
    {"n": 4, "name": "โซนกลุ่ม"},
]

SEAT_TYPES = {
    "window": {"name": "โต๊ะติดหน้าต่าง", "icon": "window", "zone": "A", "zone_name": "ติดหน้าต่าง", "cols": 8},
    "power": {"name": "โต๊ะเดี่ยวพร้อมปลั๊กไฟ", "icon": "plug", "zone": "B", "zone_name": "โต๊ะเดี่ยว มีปลั๊กไฟ", "cols": 6},
    "group": {"name": "โต๊ะกลุ่ม 4 คน", "icon": "users", "zone": "C", "zone_name": "โต๊ะกลุ่ม 4 คน", "cols": 4},
    "lamp": {"name": "โต๊ะเงียบพร้อมโคมไฟ", "icon": "lamp", "zone": "D", "zone_name": "มุมเงียบ มีโคมไฟ", "cols": 6},
}

_ZONE_SIZES = [("A", "window", 8), ("B", "power", 12), ("C", "group", 4), ("D", "lamp", 6)]
SEATS = [
    {"id": f"{zone}-{i:02d}", "zone": zone, "type": t}
    for zone, t, count in _ZONE_SIZES
    for i in range(1, count + 1)
]
SEAT_IDS = {s["id"] for s in SEATS}
FLOOR_NUMBERS = {f["n"] for f in FLOORS}

# ข้อความท้ายเว็บ (แก้เป็นชื่อรายวิชา / ชื่อกลุ่มของคุณ)
PROJECT_NAME = "Library Seat Booking"
PROJECT_CREDIT = "โปรเจกต์รายวิชา Web Programming · จัดทำโดย นิสิตกลุ่ม ..."

# ผู้ใช้ตัวอย่างสำหรับทดลองเข้าระบบ
DEMO_STUDENT = {"id": "65011212345", "name": "สมชาย ใจดี"}
