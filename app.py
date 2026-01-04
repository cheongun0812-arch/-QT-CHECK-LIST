import os
import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

# (선택) Google Sheets 저장소
try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSHEETS_AVAILABLE = True
except Exception:
    GSHEETS_AVAILABLE = False


# =========================================================
# 기본 텍스트(요청 반영)
# =========================================================
APP_TITLE = "1월 주만나 큐티 체크 리스트"

PROJECT_BOX_TEXT = (
    "2026년 예은 가족 큐티 프로젝트,\n"
    "큐티(QT)하는 성도가 하나님의 뷰티(BEAUTY)입니다"
)

VERSE_TEXT = "주를 경외하게 하는 주의 말씀을 주의 종에게 세우소서 [시편 119:38절]"


# =========================================================
# 월 범위(1~3월 전환) - 요청 반영
# =========================================================
SUPPORTED_MONTHS = [
    (2026, 1, "2026년 1월"),
    (2026, 2, "2026년 2월"),
    (2026, 3, "2026년 3월"),
]


def month_range(year: int, month: int):
    start = date(year, month, 1)
    # 다음 달 1일 - 1일
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


# =========================================================
# Query param helpers
# =========================================================
def get_query_param(name: str):
    try:
        qp = st.query_params
        val = qp.get(name, None)
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


# =========================================================
# 시간 형식 검증
# =========================================================
def validate_time_str(s: str):
    s = (s or "").strip()
    if not s:
        return True
    try:
        datetime.strptime(s, "%H:%M")
        return True
    except ValueError:
        return False


def now_hhmm():
    return datetime.now().strftime("%H:%M")


# =========================================================
# Storage 인터페이스
# =========================================================
@dataclass
class Record:
    uid: str
    day: str  # YYYY-MM-DD
    start_time: str | None
    end_time: str | None
    completed: int  # 0/1
    signature: str | None
    updated_at: str


class StorageBase:
    def load_month(self, uid: str, start: date, end: date) -> pd.DataFrame:
        raise NotImplementedError

    def upsert_month(self, uid: str, df: pd.DataFrame):
        raise NotImplementedError

    def upsert_one(self, uid: str, day: str, start_time=None, end_time=None, completed=None, signature=None):
        raise NotImplementedError

    def delete_month(self, uid: str, start: date, end: date):
        raise NotImplementedError

    # 관리자용
    def admin_fetch_range(self, start: date, end: date) -> pd.DataFrame:
        raise NotImplementedError


# =========================================================
# SQLite Storage (기본 동작)
# =========================================================
class SQLiteStorage(StorageBase):
    def __init__(self, path="qti_checklist.db"):
        self.path = path
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self._init()

    def _init(self):
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS qti_records (
                uid TEXT NOT NULL,
                day TEXT NOT NULL,
                start_time TEXT,
                end_time TEXT,
                completed INTEGER NOT NULL DEFAULT 0,
                signature TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (uid, day)
            )
            """
        )
        self.conn.commit()

    def load_month(self, uid: str, start: date, end: date) -> pd.DataFrame:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT day, start_time, end_time, completed, signature
            FROM qti_records
            WHERE uid = ? AND day BETWEEN ? AND ?
            ORDER BY day ASC
            """,
            (uid, start.isoformat(), end.isoformat()),
        )
        rows = cur.fetchall()

        existing = {
            r[0]: {
                "날짜": r[0],
                "QT 시작": r[1] or "",
                "QT 종료": r[2] or "",
                "완료": bool(r[3]),
                "확인(사인)": r[4] or "",
            }
            for r in rows
        }

        data = []
        for d in daterange(start, end):
            ds = d.isoformat()
            if ds in existing:
                data.append(existing[ds])
            else:
                # 2026-01-01 예시 반영
                if d == date(2026, 1, 1):
                    data.append({"날짜": ds, "QT 시작": "10:30", "QT 종료": "12:00", "완료": False, "확인(사인)": ""})
                else:
                    data.append({"날짜": ds, "QT 시작": "", "QT 종료": "", "완료": False, "확인(사인)": ""})

        return pd.DataFrame(data)

    def upsert_month(self, uid: str, df: pd.DataFrame):
        now = datetime.now().isoformat(timespec="seconds")
        cur = self.conn.cursor()
        for _, row in df.iterrows():
            day = str(row["날짜"])
            start_time = str(row["QT 시작"]).strip() or None
            end_time = str(row["QT 종료"]).strip() or None
            completed = 1 if bool(row["완료"]) else 0
            signature = str(row["확인(사인)"]).strip() or None

            cur.execute(
                """
                INSERT INTO qti_records (uid, day, start_time, end_time, completed, signature, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(uid, day) DO UPDATE SET
                    start_time = excluded.start_time,
                    end_time = excluded.end_time,
                    completed = excluded.completed,
                    signature = excluded.signature,
                    updated_at = excluded.updated_at
                """,
                (uid, day, start_time, end_time, completed, signature, now),
            )
        self.conn.commit()

    def upsert_one(self, uid: str, day: str, start_time=None, end_time=None, completed=None, signature=None):
        # 기존값 유지하면서 부분 업데이트를 위해 먼저 읽고 합쳐 저장
        cur = self.conn.cursor()
        cur.execute(
            "SELECT start_time, end_time, completed, signature FROM qti_records WHERE uid=? AND day=?",
            (uid, day),
        )
        row = cur.fetchone()
        if row:
            cur_start, cur_end, cur_completed, cur_sign = row
        else:
            cur_start, cur_end, cur_completed, cur_sign = None, None, 0, None

        new_start = cur_start if start_time is None else (start_time.strip() or None)
        new_end = cur_end if end_time is None else (end_time.strip() or None)
        new_completed = cur_completed if completed is None else (1 if bool(completed) else 0)
        new_sign = cur_sign if signature is None else (signature.strip() or None)

        now = datetime.now().isoformat(timespec="seconds")
        cur.execute(
            """
            INSERT INTO qti_records (uid, day, start_time, end_time, completed, signature, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(uid, day) DO UPDATE SET
                start_time = excluded.start_time,
                end_time = excluded.end_time,
                completed = excluded.completed,
                signature = excluded.signature,
                updated_at = excluded.updated_at
            """,
            (uid, day, new_start, new_end, new_completed, new_sign, now),
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
            """
            SELECT uid, day, start_time, end_time, completed, signature, updated_at
            FROM qti_records
            WHERE day BETWEEN ? AND ?
            ORDER BY day ASC, uid ASC
            """,
            (start.isoformat(), end.isoformat()),
        )
        rows = cur.fetchall()
        return pd.DataFrame(rows, columns=["uid", "day", "start_time", "end_time", "completed", "signature", "updated_at"])


# =========================================================
# Google Sheets Storage (권장: 배포 재시작에도 안전)
# - secrets.toml 또는 환경변수로 설정되면 자동 사용
# =========================================================
class GoogleSheetsStorage(StorageBase):
    """
    한 시트(worksheet)에 모든 레코드를 "append+upsert" 형태로 저장.
    스키마:
      uid | day | start_time | end_time | completed | signature | updated_at
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
        self._ensure_header()

    def _get_or_create_ws(self, name: str):
        try:
            return self.sh.worksheet(name)
        except Exception:
            return self.sh.add_worksheet(title=name, rows=2000, cols=20)

    def _ensure_header(self):
        header = ["uid", "day", "start_time", "end_time", "completed", "signature", "updated_at"]
        first_row = self.ws.row_values(1)
        if first_row != header:
            self.ws.clear()
            self.ws.append_row(header)

    def _fetch_all(self) -> pd.DataFrame:
        values = self.ws.get_all_values()
        if len(values) <= 1:
            return pd.DataFrame(columns=["uid", "day", "start_time", "end_time", "completed", "signature", "updated_at"])
        df = pd.DataFrame(values[1:], columns=values[0])
        # 타입 정리
        if "completed" in df.columns:
            df["completed"] = df["completed"].astype(str).replace({"": "0"}).astype(int)
        return df

    def _write_all(self, df: pd.DataFrame):
        header = ["uid", "day", "start_time", "end_time", "completed", "signature", "updated_at"]
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
        if all_df.empty:
            existing = {}
        else:
            sub = all_df[(all_df["uid"] == uid) & (all_df["day"] >= start.isoformat()) & (all_df["day"] <= end.isoformat())]
            existing = {
                r["day"]: {
                    "날짜": r["day"],
                    "QT 시작": r.get("start_time", "") or "",
                    "QT 종료": r.get("end_time", "") or "",
                    "완료": bool(int(r.get("completed", 0))),
                    "확인(사인)": r.get("signature", "") or "",
                }
                for _, r in sub.iterrows()
            }

        data = []
        for d in daterange(start, end):
            ds = d.isoformat()
            if ds in existing:
                data.append(existing[ds])
            else:
                if d == date(2026, 1, 1):
                    data.append({"날짜": ds, "QT 시작": "10:30", "QT 종료": "12:00", "완료": False, "확인(사인)": ""})
                else:
                    data.append({"날짜": ds, "QT 시작": "", "QT 종료": "", "완료": False, "확인(사인)": ""})
        return pd.DataFrame(data)

    def upsert_month(self, uid: str, df: pd.DataFrame):
        all_df = self._fetch_all()
        now = datetime.now().isoformat(timespec="seconds")

        # uid+day key
        if all_df.empty:
            all_df = pd.DataFrame(columns=["uid", "day", "start_time", "end_time", "completed", "signature", "updated_at"])

        # 기존 중복 제거 후, 이번 df로 채우는 방식
        keep = all_df[~((all_df["uid"] == uid) & (all_df["day"] >= df["날짜"].min()) & (all_df["day"] <= df["날짜"].max()))].copy()

        new_rows = []
        for _, r in df.iterrows():
            day = str(r["날짜"])
            new_rows.append(
                {
                    "uid": uid,
                    "day": day,
                    "start_time": (str(r["QT 시작"]).strip() or ""),
                    "end_time": (str(r["QT 종료"]).strip() or ""),
                    "completed": 1 if bool(r["완료"]) else 0,
                    "signature": (str(r["확인(사인)"]).strip() or ""),
                    "updated_at": now,
                }
            )

        merged = pd.concat([keep, pd.DataFrame(new_rows)], ignore_index=True)
        merged.sort_values(["day", "uid"], inplace=True)
        self._write_all(merged)

    def upsert_one(self, uid: str, day: str, start_time=None, end_time=None, completed=None, signature=None):
        all_df = self._fetch_all()
        if all_df.empty:
            all_df = pd.DataFrame(columns=["uid", "day", "start_time", "end_time", "completed", "signature", "updated_at"])

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
            all_df.at[idx, "updated_at"] = now
        else:
            all_df = pd.concat(
                [
                    all_df,
                    pd.DataFrame(
                        [{
                            "uid": uid,
                            "day": day,
                            "start_time": (start_time.strip() if start_time else ""),
                            "end_time": (end_time.strip() if end_time else ""),
                            "completed": 1 if bool(completed) else 0,
                            "signature": (signature.strip() if signature else ""),
                            "updated_at": now,
                        }]
                    ),
                ],
                ignore_index=True,
            )

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
            return pd.DataFrame(columns=["uid", "day", "start_time", "end_time", "completed", "signature", "updated_at"])
        sub = all_df[(all_df["day"] >= start.isoformat()) & (all_df["day"] <= end.isoformat())].copy()
        sub.sort_values(["day", "uid"], inplace=True)
        return sub


# =========================================================
# 저장소 선택 (Sheets 설정이 있으면 Sheets 우선)
# =========================================================
def get_storage() -> StorageBase:
    # Streamlit secrets 우선
    secrets_conf = {}
    try:
        secrets_conf = dict(st.secrets)
    except Exception:
        secrets_conf = {}

    # 1) Google Sheets 모드 조건:
    # - GSHEETS_SPREADSHEET_ID
    # - GSHEETS_WORKSHEET_NAME
    # - GSHEETS_SERVICE_ACCOUNT_JSON (dict)
    spreadsheet_id = secrets_conf.get("GSHEETS_SPREADSHEET_ID") or os.getenv("GSHEETS_SPREADSHEET_ID")
    worksheet_name = secrets_conf.get("GSHEETS_WORKSHEET_NAME") or os.getenv("GSHEETS_WORKSHEET_NAME") or "qti_records"
    sa_json = secrets_conf.get("GSHEETS_SERVICE_ACCOUNT_JSON")

    if spreadsheet_id and sa_json:
        try:
            return GoogleSheetsStorage(spreadsheet_id, worksheet_name, sa_json)
        except Exception as e:
            # 실패하면 SQLite로 fallback
            st.warning(f"Google Sheets 저장소 초기화에 실패하여 SQLite로 전환합니다: {e}")

    return SQLiteStorage()


# =========================================================
# 관리자 접근 제어 (요청 반영)
# - 로그인 대신, ADMIN_KEY로 보호
# - 접속: ?admin=1&key=ADMIN_KEY
# =========================================================
def is_admin() -> bool:
    admin_flag = get_query_param("admin")
    key = get_query_param("key")

    admin_key = None
    try:
        admin_key = st.secrets.get("ADMIN_KEY")
    except Exception:
        admin_key = None
    admin_key = admin_key or os.getenv("ADMIN_KEY")

    if admin_flag in ("1", "true", "True") and admin_key and key == admin_key:
        return True
    return False


# =========================================================
# UI
# =========================================================
st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)

storage = get_storage()

# 월 선택 UI (요청 반영)
month_label = st.selectbox(
    "📆 월 선택",
    options=[m[2] for m in SUPPORTED_MONTHS],
    index=0,
)
year, month = [(y, m) for (y, m, lbl) in SUPPORTED_MONTHS if lbl == month_label][0]
START, END = month_range(year, month)

uid = get_query_param("uid")

# 관리자 페이지
if is_admin():
    st.subheader("🛡️ 관리자 전체 현황")
    st.caption("이 화면은 ADMIN_KEY로 보호됩니다. (URL에 ?admin=1&key=... 필요)")

    admin_df = storage.admin_fetch_range(START, END)

    if admin_df.empty:
        st.info("아직 저장된 데이터가 없습니다.")
    else:
        # 지표: 참여자 수, 전체 완료율
        # 참여자 기준은 uid 단위(서명은 자유입력이라 신뢰도 낮음)
        participants = admin_df["uid"].nunique()
        total_rows = len(list(daterange(START, END))) * participants if participants else 0

        # 완료율 계산: uid+day 중 completed=1인 건수 / (참여자*일수)
        # 단, 어떤 uid가 일부 날짜만 기록했으면 분모가 과하게 커질 수 있어
        # 그래서 "기록이 존재하는 행" 기준 완료율도 함께 제공
        recorded_rows = len(admin_df)
        completed_rows = int(admin_df["completed"].astype(int).sum()) if "completed" in admin_df.columns else 0

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("참여자(uid) 수", f"{participants}명")
        col2.metric("기록된 체크 행 수", f"{recorded_rows}개")
        col3.metric("기록 기준 완료 수", f"{completed_rows}개")
        col4.metric("기록 기준 완료율", f"{(completed_rows / recorded_rows * 100):.1f}%" if recorded_rows else "0.0%")

        st.markdown("### 📌 미완료자/완료자 집계(날짜별)")
        # 날짜별 집계
        by_day = admin_df.copy()
        by_day["completed"] = by_day["completed"].astype(int)
        day_summary = (
            by_day.groupby("day")
            .agg(
                참여자수=("uid", "nunique"),
                완료수=("completed", "sum"),
            )
            .reset_index()
        )
        day_summary["완료율(%)"] = (day_summary["완료수"] / day_summary["참여자수"] * 100).round(1)

        st.dataframe(day_summary, use_container_width=True)

        st.markdown("### 🙏 개인별 진행률(중보기도 참고용)")
        # 개인별: 완료율, 미완료 날짜 리스트
        days_all = [d.isoformat() for d in daterange(START, END)]
        per_uid = []
        for u in sorted(admin_df["uid"].unique()):
            sub = admin_df[admin_df["uid"] == u].copy()
            sub["completed"] = sub["completed"].astype(int)
            # 해당 uid가 기록한 날만 보면 빠진 날이 파악 안됨 → 전체 월 기준으로 미완료 산출
            completed_days = set(sub.loc[sub["completed"] == 1, "day"].tolist())
            missing_or_incomplete = [d for d in days_all if d not in completed_days]
            # 표시가 너무 길면 앞부분만
            preview = ", ".join(missing_or_incomplete[:10]) + (" …" if len(missing_or_incomplete) > 10 else "")
            per_uid.append(
                {
                    "uid": u,
                    "완료일수": len(completed_days),
                    "총일수": len(days_all),
                    "완료율(%)": round(len(completed_days) / len(days_all) * 100, 1),
                    "미완료(미기록 포함) 날짜(앞 10개)": preview,
                    "최근 업데이트": sub["updated_at"].max() if "updated_at" in sub.columns else "",
                    "서명(참고)": sub["signature"].dropna().astype(str).replace("", pd.NA).dropna().tail(1).tolist()[0] if "signature" in sub.columns and len(sub["signature"].dropna()) else "",
                }
            )

        per_uid_df = pd.DataFrame(per_uid)
        st.dataframe(per_uid_df, use_container_width=True)

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
        f"""
        <div style="border:1px solid #ddd; border-radius:12px; padding:14px; background:#fafafa; white-space:pre-line;">
        {PROJECT_BOX_TEXT}
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""
        <div style="text-align:center; padding:10px; font-weight:600;">
        {VERSE_TEXT}
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.stop()


# 일반 사용자 페이지
if not uid:
    st.info(
        "이 체크리스트는 **로그인 없이도 기록이 유지되도록**, 처음 1회 **개인용 링크(uid)** 를 생성합니다.\n\n"
        "✅ 아래 버튼을 눌러 **내 개인 링크**를 만든 뒤, 그 링크를 **즐겨찾기/홈화면에 추가**해 주세요.\n"
        "그 다음부터는 항상 같은 링크로 접속하면 기록이 이어집니다."
    )
    if st.button("내 개인 링크 만들기 (처음 1회)", use_container_width=True):
        new_uid = secrets.token_urlsafe(12)
        set_query_param(uid=new_uid)
        st.rerun()

    st.markdown("---")
    st.caption("※ 본인 링크를 다른 사람에게 공유하면 기록이 노출/섞일 수 있어요. 꼭 본인만 보관해 주세요.")
    st.stop()


# 안내
with st.expander("📌 사용 안내", expanded=True):
    st.write(
        "- 이 페이지는 **주소(URL)에 포함된 uid**로 본인 기록을 구분합니다.\n"
        "- 따라서 **본인 링크를 다른 사람에게 공유하면 기록이 섞일 수 있어요.**\n"
        "- 가장 좋은 사용법: **개인 링크를 즐겨찾기/홈화면 추가**"
    )
    st.code(f"현재 내 uid: {uid}", language="text")

st.subheader(f"📅 {year}년 {month}월 스케줄표 ({START} ~ {END})")

# 데이터 로드
df = storage.load_month(uid, START, END)

# -------------------------
# 오늘 QT 시작/종료 버튼(요청 반영)
# -------------------------
st.markdown("### ⏱️ 오늘 QT 빠른 기록 (버튼으로 현재시간 자동 입력)")
today = date.today()
default_day = today if (today >= START and today <= END) else START
picked_day = st.date_input("날짜 선택", value=default_day, min_value=START, max_value=END)

colA, colB, colC, colD = st.columns([1, 1, 1, 2])
with colA:
    if st.button("▶ QT 시작(현재시간)", use_container_width=True):
        storage.upsert_one(uid, picked_day.isoformat(), start_time=now_hhmm())
        st.success(f"{picked_day} 시작시간 저장: {now_hhmm()}")
        st.rerun()

with colB:
    if st.button("■ QT 종료(현재시간)", use_container_width=True):
        storage.upsert_one(uid, picked_day.isoformat(), end_time=now_hhmm())
        st.success(f"{picked_day} 종료시간 저장: {now_hhmm()}")
        st.rerun()

with colC:
    if st.button("✅ 완료 체크", use_container_width=True):
        storage.upsert_one(uid, picked_day.isoformat(), completed=True)
        st.success(f"{picked_day} 완료 체크 저장")
        st.rerun()

with colD:
    sign_quick = st.text_input("오늘 서명(선택)", placeholder="예: 홍길동 / 이니셜")
    if st.button("✍️ 서명 저장", use_container_width=True):
        storage.upsert_one(uid, picked_day.isoformat(), signature=sign_quick or "")
        st.success(f"{picked_day} 서명 저장")
        st.rerun()


st.markdown("---")

# -------------------------
# 표 편집(전체 월)
# -------------------------
edited = st.data_editor(
    df,
    use_container_width=True,
    hide_index=True,
    column_config={
        "날짜": st.column_config.TextColumn("날짜", disabled=True, help="YYYY-MM-DD"),
        "QT 시작": st.column_config.TextColumn("QT 시작 시간", help="예: 06:30 (24시간, HH:MM)"),
        "QT 종료": st.column_config.TextColumn("QT 종료 시간", help="예: 07:10 (24시간, HH:MM)"),
        "완료": st.column_config.CheckboxColumn("완료 체크"),
        "확인(사인)": st.column_config.TextColumn("본인 확인(사인)", help="예: 홍길동 / 이니셜 등"),
    },
)

# 시간 검증
bad_rows = []
for i, r in edited.iterrows():
    if not validate_time_str(r["QT 시작"]) or not validate_time_str(r["QT 종료"]):
        bad_rows.append(int(i) + 1)

if bad_rows:
    st.error(f"시간 형식이 잘못된 행이 있어요: {bad_rows}  →  HH:MM (예: 10:30) 형식으로 입력해 주세요.")
    can_save = False
else:
    can_save = True

col1, col2, col3 = st.columns([1, 1, 2])

with col1:
    if st.button("💾 월 전체 저장", use_container_width=True, disabled=not can_save):
        storage.upsert_month(uid, edited)
        st.success("저장 완료! 다음에 다시 접속해도 기록이 유지됩니다.")

with col2:
    csv_bytes = edited.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "⬇️ CSV로 내보내기",
        data=csv_bytes,
        file_name=f"{year}-{month:02d}_qti_checklist.csv",
        mime="text/csv",
        use_container_width=True,
    )

with col3:
    with st.expander("⚠️ 이 달 기록 초기화(내 기록만)", expanded=False):
        st.write("이 동작은 되돌릴 수 없습니다. (이 uid의 선택한 월 기록만 삭제)")
        if st.button("내 기록 전부 삭제", type="primary", use_container_width=True):
            storage.delete_month(uid, START, END)
            st.warning("삭제 완료. 페이지를 새로고침하면 빈 표로 다시 시작합니다.")
            st.rerun()

st.markdown("---")

# 하단 박스(요청 반영)
st.markdown(
    f"""
    <div style="border:1px solid #ddd; border-radius:12px; padding:14px; background:#fafafa; white-space:pre-line;">
    {PROJECT_BOX_TEXT}
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    f"""
    <div style="text-align:center; padding:10px; font-weight:600;">
    {VERSE_TEXT}
    </div>
    """,
    unsafe_allow_html=True,
)
