"""ทดสอบกฎและหน้าเว็บ:  python -m unittest discover -s tests -v"""
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import database  # noqa: E402
import rules  # noqa: E402
from app import create_app  # noqa: E402
from rules import BookingError, Now  # noqa: E402


def at(hour, minute=0, day_offset=0):
    base = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=day_offset)
    return Now(base + timedelta(hours=hour, minutes=minute))


class RulesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = os.path.join(self.tmp.name, "t.db")
        database.init_db(path)
        self.db = database.connect(path)
        self.db.execute("INSERT INTO students(id, name) VALUES('65000000001','เอ'), ('65000000002','บี')")
        self.day = at(7).today

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def book(self, sid, seat, start, length, now=None, floor=3):
        return rules.create_booking(self.db, sid, floor, seat, self.day, start, length, now or at(7))

    def test_quota_three_hours_per_day(self):
        self.book("65000000001", "B-01", 8 * 60, 120)
        with self.assertRaisesRegex(BookingError, "โควตาเกินกำหนด"):
            self.book("65000000001", "B-02", 13 * 60, 90)
        self.book("65000000001", "B-02", 13 * 60, 60)
        self.assertEqual(rules.left_minutes(self.db, "65000000001", self.day), 0)

    def test_single_booking_over_three_hours_rejected(self):
        with self.assertRaisesRegex(BookingError, "โควตาเกินกำหนด"):
            self.book("65000000001", "B-01", 8 * 60, 210)

    def test_cancel_returns_quota(self):
        code = self.book("65000000001", "B-01", 8 * 60, 180)
        rules.cancel(self.db, code, "65000000001")
        self.assertEqual(rules.left_minutes(self.db, "65000000001", self.day), 180)

    def test_seat_cannot_be_double_booked(self):
        self.book("65000000001", "A-01", 8 * 60, 120)
        with self.assertRaisesRegex(BookingError, "ถูกจองไปแล้ว"):
            self.book("65000000002", "A-01", 9 * 60, 60)
        self.book("65000000002", "A-01", 10 * 60, 60)  # ต่อท้ายได้

    def test_user_cannot_overlap_self(self):
        self.book("65000000001", "A-01", 8 * 60, 60)
        with self.assertRaisesRegex(BookingError, "ซ้อนกัน"):
            self.book("65000000001", "A-02", 8 * 60 + 30, 60)

    def test_period_end_and_past_time(self):
        with self.assertRaises(BookingError):
            self.book("65000000001", "A-01", 11 * 60 + 30, 60)  # เลยเวลาปิดช่วงเช้า
        with self.assertRaisesRegex(BookingError, "ผ่านไปแล้ว"):
            self.book("65000000001", "A-01", 8 * 60, 60, now=at(9))

    def test_checkin_window_and_noshow_release(self):
        code = self.book("65000000001", "B-05", 18 * 60, 120)
        with self.assertRaisesRegex(BookingError, "ยังไม่ถึงเวลา"):
            rules.check_in(self.db, code, "65000000001", "id", "65000000001", at(17, 59))
        states = rules.seat_states(self.db, 3, self.day, 18 * 60, 20 * 60, "x")
        self.assertEqual(states["B-05"]["st"], "busy")  # สีแดง
        released = rules.sweep(self.db, at(18, 15))
        self.assertEqual([r["code"] for r in released], [code])
        states = rules.seat_states(self.db, 3, self.day, 18 * 60, 20 * 60, "x")
        self.assertEqual(states["B-05"]["st"], "ok")  # กลับเป็นสีเขียว
        with self.assertRaises(BookingError):
            rules.check_in(self.db, code, "65000000001", "qr", code, at(18, 16))

    def test_checkin_by_id_and_qr(self):
        c1 = self.book("65000000001", "B-05", 18 * 60, 60)
        c2 = self.book("65000000002", "B-06", 18 * 60, 60)
        with self.assertRaisesRegex(BookingError, "ไม่ตรง"):
            rules.check_in(self.db, c1, "65000000001", "id", "65000000002", at(18, 5))
        rules.check_in(self.db, c1, "65000000001", "id", "65000000001", at(18, 5))
        rules.check_in(self.db, c2, "65000000002", "qr", c2, at(18, 14, 0))
        rules.sweep(self.db, at(18, 20))
        st = rules.seat_states(self.db, 3, self.day, 18 * 60, 19 * 60, "x")
        self.assertEqual(st["B-05"]["st"], "occ")  # สีเหลือง
        rules.check_out(self.db, c1, "65000000001", at(18, 40))
        self.assertEqual(rules.seat_states(self.db, 3, self.day, 18 * 60, 19 * 60, "x")["B-05"]["st"], "ok")

    def test_ban_after_three_noshows(self):
        for i in range(3):
            day = (datetime.now() - timedelta(days=i + 1)).date().isoformat()
            self.db.execute(
                "INSERT INTO bookings(code, student_id, floor, seat, date, start_min, end_min, status, created_at) "
                "VALUES(?, '65000000001', 2, 'A-01', ?, 480, 540, 'noshow', 'x')", (f"BKX{i}", day))
        with self.assertRaisesRegex(BookingError, "ระงับสิทธิ์"):
            self.book("65000000001", "A-02", 8 * 60, 60)

    def test_demo_seed_is_consistent(self):
        rules.ensure_seeded(self.db, self.day, at(7))
        rules.ensure_seeded(self.db, self.day, at(7))  # ครั้งที่สองต้องไม่เพิ่มซ้ำ
        rows = self.db.execute(
            "SELECT a.id FROM bookings a JOIN bookings b ON a.id<b.id AND a.floor=b.floor AND a.seat=b.seat "
            "AND a.date=b.date AND a.start_min<b.end_min AND b.start_min<a.end_min "
            "WHERE a.status IN ('booked','checked_in') AND b.status IN ('booked','checked_in')").fetchall()
        self.assertEqual(rows, [])


class QRTest(unittest.TestCase):
    def test_matrix_shape(self):
        import qr
        m = qr.encode("http://127.0.0.1:5000/scan/BKABCDEF")
        self.assertEqual(len(m), 29)  # เวอร์ชัน 3
        self.assertTrue(all(len(r) == 29 for r in m))

    def test_decodes_with_opencv_if_available(self):
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest("ไม่มี OpenCV")
        import qr
        text = "http://192.168.1.10:5000/scan/BKXY7Z2Q"
        m = qr.encode(text)
        n, s = len(m), 8
        img = np.full(((n + 8) * s, (n + 8) * s), 255, np.uint8)
        for y, row in enumerate(m):
            for x, dark in enumerate(row):
                if dark:
                    img[(y + 4) * s:(y + 5) * s, (x + 4) * s:(x + 5) * s] = 0
        self.assertEqual(cv2.QRCodeDetector().detectAndDecode(img)[0], text)


class WebTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app({"DATABASE": os.path.join(self.tmp.name, "w.db"), "SECRET_KEY": "test", "TESTING": True})
        self.client = self.app.test_client()
        self.tomorrow = (datetime.now() + timedelta(days=1)).date().isoformat()

    def tearDown(self):
        self.tmp.cleanup()

    def csrf(self, html):
        return re.search(r'name="csrf_token" value="([0-9a-f]+)"', html).group(1)

    def login(self):
        page = self.client.get("/login").get_data(as_text=True)
        r = self.client.post("/login", data={"student_id": config.DEMO_STUDENT["id"], "csrf_token": self.csrf(page)})
        self.assertEqual(r.status_code, 302)

    def test_app_starts_without_explicit_secret_key(self):
        app = create_app({"DATABASE": os.path.join(self.tmp.name, "k.db")})
        self.assertTrue(app.config["SECRET_KEY"])
        self.assertEqual(app.test_client().get("/login").status_code, 200)

    def test_requires_login(self):
        self.assertIn("/login", self.client.get("/").headers["Location"])

    def test_bad_student_id(self):
        page = self.client.get("/login").get_data(as_text=True)
        r = self.client.post("/login", data={"student_id": "123", "csrf_token": self.csrf(page)})
        self.assertIn("11 หลัก", r.get_data(as_text=True))

    def test_post_without_csrf_rejected(self):
        self.login()
        self.assertEqual(self.client.post("/logout").status_code, 400)

    def test_all_pages_render(self):
        self.login()
        for url in ["/", "/book", f"/book?date={self.tomorrow}&start=480&len=60",
                    f"/book/seat?date={self.tomorrow}&start=480&len=60&floor=2&type=power",
                    f"/book/confirm?date={self.tomorrow}&start=480&len=60&floor=2&seat=A-01", "/bookings"]:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)

    def test_over_quota_shows_modal(self):
        self.login()
        html = self.client.get(f"/book?date={self.tomorrow}&start=780&len=210").get_data(as_text=True)
        self.assertIn("คุณใช้โควตาเกินกำหนด (สูงสุด 3 ชม./วัน)", html)
        self.assertIn("data-autoopen", html)
        # ขั้นต่อไปถูกบล็อก
        r = self.client.get(f"/book/seat?date={self.tomorrow}&start=780&len=210")
        self.assertEqual(r.status_code, 302)

    def test_full_booking_flow(self):
        self.login()
        with self.app.app_context():
            db = database.get_db()
            rules.ensure_seeded(db, self.tomorrow, rules.current_time(db))
            states = rules.seat_states(db, 2, self.tomorrow, 13 * 60, 14 * 60, config.DEMO_STUDENT["id"])
            seat = next(k for k, v in states.items() if v["st"] == "ok")
        page = self.client.get(f"/book/confirm?date={self.tomorrow}&start=780&len=60&floor=2&seat={seat}").get_data(as_text=True)
        self.assertIn("อย่าลืมเช็คอินภายใน 15 นาที", page)
        r = self.client.post("/book/confirm", data={"date": self.tomorrow, "start": 780, "len": 60, "floor": 2,
                                                    "seat": seat, "csrf_token": self.csrf(page)})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/book/done/", r.headers["Location"])
        done = self.client.get(r.headers["Location"]).get_data(as_text=True)
        self.assertIn("จองสำเร็จ", done)
        mine = self.client.get("/bookings").get_data(as_text=True)
        self.assertIn(seat, mine)
        with self.app.app_context():
            st = rules.seat_states(database.get_db(), 2, self.tomorrow, 780, 840, config.DEMO_STUDENT["id"])[seat]
        self.assertEqual((st["st"], st["mine"]), ("busy", True))  # โต๊ะที่จองแล้วเป็นสีแดง

    def book_today_at_8(self):
        """ตั้งนาฬิกาเป็น 07:00 แล้วจองวันนี้ 08:00–09:00 ผ่านหน้าเว็บ"""
        self.login()
        token = self.csrf(self.client.get("/bookings").get_data(as_text=True))
        self.client.post("/demo/clock", data={"action": "set", "time": "07:00", "csrf_token": token})
        today = datetime.now().date().isoformat()
        with self.app.app_context():
            db = database.get_db()
            rules.ensure_seeded(db, today, rules.current_time(db))
            states = rules.seat_states(db, 4, today, 480, 540, config.DEMO_STUDENT["id"])
            seat = next(k for k, v in states.items() if v["st"] == "ok")
        r = self.client.post("/book/confirm", data={"date": today, "start": 480, "len": 60, "floor": 4,
                                                    "seat": seat, "csrf_token": token})
        self.assertIn("/book/done/", r.headers["Location"])
        return r.headers["Location"].rsplit("/", 1)[1], token

    def test_countdown_then_auto_cancel_via_web(self):
        code, token = self.book_today_at_8()
        page = self.client.get("/bookings").get_data(as_text=True)
        self.assertIn("เช็คอินได้ตั้งแต่", page)
        self.client.post("/demo/clock", data={"action": "start", "code": code, "csrf_token": token})
        page = self.client.get("/bookings").get_data(as_text=True)
        self.assertIn(f'data-countdown=', page)
        self.assertIn("เช็คอินเลย", page)
        self.client.post("/demo/clock", data={"action": "set", "time": "08:16", "csrf_token": token})
        r = self.client.get("/bookings")
        page = r.get_data(as_text=True)
        self.assertIn("ถูกยกเลิกอัตโนมัติ", page)       # แจ้งเตือน
        self.assertIn("p-no", page)                      # ประวัติ: ยกเลิกอัตโนมัติ
        with self.app.app_context():
            b = database.get_db().execute("SELECT * FROM bookings WHERE code=?", (code,)).fetchone()
            st = rules.seat_states(database.get_db(), 4, b["date"], 480, 540, "x")[b["seat"]]["st"]
        self.assertEqual((b["status"], st), ("noshow", "ok"))  # โต๊ะกลับเป็นสีเขียว

    def test_checkin_via_web_with_qr_and_id(self):
        code, token = self.book_today_at_8()
        self.client.post("/demo/clock", data={"action": "start", "code": code, "csrf_token": token})
        bad = self.client.post(f"/bookings/{code}/checkin", data={"method": "id", "value": "65000000009", "csrf_token": token},
                               follow_redirects=True).get_data(as_text=True)
        self.assertIn("ไม่ตรงกับเจ้าของ", bad)
        ok = self.client.post(f"/bookings/{code}/checkin", data={"method": "qr", "value": code, "csrf_token": token},
                              follow_redirects=True).get_data(as_text=True)
        self.assertIn("เช็คอินโต๊ะ", ok)
        self.assertIn("กำลังใช้งาน", ok)
        out = self.client.post(f"/bookings/{code}/checkout", data={"csrf_token": token}, follow_redirects=True).get_data(as_text=True)
        self.assertIn("เช็คเอาท์โต๊ะ", out)

    def test_qr_visible_before_checkin_time(self):
        code, _ = self.book_today_at_8()
        page = self.client.get("/bookings").get_data(as_text=True)
        self.assertIn("ดู QR Code", page)
        self.assertIn(f'id="ci-{code}"', page)
        self.assertIn("<svg viewBox", page)

    def test_scan_qr_url_checks_in(self):
        code, token = self.book_today_at_8()
        early = self.client.get(f"/scan/{code}", follow_redirects=True).get_data(as_text=True)
        self.assertIn("ยังไม่ถึงเวลาเช็คอิน", early)
        self.client.post("/demo/clock", data={"action": "start", "code": code, "csrf_token": token})
        ok = self.client.get(f"/scan/{code}", follow_redirects=True).get_data(as_text=True)
        self.assertIn("สแกน QR สำเร็จ", ok)

    def test_cancel_via_web(self):
        code, token = self.book_today_at_8()
        r = self.client.post(f"/bookings/{code}/cancel", data={"csrf_token": token}, follow_redirects=True)
        self.assertIn("ยกเลิกการจองโต๊ะ", r.get_data(as_text=True))

if __name__ == "__main__":
    unittest.main()
