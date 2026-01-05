import json
import secrets
from datetime import date, datetime, timedelta
from typing import Optional
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

# =========================
# 기본 설정
# =========================
APP_TITLE = "큐티 체크 리스트"
VERSE_TEXT = "주를 경외하게 하는 주의 말씀을 주의 종에게 세우소서 [시편 119:38절]"
SUPPORTED_MONTHS = [
    (2026, 1, "2026년 1월"),
    (2026, 2, "2026년 2월"),
    (2026, 3, "2026년 3월"),
]
SHEET_WORKSHEET_NAME = "qti_records"

# =========================
# 타임존 (KST)
# =========================
KST = ZoneInfo("Asia/Seoul")


def now_kst() -> datetime:
    return datetime.now(tz=KST)


def today_kst() -> date:
    return now_kst().date()


def now_hhmm_kst() -> str:
    return now_kst().strftime("%H:%M")


# =========================
# Query Params (구버전 API로만 통일)  ✅ 중요
# =========================
def get_uid_from_url() -> Optional[str]:
    qp = st.experimental_get_query_params()
    v = qp.get("uid")
    if isinstance(v, list):
        return v[0] if v else None
    return str(v) if v is not None else None


def set_uid_in_url(uid: str) -> None:
    st.experimental_set_query_params(uid=uid)


def build_share_url(uid: str) -> str:
    base = None
    try:
        base = st.context.url
    except Exception:
        base = None

    if not base:
        base = st.secrets.get("PUBLIC_APP_URL")

    if not base:
        base = "https://<YOUR-APP>.streamlit.app"

    return f"{base}?{urlencode({'uid': uid})}"


# =========================
# 날짜 유틸
# =========================
def month_range(year: int, month: int):
    start = date(year, month, 1)
    end = (date(year, month + 1, 1) if month < 12 else date(year + 1, 1, 1)) - timedelta(days=1)
    return start, end


def daterange(d1: date, d2: date):
    cur = d1
    while cur <= d2:
        yield cur
        cur += timedelta(days=1)


def week_start_monday(d: date) -> date:
    # 월요일=0 ... 일요일=6
    return d - timedelta(days=d.weekday())


def clamp_date(d: date, start: date, end: date) -> date:
    if d < start:
        return start
    if d > end:
        return end
    return d


# =========================
# 표 스타일링(정렬)
# =========================
def style_qt_table(df: pd.DataFrame):
    """
    - 헤더: 가운데 정렬
    - 날짜/QT 시작/QT 종료/완료: 가운데 정렬
    - 나의 묵상 기도: 왼쪽 정렬
    """
    center_cols = [c for c in ["날짜", "QT 시작", "QT 종료", "완료"] if c in df.columns]
    left_cols = [c for c in ["나의 묵상 기도"] if c in df.columns]

    sty = df.style

    # 데이터 정렬
    if center_cols:
        sty = sty.set_properties(**{"text-align": "center"}, subset=center_cols)
    if left_cols:
        sty = sty.set_properties(**{"text-align": "left"}, subset=left_cols)

    # 헤더 정렬(전체 가운데)
    sty = sty.set_table_styles(
        [{"selector": "th", "props": [("text-align", "center")]}],
        overwrite=False,
    )

    # 컬럼 폭(대략) - Streamlit에서 Styler는 HTML 테이블로 렌더링되므로 폭 지정 가능
    # pandas styler에서 th/td는 col0, col1 클래스가 붙는 경우가 많아 그 방식 사용
    # (환경에 따라 완전 고정은 아니지만 대부분 잘 먹습니다)
    styles = []
    col_order = list(df.columns)

    def add_col_width(col_name: str, width_px: int):
        if col_name not in col_order:
            return
        idx = col_order.index(col_name)
        styles.extend(
            [
                {"selector": f"th.col{idx}", "props": [("width", f"{width_px}px")]},
                {"selector": f"td.col{idx}", "props": [("width", f"{width_px}px")]},
            ]
        )

    add_col_width("날짜", 110)
    add_col_width("QT 시작", 90)
    add_col_width("QT 종료", 90)
    add_col_width("완료", 70)
    add_col_width("나의 묵상 기도", 520)

    if styles:
        sty = sty.set_table_styles(styles, overwrite=False)

    return sty


# =========================
# 구글 시트 연동
# =========================
try:
    import gspread
    from google.oauth2.service_account import Credentials

    GSHEETS_AVAILABLE = True
except Exception:
    GSHEETS_AVAILABLE = False


class GoogleSheetsStorage:
    def __init__(self, spreadsheet_id: str, worksheet_name: str, sa_json: dict):
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_info(sa_json, scopes=scopes)
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(spreadsheet_id)

        try:
            ws = sh.worksheet(worksheet_name)
        except Exception:
            ws = sh.add_worksheet(title=worksheet_name, rows="2000", cols="12")
            ws.append_row(["uid", "day", "start_time", "end_time", "completed", "signature", "prayer_note", "updated_at"])

        self.ws = ws

    def _empty_month_df(self, start: date, end: date) -> pd.DataFrame:
        return pd.DataFrame(
            [{"날짜": d.isoformat(), "QT 시작": "", "QT 종료": "", "완료": False, "나의 묵상 기도": ""} for d in daterange(start, end)]
        )

    def fetch_all_records_df(self) -> pd.DataFrame:
        all_data = self.ws.get_all_records()
        df_all = pd.DataFrame(all_data)
        if df_all.empty:
            return pd.DataFrame(
                columns=["uid", "day", "start_time", "end_time", "completed", "signature", "prayer_note", "updated_at"]
            )
        for col in ["uid", "day", "start_time", "end_time", "signature", "prayer_note", "updated_at"]:
            if col in df_all.columns:
                df_all[col] = df_all[col].astype(str).fillna("")
        if "completed" in df_all.columns:
            df_all["completed"] = df_all["completed"].astype(str).fillna("0")
        return df_all

    def load_month_ui_df(self, uid: str, start: date, end: date) -> pd.DataFrame:
        try:
            df_all = self.fetch_all_records_df()
            if df_all.empty:
                return self._empty_month_df(start, end)

            user_data = df_all[
                (df_all["uid"].astype(str) == str(uid))
                & (df_all["day"] >= start.isoformat())
                & (df_all["day"] <= end.isoformat())
            ]
            lookup = {r["day"]: r for _, r in user_data.iterrows()}

            rows = []
            for d in daterange(start, end):
                ds = d.isoformat()
                if ds in lookup:
                    r = lookup[ds]
                    rows.append(
                        {
                            "날짜": ds,
                            "QT 시작": r.get("start_time", "") or "",
                            "QT 종료": r.get("end_time", "") or "",
                            "완료": str(r.get("completed", "0")) == "1",
                            # ✅ prayer_note만 사용 + 과거 signature fallback
                            "나의 묵상 기도": (r.get("prayer_note") or r.get("signature") or ""),
                        }
                    )
                else:
                    rows.append({"날짜": ds, "QT 시작": "", "QT 종료": "", "완료": False, "나의 묵상 기도": ""})

            return pd.DataFrame(rows)
        except Exception:
            return self._empty_month_df(start, end)

    def upsert_one(self, uid: str, day: str, **kwargs):
        all_records = self.ws.get_all_records()
        df = pd.DataFrame(all_records)

        row_idx = -1
        if not df.empty and "uid" in df.columns and "day" in df.columns:
            match = df[(df["uid"].astype(str) == str(uid)) & (df["day"].astype(str) == str(day))]
            if not match.empty:
                row_idx = match.index[0] + 2  # header row 때문에 +2

        now_iso = now_kst().isoformat()
        col_map = {"start_time": 3, "end_time": 4, "completed": 5, "signature": 6, "prayer_note": 7}

        def norm_value(k, v):
            if k == "completed":
                return "1" if bool(v) else "0"
            if v is None:
                return ""
            return v.strip() if isinstance(v, str) else v

        if row_idx != -1:
            for k, v in kwargs.items():
                if k in col_map:
                    self.ws.update_cell(row_idx, col_map[k], norm_value(k, v))
            self.ws.update_cell(row_idx, 8, now_iso)
        else:
            new_row = [
                str(uid),
                str(day),
                norm_value("start_time", kwargs.get("start_time", "")),
                norm_value("end_time", kwargs.get("end_time", "")),
                norm_value("completed", kwargs.get("completed", False)),
                norm_value("signature", kwargs.get("signature", "")),
                norm_value("prayer_note", kwargs.get("prayer_note", "")),
                now_iso,
            ]
            self.ws.append_row(new_row)


@st.cache_resource
def get_storage() -> Optional[GoogleSheetsStorage]:
    if not GSHEETS_AVAILABLE:
        return None

    s_id = st.secrets.get("GSHEETS_SPREADSHEET_ID")
    sa_json = st.secrets.get("GSHEETS_SERVICE_ACCOUNT_JSON")
    if not s_id or not sa_json:
        return None

    sa_obj = json.loads(sa_json) if isinstance(sa_json, str) else sa_json
    return GoogleSheetsStorage(s_id, SHEET_WORKSHEET_NAME, sa_obj)


@st.cache_data(ttl=60)
def cached_all_records(storage: GoogleSheetsStorage) -> pd.DataFrame:
    return storage.fetch_all_records_df()


# =========================
# 접근성(어르신 친화) 스타일
# =========================
def apply_accessibility_css():
    st.markdown(
        """
        <style>
          html, body, [class*="css"]  { font-size: 18px !important; }
          .stButton>button {
            height: 54px;
            font-size: 18px;
            border-radius: 14px;
          }
          textarea, input { font-size: 18px !important; }
          details summary { font-size: 18px !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )


# =========================
# UI
# =========================
st.set_page_config(page_title=APP_TITLE, layout="wide")
apply_accessibility_css()

st.title(f"✨ {APP_TITLE}")
st.caption(VERSE_TEXT)

storage = get_storage()
if not storage:
    st.error("구글 시트 설정(Secrets) 또는 gspread 라이브러리를 확인해주세요.")
    st.stop()

mode = st.radio("모드 선택", ["성도님(기록하기)", "관리자(대시보드)"], horizontal=True)

# =========================
# 성도님 모드
# =========================
if mode == "성도님(기록하기)":
    uid = get_uid_from_url()
    if not uid:
        st.info("아래 버튼을 눌러서 ‘나만의 기록지’를 만드세요. (처음 1번만)")
        if st.button("🚀 나의 큐티 링크 만들기", use_container_width=True):
            new_uid = secrets.token_urlsafe(8)
            set_uid_in_url(new_uid)
            st.rerun()
        st.stop()

    month_label = st.selectbox("📆 월 선택", [m[2] for m in SUPPORTED_MONTHS])
    year, month = [(y, m) for (y, m, lbl) in SUPPORTED_MONTHS if lbl == month_label][0]
    START, END = month_range(year, month)

    df = storage.load_month_ui_df(uid, START, END)

    done_cnt = int(df["완료"].sum()) if not df.empty else 0
    total_cnt = len(df) if len(df) > 0 else 1
    progress = done_cnt / total_cnt
    st.metric("이번 달 달성", f"{done_cnt}일", f"{progress:.1%}")
    st.progress(progress)

    share_url = build_share_url(uid)
    st.markdown("### 📌 내 기록지 주소 저장하기")
    st.markdown("**아래 주소를 복사해서 카톡 ‘나에게 보내기’에 저장하거나 즐겨찾기에 저장하세요.**")
    st.code(share_url)
    if "<YOUR-APP>" in share_url:
        st.warning("PUBLIC_APP_URL이 설정되지 않아 임시 주소가 보입니다. Secrets에 실제 앱 주소를 넣어주세요.")

    st.markdown("---")
    st.subheader("✍️ 오늘의 큐티 기록")

    col_a, col_b = st.columns([1, 2])
    with col_a:
        if st.button("📍 오늘로 이동", use_container_width=True):
            st.session_state["picked_day"] = today_kst()
            st.rerun()

    default_day = st.session_state.get("picked_day", today_kst())
    picked_day = st.date_input("날짜 선택", value=default_day, key="picked_day")
    day_str = picked_day.isoformat()

    row = df[df["날짜"] == day_str]
    cur_start = row["QT 시작"].values[0] if not row.empty else ""
    cur_end = row["QT 종료"].values[0] if not row.empty else ""
    is_done = bool(row["완료"].values[0]) if not row.empty else False

    st.info(f"현재 기록:  시작 {cur_start or '-'}  /  종료 {cur_end or '-'}  /  완료 {'예' if is_done else '아니오'}")

    c1, c2, c3 = st.columns(3)
    if c1.button("▶ 시작(현재시간)", use_container_width=True):
        storage.upsert_one(uid, day_str, start_time=now_hhmm_kst())
        st.cache_data.clear()
        st.rerun()

    if c2.button("■ 종료(현재시간)", use_container_width=True):
        storage.upsert_one(uid, day_str, end_time=now_hhmm_kst())
        st.cache_data.clear()
        st.rerun()

    if c3.button("✅ " + ("완료 취소" if is_done else "완료"), use_container_width=True):
        storage.upsert_one(uid, day_str, completed=not is_done)
        st.cache_data.clear()
        st.rerun()

    st.markdown("### 🕊️ 나의 묵상 기도 (50자 이내)")
    memo = st.text_area(
        "경건의 시간 하나님님께서 주신 감동으로 한 줄 묵상 기도를 적어 보세요.",
        height=90,
        max_chars=50,  # ✅ 50자 제한
        placeholder="예) 주님, 오늘 말씀을 붙잡고 순종할 힘을 주세요.",
    )

    if st.button("📝 묵상 기도 저장하기", use_container_width=True, type="primary"):
        memo_clean = (memo or "").strip()
        storage.upsert_one(uid, day_str, signature="", prayer_note=memo_clean)  # ✅ prayer_note에만 저장
        st.success("저장되었습니다!")
        st.cache_data.clear()
        st.rerun()

    # =========================
    # ✅ 주간 단위 기록 확인 (월~일)
    # =========================
    st.markdown("---")
    st.subheader("📋 기록 확인 (주간)")

    show_all = st.toggle("전체 보기 (한 달 전체)", value=False)

    if show_all:
        st.caption("한 달 전체 기록을 한 번에 보여드립니다.")
        st.dataframe(style_qt_table(df), use_container_width=True, hide_index=True)
    else:
        # 주간 기준 날짜(anchor)를 세팅
        # - 기본: 선택한 날짜(picked_day)를 기준으로 그 주를 보여줌
        # - 이전/다음 주 버튼으로 이동 가능
        if "week_anchor" not in st.session_state:
            st.session_state["week_anchor"] = picked_day

        # 사용자가 날짜를 바꾸면 그 날짜의 주로 따라가게
        if st.session_state.get("week_anchor_source_day") != picked_day:
            st.session_state["week_anchor"] = picked_day
            st.session_state["week_anchor_source_day"] = picked_day

        anchor = st.session_state["week_anchor"]

        # 주간 범위 계산(월~일), 월 선택 범위 밖은 clamp 해서 표시
        wk_start = week_start_monday(anchor)
        wk_end = wk_start + timedelta(days=6)

        wk_start_in = clamp_date(wk_start, START, END)
        wk_end_in = clamp_date(wk_end, START, END)

        nav1, nav2, nav3 = st.columns([1, 2, 1])

        with nav1:
            if st.button("⬅ 이전 주", use_container_width=True):
                new_anchor = anchor - timedelta(days=7)
                # 너무 이전으로 가면 월 시작 근처로 clamp
                if new_anchor < START:
                    new_anchor = START
                st.session_state["week_anchor"] = new_anchor
                st.rerun()

        with nav2:
            st.markdown(
                f"<div style='text-align:center; font-weight:600;'>"
                f"{wk_start.strftime('%m/%d')} (월) ~ {wk_end.strftime('%m/%d')} (일)"
                f"</div>",
                unsafe_allow_html=True,
            )
            if wk_start != wk_start_in or wk_end != wk_end_in:
                st.caption("※ 선택한 월 범위에 해당하는 날짜만 표시됩니다.")

        with nav3:
            if st.button("다음 주 ➡", use_container_width=True):
                new_anchor = anchor + timedelta(days=7)
                if new_anchor > END:
                    new_anchor = END
                st.session_state["week_anchor"] = new_anchor
                st.rerun()

        df_week = df[(df["날짜"] >= wk_start_in.isoformat()) & (df["날짜"] <= wk_end_in.isoformat())].copy()
        st.dataframe(style_qt_table(df_week), use_container_width=True, hide_index=True)

# =========================
# 관리자 모드
# =========================
else:
    st.subheader("🔐 관리자 로그인")

    admin_pw = st.secrets.get("ADMIN_PASSWORD", "")
    if not admin_pw:
        st.warning("Secrets에 ADMIN_PASSWORD가 설정되어 있지 않습니다. 관리자 모드를 사용하려면 설정해주세요.")
        st.stop()

    if "is_admin" not in st.session_state:
        st.session_state["is_admin"] = False

    pw = st.text_input("관리자 비밀번호", type="password", placeholder="비밀번호 입력")
    if st.button("로그인", use_container_width=True):
        if pw == admin_pw:
            st.session_state["is_admin"] = True
            st.success("관리자 모드로 전환되었습니다.")
        else:
            st.error("비밀번호가 올바르지 않습니다.")

    if not st.session_state.get("is_admin"):
        st.stop()

    st.markdown("---")
    st.subheader("📊 전체 큐티 현황 대시보드")

    month_label = st.selectbox("📆 월 선택", [m[2] for m in SUPPORTED_MONTHS], key="admin_month")
    year, month = [(y, m) for (y, m, lbl) in SUPPORTED_MONTHS if lbl == month_label][0]
    START, END = month_range(year, month)

    df_all = cached_all_records(storage)
    if df_all.empty:
        st.info("아직 기록이 없습니다.")
        st.stop()

    df_month = df_all[(df_all["day"] >= START.isoformat()) & (df_all["day"] <= END.isoformat())].copy()
    if df_month.empty:
        st.info("선택한 월에는 기록이 없습니다.")
        st.stop()

    df_month["completed_bool"] = df_month["completed"].astype(str).eq("1")

    total_users = df_month["uid"].nunique()
    active_users = df_month[df_month["completed_bool"]]["uid"].nunique()
    total_days = (END - START).days + 1
    total_possible = total_users * total_days if total_users > 0 else 0
    total_completed = int(df_month["completed_bool"].sum())
    completion_rate = (total_completed / total_possible) if total_possible else 0.0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("기록지 사용자 수(UID)", f"{total_users}명")
    c2.metric("완료 경험 사용자 수", f"{active_users}명")
    c3.metric("이번 달 완료 건수", f"{total_completed}건")
    c4.metric("추정 참여율(완료/가능)", f"{completion_rate:.1%}")

    st.markdown("### 📅 일자별 완료 현황")
    by_day = (
        df_month[df_month["completed_bool"]]
        .groupby("day", as_index=False)
        .size()
        .rename(columns={"size": "완료 건수"})
        .sort_values("day")
    )

    full_days = pd.DataFrame({"day": [d.isoformat() for d in daterange(START, END)]})
    by_day = full_days.merge(by_day, on="day", how="left").fillna({"완료 건수": 0})
    by_day["완료 건수"] = by_day["완료 건수"].astype(int)

    st.dataframe(by_day, use_container_width=True, hide_index=True)
    st.bar_chart(by_day.set_index("day")["완료 건수"])

    st.markdown("### 👥 사용자별 완료 횟수(상위)")
    by_user = (
        df_month[df_month["completed_bool"]]
        .groupby("uid", as_index=False)
        .size()
        .rename(columns={"size": "완료 일수"})
        .sort_values("완료 일수", ascending=False)
    )
    st.dataframe(by_user.head(50), use_container_width=True, hide_index=True)

    st.markdown("---")
    st.subheader("⬇️ 구글 시트 데이터 다운로드")

    csv_all = df_all.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        label="전체 원본 데이터 CSV 다운로드",
        data=csv_all,
        file_name="qti_records_all.csv",
        mime="text/csv",
        use_container_width=True,
    )

    csv_month = df_month.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        label=f"{month_label} 데이터 CSV 다운로드",
        data=csv_month,
        file_name=f"qti_records_{year}_{month:02d}.csv",
        mime="text/csv",
        use_container_width=True,
    )

    st.caption("※ 다운로드한 CSV를 엑셀/구글시트에서 열어 추가 분석할 수 있습니다.")

