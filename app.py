import json
import secrets
import re
from datetime import date, datetime, timedelta
from typing import Optional
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from io import BytesIO

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# (선택) 리포트 생성용
try:
    import matplotlib.pyplot as plt
    MATPLOTLIB_OK = True
except Exception:
    MATPLOTLIB_OK = False

try:
    # PDF는 reportlab 있으면 제공(없으면 자동으로 비활성화)
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    REPORTLAB_OK = True
except Exception:
    REPORTLAB_OK = False


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

KST = ZoneInfo("Asia/Seoul")

# 경고 기준(관리자)
DEFAULT_DROP_ALERT_PCT = 0.05     # 지난주 대비 -5%p 하락하면 경고
DEFAULT_LOW_WEEK_RATE_PCT = 0.20  # 이번주 참여율이 20% 미만이면 경고


def now_kst() -> datetime:
    return datetime.now(tz=KST)


def today_kst() -> date:
    return now_kst().date()


def now_hhmm_kst() -> str:
    return now_kst().strftime("%H:%M")


# =========================
# 무결성(시간/텍스트)
# =========================
_HHMM = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def normalize_hhmm(s: str) -> str:
    s = (s or "").strip()
    return s if _HHMM.match(s) else ""


def clamp_50(s: str) -> str:
    s = (s or "").strip()
    return s[:50]


def iso_to_date(s: str) -> Optional[date]:
    try:
        return date.fromisoformat(str(s))
    except Exception:
        return None


def days_in_year(year: int) -> int:
    return (date(year + 1, 1, 1) - date(year, 1, 1)).days


# =========================
# Query Params (구버전 API로 통일)
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


# =========================
# 재미 요소(뱃지/스트릭)
# =========================
def badge_for_rate(rate: float) -> tuple[str, str]:
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
    if not completed_days:
        return 0, None

    d = ref
    if d not in completed_days:
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
# 표 렌더링(정렬 고정: HTML)
# =========================
def render_qt_table_html(df: pd.DataFrame):
    if df is None or df.empty:
        st.info("표시할 기록이 없습니다.")
        return

    dfx = df.copy()
    if "완료" in dfx.columns:
        dfx["완료"] = dfx["완료"].apply(lambda x: "✅" if bool(x) else "")

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
            font-weight: 900;
            background: linear-gradient(135deg, #f7fbff 0%, #fff7fb 100%);
            padding: 10px 10px;
            border-bottom: 1px solid rgba(0,0,0,0.06);
            white-space: nowrap;
          }
          table.qti-table tbody td {
            text-align: center !important;
            padding: 10px 10px;
            border-bottom: 1px solid rgba(0,0,0,0.06);
            background: #ffffff;
            white-space: nowrap;
            vertical-align: top;
          }
          table.qti-table tbody td:nth-child(5) {
            text-align: left !important;
            white-space: normal;
            line-height: 1.35;
          }
          table.qti-table th:nth-child(1), table.qti-table td:nth-child(1) { width: 120px; }
          table.qti-table th:nth-child(2), table.qti-table td:nth-child(2) { width: 90px; }
          table.qti-table th:nth-child(3), table.qti-table td:nth-child(3) { width: 90px; }
          table.qti-table th:nth-child(4), table.qti-table td:nth-child(4) { width: 70px; }
          table.qti-table th:nth-child(5), table.qti-table td:nth-child(5) { width: auto; }
          table.qti-table tbody tr:last-child td { border-bottom: none; }
        </style>
        """,
        unsafe_allow_html=True,
    )

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
                            "QT 시작": normalize_hhmm(r.get("start_time", "") or ""),
                            "QT 종료": normalize_hhmm(r.get("end_time", "") or ""),
                            "완료": str(r.get("completed", "0")) == "1",
                            "나의 묵상 기도": clamp_50((r.get("prayer_note") or r.get("signature") or "")),
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
            if k in ("start_time", "end_time"):
                return normalize_hhmm(str(v))
            if k in ("prayer_note", "signature"):
                return clamp_50(str(v))
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
def cached_all_records() -> pd.DataFrame:
    s = get_storage()
    if not s:
        return pd.DataFrame(columns=["uid", "day", "start_time", "end_time", "completed", "signature", "prayer_note", "updated_at"])
    try:
        return s.fetch_all_records_df()
    except Exception:
        return pd.DataFrame(columns=["uid", "day", "start_time", "end_time", "completed", "signature", "prayer_note", "updated_at"])


# =========================
# 스타일(접근성 + 대시보드 + 자동슬라이드)
# =========================
def apply_css():
    st.markdown(
        """
        <style>
          html, body, [class*="css"]  { font-size: 18px !important; }
          .stButton>button { height: 54px; font-size: 18px; border-radius: 14px; }
          textarea, input { font-size: 18px !important; }

          .qti-cards { display:flex; gap:14px; flex-wrap:wrap; margin: 8px 0 8px 0; }
          .qti-card {
            flex: 1 1 260px;
            border-radius: 18px;
            padding: 14px 16px;
            box-shadow: 0 8px 22px rgba(0,0,0,0.08);
            border: 1px solid rgba(0,0,0,0.06);
          }
          .qti-title { font-weight: 900; font-size: 18px; margin-bottom: 6px; }
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
          .qti-strip-line { font-weight: 800; font-size: 16px; }
          .qti-chip {
            display:inline-block;
            padding: 4px 10px;
            border-radius: 999px;
            font-size: 13px;
            font-weight: 900;
            background: rgba(255,255,255,0.75);
            border: 1px solid rgba(0,0,0,0.06);
            margin-left: 8px;
          }

          /* 주소 블록 자동 슬라이드 */
          #shareBlock {
            overflow: hidden;
            transition: max-height 550ms ease, opacity 550ms ease, transform 550ms ease;
            max-height: 500px;
            opacity: 1;
            transform: translateY(0px);
          }
          #shareBlock.hidden {
            max-height: 0px;
            opacity: 0;
            transform: translateY(10px);
          }
        </style>
        """,
        unsafe_allow_html=True,
    )


def js_hide_share_block_after_5s():
    components.html(
        """
        <script>
          (function() {
            const el = window.parent.document.getElementById('shareBlock');
            if (!el) return;
            if (window.__shareHideTimer) clearTimeout(window.__shareHideTimer);
            window.__shareHideTimer = setTimeout(() => {
              el.classList.add('hidden');
            }, 5000);
          })();
        </script>
        """,
        height=0,
    )


def js_show_share_block_now():
    components.html(
        """
        <script>
          (function() {
            const el = window.parent.document.getElementById('shareBlock');
            if (!el) return;
            el.classList.remove('hidden');
          })();
        </script>
        """,
        height=0,
    )


# =========================
# 대시보드 렌더(성도님)
# =========================
def render_my_dashboard(
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
# 관리자 대시보드 유틸
# =========================
def uid_completed_days(df_all: pd.DataFrame, start: date, end: date) -> set[str]:
    if df_all.empty:
        return set()
    dfx = df_all.copy()
    dfx["completed_bool"] = dfx["completed"].astype(str).eq("1")
    dfx = dfx[dfx["completed_bool"]]
    dfx = dfx[(dfx["day"] >= start.isoformat()) & (dfx["day"] <= end.isoformat())]
    return set(dfx["uid"].astype(str).unique().tolist())


def make_week_series(df_all: pd.DataFrame, weeks: int = 8, anchor: Optional[date] = None) -> pd.DataFrame:
    anchor = anchor or today_kst()
    cur_wk_start = week_start_monday(anchor)
    rows = []
    for i in range(weeks):
        wk_start = cur_wk_start - timedelta(days=7 * (weeks - 1 - i))
        wk_end = wk_start + timedelta(days=6)
        uids = uid_completed_days(df_all, wk_start, wk_end)
        rows.append({"주(월~일)": f"{wk_start.strftime('%m/%d')}~{wk_end.strftime('%m/%d')}", "참여 UID 수": len(uids)})
    return pd.DataFrame(rows)


# =========================
# 관리자 리포트 생성(PNG/PDF)
# =========================
def build_weekly_report_png(
    title: str,
    this_wk_start: date,
    this_wk_end: date,
    month_label: str,
    month_users_cnt: int,
    this_week_participants: int,
    this_week_rate: float,
    last_week_rate: float,
    delta: float,
    drop_count: int,
    wk_series: pd.DataFrame,
) -> Optional[bytes]:
    if not MATPLOTLIB_OK:
        return None

    fig = plt.figure(figsize=(10, 6), dpi=160)
    ax = fig.add_axes([0.06, 0.12, 0.88, 0.78])
    ax.axis("off")

    # 상단 텍스트 블록
    lines = [
        title,
        f"기간: {this_wk_start.isoformat()} ~ {this_wk_end.isoformat()} (월~일)",
        f"기준 월: {month_label} / 월 사용자 수(UID): {month_users_cnt}명",
        "",
        f"이번 주 참여: {this_week_participants}명 / 참여율: {this_week_rate:.1%}",
        f"지난 주 참여율: {last_week_rate:.1%} / 변화: {delta:+.1%}",
        f"지난주 참여→이번주 미참여(케어 후보): {drop_count}명",
        "",
        "최근 8주 참여 UID 수(추이):",
    ]
    ax.text(0.0, 1.0, "\n".join(lines), va="top", ha="left", fontsize=12, fontweight="bold")

    # 간단 미니바 차트(최근 8주)
    if wk_series is not None and not wk_series.empty:
        # 아래쪽에 작은 차트 공간
        ax2 = fig.add_axes([0.08, 0.06, 0.84, 0.18])
        x = list(range(len(wk_series)))
        y = wk_series["참여 UID 수"].astype(int).tolist()
        ax2.bar(x, y)
        ax2.set_xticks(x)
        ax2.set_xticklabels(wk_series["주(월~일)"].tolist(), rotation=45, ha="right", fontsize=8)
        ax2.set_ylabel("UID 수", fontsize=9)
        ax2.grid(axis="y", linestyle="--", alpha=0.3)

    buf = BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def build_weekly_report_pdf(png_bytes: bytes, title: str) -> Optional[bytes]:
    if not REPORTLAB_OK or not png_bytes:
        return None

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4

    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, h - 50, title)

    # 이미지 삽입
    img = ImageReader(BytesIO(png_bytes))
    # A4에 맞게 적당히 축소
    img_w = w - 80
    img_h = h - 120
    c.drawImage(img, 40, 60, width=img_w, height=img_h, preserveAspectRatio=True, anchor="c")

    c.showPage()
    c.save()
    return buf.getvalue()


# =========================
# UI
# =========================
st.set_page_config(page_title=APP_TITLE, layout="wide")
apply_css()

st.title(f"✨ {APP_TITLE}")
st.caption(VERSE_TEXT)

storage = get_storage()
if not storage:
    st.error("구글 시트 설정(Secrets) 또는 gspread 라이브러리를 확인해주세요.")
    st.stop()

# ---- 사이드바: 혼란 줄이기 + 관리자 설정
st.sidebar.header("⚙️ 설정")
simple_mode = st.sidebar.toggle("어르신 모드(간단 화면)", value=False, help="오늘 기록 중심으로 크게 보여드려요.")
st.sidebar.caption("※ 기본 화면은 지금처럼 유지됩니다.")

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

    # 전체 기록(연간/스트릭/통계용)
    df_all = cached_all_records()
    df_user = df_all[df_all["uid"].astype(str) == str(uid)].copy() if not df_all.empty else pd.DataFrame(columns=df_all.columns)
    if not df_user.empty:
        df_user["completed_bool"] = df_user["completed"].astype(str).eq("1")
    else:
        df_user["completed_bool"] = False

    completed_dates: set[date] = set()
    if not df_user.empty:
        for s in df_user[df_user["completed_bool"]]["day"].astype(str).unique().tolist():
            d = iso_to_date(s)
            if d:
                completed_dates.add(d)

    # 주간 기준일
    base_day = st.session_state.get("picked_day", today_kst())
    wk_start = week_start_monday(base_day)
    wk_end = wk_start + timedelta(days=6)
    week_completed_days = sum(1 for d in daterange(wk_start, wk_end) if d in completed_dates)
    week_den = 7
    week_rate = week_completed_days / week_den if week_den else 0.0

    # 월/연
    month_completed_days = int(df["완료"].sum()) if not df.empty else 0
    month_den = (END - START).days + 1
    month_rate = month_completed_days / month_den if month_den else 0.0

    this_year = today_kst().year
    year_den = days_in_year(this_year)
    y_start = date(this_year, 1, 1)
    y_end = date(this_year, 12, 31)
    year_completed_days = sum(1 for d in daterange(y_start, y_end) if d in completed_dates)
    year_rate = year_completed_days / year_den if year_den else 0.0

    badge_emoji, badge_name = badge_for_rate(month_rate)
    current_streak, streak_end_day = compute_current_streak(completed_dates, today_kst())
    best_streak_month = compute_best_streak_in_month(df)

    render_my_dashboard(
        week_rate, week_completed_days, week_den,
        month_rate, month_completed_days, month_den,
        year_rate, year_completed_days, year_den,
        badge_emoji, badge_name,
        current_streak, streak_end_day,
        best_streak_month
    )

    # =========================
    # 내 기록지 주소 저장하기(접속시 5초 후 자동 숨김)
    # =========================
    share_url = build_share_url(uid)

    if st.button("🔗 주소 다시 보기", use_container_width=True):
        js_show_share_block_now()

    st.markdown("<div id='shareBlock'>", unsafe_allow_html=True)
    st.markdown("### 📌 내 기록지 주소 저장하기")
    st.markdown("**아래 주소를 복사해서 카톡 ‘나에게 보내기’에 저장하거나 즐겨찾기에 저장하세요.**")
    st.code(share_url)
    if "<YOUR-APP>" in share_url:
        st.warning("PUBLIC_APP_URL이 설정되지 않아 임시 주소가 보입니다. Secrets에 실제 앱 주소를 넣어주세요.")
    st.markdown("</div>", unsafe_allow_html=True)

    js_hide_share_block_after_5s()

    st.markdown("---")
    st.subheader("✍️ 오늘의 큐티 기록")

    # 날짜 선택
    if simple_mode:
        picked_day = today_kst()
        st.info(f"오늘 날짜: {picked_day.isoformat()} (어르신 모드)")
    else:
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

    def safe_write(action_fn):
        try:
            action_fn()
            st.cache_data.clear()
            st.rerun()
        except Exception:
            st.error("저장 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요. (네트워크/권한/구글시트 상태 확인)")

    c1, c2, c3 = st.columns(3)
    if c1.button("▶ 시작(현재시간)", use_container_width=True):
        safe_write(lambda: storage.upsert_one(uid, day_str, start_time=now_hhmm_kst()))
    if c2.button("■ 종료(현재시간)", use_container_width=True):
        safe_write(lambda: storage.upsert_one(uid, day_str, end_time=now_hhmm_kst()))
    if c3.button("✅ " + ("완료 취소" if is_done else "완료"), use_container_width=True):
        safe_write(lambda: storage.upsert_one(uid, day_str, completed=not is_done))

    st.markdown("### 🕊️ 나의 묵상 기도 (50자 이내)")
    memo = st.text_area(
        "경건의 시간 하나님님께서 주신 감동으로 한 줄 묵상 기도를 적어 보세요.",
        height=90,
        max_chars=50,
        placeholder="예) 주님, 오늘 말씀을 붙잡고 순종할 힘을 주세요.",
    )
    if st.button("📝 묵상 기도 저장하기", use_container_width=True, type="primary"):
        memo_clean = clamp_50(memo)
        safe_write(lambda: storage.upsert_one(uid, day_str, signature="", prayer_note=memo_clean))

    st.markdown("---")
    if simple_mode:
        with st.expander("📋 기록 확인(주간/전체) 열기", expanded=False):
            show_all = st.toggle("전체 보기 (한 달 전체)", value=False, key="show_all_simple")
            if show_all:
                st.caption("한 달 전체 기록을 한 번에 보여드립니다.")
                render_qt_table_html(df)
            else:
                anchor = picked_day
                wk_start2 = week_start_monday(anchor)
                wk_end2 = wk_start2 + timedelta(days=6)
                wk_start_in = clamp_date(wk_start2, START, END)
                wk_end_in = clamp_date(wk_end2, START, END)
                df_week = df[(df["날짜"] >= wk_start_in.isoformat()) & (df["날짜"] <= wk_end_in.isoformat())].copy()
                render_qt_table_html(df_week)
    else:
        st.subheader("📋 기록 확인 (주간)")
        show_all = st.toggle("전체 보기 (한 달 전체)", value=False)

        if show_all:
            st.caption("한 달 전체 기록을 한 번에 보여드립니다.")
            render_qt_table_html(df)
        else:
            if "week_anchor" not in st.session_state:
                st.session_state["week_anchor"] = picked_day
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
            render_qt_table_html(df_week)

# =========================
# 관리자 모드(강화 + 주간리포트 + 경고배너)
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

    # 관리자 경고 임계치 설정
    st.sidebar.subheader("📣 관리자 경고 설정")
    drop_alert = st.sidebar.slider("지난 주 대비 하락 경고(퍼센트포인트)", 0.0, 0.30, DEFAULT_DROP_ALERT_PCT, 0.01)
    low_week_alert = st.sidebar.slider("이번 주 참여율 낮음 경고", 0.0, 0.50, DEFAULT_LOW_WEEK_RATE_PCT, 0.01)

    month_label = st.selectbox("📆 월 선택", [m[2] for m in SUPPORTED_MONTHS], key="admin_month")
    year, month = [(y, m) for (y, m, lbl) in SUPPORTED_MONTHS if lbl == month_label][0]
    START, END = month_range(year, month)

    df_all = cached_all_records()
    if df_all.empty:
        st.info("아직 기록이 없습니다.")
        st.stop()

    df_all["completed_bool"] = df_all["completed"].astype(str).eq("1")

    df_month = df_all[(df_all["day"] >= START.isoformat()) & (df_all["day"] <= END.isoformat())].copy()
    if df_month.empty:
        st.info("선택한 월에는 기록이 없습니다.")
        st.stop()

    total_users = df_month["uid"].astype(str).nunique()
    active_users = df_month[df_month["completed_bool"]]["uid"].astype(str).nunique()
    total_days = (END - START).days + 1
    total_possible = total_users * total_days if total_users > 0 else 0
    total_completed = int(df_month["completed_bool"].sum())
    completion_rate = (total_completed / total_possible) if total_possible else 0.0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("기록지 사용자 수(UID)", f"{total_users}명")
    c2.metric("완료 경험 사용자 수", f"{active_users}명")
    c3.metric("이번 달 완료 건수", f"{total_completed}건")
    c4.metric("추정 참여율(완료/가능)", f"{completion_rate:.1%}")

    # ===== 주간 비교(이번주 vs 지난주) =====
    today = today_kst()
    this_wk_start = week_start_monday(today)
    this_wk_end = this_wk_start + timedelta(days=6)
    last_wk_start = this_wk_start - timedelta(days=7)
    last_wk_end = last_wk_start + timedelta(days=6)

    this_week_uids = uid_completed_days(df_all, this_wk_start, this_wk_end)
    last_week_uids = uid_completed_days(df_all, last_wk_start, last_wk_end)

    month_users = set(df_month["uid"].astype(str).unique().tolist())
    month_users_cnt = len(month_users)

    this_week_participants = len(this_week_uids & month_users)
    last_week_participants = len(last_week_uids & month_users)

    this_week_rate = (this_week_participants / month_users_cnt) if month_users_cnt else 0.0
    last_week_rate = (last_week_participants / month_users_cnt) if month_users_cnt else 0.0
    delta = this_week_rate - last_week_rate

    drop_uids = sorted(list((last_week_uids & month_users) - (this_week_uids & month_users)))
    drop_count = len(drop_uids)

    # ✅ 경고 배너(상단)
    if month_users_cnt > 0:
        if this_week_rate < low_week_alert:
            st.error(f"⚠️ 이번 주 참여율이 낮습니다: {this_week_rate:.1%} (기준 {low_week_alert:.1%} 미만)")
        if delta <= -abs(drop_alert):
            st.warning(f"📉 지난 주 대비 참여율이 하락했습니다: {delta:+.1%}p (경고 기준 -{drop_alert:.1%}p)")

    st.markdown("### 📈 주간 참여 현황(목회자용)")
    w1, w2, w3, w4 = st.columns(4)
    w1.metric("이번 주 참여율", f"{this_week_rate:.1%}", f"{delta:+.1%}")
    w2.metric("이번 주 참여 UID", f"{this_week_participants}명")
    w3.metric("지난 주 참여 UID", f"{last_week_participants}명")
    w4.metric("이번 주 미참여 추정", f"{max(0, month_users_cnt - this_week_participants)}명")

    if drop_uids:
        with st.expander("⚠️ 지난주 참여했는데 이번주 미참여(케어 우선 후보)", expanded=False):
            st.write(drop_uids[:300])
            if len(drop_uids) > 300:
                st.caption(f"… {len(drop_uids)-300}명 더 있음")
    else:
        st.success("이번 주에는 지난주 대비 참여 이탈이 발견되지 않았습니다(월 기준 사용자 내).")

    # ===== 최근 8주 추이 =====
    st.markdown("### 🗓️ 최근 8주 참여 추이(완료한 UID 수)")
    wk_series = make_week_series(df_all, weeks=8, anchor=today)
    st.dataframe(wk_series, use_container_width=True, hide_index=True)
    st.bar_chart(wk_series.set_index("주(월~일)")["참여 UID 수"])

    # ===== 상위 참여자 =====
    st.markdown("### 🏆 Top 참여자(이번 달, 익명 UID)")
    top = (
        df_month[df_month["completed_bool"]]
        .groupby("uid", as_index=False)["day"]
        .nunique()
        .rename(columns={"day": "완료 일수"})
        .sort_values("완료 일수", ascending=False)
    )
    st.dataframe(top.head(50), use_container_width=True, hide_index=True)

    # =========================
    # ✅ 주간 리포트 자동 생성(PNG/PDF)
    # =========================
    st.markdown("---")
    st.subheader("🧾 주간 리포트(카톡/공유용)")

    report_title = f"큐티 참여 현황 주간 리포트"

    png_bytes = build_weekly_report_png(
        title=report_title,
        this_wk_start=this_wk_start,
        this_wk_end=this_wk_end,
        month_label=month_label,
        month_users_cnt=month_users_cnt,
        this_week_participants=this_week_participants,
        this_week_rate=this_week_rate,
        last_week_rate=last_week_rate,
        delta=delta,
        drop_count=drop_count,
        wk_series=wk_series,
    )

    if png_bytes:
        st.image(png_bytes, caption="주간 리포트 이미지(미리보기)", use_container_width=True)
        st.download_button(
            "📸 리포트 이미지(PNG) 다운로드",
            data=png_bytes,
            file_name=f"qti_week_report_{this_wk_start.isoformat()}.png",
            mime="image/png",
            use_container_width=True,
        )

        pdf_bytes = build_weekly_report_pdf(png_bytes, report_title)
        if pdf_bytes:
            st.download_button(
                "📄 리포트 PDF 다운로드",
                data=pdf_bytes,
                file_name=f"qti_week_report_{this_wk_start.isoformat()}.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
        else:
            st.info("PDF 생성(reportlab)이 앱 환경에 없어서 PNG만 제공합니다.")
    else:
        st.info("리포트 이미지 생성을 위해 matplotlib이 필요합니다. (환경에 없으면 PNG/PDF 기능이 비활성화됩니다.)")

    # =========================
    # 다운로드(원본 + 요약)
    # =========================
    st.markdown("---")
    st.subheader("⬇️ 구글 시트 데이터 다운로드")

    csv_all = df_all.drop(columns=["completed_bool"], errors="ignore").to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        label="전체 원본 데이터 CSV 다운로드",
        data=csv_all,
        file_name="qti_records_all.csv",
        mime="text/csv",
        use_container_width=True,
    )

    csv_month = df_month.drop(columns=["completed_bool"], errors="ignore").to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        label=f"{month_label} 데이터 CSV 다운로드",
        data=csv_month,
        file_name=f"qti_records_{year}_{month:02d}.csv",
        mime="text/csv",
        use_container_width=True,
    )

    summary_week = pd.DataFrame(
        {
            "week_start": [this_wk_start.isoformat()],
            "week_end": [this_wk_end.isoformat()],
            "month_label": [month_label],
            "month_users": [month_users_cnt],
            "this_week_participants": [this_week_participants],
            "this_week_rate": [this_week_rate],
            "last_week_rate": [last_week_rate],
            "delta": [delta],
            "drop_count": [drop_count],
        }
    )
    st.download_button(
        label="이번 주 요약 CSV 다운로드",
        data=summary_week.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"qti_week_summary_{this_wk_start.isoformat()}.csv",
        mime="text/csv",
        use_container_width=True,
    )

    st.caption("※ 소그룹(셀/구역) 분석을 하려면 ‘group’ 컬럼(예: 1구역, 2구역)을 추가해주면, 그룹별 대시보드/리포트까지 자동 확장 가능합니다.")
