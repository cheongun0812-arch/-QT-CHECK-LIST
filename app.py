import os
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional, Tuple

import pandas as pd
import streamlit as st

# (선택) Google Sheets 저장소
try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSHEETS_AVAILABLE = True
except Exception:
    GSHEETS_AVAILABLE = False


# =========================================================
# 기본 텍스트
# =========================================================
APP_TITLE = "1월 주만나 큐티 체크 리스트"

PROJECT_BOX_TEXT = (
    "2026년 예은 가족 큐티 프로젝트,\n"
    "큐티(QT)하는 성도가 하나님의 뷰티(BEAUTY)입니다"
)

VERSE_TEXT = "주를 경외하게 하는 주의 말씀을 주의 종에게 세우소서 [시편 119:38절]"


# =========================================================
# 월 범위(1~3월 전환)
# =========================================================
SUPPORTED_MONTHS = [
    (2026, 1, "2026년 1월"),
    (2026, 2, "2026년 2월"),
    (2026, 3, "2026년 3월"),
]


def month_range(year: int, month: int):
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    return start, end


def daterange(d1: date, d2: date):
    cur = d1
    while cur <= d2:
        yield cur
        cur += timedelta(days=1)


# =========================================================
# Query param helpers
# =========================================================
def get_query_param(name: str):
    try:
        qp = st.query_params
        val = qp.get(name, None)
        if isinstance(val, list):
            return val[0] if val else None
        return val
    except Exception:
        qp = st.experimental_get_query_params()
        val = qp.get(name, None)
        return val[0] if isinstance(val, list) and val else None


def set_query_param(**kwargs):
    try:
        st.query_params.update(kwargs)
    except Exception:
        st.experimental_set_query_params(**kwargs)


# =========================================================
# 시간/입력 검증
# =========================================================
def validate_time_str(s: str):
    s = (s or "").strip()
    if not s:
        return True
    try:
        datetime.strptime(s, "%H:%M")
        return True
    except ValueError:
        return False


def now_hhmm():
    return datetime.now().strftime("%H:%M")


def parse_sign_and_prayer(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    입력 예:
      - "정청운/주를 경외하게..."  -> ("정청운", "주를 경외하게...")
      - "정청운"                 -> ("정청운", None)
      - "/주를 경외하게..."      -> (None, "주를 경외하게...")
    """
    t = (text or "").strip()
    if not t:
        return None, None
    if "/" not in t:
        return (t or None), None

    left, right = t.split("/", 1)
    left = left.strip() or None
    right = right.strip() or None
    return left, right


def combine_sign_prayer(signature: Optional[str], prayer_note: Optional[str]) -> str:
    sig = (signature or "").strip()
    pray = (prayer_note or "").strip()
    if sig and pray:
        return f"{sig}/{pray}"
    if sig and not pray:
        return sig
    if (not sig) and pray:
        return f"/{pray}"
    return ""


# =========================================================
# Storage 인터페이스
# =========================================================
@dataclass
class Record:
    uid: str
    day: str
    start_time: Optional[str]
    end_time: Optional[str]
    completed: int
    signature: Optional[str]
    prayer_note: Optional[str]
    updated_at: str


class StorageBase:
    def load_month(self, uid: str, start: date, end: date) -> pd.DataFrame:
        raise NotImplementedError

    def upsert_month(self, uid: str, df: pd.DataFrame):
        raise NotImplementedError

    def upsert_one(
        self,
        uid: str,
        day: str,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        completed: Optional[bool] = None,
        signature: Optional[str] = None,
        prayer_note: Optional[str] = None,
    ):
        raise NotImplementedError

    def delete_month(self, uid: str, start: date, end: date):
        raise NotImplementedError

    # 관리자용
    def admin_fetch_range(self, start: date, end: date) -> pd.DataFrame:
        raise NotImplementedError


# =========================================================
# SQLite Storage
# =========================================================
class SQLiteStorage(StorageBase):
    def __init__(self, path="qti_checklist.db"):
        self.path = path
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self._init()
        self._migrate_if_needed()

    def _init(self):
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS qti_records (
                uid TEXT NOT NULL,
                day TEXT NOT NULL,
                start_time TEXT,
                end_time TEXT,
                completed INTEGER NOT NULL DEFAULT 0,
                signature TEXT,
                prayer_note TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (uid, day)
            )
            """
        )
        self.conn.commit()

    def _migrate_if_needed(self):
        # 과거 DB에 prayer_note가 없을 수 있어서 컬럼 추가(안전)
        cur = self.conn.cursor()
        cur.execute("PRAGMA table_info(qti_records)")
        cols = [r[1] for r in cur.fetchall()]
        if "prayer_note" not in cols:
            self.conn.execute("ALTER TABLE qti_records ADD COLUMN prayer_note TEXT")
            self.conn.commit()

    def load_month(self, uid: str, start: date, end: date) -> pd.DataFrame:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT day, start_time, end_time, completed, signature, prayer_note
            FROM qti_records
            WHERE uid = ? AND day BETWEEN ? AND ?
            ORDER BY day ASC
            """,
            (uid, start.isoformat(), end.isoformat()),
        )
        rows = cur.fetchall()

        existing = {
            r[0]: {
                "날짜": r[0],
                "QT 시작": r[1] or "",
                "QT 종료": r[2] or "",
                "완료": bool(r[3]),
                "확인 서명/나의 묵상 기도": combine_sign_prayer(r[4], r[5]),
            }
            for r in rows
        }

        data = []
        for d in daterange(start, end):
            ds = d.isoformat()
            if ds in existing:
                data.append(existing[ds])
            else:
                if d == date(2026, 1, 1):
                    data.append(
                        {
                            "날짜": ds,
                            "QT 시작": "10:30",
                            "QT 종료": "12:00",
                            "완료": False,
                            "확인 서명/나의 묵상 기도": "",
                        }
                    )
                else:
                    data.append(
                        {
                            "날짜": ds,
                            "QT 시작": "",
                            "QT 종료": "",
                            "완료": False,
                            "확인 서명/나의 묵상 기도": "",
                        }
                    )
        return pd.DataFrame(data)

    def upsert_month(self, uid: str, df: pd.DataFrame):
        now = datetime.now().isoformat(timespec="seconds")
        cur = self.conn.cursor()
        for _, row in df.iterrows():
            day = str(row["날짜"])
            start_time = str(row["QT 시작"]).strip() or None
            end_time = str(row["QT 종료"]).strip() or None
            completed = 1 if bool(row["완료"]) else 0

            sig_pray = str(row["확인 서명/나의 묵상 기도"]).strip()
            signature, prayer_note = parse_sign_and_prayer(sig_pray)

            cur.execute(
                """
                INSERT INTO qti_records (uid, day, start_time, end_time, completed, signature, prayer_note, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(uid, day) DO UPDATE SET
                    start_time = excluded.start_time,
                    end_time = excluded.end_time,
                    completed = excluded.completed,
                    signature = excluded.signature,
                    prayer_note = excluded.prayer_note,
                    updated_at = excluded.updated_at
                """,
                (uid, day, start_time, end_time, completed, signature, prayer_note, now),
            )
        self.conn.commit()

    def upsert_one(
        self,
        uid: str,
        day: str,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        completed: Optional[bool] = None,
        signature: Optional[str] = None,
        prayer_note: Optional[str] = None,
    ):
        cur = self.conn.cursor()
        cur.execute(
            "SELECT start_time, end_time, completed, signature, prayer_note FROM qti_records WHERE uid=? AND day=?",
            (uid, day),
        )
        row = cur.fetchone()
        if row:
            cur_start, cur_end, cur_completed, cur_sign, cur_pray = row
        else:
            cur_start, cur_end, cur_completed, cur_sign, cur_pray = None, None, 0, None, None

        new_start = cur_start if start_time is None else (start_time.strip() or None)
        new_end = cur_end if end_time is None else (end_time.strip() or None)
        new_completed = cur_completed if completed is None else (1 if bool(completed) else 0)
        new_sign = cur_sign if signature is None else (signature.strip() or None)
        new_pray = cur_pray if prayer_note is None else (prayer_note.strip() or None)

        now = datetime.now().isoformat(timespec="seconds")
        cur.execute(
            """
            INSERT INTO qti_records (uid, day, start_time, end_time, completed, signature, prayer_note, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(uid, day) DO UPDATE SET
                start_time = excluded.start_time,
                end_time = excluded.end_time,
                completed = excluded.completed,
                signature = excluded.signature,
                prayer_note = excluded.prayer_note,
                updated_at = excluded.updated_at
            """,
            (uid, day, new_start, new_end, new_completed, new_sign, new_pray, now),
        )
        self.conn.commit()

    def delete_month(self, uid: str, start: date, end: date):
        cur = self.conn.cursor()
        cur.execute(
            "DELETE FROM qti_records WHERE uid=? AND day BETWEEN ? AND ?",
            (uid, start.isoformat(), end.isoformat()),
        )
        self.conn.commit()

    def admin_fetch_range(self, start: date, end: date) -> pd.DataFrame:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT