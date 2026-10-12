"""การเชื่อมต่อฐานข้อมูล SQLite และโครงสร้างตาราง"""
import os
import sqlite3

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    id         TEXT PRIMARY KEY,          -- รหัสนักศึกษา 11 หลัก
    name       TEXT NOT NULL,
    is_demo    INTEGER NOT NULL DEFAULT 0 -- 1 = นักศึกษาจำลองที่ระบบสร้างให้
);

CREATE TABLE IF NOT EXISTS bookings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    code         TEXT NOT NULL UNIQUE,    -- รหัสการจอง (ใช้ใน QR)
    student_id   TEXT NOT NULL REFERENCES students(id),
    floor        INTEGER NOT NULL,
    seat         TEXT NOT NULL,
    date         TEXT NOT NULL,           -- YYYY-MM-DD
    start_min    INTEGER NOT NULL,        -- นาทีนับจากเที่ยงคืน
    end_min      INTEGER NOT NULL,
    status       TEXT NOT NULL DEFAULT 'booked',
                 -- booked | checked_in | done | cancelled | noshow
    checkin_at   TEXT,
    checkout_at  TEXT,
    created_at   TEXT NOT NULL,
    is_sample    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_bookings_slot ON bookings(date, floor, seat);
CREATE INDEX IF NOT EXISTS idx_bookings_student ON bookings(student_id, date);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS seeded_dates (
    date TEXT PRIMARY KEY
);
"""


def connect(path):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)  # autocommit, เปิด transaction เอง
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(path):
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()
