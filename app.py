# coding: utf-8
import os
import secrets
import json
import re
from datetime import date, datetime, timedelta
from typing import Optional, Tuple
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from io import BytesIO

APP_BUILD = "weeklyfree_v2_2026-01-21_layout_v3_width"


import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# -------------------------
# 구글 시트 라이브러리
# -------------------------
try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSHEETS_AVAILABLE = True
except Exception:
    GSHEETS_AVAILABLE = False

# -------------------------
# 기본 설정
# -------------------------
APP_TITLE = "주만나와 함께 빚어가는, 예은의 향기"
VERSE_TEXT = "하나님 보시기에 아름다운 예은 성도님, 오늘도 주만나와 함께 은혜의 깊은 곳으로 한 걸음 더 들어가 볼까요?"
SUPPORTED_MONTHS = [(2026, 1, "2026년 1월"), (2026, 2, "2026년 2월"), (2026, 3, "2026년 3월"), (2026, 4, "2026년 4월"), (2026, 5, "2026년 5월"), (2026, 6, "2026년 6월"), (2026, 7, "2026년 7월"), (2026, 8, "2026년 8월"), (2026, 9, "2026년 9월"), (2026, 10, "2026년 10월"), (2026, 11, "2026년 11월"), (2026, 12, "2026년 12월"),]

SHEET_RECORDS = "qti_records"  # 일별 기록
SHEET_USERS = "qti_users"      # uid별 성도 정보(직분/이름)
SHEET_PRAYERS = "intercessory_prayers"  # 중보기도 요청(Pray together in the Lord)

MEMBER_ROLES = ["평신도", "서리집사", "안수집사", "권사", "장로", "강도사", "목사", "기타"]

KST = ZoneInfo("Asia/Seoul")
ADMIN_KEY_FALLBACK = "yeiun1234"  # secrets에 없을 때만 fallback

_HHMM = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


# -------------------------
# 유틸
# -------------------------
def now_kst() -> datetime:
    return datetime.now(tz=KST)


def today_kst() -> date:
    return now_kst().date()


def now_hhmm_kst() -> str:
    return now_kst().strftime("%H:%M")


def normalize_hhmm(s: str) -> str:
    s = (s or "").strip()
    return s if _HHMM.match(s) else ""


def clamp_50(s: str) -> str:
    return (s or "").strip()[:50]


def clamp_20(s: str) -> str:
    return (s or "").strip()[:20]


def clamp_300(s: str) -> str:
    return (s or "").strip()[:300]


def clamp_1000(s: str) -> str:
    return (s or "").strip()[:1000]


def normalize_role(s: str) -> str:
    s = (s or "").strip()
    return s if s in MEMBER_ROLES else (MEMBER_ROLES[-1] if s else "")


def month_range(year: int, month: int) -> Tuple[date, date]:
    start = date(year, month, 1)
    end = (date(year, month + 1, 1) if month < 12 else date(year + 1, 1, 1)) - timedelta(days=1)
    return start, end


def daterange(d1: date, d2: date):
    curr = d1
    while curr <= d2:
        yield curr
        curr += timedelta(days=1)


def week_start_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())  # 월=0


def clamp_date(d: date, start: date, end: date) -> date:
    return max(start, min(end, d))


# -------------------------
# 공유 URL (하드코딩 제거)
# -------------------------
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


# -------------------------
# 주소 패널 자동 숨김 + 토글
# -------------------------
def inject_share_panel_js():
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


def apply_css():
    st.markdown(
        """
        <style>
          html, body, [class*="css"]  { font-size: 18px !important; }
          .stButton>button { height: 54px; font-size: 18px; border-radius: 14px; }
          textarea, input { font-size: 18px !important; }

          /* share panel */
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

          /* table alignment */
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
          /* 나의 묵상 기도만 왼쪽 정렬 */
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
    st.markdown(f"<div class='qti-table-wrap'>{html}</div>", unsafe_allow_html=True)


# -------------------------
# 구글 시트 저장소
# -------------------------
class GoogleSheetsStorage:
    RECORDS_REQUIRED = [
        "uid", "member_role", "member_name", "day",
        "start_time", "end_time", "completed",
        "signature", "prayer_note", "updated_at"
    ]
    USERS_REQUIRED = ["uid", "member_role", "member_name", "updated_at"]
    PRAYERS_REQUIRED = [
        "uid", "member_role", "member_name", "saints_info",
        "prayer_title", "prayer_content", "is_public",
        "created_at", "linked_day"
    ]

    def __init__(self, spreadsheet_id: str, worksheet_records: str, sa_json: dict):
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_info(sa_json, scopes=scopes)
        self.gc = gspread.authorize(creds)
        self.sh = self.gc.open_by_key(spreadsheet_id)

        # worksheets
        try:
            self.ws = self.sh.worksheet(worksheet_records)
        except Exception:
            self.ws = self.sh.add_worksheet(title=worksheet_records, rows=2000, cols=20)

        try:
            self.ws_users = self.sh.worksheet("users")
        except Exception:
            self.ws_users = self.sh.add_worksheet(title="users", rows=2000, cols=10)

        # prayers worksheet
        try:
            self.ws_prayers = self.sh.worksheet(SHEET_PRAYERS)
        except Exception:
            self.ws_prayers = self.sh.add_worksheet(title=SHEET_PRAYERS, rows=3000, cols=20)

        # schema/index cache (process-wide via st.cache_resource)
        self._schema_verified = False
        self._records_header: list[str] = []
        self._users_header: list[str] = []
        self._prayers_header: list[str] = []
        self.col_idx: dict[str, int] = {}  # 1-indexed col index for records
        self.users_col_idx: dict[str, int] = {}  # 1-indexed col index for users
        self.prayers_col_idx: dict[str, int] = {}  # 1-indexed col index for prayers

        self._row_index: dict[tuple[str, str], int] = {}  # (uid, day) -> row_idx
        self._index_built_at: float = 0.0

        # Verify schema once at creation (with retry/backoff)
        self._ensure_schema()

    # -------------------------
    # Low-level: retry wrapper
    # -------------------------
    def _call_with_retries(self, fn, *args, **kwargs):
        """Retry transient gspread API errors (429/5xx) with exponential backoff."""
        import time
        last_err = None
        for attempt in range(3):
            try:
                return fn(*args, **kwargs)
            except gspread.exceptions.APIError as e:
                last_err = e
                # Best-effort: retry on rate limit / transient backend issues
                msg = str(e)
                retryable = any(code in msg for code in ("429", "500", "502", "503", "504"))
                if not retryable or attempt == 2:
                    raise
                time.sleep(1.0 * (2 ** attempt))
            except Exception as e:
                # Non-API errors: don't spin unless clearly transient
                last_err = e
                raise
        if last_err:
            raise last_err

    # -------------------------
    # Schema: verify once
    # -------------------------
    def _ensure_schema(self):
        if self._schema_verified:
            return

        # records header
        hdr = self._call_with_retries(self.ws.row_values, 1) or []
        hdr = [str(x).strip() for x in hdr if str(x).strip()]

        if not hdr:
            hdr = list(self.RECORDS_REQUIRED)
            # Ensure enough columns
            try:
                if self.ws.col_count < len(hdr):
                    self._call_with_retries(self.ws.add_cols, len(hdr) - self.ws.col_count)
            except Exception:
                pass
            self._call_with_retries(self.ws.update, "A1", [hdr])
        else:
            missing = [c for c in self.RECORDS_REQUIRED if c not in hdr]
            if missing:
                new_hdr = hdr + missing
                try:
                    if self.ws.col_count < len(new_hdr):
                        self._call_with_retries(self.ws.add_cols, len(new_hdr) - self.ws.col_count)
                except Exception:
                    pass
                self._call_with_retries(self.ws.update, "A1", [new_hdr])
                hdr = new_hdr

        self._records_header = hdr
        self._refresh_col_index()

        # users header
        uhdr = self._call_with_retries(self.ws_users.row_values, 1) or []
        uhdr = [str(x).strip() for x in uhdr if str(x).strip()]

        if not uhdr:
            uhdr = list(self.USERS_REQUIRED)
            try:
                if self.ws_users.col_count < len(uhdr):
                    self._call_with_retries(self.ws_users.add_cols, len(uhdr) - self.ws_users.col_count)
            except Exception:
                pass
            self._call_with_retries(self.ws_users.update, "A1", [uhdr])
        else:
            umissing = [c for c in self.USERS_REQUIRED if c not in uhdr]
            if umissing:
                new_uhdr = uhdr + umissing
                try:
                    if self.ws_users.col_count < len(new_uhdr):
                        self._call_with_retries(self.ws_users.add_cols, len(new_uhdr) - self.ws_users.col_count)
                except Exception:
                    pass
                self._call_with_retries(self.ws_users.update, "A1", [new_uhdr])
                uhdr = new_uhdr

        self._users_header = uhdr
        self._refresh_users_col_index()

        # prayers header
        phdr = self._call_with_retries(self.ws_prayers.row_values, 1) or []
        phdr = [str(x).strip() for x in phdr if str(x).strip()]

        if not phdr:
            phdr = list(self.PRAYERS_REQUIRED)
            try:
                if self.ws_prayers.col_count < len(phdr):
                    self._call_with_retries(self.ws_prayers.add_cols, len(phdr) - self.ws_prayers.col_count)
            except Exception:
                pass
            self._call_with_retries(self.ws_prayers.update, "A1", [phdr])
        else:
            pmissing = [c for c in self.PRAYERS_REQUIRED if c not in phdr]
            if pmissing:
                new_phdr = phdr + pmissing
                try:
                    if self.ws_prayers.col_count < len(new_phdr):
                        self._call_with_retries(self.ws_prayers.add_cols, len(new_phdr) - self.ws_prayers.col_count)
                except Exception:
                    pass
                self._call_with_retries(self.ws_prayers.update, "A1", [new_phdr])
                phdr = new_phdr

        self._prayers_header = phdr
        self._refresh_prayers_col_index()

        self._schema_verified = True

    def _refresh_col_index(self):
        self.col_idx = {name: i + 1 for i, name in enumerate(self._records_header)}

    def _refresh_users_col_index(self):
        self.users_col_idx = {name: i + 1 for i, name in enumerate(self._users_header)}

    def _refresh_prayers_col_index(self):
        self.prayers_col_idx = {name: i + 1 for i, name in enumerate(self._prayers_header)}

    # -------------------------
    # Data helpers
    # -------------------------
    def _empty_df(self, start: date, end: date) -> pd.DataFrame:
        return pd.DataFrame(
            [{"날짜": d.isoformat(), "QT 시작": "", "QT 종료": "", "완료": False, "나의 묵상 기도": ""} for d in daterange(start, end)]
        )

    def fetch_all_records_df(self) -> pd.DataFrame:
        """(관리/분석용) 전체 로드. 호출 횟수는 최소화해서 사용하세요."""
        self._ensure_schema()
        rows = self._call_with_retries(self.ws.get_all_records)
        df_all = pd.DataFrame(rows)
        if df_all.empty:
            return pd.DataFrame(columns=self.RECORDS_REQUIRED)
        for c in self.RECORDS_REQUIRED:
            if c not in df_all.columns:
                df_all[c] = ""
        return df_all[self.RECORDS_REQUIRED]

    # -------------------------
    # Prayers (intercessory)
    # -------------------------
    def fetch_all_prayers_df(self) -> pd.DataFrame:
        """(관리/목회자용) 중보기도 요청 전체 로드."""
        self._ensure_schema()
        rows = self._call_with_retries(self.ws_prayers.get_all_records)
        dfp = pd.DataFrame(rows)
        if dfp.empty:
            return pd.DataFrame(columns=self.PRAYERS_REQUIRED)
        for c in self.PRAYERS_REQUIRED:
            if c not in dfp.columns:
                dfp[c] = ""
        return dfp[self.PRAYERS_REQUIRED]

    def insert_prayer_request(
        self,
        uid: str,
        member_role: str,
        member_name: str,
        prayer_title: str,
        prayer_content: str,
        is_public: bool = True,
        linked_day: str = "",
    ):
        """중보기도 요청은 "추가(append)"로만 저장합니다(기록 변경 이력 보존)."""
        self._ensure_schema()
        now_iso = datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")
        role = normalize_role(member_role)
        name = clamp_20(member_name)
        title = clamp_50(prayer_title)
        content = clamp_300(prayer_content)
        saints_info = f"{role} {name} ({uid})".strip()
        if linked_day:
            try:
                linked_day = str(date.fromisoformat(str(linked_day))).strip()
            except Exception:
                linked_day = str(linked_day).strip()

        row = []
        for h in self._prayers_header:
            if h == "uid":
                row.append(str(uid))
            elif h == "member_role":
                row.append(role)
            elif h == "member_name":
                row.append(name)
            elif h == "saints_info":
                row.append(saints_info)
            elif h == "prayer_title":
                row.append(title)
            elif h == "prayer_content":
                row.append(content)
            elif h == "is_public":
                row.append("TRUE" if bool(is_public) else "FALSE")
            elif h == "created_at":
                row.append(now_iso)
            elif h == "linked_day":
                row.append(str(linked_day or ""))
            else:
                row.append("")

        self._call_with_retries(self.ws_prayers.append_row, row, value_input_option="USER_ENTERED")


    # -------------------------
    # Profile (users sheet)
    # -------------------------
    def get_profile(self, uid: str) -> Tuple[str, str]:
        self._ensure_schema()
        try:
            rows = self._call_with_retries(self.ws_users.get_all_records)
            dfu = pd.DataFrame(rows)
            if dfu.empty:
                return "", ""
            hit = dfu[dfu["uid"].astype(str) == str(uid)]
            if hit.empty:
                return "", ""
            if "updated_at" in hit.columns:
                hit = hit.sort_values("updated_at")
            r = hit.iloc[-1]
            return normalize_role(r.get("member_role", "")), clamp_20(r.get("member_name", ""))
        except Exception:
            return "", ""

    def upsert_profile(self, uid: str, member_role: str, member_name: str):
        """프로필 저장은 자주 호출되지 않으므로 단순/안전하게 처리."""
        self._ensure_schema()
        now_iso = datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")
        role = normalize_role(member_role)
        name = clamp_20(member_name)

        # 최소 호출: uid 컬럼만 읽어서 기존 행 찾기 (1회)
        uid_col = self.users_col_idx.get("uid", 1)
        max_col = uid_col
        end_letter = _col_to_letter(max_col)
        values = self._call_with_retries(self.ws_users.get, f"A2:{end_letter}")
        row_idx = None
        for i, row in enumerate(values, start=2):
            v_uid = row[uid_col - 1] if len(row) >= uid_col else ""
            if str(v_uid) == str(uid):
                row_idx = i
                break

        if row_idx is None:
            new_row = []
            for h in self._users_header:
                if h == "uid":
                    new_row.append(str(uid))
                elif h == "member_role":
                    new_row.append(role)
                elif h == "member_name":
                    new_row.append(name)
                elif h == "updated_at":
                    new_row.append(now_iso)
                else:
                    new_row.append("")
            self._call_with_retries(self.ws_users.append_row, new_row, value_input_option="USER_ENTERED")
        else:
            cells = []
            def q(colname, val):
                c = self.users_col_idx.get(colname)
                if c:
                    cells.append(gspread.Cell(row_idx, c, str(val)))
            q("member_role", role)
            q("member_name", name)
            q("updated_at", now_iso)
            if cells:
                self._call_with_retries(self.ws_users.update_cells, cells, value_input_option="USER_ENTERED")

    # -------------------------
    # Index build: (uid, day) -> row_idx
    # -------------------------
    def _build_row_index(self, force: bool = False):
        import time
        if (not force) and self._row_index and (time.time() - self._index_built_at) < 60:
            return

        self._ensure_schema()
        uid_col = self.col_idx.get("uid", 1)
        day_col = self.col_idx.get("day", 4)
        max_col = max(uid_col, day_col)
        end_letter = _col_to_letter(max_col)

        # 1회 호출로 필요한 범위만 읽기
        values = self._call_with_retries(self.ws.get, f"A2:{end_letter}")

        idx = {}
        for r_i, row in enumerate(values, start=2):
            v_uid = row[uid_col - 1] if len(row) >= uid_col else ""
            v_day = row[day_col - 1] if len(row) >= day_col else ""
            if v_uid and v_day:
                idx[(str(v_uid), str(v_day))] = r_i

        self._row_index = idx
        self._index_built_at = time.time()

    # -------------------------
    # Month load (UI)
    # -------------------------
    def load_month(self, uid: str, start: date, end: date) -> pd.DataFrame:
        try:
            df_all = self.fetch_all_records_df()
            if df_all.empty:
                return self._empty_df(start, end)

            user_data = df_all[
                (df_all["uid"].astype(str) == str(uid))
                & (df_all["day"] >= start.isoformat())
                & (df_all["day"] <= end.isoformat())
            ].copy()

            if user_data.empty:
                return self._empty_df(start, end)

            # Normalize
            user_data["day"] = user_data["day"].astype(str)

            # Build view
            out = []
            mp = {row["day"]: row for _, row in user_data.iterrows()}
            for d in daterange(start, end):
                ds = d.isoformat()
                r = mp.get(ds, {})
                out.append(
                    {
                        "날짜": ds,
                        "QT 시작": normalize_hhmm(r.get("start_time", "")),
                        "QT 종료": normalize_hhmm(r.get("end_time", "")),
                        "완료": str(r.get("completed", "")).lower() in ("true", "1", "yes", "y", "완료"),
                        "나의 묵상 기도": (r.get("prayer_note", "") or ""),
                    }
                )
            return pd.DataFrame(out)
        except Exception:
            return self._empty_df(start, end)

    # -------------------------
    # Upsert record: minimal calls
    # -------------------------
    def upsert_one(self, uid: str, day: str, **kwargs):
        self._ensure_schema()

        # Build (or reuse) index without reading entire sheet every time
        self._build_row_index()

        key = (str(uid), str(day))
        row_idx = self._row_index.get(key)

        now_iso = datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")

        def norm_value(k, v):
            if k in ("start_time", "end_time"):
                return normalize_hhmm(str(v))
            if k == "completed":
                return "TRUE" if bool(v) else "FALSE"
            if k == "member_role":
                return normalize_role(str(v))
            if k == "member_name":
                return clamp_20(str(v))
            if k == "prayer_note":
                return str(v)[:5000]
            return str(v)

        if row_idx is None:
            # append 1회 호출
            row = []
            for h in self._records_header:
                if h == "uid":
                    row.append(str(uid))
                elif h == "day":
                    row.append(str(day))
                elif h == "updated_at":
                    row.append(now_iso)
                elif h in kwargs:
                    row.append(norm_value(h, kwargs[h]))
                else:
                    row.append("")
            self._call_with_retries(self.ws.append_row, row, value_input_option="USER_ENTERED")
            # index is now stale; rebuild later
            self._row_index = {}
            self._index_built_at = 0.0
            return

        # update_cells 1회 호출
        cells = []
        def queue_cell(col_name: str, val):
            c = self.col_idx.get(col_name)
            if c:
                cells.append(gspread.Cell(row_idx, c, str(val)))

        # always keep these consistent
        queue_cell("uid", str(uid))
        queue_cell("day", str(day))
        queue_cell("updated_at", now_iso)

        for k, v in kwargs.items():
            if k in self.col_idx:
                queue_cell(k, norm_value(k, v))

        if cells:
            self._call_with_retries(self.ws.update_cells, cells, value_input_option="USER_ENTERED")

# local helper (kept near class; no other code touched)
def _col_to_letter(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s
@st.cache_resource
def get_storage() -> Optional[GoogleSheetsStorage]:
    if not GSHEETS_AVAILABLE:
        return None
    s_id = st.secrets.get("GSHEETS_SPREADSHEET_ID")
    sa_json = st.secrets.get("GSHEETS_SERVICE_ACCOUNT_JSON")
    if s_id and sa_json:
        sa_obj = json.loads(sa_json) if isinstance(sa_json, str) else sa_json
        return GoogleSheetsStorage(s_id, SHEET_RECORDS, sa_obj)
    return None


@st.cache_data(ttl=60)
def cached_all_records_df() -> pd.DataFrame:
    s = get_storage()
    if not s:
        return pd.DataFrame()
    return s.fetch_all_records_df()


@st.cache_data(ttl=60)
def cached_all_prayers_df() -> pd.DataFrame:
    s = get_storage()
    if not s:
        return pd.DataFrame()
    return s.fetch_all_prayers_df()


def require_admin_login() -> bool:
    admin_pw = st.secrets.get("ADMIN_KEY") or st.secrets.get("ADMIN_PASSWORD") or ADMIN_KEY_FALLBACK

    if "is_admin" not in st.session_state:
        st.session_state["is_admin"] = False
    if st.session_state["is_admin"]:
        return True

    st.subheader("🔐 관리자 로그인")
    pw = st.text_input("관리자 비밀번호", type="password", placeholder="관리자 비밀번호 입력")
    if st.button("로그인", use_container_width=True):
        if pw == admin_pw:
            st.session_state["is_admin"] = True
            st.success("관리자 로그인 완료")
            st.rerun()
        else:
            st.error("비밀번호가 올바르지 않습니다.")
    return False


def compute_participation(df_all: pd.DataFrame, start: date, end: date) -> Tuple[int, int, float]:
    """
    반환: (참여 uid 수, 전체 uid 수, 참여율)
    기준: 기간 내 completed=1이 1회라도 있으면 '참여'
    """
    if df_all is None or df_all.empty:
        return 0, 0, 0.0

    total_uids = set(df_all["uid"].astype(str).unique().tolist())
    total = len([u for u in total_uids if u])

    dfx = df_all.copy()
    dfx = dfx[(dfx["day"] >= start.isoformat()) & (dfx["day"] <= end.isoformat())]
    dfx["completed_bool"] = dfx["completed"].astype(str).str.lower().isin(["1", "true", "yes", "y", "완료"])
    dfx = dfx[dfx["completed_bool"]]
    active = len(set(dfx["uid"].astype(str).unique().tolist()))
    rate = (active / total) if total else 0.0
    return active, total, rate


def admin_dashboard():
    st.header("📊 관리자 대시보드")

    df_all = cached_all_records_df()
    if df_all.empty:
        st.info("기록이 아직 없습니다.")
        return

    c1, c2 = st.columns([1, 1])
    with c1:
        anchor = st.date_input("기준일(주간 통계)", value=today_kst())
    with c2:
        month_label = st.selectbox("월(요약/다운로드)", [m[2] for m in SUPPORTED_MONTHS])

    y, m = [(yy, mm) for (yy, mm, lbl) in SUPPORTED_MONTHS if lbl == month_label][0]
    m_start, m_end = month_range(y, m)

    wk_start = week_start_monday(anchor)
    wk_end = wk_start + timedelta(days=6)

    a_wk, t_all, r_wk = compute_participation(df_all, wk_start, wk_end)
    a_m, _, r_m = compute_participation(df_all, m_start, m_end)

    st.markdown("### ✅ 참여 현황")
    k1, k2, k3 = st.columns(3)
    k1.metric("이번 주 참여", f"{a_wk}명", f"{r_wk:.0%}")
    k2.metric("이번 달 참여", f"{a_m}명", f"{r_m:.0%}")
    k3.metric("전체 UID 수", f"{t_all}명")

    # 최신 프로필(기록 기준 최신값)
    latest = df_all.copy()
    latest["_t"] = pd.to_datetime(latest["updated_at"], errors="coerce")
    latest = latest.sort_values(["uid", "_t"])
    prof = latest.groupby("uid", as_index=False).tail(1)[["uid", "member_role", "member_name"]].copy()
    prof["member_role"] = prof["member_role"].fillna("").astype(str)
    prof["member_name"] = prof["member_name"].fillna("").astype(str)

    # 월 기준 참여일수
    dmonth = df_all[(df_all["day"] >= m_start.isoformat()) & (df_all["day"] <= m_end.isoformat())].copy()
    dmonth["completed_bool"] = dmonth["completed"].astype(str).str.lower().isin(["1", "true", "yes", "y", "완료"])
    cnts = dmonth[dmonth["completed_bool"]].groupby("uid", as_index=False)["day"].nunique().rename(columns={"day": "완료일수"})
    merged = prof.merge(cnts, on="uid", how="left")
    merged["완료일수"] = merged["완료일수"].fillna(0).astype(int)

    st.markdown("### 👥 성도 참여(월 기준)")
    st.dataframe(
        merged.sort_values(["완료일수", "member_name"], ascending=[False, True]),
        use_container_width=True,
        hide_index=True,
    )


    st.markdown("---")
    st.markdown("### 🙏 Pray together in the Lord (중보기도 요청)")

    dfp_all = cached_all_prayers_df()
    if dfp_all.empty:
        st.info("중보기도 요청이 아직 없습니다.")
    else:
        view_mode = st.selectbox("보기 옵션", ["공동체 중보(공개)", "전체(비공개 포함)"], index=0)

        dfp = dfp_all.copy()
        dfp["is_public_bool"] = dfp["is_public"].astype(str).str.lower().isin(["true", "1", "yes", "y", "공개"])

        dfp["_created_dt"] = pd.to_datetime(dfp["created_at"], errors="coerce")
        dfp["_linked_dt"] = pd.to_datetime(dfp["linked_day"], errors="coerce")
        dfp["_use_date"] = dfp["_linked_dt"].dt.date
        dfp.loc[dfp["_use_date"].isna(), "_use_date"] = dfp["_created_dt"].dt.date

        # 월 필터(선택한 월 기준)
        dfp = dfp[(dfp["_use_date"] >= m_start) & (dfp["_use_date"] <= m_end)] if not dfp.empty else dfp

        if view_mode.startswith("공동체"):
            dfp = dfp[dfp["is_public_bool"]]

        dfp = dfp.sort_values(by=["_created_dt"], ascending=False, na_position="last")

        view = dfp.rename(
            columns={
                "saints_info": "성도 정보",
                "prayer_title": "기도 제목",
                "prayer_content": "기도 내용",
                "is_public_bool": "공동체 중보",
                "linked_day": "연결 QT 날짜",
                "created_at": "작성 시각",
            }
        )

        cols = [c for c in ["성도 정보", "기도 제목", "기도 내용", "공동체 중보", "연결 QT 날짜", "작성 시각"] if c in view.columns]
        st.dataframe(view[cols], use_container_width=True, hide_index=True)

        # 다운로드(선택 월 기준)
        csv_p = dfp.drop(columns=["_created_dt", "_linked_dt", "_use_date"], errors="ignore").to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "중보기도 CSV 다운로드(선택 월)",
            data=csv_p,
            file_name=f"intercessory_prayers_{m_start.strftime('%Y%m')}.csv",
            mime="text/csv",
            use_container_width=True,
        )

        csv_all = dfp_all.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "중보기도 CSV 다운로드(전체 기간)",
            data=csv_all,
            file_name="intercessory_prayers_all.csv",
            mime="text/csv",
            use_container_width=True,
        )

    st.caption("※ 기본은 '공동체 중보(공개)'만 표시됩니다. '전체'는 목회자/관리자 전용으로만 활용하세요.")

    st.markdown("### ⬇️ 데이터 다운로드")
    csv = dmonth.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "월 데이터 CSV 다운로드",
        data=csv,
        file_name=f"qti_records_{m_start.strftime('%Y%m')}.csv",
        mime="text/csv",
        use_container_width=True,
    )
    st.caption("※ 이름이 비어있는 UID는 성도님이 성도 정보를 아직 저장하지 않은 경우입니다.")


# -------------------------
# 앱 시작
# -------------------------
st.set_page_config(page_title="Ye-eun's scent created with Ju-manna", layout="wide")
st.sidebar.caption(f"build: {APP_BUILD}")

# --- Responsive UI (PC/Mobile) ---
st.markdown(
    """
<style>
/* Base (desktop/tablet) */
html, body, [class*="css"] { font-size: 16px; }
h1 { 
  font-size: 2.0rem !important;
  line-height: 1.2 !important;
  margin-bottom: 0.25rem !important;
}

/* 모바일에서는 더 작게 */
@media (max-width: 640px) {
  h1 {
    font-size: 1.05rem !important;
  }
}

h2 { font-size: 1.15rem; line-height: 1.25; }
h3 { font-size: 0.95rem; line-height: 1.25; }   /* 더 줄임 */

.stButton button {
  font-size: 0.95rem;
  padding: 0.45rem 0.75rem;
}

label, .stMarkdown, .stText, .stCaption, .stRadio, .stSelectbox, .stTextInput, .stDateInput {
  font-size: 0.95rem;
}

.block-container { padding-top: 0.6rem; padding-bottom: 1.4rem; max-width: calc(1100px + 100mm); }

hr { margin: 0.6rem 0 !important; }
h2, h3 { margin-top: 0.4rem !important; margin-bottom: 0.25rem !important; }


/* Mobile */
@media (max-width: 640px) {
  html, body, [class*="css"] { font-size: 13px; }

  h1 { font-size: 1.2rem; }
  h2 { font-size: 1.10rem; }
  h3 { font-size: 0.95rem; }

  .stButton button {
    font-size: 0.85rem;
    padding: 0.35rem 0.6rem;
    border-radius: 10px;
  }

  label, .stMarkdown, .stText, .stCaption { font-size: 0.88rem; }

  .block-container { padding-left: 0.8rem; padding-right: 0.8rem; }

  div[data-baseweb="select"] > div { min-height: 36px; }
  input, textarea { font-size: 0.90rem !important; }

  .stDataFrame { overflow-x: auto; }
}

/* Very small phones */
@media (max-width: 380px) {
  html, body, [class*="css"] { font-size: 12.5px; }
  .stButton button { font-size: 0.82rem; padding: 0.32rem 0.55rem; }
}
</style>
    """,
    unsafe_allow_html=True
)
apply_css()

storage = get_storage()
if not storage:
    st.error("구글 시트 설정(Secrets) 또는 gspread 라이브러리를 확인해주세요.")
    st.stop()

st.title("✨ 주만나와 함께 빚어가는, 예은의 향기")
st.caption("하나님 보시기에 참 예쁜 예은 성도님, 오늘도 주만나와 함께 은혜의 깊은 곳으로 한 걸음 더 들어가 볼까요?")

mode = st.radio("모드 선택", ["성도님(기록하기)", "관리자(대시보드)"], horizontal=True)

# 관리자
if mode == "관리자(대시보드)":
    if require_admin_login():
        admin_dashboard()
    st.stop()

# -------------------------
# 성도님 모드
# -------------------------
# UID 관리
if "uid" not in st.query_params:
    st.info("### 🙏 큐티 체크리스트 시작하기\n성도님 전용 기록지를 만들기 위해 아래 버튼을 눌러주세요.")
    if st.button("🚀 나의 큐티 링크 만들기 (처음 1회)", use_container_width=True):
        new_uid = secrets.token_urlsafe(8)
        st.query_params["uid"] = new_uid
        st.rerun()
    st.stop()

uid = st.query_params["uid"]

# 성도 프로필 자동 불러오기(최초 1회)
if "profile_loaded" not in st.session_state:
    role0, name0 = storage.get_profile(uid)
    st.session_state["member_role"] = role0 or MEMBER_ROLES[0]
    st.session_state["member_name"] = name0 or ""
    st.session_state["profile_loaded"] = True

# 성도 정보 + 월 선택/주소 (1행 2열 레이아웃)
st.markdown("---")
col_info, col_panel = st.columns([1, 1], gap="medium")

with col_info:
    with st.container(border=True):
        st.subheader("🙋 성도 정보(1회 입력)")
        st.caption("한 번 입력하면 다음 접속 때 자동으로 불러오고, 이후 모든 기록에 uid/이름/직분이 함께 저장됩니다.")

        col_r, col_n, col_s = st.columns(3, gap="small")
        with col_r:
            cur_role = st.session_state.get("member_role", MEMBER_ROLES[0])
            idx = MEMBER_ROLES.index(cur_role) if cur_role in MEMBER_ROLES else 0
            st.selectbox("직분", MEMBER_ROLES, index=idx, key="member_role")
        with col_n:
            st.text_input("성도 이름", key="member_name", placeholder="예) 홍 길 동")
        with col_s:
            if st.button("💾 성도 정보 저장", use_container_width=True):
                role_clean = normalize_role(st.session_state.get("member_role", ""))
                name_clean = clamp_20(st.session_state.get("member_name", ""))
                if not name_clean:
                    st.warning("이름을 입력해 주세요.")
                else:
                    storage.upsert_profile(uid, role_clean, name_clean)
                    st.success("저장되었습니다! 다음 접속부터 자동으로 불러옵니다.")
                    st.rerun()

        st.info(f"현재 저장 값: {normalize_role(st.session_state.get('member_role','')) or '-'} / {clamp_20(st.session_state.get('member_name','')) or '-'}")

with col_panel:
    with st.container(border=True):
        st.subheader("📆 월 선택 · 이번 달 달성")

        month_label = st.selectbox("📆 월 선택", [m[2] for m in SUPPORTED_MONTHS], key="month_label")
        year, month = [(y, m) for (y, m, lbl) in SUPPORTED_MONTHS if lbl == month_label][0]
        START, END = month_range(year, month)

        # 월 데이터(월 진행률/월 전체 보기용)
        df_month = storage.load_month(uid, START, END)

        done_cnt = int(df_month["완료"].sum()) if not df_month.empty else 0
        total_cnt = len(df_month) if len(df_month) > 0 else 1
        progress = done_cnt / total_cnt

        st.metric("✅ 이번 달 달성", f"{done_cnt}일", f"{progress:.1%}")
        st.progress(progress)

        st.caption("월 전체 기록은 오른쪽 **기록 확인 → 월 전체**에서 확인됩니다.")



# -------------------------
# 본문 (오늘 기록 ↔ 기록 확인) 1행 2열 레이아웃
# -------------------------
st.markdown("---")
col_today, col_check = st.columns([1, 1], gap="medium")

with col_today:
    with st.container(border=True):
        st.subheader("✍️ 오늘의 큐티 기록")

        if "picked_day" not in st.session_state:
            st.session_state["picked_day"] = today_kst()
        picked_day = st.date_input("날짜 선택", value=st.session_state["picked_day"], key="picked_day")
        day_str = picked_day.isoformat()

        role_to_save = normalize_role(st.session_state.get("member_role", MEMBER_ROLES[0]))
        name_to_save = clamp_20(st.session_state.get("member_name", ""))

        # 오늘 기록(표시용) - 선택 월과 무관하게 '선택한 날짜'의 기록을 정확히 보여줍니다.
        df_day = storage.load_month(uid, picked_day, picked_day)
        if df_day is not None and not df_day.empty:
            row = df_day.iloc[0]
            start_t = str(row.get("QT 시작", "")).strip()
            end_t = str(row.get("QT 종료", "")).strip()
            is_done = bool(row.get("완료", False))
        else:
            start_t, end_t, is_done = "", "", False

        b1, b2, b3 = st.columns(3, gap="small")
        if b1.button("▶ 시작(현재시간)", use_container_width=True):
            storage.upsert_one(uid, day_str, start_time=now_hhmm_kst(), member_role=role_to_save, member_name=name_to_save)
            st.rerun()
        if b2.button("■ 종료(현재시간)", use_container_width=True):
            storage.upsert_one(uid, day_str, end_time=now_hhmm_kst(), member_role=role_to_save, member_name=name_to_save)
            st.rerun()
        if b3.button("✅ " + ("취소" if is_done else "완료"), use_container_width=True):
            storage.upsert_one(uid, day_str, completed=not is_done, member_role=role_to_save, member_name=name_to_save)
            st.rerun()


            
            st.markdown("### 🕊️ 나의 묵상 기도 (50자 이내)")
        memo = st.text_area(
            "경건의 시간 하나님님께서 주신 감동으로 한 줄 묵상 기도를 적어 보세요.",
            height=85,
            max_chars=50,
            placeholder="예) 주님, 오늘 말씀을 붙잡고 순종할 힘을 주세요.",
            key="memo_50",
        )
        if st.button("묵상 기도 저장", use_container_width=True, type="primary"):
            storage.upsert_one(
                uid, day_str,
                signature="",
                prayer_note=clamp_50(memo),
                member_role=role_to_save,
                member_name=name_to_save
            )
            st.success("저장되었습니다!")
            st.rerun()

with col_check:
    with st.container(border=True):
        st.subheader("📋 기록 확인")

        view_mode = st.radio("보기", ["주간", "월 전체"], horizontal=True, label_visibility="collapsed", key="view_mode")
        if view_mode == "주간":
            anchor = st.session_state.get("picked_day", today_kst())
            wk_start = week_start_monday(anchor)
            wk_end = wk_start + timedelta(days=6)

            def _shift_week(delta_days: int):
                a = st.session_state.get("picked_day", today_kst())
                st.session_state["picked_day"] = a + timedelta(days=delta_days)

            nav1, nav2, nav3 = st.columns([1, 1, 2], gap="small")
            with nav1:
                st.button("⬅️ 이전 주", use_container_width=True, on_click=_shift_week, args=(-7,))
            with nav2:
                st.button("다음 주 ➡️", use_container_width=True, on_click=_shift_week, args=(+7,))
            with nav3:
                st.caption(f"표시 기간: {wk_start.isoformat()} ~ {wk_end.isoformat()} (월~일)")

            df_week = storage.load_month(uid, wk_start, wk_end)
            render_qt_table_html(df_week)

        else:
            # 월 전체는 상단의 '월 선택'과 연동됩니다.
            st.caption(f"선택 월: {month_label}  ·  월 전체 기록")
            render_qt_table_html(df_month)


# -------------------------
# 큐티 접속 주소(하단, 가로로 길게)
# -------------------------
st.markdown("---")
share_url = build_share_url(uid)
with st.container(border=True):
    st.subheader("📌 나의 QT 접속 주소")
    cL, cR = st.columns([1.2, 2.0], gap="small")
    with cL:
        st.caption("이 주소를 복사해서 카톡 ‘나에게 보내기’에 저장하거나 즐겨찾기 해 주세요.\n(다음 접속부터 이 주소로 바로 들어오면 됩니다.)")
    with cR:
        st.code(share_url)
        if "<YOUR-APP>" in share_url:
            st.warning("PUBLIC_APP_URL이 설정되지 않아 임시 주소가 보입니다. Secrets에 실제 앱 주소를 넣어주세요.")


# -------------------------
# Pray together in the Lord (중보기도 요청) - 마지막 위치
# -------------------------
st.markdown("---")

def _reset_pray_form():
    st.session_state["pray_title"] = ""
    st.session_state["pray_content"] = ""
    st.session_state["pray_public"] = False

def _submit_pray_request():
    title = clamp_50(st.session_state.get("pray_title", ""))
    content = clamp_300(st.session_state.get("pray_content", "") or "")
    is_public = bool(st.session_state.get("pray_public", False))

    role_to_save = normalize_role(st.session_state.get("member_role", MEMBER_ROLES[0]))
    name_to_save = clamp_20(st.session_state.get("member_name", ""))
    linked_day = st.session_state.get("picked_day", today_kst()).isoformat()

    if not title:
        st.session_state["pray_error"] = "기도 제목을 입력해 주세요."
        return

    try:
        storage.insert_prayer_request(
            uid=str(uid),
            member_role=role_to_save,
            member_name=name_to_save,
            prayer_title=title,
            prayer_content=content,
            is_public=is_public,
            linked_day=linked_day,
        )
        st.session_state["pray_notice"] = "중보기도 요청이 저장되었습니다. 목회자/중보팀이 함께 기도합니다."
        _reset_pray_form()
    except Exception:
        st.session_state["pray_error"] = "저장 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."

# 위젯 키(pray_*) 기본값(처음 1회)
if "pray_title" not in st.session_state:
    st.session_state["pray_title"] = ""
if "pray_content" not in st.session_state:
    st.session_state["pray_content"] = ""
if "pray_public" not in st.session_state:
    st.session_state["pray_public"] = False

with st.expander("🙏 함께 기도해요(Pray together in the Lord))", expanded=False):
    st.subheader("🙏 Let's pray together in the Lord")
    st.caption("여기에 남긴 기도 제목은 목회자/중보팀이 수시로 확인하고 사랑으로 함께 기도합니다. (공개 게시판이 아닙니다)")

    # 저장 결과 메시지(한 번만 노출)
    err = st.session_state.pop("pray_error", "")
    ok = st.session_state.pop("pray_notice", "")
    if err:
        st.error(err)
    elif ok:
        st.success(ok)

    st.text_input("기도 제목(필수, 50자 이내)", max_chars=50, placeholder="예) 가족 구원을 위해", key="pray_title")
    st.text_area("기도 내용(선택, 300자 이내)", height=120, placeholder="자유롭게 적어주세요.", key="pray_content")
    st.checkbox("공동체 중보 요청으로 표시(필요 시 공지/공동체 기도 참여 요청에 활용)", key="pray_public")

    st.button("🙏 중보기도 요청 저장", use_container_width=True, on_click=_submit_pray_request)
