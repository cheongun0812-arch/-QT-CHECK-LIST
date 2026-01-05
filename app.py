import os
import secrets
import sqlite3
import json
from datetime import date, datetime, timedelta
from typing import Optional, Tuple
import pandas as pd
import streamlit as st

# 구글 시트 라이브러리 로드
try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSHEETS_AVAILABLE = True
except Exception:
    GSHEETS_AVAILABLE = False

# --- 기본 설정 ---
APP_TITLE = "1월 주만나 큐티 체크 리스트"
VERSE_TEXT = "주를 경외하게 하는 주의 말씀을 주의 종에게 세우소서 [시편 119:38절]"
SUPPORTED_MONTHS = [(2026, 1, "2026년 1월"), (2026, 2, "2026년 2월"), (2026, 3, "2026년 3월")]

# --- 유틸리티 함수 ---
def month_range(year: int, month: int):
    start = date(year, month, 1)
    end = (date(year, month + 1, 1) if month < 12 else date(year + 1, 1, 1)) - timedelta(days=1)
    return start, end

def daterange(d1, d2):
    curr = d1
    while curr <= d2:
        yield curr
        curr += timedelta(days=1)

def now_hhmm(): return datetime.now().strftime("%H:%M")

def parse_sign_and_prayer(text):
    if not text or "/" not in text: return text, ""
    parts = text.split("/", 1)
    return parts[0].strip(), parts[1].strip()

def combine_sign_prayer(sig, pray):
    if sig and pray: return f"{sig}/{pray}"
    return sig or pray or ""

# --- 구글 시트 저장소 로직 ---
class GoogleSheetsStorage:
    def __init__(self, spreadsheet_id, worksheet_name, sa_json):
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(sa_json, scopes=scopes)
        self.gc = gspread.authorize(creds)
        self.sh = self.gc.open_by_key(spreadsheet_id)
        try:
            self.ws = self.sh.worksheet(worksheet_name)
        except:
            self.ws = self.sh.add_worksheet(title=worksheet_name, rows="1000", cols="10")
            self.ws.append_row(["uid", "day", "start_time", "end_time", "completed", "signature", "prayer_note", "updated_at"])

    def load_month(self, uid, start, end):
        try:
            all_data = self.ws.get_all_records()
            df_all = pd.DataFrame(all_data)
            if df_all.empty: return self._empty_df(start, end)
            user_data = df_all[(df_all["uid"].astype(str) == str(uid)) & (df_all["day"] >= start.isoformat()) & (df_all["day"] <= end.isoformat())]
            lookup = {r["day"]: r for _, r in user_data.iterrows()}
            res = []
            for d in daterange(start, end):
                ds = d.isoformat()
                if ds in lookup:
                    r = lookup[ds]
                    res.append({"날짜": ds, "QT 시작": r.get("start_time",""), "QT 종료": r.get("end_time",""), "완료": str(r.get("completed"))=="1", "확인 서명/나의 묵상 기도": combine_sign_prayer(r.get("signature"), r.get("prayer_note"))})
                else:
                    res.append({"날짜": ds, "QT 시작": "", "QT 종료": "", "완료": False, "확인 서명/나의 묵상 기도": ""})
            return pd.DataFrame(res)
        except: return self._empty_df(start, end)

    def _empty_df(self, start, end):
        return pd.DataFrame([{"날짜": d.isoformat(), "QT 시작": "", "QT 종료": "", "완료": False, "확인 서명/나의 묵상 기도": ""} for d in daterange(start, end)])

    def upsert_one(self, uid, day, **kwargs):
        all_records = self.ws.get_all_records()
        df = pd.DataFrame(all_records)
        row_idx = -1
        if not df.empty:
            match = df[(df["uid"].astype(str) == str(uid)) & (df["day"] == str(day))]
            if not match.empty: row_idx = match.index[0] + 2
        now = datetime.now().isoformat()
        col_map = {"start_time": 3, "end_time": 4, "completed": 5, "signature": 6, "prayer_note": 7}
        if row_idx != -1:
            for k, v in kwargs.items():
                if k in col_map:
                    val = "1" if k == "completed" and v else ("0" if k == "completed" else v)
                    self.ws.update_cell(row_idx, col_map[k], val)
            self.ws.update_cell(row_idx, 8, now)
        else:
            new_row = [str(uid), str(day), kwargs.get("start_time",""), kwargs.get("end_time",""), "1" if kwargs.get("completed") else "0", kwargs.get("signature",""), kwargs.get("prayer_note",""), now]
            self.ws.append_row(new_row)

def get_storage():
    s_id = st.secrets.get("GSHEETS_SPREADSHEET_ID")
    sa_json = st.secrets.get("GSHEETS_SERVICE_ACCOUNT_JSON")
    if s_id and sa_json:
        return GoogleSheetsStorage(s_id, "qti_records", json.loads(sa_json) if isinstance(sa_json, str) else sa_json)
    return None

# --- UI 메인 화면 ---
st.set_page_config(page_title=APP_TITLE, layout="wide")
storage = get_storage()

if not storage:
    st.error("구글 시트 설정(Secrets)을 확인해주세요.")
    st.stop()

st.title(f"✨ {APP_TITLE}")
month_label = st.selectbox("📆 월 선택", [m[2] for m in SUPPORTED_MONTHS])
year, month = [(y, m) for (y, m, lbl) in SUPPORTED_MONTHS if lbl == month_label][0]
START, END = month_range(year, month)

# [UID 관리 및 링크 저장 안내]
if "uid" not in st.query_params:
    st.info("### 🙏 큐티 체크리스트 시작하기\n성도님 전용 기록지를 만들기 위해 아래 버튼을 눌러주세요.")
    if st.button("🚀 나의 큐티 링크 만들기 (처음 1회)", use_container_width=True):
        new_uid = secrets.token_urlsafe(8)
        st.query_params["uid"] = new_uid
        st.rerun()
    st.stop()

uid = st.query_params["uid"]
df = storage.load_month(uid, START, END)

# 진행률
done_cnt = int(df["완료"].sum()) if not df.empty else 0
total_cnt = len(df) if len(df) > 0 else 1
progress = done_cnt / total_cnt
st.metric("이번 달 달성", f"{done_cnt}일", f"{progress:.1%}")
st.progress(progress)

# 안내 문구 추가
with st.expander("📢 내 기록을 보관하려면? (즐겨찾기 필수)", expanded=False):
    st.success("성도님 전용 모드로 연결되었습니다.")
    st.markdown(f"**이 주소를 꼭 복사해서 카톡 '나에게 보내기'에 저장하거나 즐겨찾기 하세요!**")
    st.code(f"https://your-app.streamlit.app/?uid={uid}")

st.markdown("---")
with st.container(border=True):
    st.subheader("✍️ 오늘의 큐티 기록")
    picked_day = st.date_input("날짜 선택", value=date.today())
    day_str = picked_day.isoformat()
    
    c1, c2, c3 = st.columns(3)
    if c1.button("▶ 시작", use_container_width=True):
        storage.upsert_one(uid, day_str, start_time=now_hhmm())
        st.rerun()
    if c2.button("■ 종료", use_container_width=True):
        storage.upsert_one(uid, day_str, end_time=now_hhmm())
        st.rerun()
    
    is_done = df[df["날짜"] == day_str]["완료"].values[0] if not df[df["날짜"] == day_str].empty else False
    if c3.button("✅ " + ("취소" if is_done else "완료"), use_container_width=True):
        storage.upsert_one(uid, day_str, completed=not is_done)
        st.rerun()

    memo = st.text_input("서명/기도제목 (예: 홍길동/감사합니다)")
    if st.button("기록 저장하기", use_container_width=True, type="primary"):
        sig, pray = parse_sign_and_prayer(memo)
        storage.upsert_one(uid, day_str, signature=sig, prayer_note=pray)
        st.success("저장되었습니다!")
        st.rerun()

with st.expander("📋 한 달 전체 기록 확인"):
    st.dataframe(df, use_container_width=True, hide_index=True)