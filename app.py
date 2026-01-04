import os
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from typing import Optional, Tuple

import pandas as pd
import streamlit as st

# Optional: Google Sheets storage
try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSHEETS_AVAILABLE = True
except Exception:
    GSHEETS_AVAILABLE = False


APP_TITLE = "1월 주만나 큐티 체크 리스트"

PROJECT_BOX_TEXT = (
    "2026년 예은 가족 큐티 프로젝트,\n"
    "큐티(QT)하는 성도가 하나님의 뷰티(BEAUTY)입니다"
)

VERSE_TEXT = "주를 경외하게 하는 주의 말씀을 주의 종에게 세우소서 [시편 119:38절]"

SUPPORTED_MONTHS = [
    (2026, 1, "2026년 1월"),
    (2026, 2, "2026년 2월"),
    (2026, 3, "2026년 3월"),
]


# -----------------------
# Date helpers
# -----------------------
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


# -----------------------
# Query param helpers (Streamlit 버전 호환)
# -----------------------
def get_query_param(name: str):
    try:
        val = st.query_params.get(name, None)
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


# -----------------------
# Input helpers
# -----------------------
def validate_time_str(s: str) -> bool:
    s = (s or "").strip()
    if not s:
        return True
    try:
        datetime.strptime(s, "%H:%M")
        return True
    except ValueError:
        return False


def now_hhmm() -> str:
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
# Storage
# =========================================================
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

    def admin_fetch_range(self, start: date, end: date) -> pd.DataFrame:
        raise NotImplementedError


class SQLiteStorage(StorageBase):
    def __init__(self, path: str = "qti_checklist.db"):
        self.path = path
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self._init()
        self._migrate_if_needed()

    def _init(self):
        sql = (
            "CREATE TABLE IF NOT EXISTS qti_records ("
            " uid TEXT NOT NULL,"
            " day TEXT NOT NULL,"
            " start_time TEXT,"
            " end_time TEXT,"
            " completed INTEGER NOT NULL DEFAULT 0,"
            " signature TEXT,"
            " prayer_note TEXT,"
            " updated_at TEXT NOT NULL,"
            " PRIMARY KEY (uid, day)"
            ")"
        )
        self.conn.execute(sql)
        self.conn.commit()

    def _migrate_if_needed(self):
        cur = self.conn.cursor()
        cur.execute("PRAGMA table_info(qti_records)")
        cols = [r[1] for r in cur.fetchall()]
        if "prayer_note" not in cols:
            self.conn.execute("ALTER TABLE qti_records ADD COLUMN prayer_note TEXT")
            self.conn.commit()

    def load_month(self, uid: str, start: date, end: date) -> pd.DataFrame:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT day, start_time, end_time, completed, signature, prayer_note "
            "FROM qti_records "
            "WHERE uid = ? AND day BETWEEN ? AND ? "
            "ORDER BY day ASC",
            (uid, start.isoformat(), end.isoformat()),
        )
        rows = cur.fetchall()

        existing = {}
        for r in rows:
            existing[r[0]] = {
                "날짜": r[0],
                "QT 시작": r[1] or "",
                "QT 종료": r[2] or "",
                "완료": bool(r[3]),
                "확인 서명/나의 묵상 기도": combine_sign_prayer(r[4], r[5]),
            }

        data = []
        for d in daterange(start, end):
            ds = d.isoformat()
            if ds in existing:
                data.append(existing[ds])
            else:
                # 2026-01-01 예시
                if d == date(2026, 1, 1):
                    data.append(
                        {"날짜": ds, "QT 시작": "10:30", "QT 종료": "12:00", "완료": False, "확인 서명/나의 묵상 기도": ""}
                    )
                else:
                    data.append(
                        {"날짜": ds, "QT 시작": "", "QT 종료": "", "완료": False, "확인 서명/나의 묵상 기도": ""}
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
                "INSERT INTO qti_records (uid, day, start_time, end_time, completed, signature, prayer_note, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(uid, day) DO UPDATE SET "
                " start_time=excluded.start_time, "
                " end_time=excluded.end_time, "
                " completed=excluded.completed, "
                " signature=excluded.signature, "
                " prayer_note=excluded.prayer_note, "
                " updated_at=excluded.updated_at",
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
            "INSERT INTO qti_records (uid, day, start_time, end_time, completed, signature, prayer_note, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(uid, day) DO UPDATE SET "
            " start_time=excluded.start_time, "
            " end_time=excluded.end_time, "
            " completed=excluded.completed, "
            " signature=excluded.signature, "
            " prayer_note=excluded.prayer_note, "
            " updated_at=excluded.updated_at",
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
            "SELECT uid, day, start_time, end_time, completed, signature, prayer_note, updated_at "
            "FROM qti_records "
            "WHERE day BETWEEN ? AND ? "
            "ORDER BY day ASC, uid ASC",
            (start.isoformat(), end.isoformat()),
        )
        rows = cur.fetchall()
        return pd.DataFrame(
            rows,
            columns=["uid", "day", "start_time", "end_time", "completed", "signature", "prayer_note", "updated_at"],
        )


class GoogleSheetsStorage(StorageBase):
    """
    Sheet columns:
      uid | day | start_time | end_time | completed | signature | prayer_note | updated_at
    """
    def __init__(self, spreadsheet_id: str, worksheet_name: str, service_account_json: dict):
        if not GSHEETS_AVAILABLE:
            raise RuntimeError("gspread/google-auth가 설치되지 않아 Google Sheets 저장소를 사용할 수 없습니다.")

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_info(service_account_json, scopes=scopes)
        gc = gspread.authorize(creds)
        self.sh = gc.open_by_key(spreadsheet_id)
        self.ws = self._get_or_create_ws(worksheet_name)
        self._ensure_header_migrate()

    def _get_or_create_ws(self, name: str):
        try:
            return self.sh.worksheet(name)
        except Exception:
            return self.sh.add_worksheet(title=name, rows=4000, cols=20)

    def _ensure_header_migrate(self):
        desired = ["uid", "day", "start_time", "end_time", "completed", "signature", "prayer_note", "updated_at"]
        first_row = self.ws.row_values(1)

        if not first_row:
            self.ws.append_row(desired)
            return

        # prayer_note 없으면 헤더 + 행 패딩하여 추가
        if "prayer_note" not in first_row:
            values = self.ws.get_all_values()
            header = first_row + ["prayer_note"]
            data_rows = values[1:] if len(values) > 1 else []
            fixed_rows = []
            for r in data_rows:
                while len(r) < len(header):
                    r.append("")
                fixed_rows.append(r[: len(header)])
            data = [header] + fixed_rows
            self.ws.clear()
            self.ws.update(data)

    def _fetch_all(self) -> pd.DataFrame:
        values = self.ws.get_all_values()
        if len(values) <= 1:
            return pd.DataFrame(columns=["uid", "day", "start_time", "end_time", "completed", "signature", "prayer_note", "updated_at"])

        header = values[0]
        df = pd.DataFrame(values[1:], columns=header)

        for c in ["uid", "day", "start_time", "end_time", "completed", "signature", "prayer_note", "updated_at"]:
            if c not in df.columns:
                df[c] = ""

        df["completed"] = df["completed"].astype(str).replace({"": "0"}).astype(int)
        return df[["uid", "day", "start_time", "end_time", "completed", "signature", "prayer_note", "updated_at"]]

    def _write_all(self, df: pd.DataFrame):
        header = ["uid", "day", "start_time", "end_time", "completed", "signature", "prayer_note", "updated_at"]
        df = df.copy()
        for c in header:
            if c not in df.columns:
                df[c] = ""
        df = df[header].fillna("")
        data = [header] + df.astype(str).values.tolist()
        self.ws.clear()
        self.ws.update(data)

    def load_month(self, uid: str, start: date, end: date) -> pd.DataFrame:
        all_df = self._fetch_all()
        existing = {}

        if not all_df.empty:
            sub = all_df[(all_df["uid"] == uid) & (all_df["day"] >= start.isoformat()) & (all_df["day"] <= end.isoformat())]
            for _, r in sub.iterrows():
                existing[r["day"]] = {
                    "날짜": r["day"],
                    "QT 시작": r.get("start_time", "") or "",
                    "QT 종료": r.get("end_time", "") or "",
                    "완료": bool(int(r.get("completed", 0))),
                    "확인 서명/나의 묵상 기도": combine_sign_prayer(r.get("signature", ""), r.get("prayer_note", "")),
                }

        data = []
        for d in daterange(start, end):
            ds = d.isoformat()
            if ds in existing:
                data.append(existing[ds])
            else:
                if d == date(2026, 1, 1):
                    data.append({"날짜": ds, "QT 시작": "10:30", "QT 종료": "12:00", "완료": False, "확인 서명/나의 묵상 기도": ""})
                else:
                    data.append({"날짜": ds, "QT 시작": "", "QT 종료": "", "완료": False, "확인 서명/나의 묵상 기도": ""})

        return pd.DataFrame(data)

    def upsert_month(self, uid: str, df: pd.DataFrame):
        all_df = self._fetch_all()
        now = datetime.now().isoformat(timespec="seconds")

        if all_df.empty:
            all_df = pd.DataFrame(columns=["uid", "day", "start_time", "end_time", "completed", "signature", "prayer_note", "updated_at"])

        keep = all_df[~((all_df["uid"] == uid) & (all_df["day"] >= df["날짜"].min()) & (all_df["day"] <= df["날짜"].max()))].copy()

        new_rows = []
        for _, r in df.iterrows():
            day = str(r["날짜"])
            sig, pray = parse_sign_and_prayer(str(r["확인 서명/나의 묵상 기도"]).strip())
            new_rows.append({
                "uid": uid,
                "day": day,
                "start_time": (str(r["QT 시작"]).strip() or ""),
                "end_time": (str(r["QT 종료"]).strip() or ""),
                "completed": 1 if bool(r["완료"]) else 0,
                "signature": sig or "",
                "prayer_note": pray or "",
                "updated_at": now,
            })

        merged = pd.concat([keep, pd.DataFrame(new_rows)], ignore_index=True)
        merged.sort_values(["day", "uid"], inplace=True)
        self._write_all(merged)

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
        all_df = self._fetch_all()
        if all_df.empty:
            all_df = pd.DataFrame(columns=["uid", "day", "start_time", "end_time", "completed", "signature", "prayer_note", "updated_at"])

        now = datetime.now().isoformat(timespec="seconds")
        mask = (all_df["uid"] == uid) & (all_df["day"] == day)

        if mask.any():
            idx = all_df[mask].index[0]
            if start_time is not None:
                all_df.at[idx, "start_time"] = start_time.strip()
            if end_time is not None:
                all_df.at[idx, "end_time"] = end_time.strip()
            if completed is not None:
                all_df.at[idx, "completed"] = 1 if bool(completed) else 0
            if signature is not None:
                all_df.at[idx, "signature"] = signature.strip()
            if prayer_note is not None:
                all_df.at[idx, "prayer_note"] = prayer_note.strip()
            all_df.at[idx, "updated_at"] = now
        else:
            all_df = pd.concat([all_df, pd.DataFrame([{
                "uid": uid,
                "day": day,
                "start_time": (start_time.strip() if start_time else ""),
                "end_time": (end_time.strip() if end_time else ""),
                "completed": 1 if bool(completed) else 0,
                "signature": (signature.strip() if signature else ""),
                "prayer_note": (prayer_note.strip() if prayer_note else ""),
                "updated_at": now,
            }])], ignore_index=True)

        all_df.sort_values(["day", "uid"], inplace=True)
        self._write_all(all_df)

    def delete_month(self, uid: str, start: date, end: date):
        all_df = self._fetch_all()
        if all_df.empty:
            return
        keep = all_df[~((all_df["uid"] == uid) & (all_df["day"] >= start.isoformat()) & (all_df["day"] <= end.isoformat()))].copy()
        keep.sort_values(["day", "uid"], inplace=True)
        self._write_all(keep)

    def admin_fetch_range(self, start: date, end: date) -> pd.DataFrame:
        all_df = self._fetch_all()
        if all_df.empty:
            return pd.DataFrame(columns=["uid", "day", "start_time", "end_time", "completed", "signature", "prayer_note", "updated_at"])
        sub = all_df[(all_df["day"] >= start.isoformat()) & (all_df["day"] <= end.isoformat())].copy()
        sub.sort_values(["day", "uid"], inplace=True)
        return sub


def get_storage() -> StorageBase:
    # Streamlit secrets 우선
    secrets_conf = {}
    try:
        secrets_conf = dict(st.secrets)
    except Exception:
        secrets_conf = {}

    spreadsheet_id = secrets_conf.get("GSHEETS_SPREADSHEET_ID") or os.getenv("GSHEETS_SPREADSHEET_ID")
    worksheet_name = secrets_conf.get("GSHEETS_WORKSHEET_NAME") or os.getenv("GSHEETS_WORKSHEET_NAME") or "qti_records"
    sa_json = secrets_conf.get("GSHEETS_SERVICE_ACCOUNT_JSON")

    if spreadsheet_id and sa_json:
        try:
            return GoogleSheetsStorage(spreadsheet_id, worksheet_name, sa_json)
        except Exception as e:
            st.warning(f"Google Sheets 저장소 초기화 실패 → SQLite로 전환합니다: {e}")

    return SQLiteStorage()


def is_admin() -> bool:
    admin_flag = get_query_param("admin")
    key = get_query_param("key")

    admin_key = None
    try:
        admin_key = st.secrets.get("ADMIN_KEY")
    except Exception:
        admin_key = None
    admin_key = admin_key or os.getenv("ADMIN_KEY")

    return (admin_flag in ("1", "true", "True")) and bool(admin_key) and (key == admin_key)


# =========================================================
# UI
# =========================================================
st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)

storage = get_storage()

month_label = st.selectbox("📆 월 선택", options=[m[2] for m in SUPPORTED_MONTHS], index=0)
year, month = [(y, m) for (y, m, lbl) in SUPPORTED_MONTHS if lbl == month_label][0]
START, END = month_range(year, month)

uid = get_query_param("uid")

# -------------------------
# Admin page
# -------------------------
if is_admin():
    st.subheader("🛡️ 관리자 전체 현황")
    st.caption("이 화면은 ADMIN_KEY로 보호됩니다. (URL: ?admin=1&key=...)")

    admin_df = storage.admin_fetch_range(START, END)
    if admin_df.empty:
        st.info("아직 저장된 데이터가 없습니다.")
    else:
        admin_df = admin_df.copy()
        admin_df["completed"] = admin_df["completed"].astype(int)

        participants = admin_df["uid"].nunique()
        recorded_rows = len(admin_df)
        completed_rows = int(admin_df["completed"].sum())

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("참여자(uid) 수", f"{participants}명")
        c2.metric("기록된 체크 행 수", f"{recorded_rows}개")
        c3.metric("완료 수", f"{completed_rows}개")
        c4.metric("기록 기준 완료율", f"{(completed_rows / recorded_rows * 100):.1f}%" if recorded_rows else "0.0%")

        st.markdown("### 📌 날짜별 완료 집계")
        day_summary = (
            admin_df.groupby("day")
            .agg(참여자수=("uid", "nunique"), 완료수=("completed", "sum"))
            .reset_index()
        )
        day_summary["완료율(%)"] = (day_summary["완료수"] / day_summary["참여자수"] * 100).round(1)
        st.dataframe(day_summary, use_container_width=True)

        st.markdown("### 🙏 묵상 기도 메모(메모 있는 것만)")
        memo_df = admin_df.copy()
        memo_df["prayer_note"] = memo_df["prayer_note"].fillna("").astype(str)
        memo_df = memo_df[memo_df["prayer_note"].str.strip() != ""]
        memo_df = memo_df[["day", "uid", "signature", "prayer_note", "completed", "updated_at"]].sort_values(["day", "uid"])
        st.dataframe(memo_df, use_container_width=True)

        st.markdown("### ⬇️ 관리자용 CSV 내보내기")
        csv_bytes = admin_df.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "전체 데이터 CSV 다운로드",
            data=csv_bytes,
            file_name=f"{year}-{month:02d}_qti_admin_export.csv",
            mime="text/csv",
            use_container_width=True,
        )

    st.markdown("---")
    st.markdown(
        "<div style='border:1px solid #ddd; border-radius:12px; padding:14px; background:#fafafa; white-space:pre-line;'>"
        + PROJECT_BOX_TEXT +
        "</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        "<div style='text-align:center; padding:10px; font-weight:600;'>" + VERSE_TEXT + "</div>",
        unsafe_allow_html=True,
    )
    st.stop()

# -------------------------
# User page: uid 생성
# -------------------------
if not uid:
    st.info(
        "이 체크리스트는 로그인 없이도 기록이 유지되도록, 처음 1회 개인용 링크(uid)를 생성합니다.\n\n"
        "1) 아래 버튼으로 개인 링크 만들기\n"
        "2) 생성된 링크를 즐겨찾기/홈화면 추가\n"
        "3) 이후에는 항상 그 링크로 접속하면 기록이 이어집니다."
    )
    if st.button("내 개인 링크 만들기 (처음 1회)", use_container_width=True):
        new_uid = secrets.token_urlsafe(12)
        set_query_param(uid=new_uid)
        st.rerun()

    st.markdown("---")
    st.caption("※ 본인 링크를 다른 사람에게 공유하면 기록이 섞일 수 있어요. 꼭 본인만 보관해 주세요.")
    st.stop()

with st.expander("📌 사용 안내", expanded=True):
    st.write(
        "- 이 페이지는 URL에 포함된 uid로 본인 기록을 구분합니다.\n"
        "- 서명 칸은 '이름/짧은 묵상 기도' 형식으로 쓸 수 있어요. 예: 정청운/주를 경외하게...\n"
        "- 본인 링크를 공유하면 기록이 섞일 수 있어요."
    )
    st.code(f"현재 내 uid: {uid}", language="text")

st.subheader(f"📅 {year}년 {month}월 스케줄표 ({START} ~ {END})")

df = storage.load_month(uid, START, END)

# 빠른 기록
st.markdown("### ⏱️ 오늘 QT 빠른 기록 (버튼으로 현재시간 자동 입력)")
today = date.today()
default_day = today if (today >= START and today <= END) else START
picked_day = st.date_input("날짜 선택", value=default_day, min_value=START, max_value=END)

b1, b2, b3 = st.columns([1, 1, 1])
with b1:
    if st.button("▶ QT 시작(현재시간)", use_container_width=True):
        storage.upsert_one(uid, picked_day.isoformat(), start_time=now_hhmm())
        st.success(f"{picked_day} 시작시간 저장: {now_hhmm()}")
        st.rerun()

with b2:
    if st.button("■ QT 종료(현재시간)", use_container_width=True):
        storage.upsert_one(uid, picked_day.isoformat(), end_time=now_hhmm())
        st.success(f"{picked_day} 종료시간 저장: {now_hhmm()}")
        st.rerun()

with b3:
    if st.button("✅ 완료 체크", use_container_width=True):
        storage.upsert_one(uid, picked_day.isoformat(), completed=True)
        st.success(f"{picked_day} 완료 체크 저장")
        st.rerun()

st.markdown("#### ✍️ 확인 서명/나의 묵상 기도 (예: 정청운/주를 경외하게 하는...)")
sign_pray_input = st.text_input(
    "입력",
    placeholder="정청운/주를 경외하게 하는 주의 말씀을 주의 종에게 세우소서",
)
if st.button("서명 저장", use_container_width=True):
    sig, pray = parse_sign_and_prayer(sign_pray_input)
    storage.upsert_one(uid, picked_day.isoformat(), signature=sig, prayer_note=pray)
    st.success(f"{picked_day} 서명/묵상 기도 저장 완료")
    st.rerun()

st.markdown("---")

# 월 표(편집)
edited = st.data_editor(
    df,
    use_container_width=True,
    hide_index=True,
    column_config={
        "날짜": st.column_config.TextColumn("날짜", disabled=True),
        "QT 시작": st.column_config.TextColumn("QT 시작 시간", help="예: 06:30 (HH:MM)"),
        "QT 종료": st.column_config.TextColumn("QT 종료 시간", help="예: 07:10 (HH:MM)"),
        "완료": st.column_config.CheckboxColumn("완료 체크"),
        "확인 서명/나의 묵상 기도": st.column_config.TextColumn(
            "확인 서명/나의 묵상 기도",
            help="예: 정청운/주를 경외하게 하는 주의 말씀을 주의 종에게 세우소서",
        ),
    },
)

bad_rows = []
for i, r in edited.iterrows():
    if not validate_time_str(r["QT 시작"]) or not validate_time_str(r["QT 종료"]):
        bad_rows.append(int(i) + 1)

can_save = not bool(bad_rows)
if bad_rows:
    st.error(f"시간 형식이 잘못된 행이 있어요: {bad_rows} → HH:MM 형식(예: 10:30)으로 입력해 주세요.")

c1, c2, c3 = st.columns([1, 1, 2])
with c1:
    if st.button("💾 월 전체 저장", use_container_width=True, disabled=not can_save):
        storage.upsert_month(uid, edited)
        st.success("저장 완료! 다음에 다시 접속해도 기록이 유지됩니다.")

with c2:
    csv_bytes = edited.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "⬇️ CSV로 내보내기",
        data=csv_bytes,
        file_name=f"{year}-{month:02d}_qti_checklist.csv",
        mime="text/csv",
        use_container_width=True,
    )

with c3:
    with st.expander("⚠️ 이 달 기록 초기화(내 기록만)", expanded=False):
        st.write("이 동작은 되돌릴 수 없습니다. (이 uid의 선택한 월 기록만 삭제)")
        if st.button("내 기록 전부 삭제", type="primary", use_container_width=True):
            storage.delete_month(uid, START, END)
            st.warning("삭제 완료. 페이지를 새로고침하면 빈 표로 다시 시작합니다.")
            st.rerun()

st.markdown("---")
st.markdown(
    "<div style='border:1px solid #ddd; border-radius:12px; padding:14px; background:#fafafa; white-space:pre-line;'>"
    + PROJECT_BOX_TEXT +
    "</div>",
    unsafe_allow_html=True,
)
st.markdown(
    "<div style='text-align:center; padding:10px; font-weight:600;'>" + VERSE_TEXT + "</div>",
    unsafe_allow_html=True,
)
