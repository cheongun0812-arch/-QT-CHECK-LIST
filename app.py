import os
import secrets
import sqlite3
import json
from datetime import date, datetime, timedelta
from typing import Optional, Tuple

import pandas as pd
import streamlit as st

# 구글 시트 라이브러리 (설치 필요)
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

# --- [기존 데이터 처리 로직: storage 클래스 등은 업로드해주신 파일과 동일하게 유지] ---
# (지면 관계상 핵심 UI 변경점 위주로 구성하며, 실제 파일에선 storage 부분 유지)
#

# ... (StorageBase, SQLiteStorage, GoogleSheetsStorage 클래스 내용 동일) ...

# =========================================================
# UI 메인 화면 (모바일 최적화 버전)
# =========================================================
st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(f"✨ {APP_TITLE}")

storage = get_storage() #

month_label = st.selectbox("📆 월 선택", options=[m[2] for m in SUPPORTED_MONTHS], index=0)
year, month = [(y, m) for (y, m, lbl) in SUPPORTED_MONTHS if lbl == month_label][0]
START, END = month_range(year, month) #

uid = get_query_param("uid") #

# 1. 로그인/링크 생성 화면
if not uid:
    st.info("개인별 기록을 유지하기 위해 나만의 고유 링크를 생성합니다.")
    if st.button("🚀 나의 큐티 링크 만들기", use_container_width=True):
        new_uid = secrets.token_urlsafe(12)
        set_query_param(uid=new_uid)
        st.rerun()
    st.stop()

# 2. 대시보드 (진행률 표시로 동기 부여)
df = storage.load_month(uid, START, END) #
done_count = int(df["완료"].sum())
total_count = len(df)
progress = done_count / total_count

c1, c2 = st.columns(2)
c1.metric("이번 달 달성", f"{done_count}일")
c2.metric("성공률", f"{progress:.1%}")
st.progress(progress)

st.markdown("---")

# 3. [미션 2 해결] 오늘의 집중 기록 박스 (스크롤 방지)
st.subheader("✍️ 오늘의 큐티 기록")
with st.container(border=True): # 박스 형태로 시각적 분리
    today = date.today()
    default_day = today if (START <= today <= END) else START
    picked_day = st.date_input("날짜를 선택하세요", value=default_day)
    day_str = picked_day.isoformat()
    
    # 해당 날짜 정보 불러오기
    day_data = df[df["날짜"] == day_str].iloc[0] if not df[df["날짜"] == day_str].empty else None

    # 가로로 버튼 배치
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

    # 서명/기도제목 입력창 (UI 간소화)
    current_memo = day_data["확인 서명/나의 묵상 기도"] if day_data is not None else ""
    memo_input = st.text_input("확인 서명 / 묵상 기도 (예: 나큐티/오늘도 감사합니다)", value=current_memo)
    if st.button("기록 저장하기", use_container_width=True, type="primary"):
        sig, pray = parse_sign_and_prayer(memo_input)
        storage.upsert_one(uid, day_str, signature=sig, prayer_note=pray)
        st.success("저장되었습니다!")
        st.rerun()

# 4. 월간 전체 리스트 (필요할 때만 펼쳐보기)
with st.expander("📋 한 달 전체 기록 보기 (스크롤)"):
    st.data_editor(df, use_container_width=True, hide_index=True, disabled=["날짜"])

# 하단 문구
st.markdown("---")
st.markdown(f"<div style='text-align:center; padding:10px; font-weight:600;'>{VERSE_TEXT}</div>", unsafe_allow_html=True)
