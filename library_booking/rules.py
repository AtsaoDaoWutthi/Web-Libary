"""กฎทางธุรกิจทั้งหมดของระบบ: เวลา, โควตา, สถานะโต๊ะ, การจอง, เช็คอิน และยกเลิกอัตโนมัติ

ทุกฟังก์ชันรับ connection ของ SQLite เข้ามา จึงทดสอบแยกจากหน้าเว็บได้
"""
import hashlib
import secrets
from datetime import date as Date, datetime, timedelta

import config

ACTIVE = ("booked", "checked_in")            # การจองที่ยังถือโต๊ะอยู่
COUNTS_QUOTA = ("booked", "checked_in", "done")  # สถานะที่นับเป็นโควตาที่ใช้ไป


class BookingError(Exception):
    """ข้อผิดพลาดที่ต้องแจ้งผู้ใช้ (ข้อความเป็นภาษาไทย)"""


# =====================================================================
# 1) นาฬิกาของระบบ (เลื่อนได้ในโหมดทดสอบ)
# =====================================================================
def get_offset(db):
    row = db.execute("SELECT value FROM settings WHERE key='clock_offset'").fetchone()
    return float(row["value"]) if row else 0.0


def set_offset(db, seconds):
    db.execute(
        "INSERT INTO settings(key, value) VALUES('clock_offset', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(float(seconds)),),
    )


class Now:
    """ภาพรวมเวลาปัจจุบันที่ใช้ตลอดหนึ่ง request"""

    def __init__(self, dt):
        self.dt = dt
        self.today = dt.date().isoformat()
        self.minute = dt.hour * 60 + dt.minute + dt.second / 60 + dt.microsecond / 60_000_000
        self.epoch_ms = int(dt.timestamp() * 1000)

    def epoch_ms_at(self, day, minute):
        """เวลาแบบ epoch (มิลลิวินาที) ของวัน day ที่นาที minute"""
        d = Date.fromisoformat(day)
        moment = datetime(d.year, d.month, d.day) + timedelta(minutes=minute)
        return int(moment.timestamp() * 1000)


def current_time(db):
    return Now(datetime.now() + timedelta(seconds=get_offset(db)))


def set_clock_to(db, minute):
    """ตั้งนาฬิกาจำลองให้ตอนนี้เป็นเวลา minute ของวันจริง"""
    real = datetime.now()
    target = datetime(real.year, real.month, real.day) + timedelta(minutes=minute)
    set_offset(db, (target - real).total_seconds())


# =====================================================================
# 2) ตัวช่วยเรื่องเวลา
# =====================================================================
def period_of(minute):
    for p in config.PERIODS:
        if p["start"] <= minute < p["end"]:
            return p
    return None


def overlaps(a1, a2, b1, b2):
    return a1 < b2 and b1 < a2


def bookable_dates(now):
    base = now.dt.date()
    return [(base + timedelta(days=i)).isoformat() for i in range(config.BOOK_AHEAD_DAYS)]


def start_options(day, period, now):
    """เวลาเริ่มที่ยังเลือกได้ในช่วง (period) ของวัน day"""
    earliest = now.minute if day == now.today else 0
    return [m for m in range(period["start"], period["end"], config.STEP) if m >= earliest]


# =====================================================================
# 3) ยกเลิกอัตโนมัติ (No-show) และปิดการใช้งานที่หมดเวลา
# =====================================================================
def sweep(db, now):
    """เปลี่ยนสถานะตามเวลา: ไม่เช็คอินใน 15 นาที → noshow (โต๊ะว่าง), ใช้ครบเวลา → done
    คืนค่ารายการการจองที่เพิ่งถูกยกเลิกอัตโนมัติ"""
    expired_sql = (
        "status='booked' AND (date < ? OR (date = ? AND start_min + ? <= ?))"
    )
    params = (now.today, now.today, config.CHECKIN_WINDOW, now.minute)
    released = db.execute(f"SELECT * FROM bookings WHERE {expired_sql}", params).fetchall()
    if released:
        db.execute(f"UPDATE bookings SET status='noshow' WHERE {expired_sql}", params)
    db.execute(
        "UPDATE bookings SET status='done', checkout_at=COALESCE(checkout_at, printf('%02d:%02d', end_min/60, end_min%60)) "
        "WHERE status='checked_in' AND (date < ? OR (date = ? AND end_min <= ?))",
        (now.today, now.today, now.minute),
    )
    return released


def display_status(b, now):
    """สถานะสำหรับแสดงผล: booked แยกเป็น upcoming / ready"""
    if b["status"] == "booked":
        if b["date"] == now.today and now.minute >= b["start_min"]:
            return "ready"
        return "upcoming"
    if b["status"] == "checked_in":
        return "in"
    return b["status"]


# =====================================================================
# 4) โควตา
# =====================================================================
def used_minutes(db, student_id, day):
    row = db.execute(
        f"SELECT COALESCE(SUM(end_min - start_min), 0) AS m FROM bookings "
        f"WHERE student_id=? AND date=? AND status IN ({','.join('?' * len(COUNTS_QUOTA))})",
        (student_id, day, *COUNTS_QUOTA),
    ).fetchone()
    return int(row["m"])


def left_minutes(db, student_id, day):
    return max(0, config.DAY_LIMIT - used_minutes(db, student_id, day))


def ban_info(db, student_id, now):
    """คืนค่า (จำนวนครั้งที่ไม่มาใน 30 วัน, วันที่สิ้นสุดการระงับหรือ None)"""
    since = (now.dt.date() - timedelta(days=30)).isoformat()
    rows = db.execute(
        "SELECT date FROM bookings WHERE student_id=? AND status='noshow' AND date>=? ORDER BY date DESC",
        (student_id, since),
    ).fetchall()
    count = len(rows)
    if count >= config.MAX_NOSHOW:
        until = Date.fromisoformat(rows[0]["date"]) + timedelta(days=config.BAN_DAYS)
        if now.dt.date() < until:
            return count, until.isoformat()
    return count, None


# =====================================================================
# 5) สถานะโต๊ะ
# =====================================================================
def user_clash(db, student_id, day, start, end):
    return db.execute(
        "SELECT * FROM bookings WHERE student_id=? AND date=? AND status IN ('booked','checked_in') "
        "AND start_min < ? AND ? < end_min LIMIT 1",
        (student_id, day, end, start),
    ).fetchone()


def max_length_from(db, student_id, day, start):
    """ระยะเวลายาวสุดที่เลือกได้จากเวลาเริ่ม: ไม่เลยเวลาปิดของช่วง และไม่ชนการจองเดิมของตัวเอง"""
    p = period_of(start)
    if p is None:
        return 0
    limit = min(p["end"], start + config.MAX_DURATION)
    row = db.execute(
        "SELECT MIN(start_min) AS s FROM bookings WHERE student_id=? AND date=? "
        "AND status IN ('booked','checked_in') AND start_min > ?",
        (student_id, day, start),
    ).fetchone()
    if row["s"] is not None:
        limit = min(limit, row["s"])
    return limit - start


def seat_states(db, floor, day, start, end, student_id):
    """สถานะของทุกโต๊ะในชั้น สำหรับช่วงเวลาที่เลือก
    ok = ว่าง (เขียว), busy = มีผู้จอง (แดง), occ = กำลังใช้งาน (เหลือง)"""
    rows = db.execute(
        "SELECT seat, status, student_id FROM bookings WHERE floor=? AND date=? "
        "AND status IN ('booked','checked_in') AND start_min < ? AND ? < end_min",
        (floor, day, end, start),
    ).fetchall()
    states = {s["id"]: {"st": "ok", "mine": False} for s in config.SEATS}
    for r in rows:
        cur = states[r["seat"]]
        st = "occ" if r["status"] == "checked_in" else "busy"
        if cur["st"] != "occ":
            cur["st"] = st
        if r["student_id"] == student_id:
            cur["mine"] = True
    return states


def free_count(db, floor, day, start, end):
    taken = db.execute(
        "SELECT COUNT(DISTINCT seat) AS n FROM bookings WHERE floor=? AND date=? "
        "AND status IN ('booked','checked_in') AND start_min < ? AND ? < end_min",
        (floor, day, end, start),
    ).fetchone()["n"]
    return len(config.SEATS) - taken


# =====================================================================
# 6) ตรวจสอบช่วงเวลาที่เลือก
# =====================================================================
def check_time_choice(day, start, length, now):
    """ตรวจรูปแบบวัน/เวลา คืนค่า period หรือโยน BookingError"""
    if day not in bookable_dates(now):
        raise BookingError("เลือกวันได้ตั้งแต่วันนี้ถึงอีก 6 วันข้างหน้า")
    period = period_of(start)
    if period is None or (start - period["start"]) % config.STEP:
        raise BookingError("เวลาเริ่มไม่อยู่ในช่วงเปิดให้บริการ")
    if length < config.STEP or length % config.STEP:
        raise BookingError("ระยะเวลาต้องเป็นช่วงละ 30 นาที")
    if start + length > period["end"]:
        raise BookingError(f"{period['label']}ปิดเวลา {period['end'] // 60:02d}:{period['end'] % 60:02d} เลือกระยะเวลาให้สั้นลง")
    if day == now.today and start < now.minute:
        raise BookingError("เวลาที่เลือกผ่านไปแล้ว")
    return period


# =====================================================================
# 7) สร้าง / เช็คอิน / เช็คเอาท์ / ยกเลิก
# =====================================================================
def new_code(db):
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        code = "BK" + "".join(secrets.choice(alphabet) for _ in range(6))
        if not db.execute("SELECT 1 FROM bookings WHERE code=?", (code,)).fetchone():
            return code


def create_booking(db, student_id, floor, seat, day, start, length, now):
    if floor not in config.FLOOR_NUMBERS or seat not in config.SEAT_IDS:
        raise BookingError("ไม่พบโต๊ะที่เลือก")
    check_time_choice(day, start, length, now)
    end = start + length
    db.execute("BEGIN IMMEDIATE")  # ล็อกกันจองชนกันในเวลาเดียวกัน
    try:
        _, banned_until = ban_info(db, student_id, now)
        if banned_until:
            raise BookingError(f"บัญชีถูกระงับสิทธิ์การจองถึง {banned_until} เพราะไม่มาเช็คอินครบ {config.MAX_NOSHOW} ครั้ง")
        left = left_minutes(db, student_id, day)
        if length > left:
            raise BookingError("คุณใช้โควตาเกินกำหนด (สูงสุด 3 ชม./วัน)")
        clash = user_clash(db, student_id, day, start, end)
        if clash:
            raise BookingError(f"คุณจองโต๊ะ {clash['seat']} ในช่วงเวลาที่ซ้อนกันไว้แล้ว")
        taken = db.execute(
            "SELECT 1 FROM bookings WHERE floor=? AND seat=? AND date=? AND status IN ('booked','checked_in') "
            "AND start_min < ? AND ? < end_min",
            (floor, seat, day, end, start),
        ).fetchone()
        if taken:
            raise BookingError(f"โต๊ะ {seat} ถูกจองไปแล้วในช่วงเวลานี้ เลือกโต๊ะอื่น")
        code = new_code(db)
        db.execute(
            "INSERT INTO bookings(code, student_id, floor, seat, date, start_min, end_min, status, created_at) "
            "VALUES(?,?,?,?,?,?,?, 'booked', ?)",
            (code, student_id, floor, seat, day, start, end, now.dt.isoformat(timespec="seconds")),
        )
        db.execute("COMMIT")
        return code
    except Exception:
        db.execute("ROLLBACK")
        raise


def get_own_booking(db, code, student_id):
    b = db.execute("SELECT * FROM bookings WHERE code=?", (code,)).fetchone()
    if b is None or b["student_id"] != student_id:
        raise BookingError("ไม่พบการจองนี้")
    return b


def check_in(db, code, student_id, method, value, now):
    b = get_own_booking(db, code, student_id)
    if method == "qr":
        if (value or "").strip().upper() != b["code"]:
            raise BookingError("QR Code ไม่ถูกต้อง")
    elif method == "id":
        value = (value or "").strip()
        if not (value.isdigit() and len(value) == 11):
            raise BookingError("รหัสนักศึกษาต้องเป็นตัวเลข 11 หลัก")
        if value != b["student_id"]:
            raise BookingError("รหัสนักศึกษาไม่ตรงกับเจ้าของการจอง")
    else:
        raise BookingError("วิธีเช็คอินไม่ถูกต้อง")

    if b["status"] == "checked_in":
        raise BookingError("เช็คอินไปแล้ว")
    if b["status"] != "booked":
        raise BookingError("การจองนี้ถูกยกเลิกหรือหมดเวลาเช็คอินแล้ว")
    if b["date"] != now.today or now.minute < b["start_min"]:
        raise BookingError(f"ยังไม่ถึงเวลาเช็คอิน เช็คอินได้ตั้งแต่ {b['start_min'] // 60:02d}:{b['start_min'] % 60:02d}")
    if now.minute >= b["start_min"] + config.CHECKIN_WINDOW:
        raise BookingError("เลยเวลาเช็คอิน 15 นาทีแล้ว การจองถูกยกเลิกอัตโนมัติ")
    db.execute(
        "UPDATE bookings SET status='checked_in', checkin_at=? WHERE id=?",
        (now.dt.strftime("%H:%M"), b["id"]),
    )
    return b


def check_out(db, code, student_id, now):
    b = get_own_booking(db, code, student_id)
    if b["status"] != "checked_in":
        raise BookingError("เช็คเอาท์ได้เฉพาะการจองที่กำลังใช้งาน")
    db.execute("UPDATE bookings SET status='done', checkout_at=? WHERE id=?", (now.dt.strftime("%H:%M"), b["id"]))
    return b


def cancel(db, code, student_id):
    b = get_own_booking(db, code, student_id)
    if b["status"] != "booked":
        raise BookingError("ยกเลิกได้เฉพาะการจองที่ยังไม่เช็คอิน")
    db.execute("UPDATE bookings SET status='cancelled' WHERE id=?", (b["id"],))
    return b


# =====================================================================
# 8) ข้อมูลตัวอย่าง: นักศึกษาคนอื่นจองโต๊ะไว้บางส่วน
# =====================================================================
DEMO_NAMES = ["กานดา", "ธนพล", "ปิยะ", "วรรณา", "ณัฐวุฒิ", "สุภาพร", "อนุชา", "พิมพ์ชนก",
              "ภูมิ", "จิราพร", "ศุภชัย", "เมธินี", "ชยพล", "ปวีณา", "กิตติ", "รัชนี"]


def _hash(text):
    return int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF


def ensure_demo_students(db):
    for i, name in enumerate(DEMO_NAMES):
        db.execute("INSERT OR IGNORE INTO students(id, name, is_demo) VALUES(?,?,1)", (f"6601100{i:04d}", name))


def ensure_seeded(db, day, now):
    """สร้างการจองของนักศึกษาจำลองสำหรับวัน day ครั้งเดียว (ค่าคงที่จาก hash)"""
    if db.execute("SELECT 1 FROM seeded_dates WHERE date=?", (day,)).fetchone():
        return
    ensure_demo_students(db)
    db.execute("BEGIN IMMEDIATE")
    try:
        if db.execute("SELECT 1 FROM seeded_dates WHERE date=?", (day,)).fetchone():
            db.execute("COMMIT")
            return
        load = 0.42 if day == now.today else 0.3
        for f in config.FLOORS:
            for s in config.SEATS:
                for p in config.PERIODS:
                    key = f"{f['n']}|{s['id']}|{day}|{p['id']}"
                    r = _hash(key)
                    if r >= load:
                        continue
                    cells = (p["end"] - p["start"]) // config.STEP
                    length = min(cells, 2 + int(_hash(key + "L") * 5))
                    first = int(_hash(key + "S") * (cells - length + 1))
                    start = p["start"] + first * config.STEP
                    end = start + length * config.STEP
                    busy = db.execute(
                        "SELECT 1 FROM bookings WHERE floor=? AND seat=? AND date=? "
                        "AND status IN ('booked','checked_in') AND start_min < ? AND ? < end_min",
                        (f["n"], s["id"], day, end, start),
                    ).fetchone()
                    if busy:
                        continue
                    shows_up = r < load * 0.75
                    status, cin = "booked", None
                    if day == now.today and now.minute >= start:
                        if shows_up:
                            status, cin = ("done" if now.minute >= end else "checked_in"), f"{start // 60:02d}:{start % 60 + 3:02d}"
                        elif now.minute >= start + config.CHECKIN_WINDOW:
                            status = "noshow"
                    student = f"6601100{int(_hash(key + 'U') * len(DEMO_NAMES)):04d}"
                    db.execute(
                        "INSERT INTO bookings(code, student_id, floor, seat, date, start_min, end_min, status, checkin_at, created_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (new_code(db), student, f["n"], s["id"], day, start, end, status, cin,
                         now.dt.isoformat(timespec="seconds")),
                    )
        db.execute("INSERT INTO seeded_dates(date) VALUES(?)", (day,))
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise


def bootstrap(db, now):
    """สร้างผู้ใช้ตัวอย่างและประวัติการจองตัวอย่างในการเปิดระบบครั้งแรก"""
    if db.execute("SELECT 1 FROM students WHERE id=?", (config.DEMO_STUDENT["id"],)).fetchone():
        return
    sid = config.DEMO_STUDENT["id"]
    db.execute("INSERT INTO students(id, name) VALUES(?,?)", (sid, config.DEMO_STUDENT["name"]))
    stamp = now.dt.isoformat(timespec="seconds")

    def add(day, floor, seat, start, end, status, cin=None, cout=None):
        db.execute(
            "INSERT INTO bookings(code, student_id, floor, seat, date, start_min, end_min, status, checkin_at, checkout_at, created_at, is_sample) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,1)",
            (new_code(db), sid, floor, seat, day, start, end, status, cin, cout, stamp),
        )

    # การจองที่กำลังจะถึง: ครึ่งชั่วโมงถัดไปของวันนี้ ยาว 2 ชม.
    day, start = now.today, (int(now.minute) // config.STEP + 1) * config.STEP
    p = period_of(start) or next((x for x in config.PERIODS if x["start"] > start), None)
    if p and start < p["start"]:
        start = p["start"]
    if not p or start + config.STEP > p["end"]:
        day, p, start = (now.dt.date() + timedelta(days=1)).isoformat(), config.PERIODS[0], 8 * 60 + 30
    add(day, 3, "B-07", start, min(start + 120, p["end"]), "booked")
    # ประวัติ
    add((now.dt.date() - timedelta(days=1)).isoformat(), 2, "A-03", 13 * 60, 15 * 60 + 30, "done", "13:06", "15:25")
    add((now.dt.date() - timedelta(days=3)).isoformat(), 4, "C-02", 9 * 60, 11 * 60, "noshow")
