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

# 관리자 비밀번호 (Secrets 우선, 없으면 코드 상수 fallback)
# - Streamlit Secrets: ADMIN_PASSWORD 또는 ADMIN_KEY
ADMIN_KEY_FALLBACK = "yeiun1234"

KST = ZoneInfo("Asia/Seoul")

# 경고 기준(관리자)
DEFAULT_DROP_ALERT_PCT = 0.05     # 지난주 대비 -5%p 하락하면 경고
DEFAULT_LOW_WEEK_RATE_PCT = 0.20  # 이번주 참여율이 20% 미만이면 경고

# 성도 직분(콤보)
MEMBER_ROLES = ["평신도", "서리집사", "안수집사", "권사", "장로", "강도사", "목사", "기타"]


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


def clamp_20(s: str) -> str:
    s = (s or "").strip()
    return s[:20]


def normalize_role(s: str) -> str:
    s = (s or "").strip()
    return s if s in MEMBER_ROLES else ("기타" if s else "")


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
    """
    qti_records 시트(일별 기록) 스키마(권장):
      uid | member_role | member_name | day | start_time | end_time | completed | signature | prayer_note | updated_at

    - 기존 시트가 uid/day/start_time...만 있어도 자동으로 member_role/member_name 컬럼을 "추가"하여 호환합니다.
    - 컬럼 순서는 시트 상황에 따라 다를 수 있어, 헤더명을 기반으로 업데이트합니다.
    """

    REQUIRED_COLS = [
        "uid",
        "member_role",
        "member_name",
        "day",
        "start_time",
        "end_time",
        "completed",
        "signature",
        "prayer_note",
        "updated_at",
    ]

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
            ws = sh.add_worksheet(title=worksheet_name, rows="2000", cols="14")
            ws.append_row(self.REQUIRED_COLS)

        self.ws = ws
        self._ensure_schema()

    def _ensure_schema(self):
        header = self.ws.row_values(1) or []
        if not header:
            self.ws.update("A1", [self.REQUIRED_COLS])
            header = self.REQUIRED_COLS[:]

        missing = [c for c in self.REQUIRED_COLS if c not in header]
        if missing:
            try:
                self.ws.add_cols(len(missing))
            except Exception:
                pass

            header = self.ws.row_values(1) or header
            start_col = len(header) + 1
            for i, col in enumerate(missing):
                self.ws.update_cell(1, start_col + i, col)

        self._refresh_header_index()

    def _refresh_header_index(self):
        header = self.ws.row_values(1) or []
        self.col_idx = {name: i + 1 for i, name in enumerate(header)}

    def _empty_month_df(self, start: date, end: date) -> pd.DataFrame:
        return pd.DataFrame(
            [{"날짜": d.isoformat(), "QT 시작": "", "QT 종료": "", "완료": False, "나의 묵상 기도": ""} for d in daterange(start, end)]
        )

    def fetch_all_records_df(self) -> pd.DataFrame:
        all_data = self.ws.get_all_records()
        df_all = pd.DataFrame(all_data)
        if df_all.empty:
            return pd.DataFrame(columns=self.REQUIRED_COLS)

        for c in self.REQUIRED_COLS:
            if c not in df_all.columns:
                df_all[c] = ""

        for col in ["uid", "member_role", "member_name", "day", "start_time", "end_time", "signature", "prayer_note", "updated_at"]:
            df_all[col] = df_all[col].astype(str).fillna("")
        df_all["completed"] = df_all["completed"].astype(str).fillna("0")

        return df_all[self.REQUIRED_COLS]

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
        self._ensure_schema()

        all_records = self.ws.get_all_records()
        df = pd.DataFrame(all_records)

        row_idx = -1
        if not df.empty and "uid" in df.columns and "day" in df.columns:
            match = df[(df["uid"].astype(str) == str(uid)) & (df["day"].astype(str) == str(day))]
            if not match.empty:
                row_idx = match.index[0] + 2

        now_iso = now_kst().isoformat()

        def norm_value(k, v):
            if k == "completed":
                return "1" if bool(v) else "0"
            if k in ("start_time", "end_time"):
                return normalize_hhmm(str(v))
            if k in ("prayer_note", "signature"):
                return clamp_50(str(v))
            if k == "member_name":
                return clamp_20(str(v))
            if k == "member_role":
                return normalize_role(str(v))
            if v is None:
                return ""
            return v.strip() if isinstance(v, str) else v

        def set_cell(r, col_name, value):
            c = self.col_idx.get(col_name)
            if not c:
                return
            self.ws.update_cell(r, c, value)

        if row_idx == -1:
            header = self.ws.row_values(1)
            row = []
            for h in header:
                if h == "uid":
                    row.append(str(uid))
                elif h == "day":
                    row.append(str(day))
                elif h == "updated_at":
                    row.append(now_iso)
                else:
                    row.append("")
            self.ws.append_row(row)
            row_idx = len(self.ws.get_all_values())

        updates = dict(kwargs)
        if "member_name" in updates:
            updates["member_name"] = clamp_20(updates.get("member_name", ""))
        if "member_role" in updates:
            updates["member_role"] = normalize_role(updates.get("member_role", ""))

        for k, v in updates.items():
            if k in self.REQUIRED_COLS:
                set_cell(row_idx, k, norm_value(k, v))

        set_cell(row_idx, "uid", str(uid))
        set_cell(row_idx, "day", str(day))
        set_cell(row_idx, "updated_at", now_iso)


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
        return pd.DataFrame(columns=GoogleSheetsStorage.REQUIRED_COLS)
    try:
        return s.fetch_all_records_df()
    except Exception:
        return pd.DataFrame(columns=GoogleSheetsStorage.REQUIRED_COLS)


# =========================
# 스타일(접근성 + 대시보드 + 주소패널 토글)
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

          /* ===== 주소 패널(헤더는 남고, 내용만 접힘) ===== */
          #sharePanel {
            border-radius: 16px;
            border: 1px solid rgba(0,0,0,0.06);
            box-shadow: 0 8px 22px rgba(0,0,0,0.06);
            background: linear-gradient(135deg, #f7fbff 0%, #fff7fb 55%, #f6fff8 100%);
            overflow: hidden;
            margin-top: 6px;
            margin-bottom: 8px;
          }

          #shareHeader {
            display:flex;
            align-items:center;
            justify-content:space-between;
            padding: 10px 12px;
            font-weight: 900;
          }

          #shareTitle { font-size: 18px; }

          #shareToggleBtn {
            appearance:none;
            border: 1px solid rgba(0,0,0,0.10);
            background: rgba(255,255,255,0.75);
            border-radius: 12px;
            width: 42px;
            height: 36px;
            cursor: pointer;
            display:flex;
            align-items:center;
            justify-content:center;
            box-shadow: 0 6px 16px rgba(0,0,0,0.07);
            font-size: 18px;
            font-weight: 900;
          }
          #shareToggleBtn:active { transform: scale(0.98); }

          #shareContent {
            padding: 0 12px 12px 12px;
            overflow: hidden;
            transition: max-height 520ms ease, opacity 520ms ease, transform 520ms ease;
            max-height: 520px;
            opacity: 1;
            transform: translateY(0px);
          }

          #sharePanel.collapsed #shareContent {
            max-height: 0px;
            opacity: 0;
            transform: translateY(-6px);
            padding-bottom: 0px;
          }

          #sharePanel.collapsed #shareToggleBtn {
            background: rgba(255,255,255,0.92);
          }
        </style>
        """,
        unsafe_allow_html=True,
    )


def js_init_share_panel_autohide_5s():
    components.html(
        """
        <script>
          (function() {
            const doc = window.parent.document;
            const panel = doc.getElementById('sharePanel');
            const btn = doc.getElementById('shareToggleBtn');

            if (!panel || !btn) return;

            const setIcon = () => {
              const collapsed = panel.classList.contains('collapsed');
              btn.textContent = collapsed ? '▾' : '▴';
              btn.setAttribute('aria-label', collapsed ? '펼치기' : '숨기기');
              btn.setAttribute('title', collapsed ? '펼치기' : '숨기기');
            };

            if (!window.__sharePanelBound) {
              window.__sharePanelBound = true;
              btn.addEventListener('click', () => {
                panel.classList.toggle('collapsed');
                setIcon();
              });
            }

            setIcon();

            if (window.__shareAutoHideTimer) clearTimeout(window.__shareAutoHideTimer);
            window.__shareAutoHideTimer = setTimeout(() => {
              panel.classList.add('collapsed');
              setIcon();
            }, 5000);
          })();
        </script>
        """,
        height=0,
    )


# =========================
# 성도님 대시보드 렌더
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
# 관리자 유틸 + 리포트
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

    if wk_series is not None and not wk_series.empty:
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

    img = ImageReader(BytesIO(png_bytes))
    img_w = w - 80
    img_h = h - 120
    c.drawImage(img, 40, 60, width=img_w, height=img_h, preserveAspectRatio=True, anchor="c")

    c.showPage()
    c.save()
    return buf.getvalue()


# =========================
# 프로필(성도정보) 추출
# =========================
def infer_member_profile_from_records(df_user: pd.DataFrame) -> tuple[str, str]:
    if df_user is None or df_user.empty:
        return "", ""

    dfx = df_user.copy()
    dfx = dfx[(dfx["member_name"].astype(str).str.strip() != "") | (dfx["member_role"].astype(str).str.strip() != "")]
    if dfx.empty:
        return "", ""

    if "updated_at" in dfx.columns:
        try:
            dfx["_t"] = pd.to_datetime(dfx["updated_at"], errors="coerce")
        except Exception:
            dfx["_t"] = pd.NaT
    else:
        dfx["_t"] = pd.NaT

    if dfx["_t"].notna().any():
        r = dfx.sort_values("_t").iloc[-1]
    else:
        r = dfx.sort_values("day").iloc[-1]

    return normalize_role(r.get("member_role", "")), clamp_20(r.get("member_name", ""))


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

    df_all = cached_all_records()
    df_user = df_all[df_all["uid"].astype(str) == str(uid)].copy() if not df_all.empty else pd.DataFrame(columns=df_all.columns)

    default_role, default_name = infer_member_profile_from_records(df_user) if not df_user.empty else ("", "")
    if "member_role" not in st.session_state:
        st.session_state["member_role"] = default_role
    if "member_name" not in st.session_state:
        st.session_state["member_name"] = default_name

    member_role = normalize_role(st.session_state.get("member_role", ""))
    member_name = clamp_20(st.session_state.get("member_name", ""))

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

    base_day = st.session_state.get("picked_day", today_kst())
    wk_start = week_start_monday(base_day)
    wk_end = wk_start + timedelta(days=6)
    week_completed_days = sum(1 for d in daterange(wk_start, wk_end) if d in completed_dates)
    week_rate = week_completed_days / 7

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
        week_rate, week_completed_days, 7,
        month_rate, month_completed_days, month_den,
        year_rate, year_completed_days, year_den,
        badge_emoji, badge_name,
        current_streak, streak_end_day,
        best_streak_month
    )

    share_url = build_share_url(uid)

    st.markdown(
        """
        <div id="sharePanel">
          <div id="shareHeader">
            <div id="shareTitle">📌 내 기록지 주소 저장하기</div>
            <button id="shareToggleBtn" type="button" aria-label="숨기기" title="숨기기">▴</button>
          </div>
          <div id="shareContent">
            <div style="font-weight:800; margin-bottom:8px;">
              아래 주소를 복사해서 카톡 ‘나에게 보내기’에 저장하거나 즐겨찾기에 저장하세요.
            </div>
        """,
        unsafe_allow_html=True,
    )
    st.code(share_url)
    if "<YOUR-APP>" in share_url:
        st.warning("PUBLIC_APP_URL이 설정되지 않아 임시 주소가 보입니다. Secrets에 실제 앱 주소를 넣어주세요.")
    st.markdown("</div></div>", unsafe_allow_html=True)
    js_init_share_panel_autohide_5s()

    st.markdown("---")
    st.subheader("✍️ 오늘의 큐티 기록")

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
        safe_write(lambda: storage.upsert_one(uid, day_str, start_time=now_hhmm_kst(), member_role=member_role, member_name=member_name))
    if c2.button("■ 종료(현재시간)", use_container_width=True):
        safe_write(lambda: storage.upsert_one(uid, day_str, end_time=now_hhmm_kst(), member_role=member_role, member_name=member_name))
    if c3.button("✅ " + ("완료 취소" if is_done else "완료"), use_container_width=True):
        safe_write(lambda: storage.upsert_one(uid, day_str, completed=not is_done, member_role=member_role, member_name=member_name))

    st.markdown("### 🕊️ 나의 묵상 기도 (50자 이내)")
    memo = st.text_area(
        "경건의 시간 하나님님께서 주신 감동으로 한 줄 묵상 기도를 적어 보세요.",
        height=90,
        max_chars=50,
        placeholder="예) 주님, 오늘 말씀을 붙잡고 순종할 힘을 주세요.",
    )
    if st.button("📝 묵상 기도 저장하기", use_container_width=True, type="primary"):
        memo_clean = clamp_50(memo)
        safe_write(lambda: storage.upsert_one(uid, day_str, signature="", prayer_note=memo_clean, member_role=member_role, member_name=member_name))

    st.markdown("---")
    with st.container(border=True):
        st.subheader("🙋 성도 정보(1회 입력)")
        st.caption("한 번 저장하면 다음 접속 때 자동으로 불러오고, 이후 기록에도 함께 저장됩니다(분석용).")

        col_r, col_n, col_s = st.columns([1, 2, 1])
        with col_r:
            idx = MEMBER_ROLES.index(member_role) if member_role in MEMBER_ROLES else 0
            st.selectbox("직분", MEMBER_ROLES, index=idx, key="member_role")
        with col_n:
            st.text_input("성도 이름", value=member_name, key="member_name", placeholder="예) 홍길동")
        with col_s:
            st.write("")
            st.write("")
            if st.button("💾 성도 정보 저장", use_container_width=True):
                role_clean = normalize_role(st.session_state.get("member_role", ""))
                name_clean = clamp_20(st.session_state.get("member_name", ""))
                if not name_clean:
                    st.warning("이름을 입력해 주세요.")
                else:
                    safe_write(lambda: storage.upsert_one(uid, day_str, member_role=role_clean, member_name=name_clean))

        st.info(f"현재 저장된 성도 정보:  {normalize_role(st.session_state.get('member_role','')) or '-'} / {clamp_20(st.session_state.get('member_name','')) or '-'}")

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
                st.session


