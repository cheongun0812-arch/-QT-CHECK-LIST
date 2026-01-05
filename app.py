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
APP_TITLE = "1월 주만나 큐티 체크 리스트"
VERSE_TEXT = "주를 경외하게 하는 주의 말씀을 주의 종에게 세우소서 [시편 119:38절]"
SUPPORTED_MONTHS = [
    (2026, 1, "2026년 1월"),
    (2026, 2, "2026년 2월"),
    (2026, 3, "2026년 3월"),
]

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
    """
    현재 앱의 base URL로 공유 링크 생성
    - st.context.url이 가능한 환경이면 그 값을 사용
    - 아니면 Secrets의 PUBLIC_APP_URL 사용
    """
    base = None
    try:
        base = st.context.url  # 쿼리스트링 제외 base URL
    except Exception:
        base = None

    if not base:
        base = st.secrets.get("PUBLIC_APP_URL")

    if not base:
        # 마지막 fallback (사용자가 직접 설정 유도)
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


def parse_sign_and_prayer(text: str):
    if not text or "/" not in text:
        return (text or "").strip(), ""
    a, b = text.split("/", 1)
    return a.strip(), b.strip()


def combine_sign_prayer(sig: str, pray: str) -> str:
    sig = (sig or "").strip()
    pray = (pray or "").strip()
    if sig and pray:
        return f"{sig}/{pray}"
    return sig or pray or ""


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
            ws = sh.add_worksheet(title=worksheet_name, rows="2000", cols="10")
            ws.append_row(
                ["uid", "day", "start_time", "end_time", "completed", "signature", "prayer_note", "updated_at"]
            )

        self.ws = ws

    def _empty_df(self, start: date, end: date) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"날짜": ds, "QT 시작": "", "QT 종료": "", "완료": False, "나의 묵상 기도": ""}
                for d in daterange(start, end)
            ]
        )

    def load_month(self, uid: str, start: date, end: date) -> pd.DataFrame:
        try:
            all_data = self.ws.get_all_records()
            df_all = pd.DataFrame(all_data)
            if df_all.empty:
                return self._empty_df(start, end)

            # day는 ISO 문자열이므로 문자열 비교 OK
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
                            "나의 묵상 기도": combine_sign_prayer(r.get("signature", ""), r.get("prayer_note", "")),
                        }
                    )
                else:
                    rows.append({"날짜": ds, "QT 시작": "", "QT 종료": "", "완료": False, "확인 서명/나의 묵상 기도": ""})

            return pd.DataFrame(rows)
        except Exception:
            return self._empty_df(start, end)

    def upsert_one(self, uid: str, day: str, **kwargs):
        # day: "YYYY-MM-DD"
        all_records = self.ws.get_all_records()
        df = pd.DataFrame(all_records)

        row_idx = -1
        if not df.empty:
            match = df[(df["uid"].astype(str) == str(uid)) & (df["day"] == str(day))]
            if not match.empty:
                row_idx = match.index[0] + 2  # header 때문에 +2

        # ✅ KST로 저장
        now_iso = now_kst().isoformat()

        col_map = {
            "start_time": 3,
            "end_time": 4,
            "completed": 5,
            "signature": 6,
            "prayer_note": 7,
        }

        def norm_value(k, v):
            if k == "completed":
                return "1" if bool(v) else "0"
            return v if v is not None else ""

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
    return GoogleSheetsStorage(s_id, "qti_records", sa_obj)


# =========================
# UI
# =========================
st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(f"✨ {APP_TITLE}")
st.caption(VERSE_TEXT)

storage = get_storage()
if not storage:
    st.error("구글 시트 설정(Secrets) 또는 gspread 라이브러리를 확인해주세요.")
    st.stop()

month_label = st.selectbox("📆 월 선택", [m[2] for m in SUPPORTED_MONTHS])
year, month = [(y, m) for (y, m, lbl) in SUPPORTED_MONTHS if lbl == month_label][0]
START, END = month_range(year, month)

# UID 확인
uid = get_uid_from_url()

if not uid:
    st.info("### 🙏 큐티 체크리스트 시작하기\n성도님 전용 기록지를 만들기 위해 아래 버튼을 눌러주세요.")
    if st.button("🚀 나의 큐티 링크 만들기 (처음 1회)", use_container_width=True):
        new_uid = secrets.token_urlsafe(8)
        set_uid_in_url(new_uid)  # ✅ 구버전 API만 사용 (섞이지 않음)
        st.rerun()
    st.stop()

# 데이터 로드
df = storage.load_month(uid, START, END)

# 진행률
done_cnt = int(df["완료"].sum()) if not df.empty else 0
total_cnt = len(df) if len(df) > 0 else 1
progress = done_cnt / total_cnt
st.metric("이번 달 달성", f"{done_cnt}일", f"{progress:.1%}")
st.progress(progress)

# 공유 URL
share_url = build_share_url(uid)

with st.expander("📢 내 기록을 보관하려면? (즐겨찾기 필수)", expanded=False):
    st.success("성도님 전용 모드로 연결되었습니다.")
    st.markdown("**이 주소를 꼭 복사해서 카톡 '나에게 보내기'에 저장하거나 즐겨찾기 하세요!**")
    st.code(share_url)
    if "<YOUR-APP>" in share_url:
        st.warning("PUBLIC_APP_URL이 설정되지 않아 임시 주소가 보입니다. Streamlit Secrets에 PUBLIC_APP_URL을 실제 앱 주소로 넣어주세요.")

st.markdown("---")

with st.container(border=True):
    st.subheader("✍️ 오늘의 큐티 기록")

    # ✅ rerun 되어도 날짜 선택 유지
    default_day = st.session_state.get("picked_day", today_kst())
    picked_day = st.date_input("날짜 선택", value=default_day, key="picked_day")
    day_str = picked_day.isoformat()

    row = df[df["날짜"] == day_str]
    cur_start = row["QT 시작"].values[0] if not row.empty else ""
    cur_end = row["QT 종료"].values[0] if not row.empty else ""
    is_done = bool(row["완료"].values[0]) if not row.empty else False

    st.caption(f"현재 기록 → 시작: {cur_start or '-'} / 종료: {cur_end or '-'} / 완료: {'예' if is_done else '아니오'}")

    c1, c2, c3 = st.columns(3)

    if c1.button("▶ 시작(현재시간)", use_container_width=True):
        storage.upsert_one(uid, day_str, start_time=now_hhmm_kst())
        st.rerun()

    if c2.button("■ 종료(현재시간)", use_container_width=True):
        storage.upsert_one(uid, day_str, end_time=now_hhmm_kst())
        st.rerun()

    if c3.button("✅ " + ("취소" if is_done else "완료"), use_container_width=True):
        storage.upsert_one(uid, day_str, completed=not is_done)
        st.rerun()

    memo = st.text_input("경건의 시간 하나님님께서 주신 감동으로 한 줄 묵상 기도를 적어 보세요.")
    if st.button("기록 저장하기", use_container_width=True, type="primary"):
        sig, pray = parse_sign_and_prayer(memo)
        storage.upsert_one(uid, day_str, signature=sig, prayer_note=pray)
        st.success("저장되었습니다!")
        st.rerun()

with st.expander("📋 한 달 전체 기록 확인"):
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "날짜": st.column_config.TextColumn(width="small"),
            "QT 시작": st.column_config.TextColumn(width="small"),
            "QT 종료": st.column_config.TextColumn(width="small"),
            "완료": st.column_config.CheckboxColumn(width="small"),
            "나의 묵상 기도": st.column_config.TextColumn(width="large"),
        },
    )
