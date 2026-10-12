"""ระบบจองโต๊ะและพื้นที่นั่งอ่านหนังสือในห้องสมุด (Flask)

รัน:  python app.py   แล้วเปิด http://127.0.0.1:5000
"""
import os
import secrets
from functools import wraps

from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   session, url_for)

import config
import database
import helpers
import rules
from rules import BookingError


# =====================================================================
# สร้างแอป
# =====================================================================
def _secret_key(instance_dir):
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    os.makedirs(instance_dir, exist_ok=True)
    path = os.path.join(instance_dir, "secret_key")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(secrets.token_hex(32))
    with open(path, encoding="utf-8") as fh:
        return fh.read().strip()


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(DATABASE=config.DATABASE)
    if test_config:
        app.config.update(test_config)
    if not app.config.get("SECRET_KEY"):  # Flask ตั้งค่าเริ่มต้นเป็น None ไว้ จึงต้องเช็คแบบนี้
        app.config["SECRET_KEY"] = _secret_key(os.path.join(config.BASE_DIR, "instance"))

    database.init_db(app.config["DATABASE"])
    with app.app_context():
        db = database.get_db()
        rules.bootstrap(db, rules.current_time(db))
        database.close_db()

    app.teardown_appcontext(database.close_db)
    register_template_helpers(app)
    register_routes(app)
    return app


def register_template_helpers(app):
    app.jinja_env.filters.update(
        hhmm=helpers.hhmm, dur=helpers.dur, dur_short=helpers.dur_short,
    )

    @app.context_processor
    def inject():
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        now = g.get("now")
        return {
            "cfg": config,
            "now": now,
            "user": g.get("user"),
            "csrf_token": session["csrf"],
            "thai_date": lambda d, full=False: helpers.thai_date(d, full, now.today if now else None),
            "day_chip": helpers.day_chip,
            "qr_svg": helpers.qr_svg,
            "qr_url": lambda code: url_for("scan_qr", code=code, _external=True),
            "ready_count": g.get("ready_count", 0),
            "clock_shifted": abs(rules.get_offset(database.get_db())) > 1 if now else False,
        }


# =====================================================================
# ตัวช่วยของหน้าเว็บ
# =====================================================================
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login", next=request.full_path))
        return view(*args, **kwargs)
    return wrapped


def to_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def booking_view(b, now):
    """แปลงแถวการจองเป็นข้อมูลพร้อมแสดงผล"""
    seat = next(s for s in config.SEATS if s["id"] == b["seat"])
    return {
        "code": b["code"], "seat": b["seat"], "floor": b["floor"], "date": b["date"],
        "start": b["start_min"], "end": b["end_min"], "length": b["end_min"] - b["start_min"],
        "st": rules.display_status(b, now), "type": config.SEAT_TYPES[seat["type"]],
        "checkin_at": b["checkin_at"], "checkout_at": b["checkout_at"], "sample": b["is_sample"],
        "start_ms": now.epoch_ms_at(b["date"], b["start_min"]),
        "deadline_ms": now.epoch_ms_at(b["date"], b["start_min"] + config.CHECKIN_WINDOW),
        "checkin_until": b["start_min"] + config.CHECKIN_WINDOW,
    }


def user_bookings(db, sid, now, active):
    if active:
        rows = db.execute(
            "SELECT * FROM bookings WHERE student_id=? AND status IN ('booked','checked_in') ORDER BY date, start_min",
            (sid,)).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM bookings WHERE student_id=? AND status NOT IN ('booked','checked_in') "
            "ORDER BY date DESC, start_min DESC LIMIT 30", (sid,)).fetchall()
    return [booking_view(r, now) for r in rows]


def default_choice(db, sid, day, now):
    """เวลาเริ่มแรกที่ว่าง และระยะเวลาเริ่มต้น 2 ชม. (ไม่เกินโควตาที่เหลือ)"""
    for p in config.PERIODS:
        for m in rules.start_options(day, p, now):
            if not rules.user_clash(db, sid, day, m, m + config.STEP):
                left = rules.left_minutes(db, sid, day)
                fit = (left // config.STEP) * config.STEP or config.STEP
                return m, max(config.STEP, min(120, fit, rules.max_length_from(db, sid, day, m)))
    return None, 120


def read_choice(db, sid, now, args):
    """อ่านและทำความสะอาดค่าที่เลือก (วัน เวลาเริ่ม ระยะเวลา) จาก query string"""
    dates = rules.bookable_dates(now)
    day = args.get("date") if args.get("date") in dates else now.today
    if "date" not in args and default_choice(db, sid, day, now)[0] is None:
        # วันนี้หมดเวลาจองแล้ว: ข้ามไปวันถัดไปที่ยังมีเวลาว่าง
        day = next((d for d in dates[1:] if default_choice(db, sid, d, now)[0] is not None), day)
    start, length = to_int(args.get("start")), to_int(args.get("len"))
    p = rules.period_of(start) if start is not None else None
    valid = (
        p is not None and (start - p["start"]) % config.STEP == 0
        and start in rules.start_options(day, p, now)
        and not rules.user_clash(db, sid, day, start, start + config.STEP)
    )
    if not valid:
        start, default_len = default_choice(db, sid, day, now)
        length = length or default_len
        p = rules.period_of(start) if start is not None else None
    if start is None:
        return {"date": day, "start": None, "len": None, "period": None}
    max_len = rules.max_length_from(db, sid, day, start)
    if not length:  # ยังไม่ได้เลือก: ใช้ 2 ชม. หรือเท่าที่โควตาเหลือ
        fit = (rules.left_minutes(db, sid, day) // config.STEP) * config.STEP
        length = min(120, fit) or config.STEP
    length = max(config.STEP, min(max_len, (length // config.STEP) * config.STEP))
    return {"date": day, "start": start, "len": length, "end": start + length, "period": p}


def choice_args(c, **extra):
    data = {"date": c["date"], "start": c["start"], "len": c["len"]}
    data.update(extra)
    return data


# =====================================================================
# เส้นทาง (routes)
# =====================================================================
def register_routes(app):

    @app.before_request
    def load_context():
        if request.endpoint == "static":
            return
        token = request.form.get("csrf_token")
        if request.method == "POST" and (not token or token != session.get("csrf")):
            abort(400, "แบบฟอร์มหมดอายุ กรุณาโหลดหน้าใหม่")
        db = database.get_db()
        g.now = rules.current_time(db)
        released = rules.sweep(db, g.now)
        g.user = None
        sid = session.get("sid")
        if sid:
            g.user = db.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone()
            if g.user is None:
                session.pop("sid", None)
        if g.user:
            for b in released:
                if b["student_id"] == g.user["id"]:
                    flash(f"หมดเวลาเช็คอิน 15 นาที การจองโต๊ะ {b['seat']} ถูกยกเลิกอัตโนมัติ โต๊ะว่างให้คนอื่นแล้ว", "error")
            for day in rules.bookable_dates(g.now):
                rules.ensure_seeded(db, day, g.now)
            g.ready_count = sum(
                1 for b in user_bookings(db, g.user["id"], g.now, active=True) if b["st"] == "ready")

    @app.errorhandler(400)
    def bad_request(err):
        return render_template("error.html", message=getattr(err, "description", "คำขอไม่ถูกต้อง")), 400

    @app.errorhandler(404)
    def not_found(_err):
        return render_template("error.html", message="ไม่พบหน้าที่ต้องการ"), 404

    # ---------------- เข้าสู่ระบบ ----------------
    @app.route("/login", methods=["GET", "POST"])
    def login():
        db = database.get_db()
        error, form = None, {"student_id": "", "name": ""}
        if request.method == "POST":
            form["student_id"] = sid = request.form.get("student_id", "").strip()
            form["name"] = name = request.form.get("name", "").strip()
            row = db.execute("SELECT * FROM students WHERE id=?", (sid,)).fetchone()
            if not (sid.isdigit() and len(sid) == 11):
                error = "รหัสนักศึกษาต้องเป็นตัวเลข 11 หลัก"
            elif row is None and len(name) < 2:
                error = "ยังไม่มีบัญชีนี้ กรอกชื่อเพื่อสร้างบัญชีใหม่"
            elif row is not None and row["is_demo"]:
                error = "รหัสนี้เป็นนักศึกษาจำลองของระบบ ใช้รหัสอื่น"
            else:
                if row is None:
                    db.execute("INSERT INTO students(id, name) VALUES(?,?)", (sid, name))
                session.clear()
                session["sid"] = sid
                nxt = request.args.get("next", "")
                return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("home"))
        return render_template("login.html", error=error, form=form)

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ---------------- หน้าแรก ----------------
    @app.route("/")
    @login_required
    def home():
        db, sid = database.get_db(), g.user["id"]
        upcoming = user_bookings(db, sid, g.now, active=True)
        return render_template(
            "home.html", tab="home", next_booking=upcoming[0] if upcoming else None,
            used=rules.used_minutes(db, sid, g.now.today), left=rules.left_minutes(db, sid, g.now.today))

    # ---------------- จอง ขั้นที่ 1: เลือกวันและเวลา ----------------
    @app.route("/book")
    @login_required
    def book_time():
        db, sid = database.get_db(), g.user["id"]
        c = read_choice(db, sid, g.now, request.args)
        used, left = rules.used_minutes(db, sid, c["date"]), rules.left_minutes(db, sid, c["date"])
        groups, durations = [], []
        if c["start"] is not None:
            for p in config.PERIODS:
                opts = [{"m": m, "taken": bool(rules.user_clash(db, sid, c["date"], m, m + config.STEP))}
                        for m in rules.start_options(c["date"], p, g.now)]
                if opts:
                    groups.append({"p": p, "opts": opts})
            max_len = rules.max_length_from(db, sid, c["date"], c["start"])
            durations = [{"m": m, "over": m > left} for m in range(config.STEP, max_len + 1, config.STEP)]
        over = c["start"] is not None and c["len"] > left
        picked = "len" in request.args or "start" in request.args
        limit_end = c["start"] + max_len if c["start"] is not None else None
        return render_template(
            "book_time.html", limit_end=limit_end, tab="book", step=1, c=c, dates=rules.bookable_dates(g.now),
            groups=groups, durations=durations, used=used, left=left, over=over,
            show_modal=over and picked, choice_args=choice_args)

    # ---------------- จอง ขั้นที่ 2: เลือกโต๊ะ ----------------
    @app.route("/book/seat")
    @login_required
    def book_seat():
        db, sid = database.get_db(), g.user["id"]
        c = read_choice(db, sid, g.now, request.args)
        if c["start"] is None:
            flash("ไม่มีเวลาว่างให้จองในวันนี้แล้ว ลองเลือกวันอื่น", "error")
            return redirect(url_for("book_time"))
        if c["len"] > rules.left_minutes(db, sid, c["date"]):
            return redirect(url_for("book_time", **choice_args(c)))
        floor = to_int(request.args.get("floor"), 3)
        floor = floor if floor in config.FLOOR_NUMBERS else 3
        kind = request.args.get("type", "all")
        kind = kind if kind in config.SEAT_TYPES else "all"
        states = rules.seat_states(db, floor, c["date"], c["start"], c["end"], sid)
        zones = [{"t": t, "key": k, "seats": [s for s in config.SEATS if s["type"] == k]}
                 for k, t in config.SEAT_TYPES.items()]
        free = {f["n"]: rules.free_count(db, f["n"], c["date"], c["start"], c["end"]) for f in config.FLOORS}
        return render_template(
            "book_seat.html", tab="book", step=2, c=c, floor=floor, kind=kind, states=states,
            zones=zones, free=free, choice_args=choice_args)

    # ---------------- จอง ขั้นที่ 3: ยืนยัน ----------------
    @app.route("/book/confirm", methods=["GET", "POST"])
    @login_required
    def book_confirm():
        db, sid = database.get_db(), g.user["id"]
        src = request.form if request.method == "POST" else request.args
        c = read_choice(db, sid, g.now, src)
        floor, seat = to_int(src.get("floor")), src.get("seat", "")
        if c["start"] is None or floor not in config.FLOOR_NUMBERS or seat not in config.SEAT_IDS:
            flash("ข้อมูลการจองไม่ครบ เริ่มเลือกใหม่อีกครั้ง", "error")
            return redirect(url_for("book_time"))

        if request.method == "POST":
            try:
                code = rules.create_booking(db, sid, floor, seat, c["date"], c["start"], c["len"], g.now)
            except BookingError as err:
                flash(str(err), "error")
                return redirect(url_for("book_confirm", **choice_args(c, floor=floor, seat=seat)))
            return redirect(url_for("book_done", code=code))

        used, left = rules.used_minutes(db, sid, c["date"]), rules.left_minutes(db, sid, c["date"])
        warn = None
        clash = rules.user_clash(db, sid, c["date"], c["start"], c["end"])
        _, banned_until = rules.ban_info(db, sid, g.now)
        if c["len"] > left:
            warn = "คุณใช้โควตาเกินกำหนด (สูงสุด 3 ชม./วัน)"
        elif clash:
            warn = f"คุณจองโต๊ะ {clash['seat']} เวลา {helpers.hhmm(clash['start_min'])}–{helpers.hhmm(clash['end_min'])} ซ้อนกับช่วงนี้แล้ว"
        elif rules.seat_states(db, floor, c["date"], c["start"], c["end"], sid)[seat]["st"] != "ok":
            warn = "โต๊ะนี้ไม่ว่างแล้ว กลับไปเลือกโต๊ะอื่น"
        elif banned_until:
            warn = f"บัญชีถูกระงับสิทธิ์การจองถึง {banned_until}"
        s = next(x for x in config.SEATS if x["id"] == seat)
        return render_template(
            "book_confirm.html", tab="book", step=3, c=c, floor=floor, seat=s,
            seat_type=config.SEAT_TYPES[s["type"]], used=used, left=left, warn=warn, choice_args=choice_args)

    @app.route("/book/done/<code>")
    @login_required
    def book_done(code):
        db = database.get_db()
        try:
            b = rules.get_own_booking(db, code, g.user["id"])
        except BookingError:
            abort(404)
        return render_template("book_done.html", tab="book", b=booking_view(b, g.now))

    # ---------------- การจองของฉัน ----------------
    @app.route("/bookings")
    @login_required
    def my_bookings():
        db, sid = database.get_db(), g.user["id"]
        upcoming = user_bookings(db, sid, g.now, active=True)
        noshow, banned_until = rules.ban_info(db, sid, g.now)
        test_target = next((b for b in upcoming if b["date"] == g.now.today and b["st"] in ("upcoming", "ready")), None)
        return render_template(
            "bookings.html", tab="mine", upcoming=upcoming, history=user_bookings(db, sid, g.now, active=False),
            noshow=noshow, banned_until=banned_until, test_target=test_target)

    def back_to():
        nxt = request.form.get("next", "")
        return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("my_bookings"))

    @app.post("/bookings/<code>/checkin")
    @login_required
    def do_checkin(code):
        db = database.get_db()
        try:
            b = rules.check_in(db, code, g.user["id"], request.form.get("method"), request.form.get("value"), g.now)
            flash(f"เช็คอินโต๊ะ {b['seat']} เรียบร้อย ขอให้อ่านหนังสืออย่างมีสมาธิ", "ok")
        except BookingError as err:
            flash(str(err), "error")
        return back_to()

    @app.route("/scan/<code>")
    @login_required
    def scan_qr(code):
        """ปลายทางของ QR Code: สแกนด้วยกล้องมือถือ (ที่ล็อกอินบัญชีเดียวกัน) เพื่อเช็คอิน"""
        db = database.get_db()
        try:
            b = rules.check_in(db, code, g.user["id"], "qr", code, g.now)
            flash(f"สแกน QR สำเร็จ เช็คอินโต๊ะ {b['seat']} เรียบร้อย", "ok")
        except BookingError as err:
            flash(str(err), "error")
        return redirect(url_for("my_bookings"))

    @app.post("/bookings/<code>/checkout")
    @login_required
    def do_checkout(code):
        db = database.get_db()
        try:
            b = rules.check_out(db, code, g.user["id"], g.now)
            flash(f"เช็คเอาท์โต๊ะ {b['seat']} แล้ว โต๊ะว่างให้คนถัดไป", "ok")
        except BookingError as err:
            flash(str(err), "error")
        return back_to()

    @app.post("/bookings/<code>/cancel")
    @login_required
    def do_cancel(code):
        db = database.get_db()
        try:
            b = rules.cancel(db, code, g.user["id"])
            flash(f"ยกเลิกการจองโต๊ะ {b['seat']} แล้ว คืนโควตา {helpers.dur(b['end_min'] - b['start_min'])}", "ok")
        except BookingError as err:
            flash(str(err), "error")
        return back_to()

    # ---------------- โหมดทดสอบ: เลื่อนนาฬิกาของระบบ ----------------
    @app.post("/demo/clock")
    @login_required
    def demo_clock():
        db = database.get_db()
        action = request.form.get("action")
        if action == "real":
            rules.set_offset(db, 0)
            flash("กลับมาใช้เวลาจริงแล้ว", "ok")
        elif action == "set":
            raw = request.form.get("time", "")
            try:
                h, m = (int(x) for x in raw.split(":"))
                if not (0 <= h < 24 and 0 <= m < 60):
                    raise ValueError
            except ValueError:
                flash("รูปแบบเวลาไม่ถูกต้อง", "error")
                return back_to()
            rules.set_clock_to(db, h * 60 + m)
            flash(f"ตั้งเวลาจำลองเป็น {raw} แล้ว", "ok")
        elif action in ("start", "late"):
            b = db.execute("SELECT * FROM bookings WHERE code=? AND student_id=?",
                           (request.form.get("code"), g.user["id"])).fetchone()
            if b is None:
                flash("ไม่พบการจองที่จะทดสอบ", "error")
            else:
                target = b["start_min"] if action == "start" else b["start_min"] + config.CHECKIN_WINDOW - 0.5
                rules.set_clock_to(db, target)
                flash("เลื่อนเวลาแล้ว " + ("นาฬิกาเช็คอิน 15 นาทีเริ่มนับ" if action == "start" else "เหลือ 30 วินาทีก่อนหมดเวลาเช็คอิน"), "ok")
        return back_to()


if __name__ == "__main__":
    # ตั้ง HOST=0.0.0.0 เพื่อให้มือถือในวง Wi-Fi เดียวกันเปิดเว็บและสแกน QR ได้
    create_app().run(debug=True, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 5000)))
