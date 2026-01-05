import os
import secrets
import sqlite3
import json
from datetime import date, datetime, timedelta
from typing import Optional, Tuple

import pandas as pd
import streamlit as st

# --- [기존 유틸리티 함수 및 설정] ---
try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSHEETS_AVAILABLE = True
except Exception:
    GSHEETS_AVAILABLE = False

APP_TITLE = "1월 주만나 큐티 체크 리스트"
PROJECT_BOX_TEXT = "2026년 예은 가족 큐티 프로젝트,\n큐티(QT)하는 성도가 하나님의 뷰티(BEAUTY)입니다"
VERSE_TEXT = "주를 경외하게 하는 주의 말씀을 주의 종에게 세우소서 [시편 119:38절]"

SUPPORTED_MONTHS = [
    (2026, 1, "2026년 1월"),
    (2026, 2, "2026년 2월"),
    (2026, 3, "2026년 3월"),
]

# 날짜 도우미
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

def get_query_param(name: str):
    try:
        val = st.query_params.get(name, None)
        return val
    except:
        return None

def set_query_param(**kwargs):
    st.query_params.update(kwargs)

def now_hhmm() -> str:
    return datetime.now().strftime("%H:%M")

def parse_sign_and_prayer(text: str):
    t = (text or "").strip()
    if not t: return None, None
    if "/" not in t: return t, None
    parts = t.split("/", 1)
    return parts[0].strip(), parts[1].strip()

def combine_sign_prayer(sig, pray):
    if sig and pray: return f"{sig}/{pray}"
    return sig or pray or ""

# --- [저장소 클래스 정의] ---
class StorageBase:
    def load_month(self, uid, start, end): raise NotImplementedError
    def upsert_one(self, uid, day, **kwargs): raise NotImplementedError

class SQLiteStorage(StorageBase):
    def __init__(self, path="qti_checklist.db"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("CREATE TABLE IF NOT EXISTS qti_records (uid TEXT, day TEXT, start_time TEXT, end_time TEXT, completed INTEGER, signature TEXT, prayer_note TEXT, updated_at TEXT, PRIMARY KEY (uid, day))")
    def load_month(self, uid, start, end):
        cur = self.conn.cursor()
        cur.execute("SELECT day, start_time, end_time, completed, signature, prayer_note FROM qti_records WHERE uid=? AND day BETWEEN ? AND ?", (uid, start.isoformat(), end.isoformat()))
        rows = cur.fetchall()
        existing = {r[0]: {"날짜": r[0], "QT 시작": r[1] or "", "QT 종료": r[2] or "", "완료": bool(r[3]), "확인 서명/나의 묵상 기도": combine_sign_prayer(r[4], r[5])} for r in rows}
        return pd.DataFrame([existing.get(d.isoformat(), {"날짜": d.isoformat(), "QT 시작": "", "QT 종료": "", "완료": False, "확인 서명/나의 묵상 기도": ""}) for d in daterange(start, end)])
    def upsert_one(self, uid, day, **kwargs):
        now = datetime.now().isoformat()
        cur = self.conn.cursor()
        cur.execute("SELECT start_time, end_time, completed, signature, prayer_note FROM qti_records WHERE uid=? AND day=?", (uid, day))
        row = cur.fetchone() or (None, None, 0, None, None)
        new = { "start_time": kwargs.get("start_time", row[0]), "end_time": kwargs.get("end_time", row[1]), "completed": 1 if kwargs.get("completed", row[2]) else 0, "signature": kwargs.get("signature", row[3]), "prayer_note": kwargs.get("prayer_note", row[4]) }
        cur.execute("INSERT INTO qti_records (uid, day, start_time, end_time, completed, signature, prayer_note, updated_at) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(uid, day) DO UPDATE SET start_time=excluded.start_time, end_time=excluded.end_time, completed=excluded.completed, signature=excluded.signature, prayer_note=excluded.prayer_note, updated_at=excluded.updated_at", (uid, day, new["start_time"], new["end_time"], new["completed"], new["signature"], new["prayer_note"], now))
        self.conn.commit()

# (앞부분 import 및 유틸리티 함수 생략 - 이전 답변과 동일하게 유지)

class GoogleSheetsStorage(StorageBase):
    def __init__(self, spreadsheet_id, worksheet_name, sa_json):
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(sa_json, scopes=scopes)
        self.gc = gspread.authorize(creds)
        self.sh = self.gc.open_by_key(spreadsheet_id)
        try:
            self.ws = self.sh.worksheet(worksheet_name)
        except:
            # 시트가 없으면 생성 (헤더 포함)
            self.ws = self.sh.add_worksheet(title=worksheet_name, rows="1000", cols="20")
            self.ws.append_row(["uid", "day", "start_time", "end_time", "completed", "signature", "prayer_note", "updated_at"])

    def load_month(self, uid, start, end):
        try:
            records = self.ws.get_all_records()
            df = pd.DataFrame(records)
            if df.empty: return self._empty_df(start, end)
            # 해당 사용자의 이번 달 데이터만 필터링
            sub = df[(df["uid"] == str(uid)) & (df["day"] >= start.isoformat()) & (df["day"] <= end.isoformat())]
            existing = {r["day"]: {
                "날짜": r["day"], 
                "QT 시작": r.get("start_time",""), 
                "QT 종료": r.get("end_time",""), 
                "완료": str(r.get("completed",0))=="1", 
                "확인 서명/나의 묵상 기도": combine_sign_prayer(r.get("signature",""), r.get("prayer_note",""))
            } for _, r in sub.iterrows()}
            return pd.DataFrame([existing.get(d.isoformat(), {"날짜": d.isoformat(), "QT 시작": "", "QT 종료": "", "완료": False, "확인 서명/나의 묵상 기도": ""}) for d in daterange(start, end)])
        except:
            return self._empty_df(start, end)

    def _empty_df(self, start, end):
        return pd.DataFrame([{"날짜": d.isoformat(), "QT 시작": "", "QT 종료": "", "완료": False, "확인 서명/나의 묵상 기도": ""} for d in daterange(start, end)])

    # [핵심] 실제로 시트에 데이터를 기록하는 함수입니다.
    def upsert_one(self, uid, day, **kwargs):
        records = self.ws.get_all_records()
        df = pd.DataFrame(records)
        now = datetime.now().isoformat()
        
        # 기존 데이터가 있는지 확인 (uid와 day가 일치하는 행 찾기)
        idx = -1
        if not df.empty:
            match = df[(df["uid"] == str(uid)) & (df["day"] == str(day))]
            if not match.empty:
                idx = match.index[0] + 2 # 헤더가 1번이므로 인덱스 보정

        if idx != -1:
            # 기존 데이터 수정 (각 열 번호에 맞춰 업데이트)
            if "start_time" in kwargs: self.ws.update_cell(idx, 3, kwargs["start_time"])
            if "end_time" in kwargs: self.ws.update_cell(idx, 4, kwargs["end_time"])
            if "completed" in kwargs: self.ws.update_cell(idx, 5, "1" if kwargs["completed"] else "0")
            if "signature" in kwargs: self.ws.update_cell(idx, 6, kwargs["signature"])
            if "prayer_note" in kwargs: self.ws.update_cell(idx, 7, kwargs["prayer_note"])
            self.ws.update_cell(idx, 8, now)
        else:
            # 새 데이터 추가
            row = [
                str(uid), str(day), 
                kwargs.get("start_time", ""), 
                kwargs.get("end_time", ""), 
                "1" if kwargs.get("completed", False) else "0", 
                kwargs.get("signature", ""), 
                kwargs.get("prayer_note", ""), 
                now
            ]
            self.ws.append_row(row)

# (이하 UI 부분 동일)
# --- [UI 메인 화면] ---
st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(f"✨ {APP_TITLE}")

storage = get_storage()
month_label = st.selectbox("📆 월 선택", options=[m[2] for m in SUPPORTED_MONTHS], index=0)
year, month = [(y, m) for (y, m, lbl) in SUPPORTED_MONTHS if lbl == month_label][0]
START, END = month_range(year, month)

uid = get_query_param("uid")

if not uid:
    st.info("개인별 기록을 유지하기 위해 나만의 고유 링크를 생성합니다.")
    if st.button("🚀 나의 큐티 링크 만들기", use_container_width=True):
        new_uid = secrets.token_urlsafe(12)
        set_query_param(uid=new_uid)
        st.rerun()
    st.stop()

# 진행률 계산 (ZeroDivisionError 방지 처리)
df = storage.load_month(uid, START, END)
done_count = int(df["완료"].sum())
total_count = len(df)
progress = done_count / total_count if total_count > 0 else 0.0

c1, c2 = st.columns(2)
c1.metric("이번 달 달성", f"{done_count}일")
c2.metric("성공률", f"{progress:.1%}")
st.progress(progress)

st.markdown("---")

# 오늘의 기록 박스
st.subheader("✍️ 오늘의 큐티 기록")
with st.container(border=True):
    today = date.today()
    default_day = today if (START <= today <= END) else START
    picked_day = st.date_input("날짜를 선택하세요", value=default_day)
    day_str = picked_day.isoformat()
    
    day_data = df[df["날짜"] == day_str].iloc[0] if not df[df["날짜"] == day_str].empty else None

    btn_col1, btn_col2, btn_col3 = st.columns(3)
    with btn_col1:
        if st.button("▶ 시작", use_container_width=True):
            storage.upsert_one(uid, day_str, start_time=now_hhmm())
            st.rerun()
    with btn_col2:
        if st.button("■ 종료", use_container_width=True):
            storage.upsert_one(uid, day_str, end_time=now_hhmm())
            st.rerun()
    with btn_col3:
        is_done = day_data["완료"] if day_data is not None else False
        if st.button("✅ 완료" if not is_done else "🔄 취소", use_container_width=True):
            storage.upsert_one(uid, day_str, completed=not is_done)
            st.rerun()

    current_memo = day_data["확인 서명/나의 묵상 기도"] if day_data is not None else ""
    memo_input = st.text_input("확인 서명 / 묵상 기도", value=current_memo)
    if st.button("기록 저장하기", use_container_width=True, type="primary"):
        sig, pray = parse_sign_and_prayer(memo_input)
        storage.upsert_one(uid, day_str, signature=sig, prayer_note=pray)
        st.success("저장되었습니다!")
        st.rerun()

with st.expander("📋 한 달 전체 기록 보기"):
    st.dataframe(df, use_container_width=True, hide_index=True)

st.markdown("---")
st.markdown(f"<div style='text-align:center; padding:10px; font-weight:600;'>{VERSE_TEXT}</div>", unsafe_allow_html=True)