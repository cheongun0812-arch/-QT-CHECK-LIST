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
# Query Params (구버전 API로만 통일)
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
    return d - timedelta(days=d.weekday())  # 월=0


def clamp_date(d: date, start: date, end: date) -> date:
    if d < start:
        return start
    if d > end:
        return end
    return d


def days_in_year(year: int) -> int:
    return (date(year + 1, 1, 1) - date(year, 1, 1)).days


def iso_to_date(s: str) -> Optional[date]:
    try:
        return date.fromisoformat(str(s))
    except Exception:
        return None


# =========================
# 재미 요소(뱃지/스트릭)
# =========================
def badge_for_rate(rate: float) -> tuple[str, str]:
    """
    월 참여율 기준 뱃지 지급
    30% 🥉 / 60% 🥈 / 90% 🥇
    """
    if rate >= 0.9:
        return "🥇", "골드 뱃지"
    if rate >= 0.6:
        return "🥈", "실버 뱃지"
    if rate >= 0.3:
        return "🥉", "브론즈 뱃지"
    return "🌱", "새싹 시작!"

def streak_label(streak: int) -> str:
    if streak >= 7:
        return f"🌟 {streak}일 연속"
    if streak >= 3:
        return f"🔥 {streak}일 연속"
    if streak >= 1:
        return f"✨ {streak}일 연속"
    return "오늘부터 시작해요! 🙏"

def compute_current_streak(completed_days: set[date], ref: date) -> tuple[int, Optional[date]]:
    """
    '현재' 스트릭:
    - ref(오늘)부터 거꾸로 연속 완료면 계속 카운트
    - 오늘이 미완료면, 마지막 완료일을 기준으로 연속 완료를 계산 (끊김 상태도 보여주기 좋게)
    반환: (streak_length, streak_end_day)
    """
    if not completed_days:
        return 0, None

    d = ref
    if d not in completed_days:
        # 마지막 완료일로 이동
        past = [x for x in completed_days if x <= ref]
        if not past:
            return 0, None
        d = max(past)

    end_day = d
    cnt = 0
    while d in completed_days:
        cnt += 1
        d = d - timedelta(days=1)
    return cnt, end_day

def compute_best_streak_in_month(df_month_ui: pd.DataFrame) -> int:
    """
    df_month_ui: 날짜(ISO), 완료(bool)
    """
    if df_month_ui.empty:
        return 0
    df2 = df_month_ui.copy()
    df2["d"] = df2["날짜"].apply(lambda x: iso_to_date(x))
    df2 = df2.dropna(subset=["d"]).sort_values("d")
    best = 0
    cur = 0
    for _, r in df2.iterrows():
        if bool(r["완료"]):
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


# =========================
# 기록 표 렌더링(정렬 고정: HTML 테이블)
# =========================
def render_qt_table_html(df: pd.DataFrame, title: Optional[str] = None):
    """
    - 날짜/QT 시작/QT 종료/완료: 중앙 정렬 (헤더+데이터)
    - 나의 묵상 기도: 왼쪽 정렬
    - Streamlit dataframe 정렬이 환경에 따라 풀리는 문제를 방지하기 위해 HTML로 렌더링
    """
    if df is None or df.empty:
        if title:
            st.markdown(f"**{title}**")
        st.info("표시할 기록이 없습니다.")
        return

    dfx = df.copy()

    # 완료 체크는 보기 좋게 ✅로 표시
    if "완료" in dfx.columns:
        dfx["완료"] = dfx["완료"].apply(lambda x: "✅" if bool(x) else "")

    # 컬럼 순서 보장
    cols = [c for c in ["날짜", "QT 시작", "QT 종료", "완료", "나의 묵상 기도"] if c in dfx.columns]
    dfx = dfx[cols]

    html = dfx.to_html(index=False, escape=True, classes="qti-table")

    st.markdown(
        """
        <style>
          .qti-table-wrap { overflow-x: auto; }
          table.qti-table {
            width: 100%;
            border-collapse: separate;
            border-spacing: 0;
            border-radius: 16px;
            overflow: hidden;
            box-shadow: 0 8px 22px rgba(0,0,0,0.07);
            border: 1px solid rgba(0,0,0,0.06);
          }
          table.qti-table thead th {
            text-align: center !important;
            font-weight: 800;
            background: linear-gradient(135deg, #f7fbff 0%, #fff7fb 100%);
            padding: 10px 10px;
            border-bottom: 1px solid rgba(0,0,0,0.06);
            white-space: nowrap;
          }
          table.qti-table tbody td {
            text-align: center !important;   /* 기본: 중앙 */
            padding: 10px 10px;
            border-bottom: 1px solid rgba(0,0,0,0.06);
            background: #ffffff;
            white-space: nowrap;
            vertical-align: top;
          }

          /* 5번째 컬럼(나의 묵상 기도)은 왼쪽 정렬 + 줄바꿈 허용 */
          table.qti-table tbody td:nth-child(5) {
            text-align: left !important;
            white-space: normal;
            line-height: 1.35;
          }

          /* 컬럼 폭(대략) */
          table.qti-table th:nth-child(1), table.qti-table td:nth-child(1) { width: 120px; }
          table.qti-table th:nth-child(2), table.qti-table td:nth-child(2) { width: 90px; }
          table.qti-table th:nth-child(3), table.qti-table td:nth-child(3) { width: 90px; }
          table.qti-table th:nth-child(4), table.qti-table td:nth-child(4) { width: 70px; }
          table.qti-table th:nth-child(5), table.qti-table td:nth-child(5) { width: auto; }

          /* 마지막 줄 border 제거 */
          table.qti-table tbody tr:last-child td { border-bottom: none; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    if title:
        st.markdown(f"**{title}**")

    st.markdown(f"<div class='qti-table-wrap'>{html}</div>", unsafe_allow_html=True)


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
                row_idx = match.index[0] + 2  # header row +2

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
# 접근성 + 대시보드 카드 스타일
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

          .qti-cards { display:flex; gap:14px; flex-wrap:wrap; margin: 8px 0 8px 0; }
          .qti-card {
            flex: 1 1 260px;
            border-radius: 18px;
            padding: 14px 16px;
            box-shadow: 0 8px 22px rgba(0,0,0,0.08);
            border: 1px solid rgba(0,0,0,0.06);
          }
          .qti-title { font-weight: 800; font-size: 18px; margin-bottom: 6px; }
          .qti-rate { font-weight: 900; font-size: 26px; line-height: 1.1; }
          .qti-sub { opacity: 0.85; font-size: 15px; margin-top: 4px; }

          .qti-strip {
            margin-top: 10px;
            padding: 12px 14px;
            border-radius: 16px;
            border: 1px solid rgba(0,0,0,0.06);
            box-shadow: 0 8px 22px rgba(0,0,0,0.06);
            background: linear-gradient(135deg, #fff7d6 0%, #f2f8ff 45%, #ffe7ff 100%);
          }
          .qti-strip-title { font-weight: 900; font-size: 18px; margin-bottom: 6px; }
          .qti-strip-line { font-weight: 700; font-size: 16px; }
          .qti-chip {
            display:inline-block;
            padding: 4px 10px;
            border-radius: 999px;
            font-size: 13px;
            font-weight: 800;
            background: rgba(255,255,255,0.7);
            border: 1px solid rgba(0,0,0,0.06);
            margin-left: 8px;
          }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_qti_dashboard(
    week_rate, week_n, week_den,
    month_rate, month_n, month_den,
    year_rate, year_n, year_den,
    badge_emoji, badge_name,
    current_streak, streak_end_day,
    best_streak_month
):
    st.markdown("## 🌈 나의 큐티 현황")

    st.markdown(
        f"""
        <div class="qti-cards">
          <div class="qti-card" style="background: linear-gradient(135deg, #ffe1e1 0%, #fff7f7 45%, #e8f1ff 100%);">
            <div class="qti-title">주별 큐티 현황 <span class="qti-chip">월~일</span></div>
            <div class="qti-rate">참여율 {week_rate:.0%}</div>
            <div class="qti-sub">({week_n}/{week_den}일)</div>
          </div>

          <div class="qti-card" style="background: linear-gradient(135deg, #e6f0ff 0%, #f2f8ff 45%, #fff1d6 100%);">
            <div class="qti-title">월 누적 현황 <span class="qti-chip">이번 달</span></div>
            <div class="qti-rate">참여율 {month_rate:.0%}</div>
            <div class="qti-sub">({month_n}/{month_den}일)</div>
          </div>

          <div class="qti-card" style="background: linear-gradient(135deg, #eafff1 0%, #f4fff8 45%, #ffe7ff 100%);">
            <div class="qti-title">연간 참여율 <span class="qti-chip">올해</span></div>
            <div class="qti-rate">참여율 {year_rate:.0%}</div>
            <div class="qti-sub">({year_n}/{year_den}일)</div>
          </div>
        </div>

        <div class="qti-strip">
          <div class="qti-strip-title">🎁 오늘의 응원</div>
          <div class="qti-strip-line">이번 달 뱃지: <b>{badge_emoji} {badge_name}</b></div>
          <div class="qti-strip-line">연속 참여: <b>{streak_label(current_streak)}</b> <span class="qti-chip">{('기준일 ' + str(streak_end_day)) if streak_end_day else '아직 기록이 없어요'}</span></div>
          <div class="qti-strip-line">이번 달 최고 연속: <b>{('🌟 ' + str(best_streak_month) + '일') if best_streak_month >= 7 else ('🔥 ' + str(best_streak_month) + '일') if best_streak_month >= 3 else (str(best_streak_month) + '일')}</b></div>
        </div>
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

    # 월 선택
    month_label = st.selectbox("📆 월 선택", [m[2] for m in SUPPORTED_MONTHS])
    year, month = [(y, m) for (y, m, lbl) in SUPPORTED_MONTHS if lbl == month_label][0]
    START, END = month_range(year, month)

    # 사용자 월 데이터(표용)
    df = storage.load_month_ui_df(uid, START, END)

    # ====== 오늘/선택일(대시보드 주간 기준일) ======
    base_day = st.session_state.get("picked_day", today_kst())

    # ====== 사용자 전체 기록 로드(연간/스트릭용) ======
    df_all = cached_all_records(storage)
    df_user = df_all[df_all["uid"].astype(str) == str(uid)].copy() if not df_all.empty else pd.DataFrame(columns=df_all.columns)
    if not df_user.empty:
        df_user["completed_bool"] = df_user["completed"].astype(str).eq("1")
    else:
        df_user["completed_bool"] = False

    completed_dates = set()
    if not df_user.empty:
        for s in df_user[df_user["completed_bool"]]["day"].astype(str).unique().tolist():
            d = iso_to_date(s)
            if d:
                completed_dates.add(d)

    # ====== 주/월/연 통계 ======
    # 주(월~일)
    wk_start = week_start_monday(base_day)
    wk_end = wk_start + timedelta(days=6)
    week_completed_days = sum(1 for d in daterange(wk_start, wk_end) if d in completed_dates)
    week_den = 7
    week_rate = week_completed_days / week_den if week_den else 0.0

    # 월 누적(선택 월)
    month_completed_days = int(df["완료"].sum()) if not df.empty else 0
    month_den = (END - START).days + 1
    month_rate = month_completed_days / month_den if month_den else 0.0

    # 연간(올해)
    this_year = today_kst().year
    year_den = days_in_year(this_year)
    y_start = date(this_year, 1, 1)
    y_end = date(this_year, 12, 31)
    year_completed_days = sum(1 for d in daterange(y_start, y_end) if d in completed_dates)
    year_rate = year_completed_days / year_den if year_den else 0.0

    # ====== 뱃지 + 스트릭 ======
    badge_emoji, badge_name = badge_for_rate(month_rate)

    current_streak, streak_end_day = compute_current_streak(completed_dates, today_kst())
    best_streak_month = compute_best_streak_in_month(df)

    # ====== 대시보드 출력 ======
    render_qti_dashboard(
        week_rate, week_completed_days, week_den,
        month_rate, month_completed_days, month_den,
        year_rate, year_completed_days, year_den,
        badge_emoji, badge_name,
        current_streak, streak_end_day,
        best_streak_month
    )

    # 공유 URL
    share_url = build_share_url(uid)
    st.markdown("### 📌 내 기록지 주소 저장하기")
    st.markdown("**아래 주소를 복사해서 카톡 ‘나에게 보내기’에 저장하거나 즐겨찾기에 저장하세요.**")
    st.code(share_url)
    if "<YOUR-APP>" in share_url:
        st.warning("PUBLIC_APP_URL이 설정되지 않아 임시 주소가 보입니다. Secrets에 실제 앱 주소를 넣어주세요.")

    st.markdown("---")
    st.subheader("✍️ 오늘의 큐티 기록")

    col_a, _ = st.columns([1, 2])
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
        max_chars=50,
        placeholder="예) 주님, 오늘 말씀을 붙잡고 순종할 힘을 주세요.",
    )

    if st.button("📝 묵상 기도 저장하기", use_container_width=True, type="primary"):
        memo_clean = (memo or "").strip()
        storage.upsert_one(uid, day_str, signature="", prayer_note=memo_clean)
        st.success("저장되었습니다!")
        st.cache_data.clear()
        st.rerun()

    # =========================
    # 주간 단위 기록 확인(월~일) + 전체 보기 토글
    # =========================
    st.markdown("---")
    st.subheader("📋 기록 확인 (주간)")

    show_all = st.toggle("전체 보기 (한 달 전체)", value=False)

    if show_all:
        st.caption("한 달 전체 기록을 한 번에 보여드립니다.")
        render_qt_table_html(df, title=None)
    else:
        # 주간 기준 날짜(anchor)
        if "week_anchor" not in st.session_state:
            st.session_state["week_anchor"] = picked_day

        # 날짜 변경 시 그 주로 따라가기
        if st.session_state.get("week_anchor_source_day") != picked_day:
            st.session_state["week_anchor"] = picked_day
            st.session_state["week_anchor_source_day"] = picked_day

        anchor = st.session_state["week_anchor"]
        wk_start2 = week_start_monday(anchor)
        wk_end2 = wk_start2 + timedelta(days=6)

        wk_start_in = clamp_date(wk_start2, START, END)
        wk_end_in = clamp_date(wk_end2, START, END)

        nav1, nav2, nav3 = st.columns([1, 2, 1])

        with nav1:
            if st.button("⬅ 이전 주", use_container_width=True):
                new_anchor = anchor - timedelta(days=7)
                if new_anchor < START:
                    new_anchor = START
                st.session_state["week_anchor"] = new_anchor
                st.rerun()

        with nav2:
            st.markdown(
                f"<div style='text-align:center; font-weight:900;'>"
                f"{wk_start2.strftime('%m/%d')} (월) ~ {wk_end2.strftime('%m/%d')} (일)"
                f"</div>",
                unsafe_allow_html=True,
            )
            if wk_start2 != wk_start_in or wk_end2 != wk_end_in:
                st.caption("※ 선택한 월 범위에 해당하는 날짜만 표시됩니다.")

        with nav3:
            if st.button("다음 주 ➡", use_container_width=True):
                new_anchor = anchor + timedelta(days=7)
                if new_anchor > END:
                    new_anchor = END
                st.session_state["week_anchor"] = new_anchor
                st.rerun()

        df_week = df[(df["날짜"] >= wk_start_in.isoformat()) & (df["날짜"] <= wk_end_in.isoformat())].copy()
        render_qt_table_html(df_week, title=None)

# =========================
# 관리자 모드(기존 유지)
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

