# coding: utf-8
import os
import secrets
import json
import re
import time
from datetime import date, datetime, timedelta
from typing import Optional, Tuple
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from io import BytesIO

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# -------------------------
# 구글 시트 라이브러리
# -------------------------
try:
    import gspread
    from google.oauth2.service_account import Credentials
    from gspread.exceptions import APIError
    GSHEETS_AVAILABLE = True
except Exception:
    GSHEETS_AVAILABLE = False

# -------------------------
# 기본 설정
# -------------------------
APP_TITLE = "1월 주만나 큐티 체크 리스트"
VERSE_TEXT = "주를 경외하게 하는 주의 말씀을 주의 종에게 세우소서 [시편 119:38절]"
SUPPORTED_MONTHS = [(2026, 1, "2026년 1월"), (2026, 2, "2026년 2월"), (2026, 3, "2026년 3월")]

SHEET_RECORDS = "qti_records"  # 일별 기록
SHEET_USERS = "qti_users"      # uid별 성도 정보
MEMBER_ROLES = ["평신도", "서리집사", "안수집사", "권사", "장로", "강도사", "목사", "기타"]

KST = ZoneInfo("Asia/Seoul")
ADMIN_KEY_FALLBACK = "yeiun1234"
_HHMM = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")

# -------------------------
# 유틸리티 함수
# -------------------------
def now_kst() -> datetime: return datetime.now(tz=KST)
def today_kst() -> date: return now_kst().date()
def now_hhmm_kst() -> str: return now_kst().strftime("%H:%M")
def normalize_hhmm(s: str) -> str:
    s = (s or "").strip()
    return s if _HHMM.match(s) else ""
def clamp_50(s: str) -> str: return (s or "").strip()[:50]
def clamp_20(s: str) -> str: return (s or "").strip()[:20]
def normalize_role(s: str) -> str:
    s = (s or "").strip()
    return s if s in MEMBER_ROLES else (MEMBER_ROLES[-1] if s else "")

def month_range(year: int, month: int) -> Tuple[date, date]:
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    return start, end

def daterange(d1: date, d2: date):
    curr = d1
    while curr <= d2:
        yield curr
        curr += timedelta(days=1)

def week_start_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())

# -------------------------
# 구글 시트 저장소 (최적화 버전)
# -------------------------
class GoogleSheetsStorage:
    RECORDS_REQUIRED = ["uid", "member_role", "member_name", "day", "start_time", "end_time", "completed", "signature", "prayer_note", "updated_at"]
    USERS_REQUIRED = ["uid", "member_role", "member_name", "updated_at"]

    def __init__(self, spreadsheet_id: str, worksheet_records: str, sa_json: dict):
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(sa_json, scopes=scopes)
        self.gc = gspread.authorize(creds)
        self.sh = self.gc.open_by_key(spreadsheet_id)
        self._schema_verified = False
        self.col_idx = {}
        self.user_col_idx = {}

        # 시트 로드 및 생성
        self.ws = self._get_or_create_ws(worksheet_records, self.RECORDS_REQUIRED)
        self.ws_users = self._get_or_create_ws(SHEET_USERS, self.USERS_REQUIRED)

    def _get_or_create_ws(self, title, headers):
        try:
            return self.sh.worksheet(title)
        except:
            ws = self.sh.add_worksheet(title=title, rows="2000", cols="20")
            ws.append_row(headers)
            return ws

    def _ensure_schema(self):
        """매 요청마다 헤더를 읽지 않도록 메모리 캐싱 적용"""
        if self._schema_verified:
            return
        try:
            hdr = self.ws.row_values(1)
            if not hdr or "uid" not in hdr:
                self.ws.update('A1', [self.RECORDS_REQUIRED])
                hdr = self.RECORDS_REQUIRED
            self.col_idx = {h: i + 1 for i, h in enumerate(hdr)}

            uhdr = self.ws_users.row_values(1)
            if not uhdr or "uid" not in uhdr:
                self.ws_users.update('A1', [self.USERS_REQUIRED])
                uhdr = self.USERS_REQUIRED
            self.user_col_idx = {h: i + 1 for i, h in enumerate(uhdr)}
            self._schema_verified = True
        except Exception as e:
            st.error(f"스키마 확인 중 오류: {e}")

    def fetch_all_records_df(self) -> pd.DataFrame:
        self._ensure_schema()
        try:
            rows = self.ws.get_all_records()
            return pd.DataFrame(rows) if rows else pd.DataFrame(columns=self.RECORDS_REQUIRED)
        except Exception:
            return pd.DataFrame(columns=self.RECORDS_REQUIRED)

    def get_profile(self, uid: str) -> Tuple[str, str]:
        self._ensure_schema()
        try:
            rows = self.ws_users.get_all_records()
            if not rows: return "", ""
            dfu = pd.DataFrame(rows)
            hit = dfu[dfu["uid"].astype(str) == str(uid)]
            if hit.empty: return "", ""
            r = hit.iloc[-1]
            return normalize_role(r.get("member_role", "")), clamp_20(r.get("member_name", ""))
        except:
            return "", ""

    def upsert_profile(self, uid: str, member_role: str, member_name: str):
        self._ensure_schema()
        role, name = normalize_role(member_role), clamp_20(member_name)
        now_iso = now_kst().isoformat()
        
        try:
            rows = self.ws_users.get_all_values()
            row_idx = -1
            for i, r in enumerate(rows):
                if r[0] == str(uid):
                    row_idx = i + 1
                    break
            
            if row_idx != -1:
                # 최적화: 필요한 셀만 업데이트
                self.ws_users.update_cell(row_idx, self.user_col_idx["member_role"], role)
                self.ws_users.update_cell(row_idx, self.user_col_idx["member_name"], name)
                self.ws_users.update_cell(row_idx, self.user_col_idx["updated_at"], now_iso)
            else:
                self.ws_users.append_row([str(uid), role, name, now_iso])
        except Exception as e:
            st.error(f"프로필 저장 실패: {e}")

    def load_month(self, uid: str, start: date, end: date) -> pd.DataFrame:
        df_all = self.fetch_all_records_df()
        if df_all.empty:
            return pd.DataFrame([{"날짜": d.isoformat(), "QT 시작": "", "QT 종료": "", "완료": False, "나의 묵상 기도": ""} for d in daterange(start, end)])
        
        user_data = df_all[(df_all["uid"].astype(str) == str(uid)) & (df_all["day"] >= start.isoformat()) & (df_all["day"] <= end.isoformat())]
        lookup = {r["day"]: r for _, r in user_data.iterrows()}
        
        res = []
        for d in daterange(start, end):
            ds = d.isoformat()
            if ds in lookup:
                r = lookup[ds]
                res.append({"날짜": ds, "QT 시작": normalize_hhmm(r.get("start_time", "")), "QT 종료": normalize_hhmm(r.get("end_time", "")), "완료": str(r.get("completed", "0")) == "1", "나의 묵상 기도": clamp_50(r.get("prayer_note", ""))})
            else:
                res.append({"날짜": ds, "QT 시작": "", "QT 종료": "", "완료": False, "나의 묵상 기도": ""})
        return pd.DataFrame(res)

    def upsert_one(self, uid: str, day: str, **kwargs):
        """핵심 수정: API 재시도 로직 및 호출 최적화"""
        self._ensure_schema()
        now_iso = now_kst().isoformat()

        for attempt in range(3):
            try:
                all_vals = self.ws.get_all_values()
                row_idx = -1
                for i, r in enumerate(all_vals):
                    if len(r) > 3 and r[0] == str(uid) and r[3] == str(day):
                        row_idx = i + 1
                        break

                cells = []
                if row_idx == -1:
                    # 신규 행 추가
                    new_row = [""] * len(self.col_idx)
                    new_row[self.col_idx["uid"]-1] = str(uid)
                    new_row[self.col_idx["day"]-1] = str(day)
                    new_row[self.col_idx["updated_at"]-1] = now_iso
                    for k, v in kwargs.items():
                        if k in self.col_idx:
                            val = "1" if k == "completed" and v else ("0" if k == "completed" else v)
                            new_row[self.col_idx[k]-1] = str(val)
                    self.ws.append_row(new_row, value_input_option="USER_ENTERED")
                else:
                    # 기존 행 업데이트
                    for k, v in kwargs.items():
                        if k in self.col_idx:
                            val = "1" if k == "completed" and v else ("0" if k == "completed" else v)
                            cells.append(gspread.Cell(row_idx, self.col_idx[k], str(val)))
                    cells.append(gspread.Cell(row_idx, self.col_idx["updated_at"], now_iso))
                    if cells:
                        self.ws.update_cells(cells, value_input_option="USER_ENTERED")
                return # 성공 시 종료
            except APIError:
                if attempt < 2: time.sleep(2)
                else: raise

# -------------------------
# Streamlit UI 및 로직
# -------------------------
@st.cache_resource
def get_storage():
    s_id = st.secrets.get("GSHEETS_SPREADSHEET_ID")
    sa_json = st.secrets.get("GSHEETS_SERVICE_ACCOUNT_JSON")
    if s_id and sa_json:
        sa_obj = json.loads(sa_json) if isinstance(sa_json, str) else sa_json
        return GoogleSheetsStorage(s_id, SHEET_RECORDS, sa_obj)
    return None

def apply_css():
    st.markdown("""<style>
        .stButton>button { height: 50px; border-radius: 12px; font-weight: bold; }
        .qti-table { width: 100%; border-collapse: collapse; border-radius: 10px; overflow: hidden; }
        .qti-table th { background: #f0f2f6; padding: 10px; }
        .qti-table td { padding: 10px; border-bottom: 1px solid #eee; text-align: center; }
    </style>""", unsafe_allow_html=True)

def render_qt_table_html(df: pd.DataFrame):
    if df.empty: return st.info("기록이 없습니다.")
    dfx = df.copy()
    dfx["완료"] = dfx["완료"].apply(lambda x: "✅" if x else "")
    st.write(dfx.to_html(index=False, classes="qti-table", escape=False), unsafe_allow_html=True)

# 메인 앱 시작
st.set_page_config(page_title=APP_TITLE, layout="wide")
apply_css()
storage = get_storage()

if not storage:
    st.error("구글 시트 설정이 필요합니다.")
    st.stop()

st.title(f"✨ {APP_TITLE}")
st.caption(VERSE_TEXT)

# 모드 선택
mode = st.radio("모드", ["성도님용", "관리자용"], horizontal=True)

if mode == "관리자용":
    admin_pw = st.secrets.get("ADMIN_KEY", ADMIN_KEY_FALLBACK)
    pw = st.text_input("비밀번호", type="password")
    if pw == admin_pw:
        st.success("관리자 인증됨")
        df_all = storage.fetch_all_records_df()
        st.dataframe(df_all)
    st.stop()

# 성도님 모드
if "uid" not in st.query_params:
    if st.button("🚀 나의 큐티 링크 생성하기"):
        new_uid = secrets.token_urlsafe(8)
        st.query_params["uid"] = new_uid
        st.rerun()
    st.stop()

uid = st.query_params["uid"]

# 프로필 세션 관리
if "profile_loaded" not in st.session_state:
    role, name = storage.get_profile(uid)
    st.session_state["m_role"], st.session_state["m_name"] = role or MEMBER_ROLES[0], name or ""
    st.session_state["profile_loaded"] = True

with st.expander("🙋 성도 정보 설정", expanded=not st.session_state["m_name"]):
    c1, c2 = st.columns(2)
    new_role = c1.selectbox("직분", MEMBER_ROLES, index=MEMBER_ROLES.index(st.session_state["m_role"]))
    new_name = c2.text_input("이름", value=st.session_state["m_name"])
    if st.button("성도 정보 저장"):
        storage.upsert_profile(uid, new_role, new_name)
        st.session_state["m_role"], st.session_state["m_name"] = new_role, new_name
        st.success("저장되었습니다.")
        st.rerun()

# 기록 파트
st.markdown("---")
month_label = st.selectbox("📆 월 선택", [m[2] for m in SUPPORTED_MONTHS])
y, m = [(y, m) for y, m, lbl in SUPPORTED_MONTHS if lbl == month_label][0]
START, END = month_range(y, m)

picked_day = st.date_input("날짜 선택", value=today_kst(), min_value=START, max_value=END)
day_str = picked_day.isoformat()

c1, c2, c3 = st.columns(3)
if c1.button("▶ 시작(현재)"):
    storage.upsert_one(uid, day_str, start_time=now_hhmm_kst(), member_role=st.session_state["m_role"], member_name=st.session_state["m_name"])
    st.rerun()
if c2.button("■ 종료(현재)"):
    storage.upsert_one(uid, day_str, end_time=now_hhmm_kst(), member_role=st.session_state["m_role"], member_name=st.session_state["m_name"])
    st.rerun()
if c3.button("✅ 완료 토글"):
    df_now = storage.load_month(uid, START, END)
    is_done = df_now[df_now["날짜"] == day_str]["완료"].values[0]
    storage.upsert_one(uid, day_str, completed=not is_done, member_role=st.session_state["m_role"], member_name=st.session_state["m_name"])
    st.rerun()

memo = st.text_area("🕊️ 한 줄 묵상 기도 (50자)", max_chars=50)
if st.button("기록 저장"):
    storage.upsert_one(uid, day_str, prayer_note=clamp_50(memo), member_role=st.session_state["m_role"], member_name=st.session_state["m_name"])
    st.success("저장 완료!")
    st.rerun()

# 내 기록 보기
st.markdown("---")
st.subheader("📋 내 큐티 기록 확인")
df_month = storage.load_month(uid, START, END)
render_qt_table_html(df_month)

# 공유 링크
st.info(f"🔗 내 기록지 주소 (복사해서 저장하세요):\n{st.secrets.get('PUBLIC_APP_URL', 'https://share.streamlit.app')}?uid={uid}")