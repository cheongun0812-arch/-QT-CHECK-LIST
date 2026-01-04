import streamlit as st
import pandas as pd
import sqlite3
import secrets
from datetime import date, datetime, timedelta

# =========================
# 기본 설정
# =========================
APP_TITLE = "1월 주만나 큐티 체크 리스트"

PROJECT_BOX_TEXT = (
    "2026년 예은 가족 큐티 프로젝트,\n"
    "큐티(QT)하는 성도가 하나님의 뷰티(BEAUTY)입니다"
)

VERSE_TEXT = "주를 경외하게 하는 주의 말씀을 주의 종에게 세우소서 [시편 119:38절]"

MONTH_START = date(2026, 1, 1)
MONTH_END = date(2026, 1, 31)

DB_PATH = "qti_checklist.db"


# =========================
# 유틸
# =========================
def get_query_param(name: str):
    """Streamlit 버전 차이를 고려한 query param getter"""
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
    """Streamlit 버전 차이를 고려한 query param setter"""
    try:
        st.query_params.update(kwargs)
    except Exception:
        st.experimental_set_query_params(**kwargs)


def daterange(d1: date, d2: date):
    cur = d1
    while cur <= d2:
        yield cur
        cur += timedelta(days=1)


def db_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS qti_records (
            uid TEXT NOT NULL,
            day TEXT NOT NULL,          -- YYYY-MM-DD
            start_time TEXT,            -- HH:MM
            end_time TEXT,              -- HH:MM
            completed INTEGER NOT NULL DEFAULT 0,
            signature TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (uid, day)
        )
        """
    )
    conn.commit()
    return conn


def load_month(conn, uid: str):
    cur = conn.cursor()
    cur.execute(
        """
        SELECT day, start_time, end_time, completed, signature
        FROM qti_records
        WHERE uid = ? AND day BETWEEN ? AND ?
        ORDER BY day ASC
        """,
        (uid, MONTH_START.isoformat(), MONTH_END.isoformat()),
    )
    rows = cur.fetchall()

    # dict로 변환
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

    # 템플릿 생성 (없는 날짜는 생성)
    data = []
    for d in daterange(MONTH_START, MONTH_END):
        ds = d.isoformat()
        if ds in existing:
            data.append(existing[ds])
        else:
            # 1월 1일 예시는 요청대로 기본값 제공
            if d == date(2026, 1, 1):
                data.append(
                    {
                        "날짜": ds,
                        "QT 시작": "10:30",
                        "QT 종료": "12:00",
                        "완료": False,
                        "확인(사인)": "",
                    }
                )
            else:
                data.append(
                    {
                        "날짜": ds,
                        "QT 시작": "",
                        "QT 종료": "",
                        "완료": False,
                        "확인(사인)": "",
                    }
                )

    df = pd.DataFrame(data)
    return df


def upsert_month(conn, uid: str, df: pd.DataFrame):
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.cursor()
    for _, row in df.iterrows():
        day = str(row["날짜"])
        start_time = str(row["QT 시작"]).strip()
        end_time = str(row["QT 종료"]).strip()
        completed = 1 if bool(row["완료"]) else 0
        signature = str(row["확인(사인)"]).strip()

        # 빈 문자열은 NULL로 저장
        start_time = start_time if start_time else None
        end_time = end_time if end_time else None
        signature = signature if signature else None

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
    conn.commit()


def delete_user_month(conn, uid: str):
    cur = conn.cursor()
    cur.execute(
        """
        DELETE FROM qti_records
        WHERE uid = ? AND day BETWEEN ? AND ?
        """,
        (uid, MONTH_START.isoformat(), MONTH_END.isoformat()),
    )
    conn.commit()


def validate_time_str(s: str):
    """HH:MM 형태(24시간) 간단 검증. 빈 값은 OK."""
    s = (s or "").strip()
    if not s:
        return True
    try:
        datetime.strptime(s, "%H:%M")
        return True
    except ValueError:
        return False


# =========================
# UI
# =========================
st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)

uid = get_query_param("uid")

# uid가 없으면: 개인 링크 생성 유도
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
    st.caption("관리자(교회) 입장: 기본 링크만 공유하면, 각 성도는 버튼 1번으로 본인 링크를 만들고 그대로 사용합니다.")
    st.stop()

# uid가 있으면 본문
conn = db_conn()

# 상단 안내
with st.expander("📌 사용 안내 (중요)", expanded=True):
    st.write(
        "- 이 페이지는 **주소(URL)에 포함된 uid**로 본인 기록을 구분합니다.\n"
        "- 따라서 **본인 링크를 다른 사람에게 공유하면 기록이 노출/섞일 수 있어요.**\n"
        "- 가장 좋은 사용법: **개인 링크를 즐겨찾기/홈화면 추가** 해두기"
    )
    st.code(f"현재 내 uid: {uid}", language="text")

df = load_month(conn, uid)

# 편집 전 간단 검증 안내
st.subheader("📅 2026년 1월 스케줄표 (1월 1일 ~ 31일)")

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

# 시간 형식 검증
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
    if st.button("💾 저장", use_container_width=True, disabled=not can_save):
        upsert_month(conn, uid, edited)
        st.success("저장 완료! 다음에 다시 접속해도 기록이 유지됩니다.")

with col2:
    csv_bytes = edited.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "⬇️ CSV로 내보내기",
        data=csv_bytes,
        file_name="2026-01_qti_checklist.csv",
        mime="text/csv",
        use_container_width=True,
    )

with col3:
    with st.expander("⚠️ 내 1월 기록 초기화", expanded=False):
        st.write("이 동작은 되돌릴 수 없습니다. (이 uid의 2026년 1월 기록만 삭제)")
        if st.button("내 1월 기록 전부 삭제", type="primary", use_container_width=True):
            delete_user_month(conn, uid)
            st.warning("삭제 완료. 페이지를 새로고침하면 빈 표로 다시 시작합니다.")
            st.rerun()

st.markdown("---")

# 하단 박스(프로젝트 문구)
st.markdown(
    f"""
    <div style="border:1px solid #ddd; border-radius:12px; padding:14px; background:#fafafa; white-space:pre-line;">
    {PROJECT_BOX_TEXT}
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown("")

# 말씀 구절
st.markdown(
    f"""
    <div style="text-align:center; padding:10px; font-weight:600;">
    {VERSE_TEXT}
    </div>
    """,
    unsafe_allow_html=True,
)