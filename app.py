from __future__ import annotations

import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import plotly.express as px

# ── 설정 상수 ────────────────────────────────────────────
WORKSHEET_NAME   = "연금현황"      # 구글 시트 워크시트명 (실제 시트명으로 변경)
DATA_TTL         = "5m"           # 캐시 갱신 주기
REQUIRED_ITEMS   = ["공적연금", "IRP", "ISA", "목표생활비"]  # 필수 항목

# ── 헬퍼 함수 ────────────────────────────────────────────

def safe_get(df: pd.DataFrame, item: str, default: float = 0.0) -> float:
    """
    df에서 항목명으로 금액을 안전하게 추출.
    행이 없거나 변환 실패 시 default 반환.
    """
    rows = df.loc[df["항목"] == item, "금액"]
    if rows.empty:
        return default
    try:
        return float(rows.values[0])
    except (ValueError, TypeError):
        return default


def validate_df(df: pd.DataFrame) -> list[str]:
    """
    DataFrame 유효성 검사.
    문제가 있으면 오류 메시지 리스트 반환, 없으면 빈 리스트.
    """
    errors = []
    if df.empty:
        errors.append("시트가 비어 있습니다.")
        return errors
    if "항목" not in df.columns:
        errors.append("'항목' 컬럼이 없습니다.")
    if "금액" not in df.columns:
        errors.append("'금액' 컬럼이 없습니다.")
    if errors:
        return errors
    missing = [item for item in REQUIRED_ITEMS if item not in df["항목"].values]
    if missing:
        errors.append(f"다음 항목이 시트에 없습니다: {', '.join(missing)}")
    return errors


# ── 1. 페이지 설정 ───────────────────────────────────────
st.set_page_config(page_title="연금 현금흐름 관제탑", layout="wide")

# ── 2. 구글 시트 연결 ────────────────────────────────────
conn = st.connection("gsheets", type=GSheetsConnection)

with st.status("📋 구글 시트 연결 중...", expanded=False) as status:
    try:
        # ✅ 워크시트명 명시 + TTL 캐시 적용
        df = conn.read(worksheet=WORKSHEET_NAME, ttl=DATA_TTL)
        status.update(label="✅ 데이터 로드 완료", state="complete")
    except Exception as e:
        status.update(label="❌ 연결 실패", state="error")
        st.error(f"구글 시트 연결 오류: {e}")
        st.info(
            "체크리스트:\n"
            "1. `secrets.toml`의 `spreadsheet` URL이 올바른지 확인\n"
            "2. 시트 공유 설정이 '링크가 있는 모든 사용자 → 뷰어' 이상인지 확인\n"
            f"3. 워크시트 이름이 **{WORKSHEET_NAME}** 인지 확인 (대소문자·공백 주의)"
        )
        st.stop()

# ── 3. 데이터 유효성 검사 ────────────────────────────────
errors = validate_df(df)
if errors:
    st.error("📋 시트 데이터 오류")
    for err in errors:
        st.warning(f"• {err}")
    with st.expander("현재 시트 미리보기"):
        st.dataframe(df)
    st.info(
        f"구글 시트에 아래 항목이 '항목' 컬럼에 정확히 있어야 합니다:\n"
        + "\n".join(f"• {item}" for item in REQUIRED_ITEMS)
    )
    st.stop()

# ── 4. 값 추출 (safe_get으로 IndexError 방지) ────────────
public_pension  = safe_get(df, "공적연금")
irp_total       = safe_get(df, "IRP")
isa_total       = safe_get(df, "ISA")
target_monthly  = safe_get(df, "목표생활비", default=1.0)   # 0 나누기 방지

# 슬라이더 기본값도 시트에서 읽기 (없으면 코드 기본값 사용)
default_palantir = safe_get(df, "IRP기본분배율", default=1.2)
default_kodex    = safe_get(df, "ISA기본분배율", default=0.8)

# ── 5. 사이드바 — 수익률 시뮬레이션 ─────────────────────
with st.sidebar:
    st.header("📈 수익률 시뮬레이션")
    palantir_rate = st.slider(
        "IRP(팔란티어) 월 분배율 (%)",
        min_value=0.5, max_value=2.0,
        value=float(default_palantir),   # ✅ 시트값 우선
        step=0.1,
    ) / 100
    kodex_rate = st.slider(
        "ISA(KODEX) 월 분배율 (%)",
        min_value=0.3, max_value=1.5,
        value=float(default_kodex),      # ✅ 시트값 우선
        step=0.1,
    ) / 100

    st.divider()
    if st.button("🔄 데이터 갱신", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption(f"워크시트: {WORKSHEET_NAME} · 캐시: {DATA_TTL}")

# ── 6. 현금흐름 계산 ─────────────────────────────────────
irp_income   = irp_total  * palantir_rate
isa_income   = isa_total  * kodex_rate
total_income = public_pension + irp_income + isa_income
achievement  = (total_income / target_monthly) * 100 if target_monthly > 0 else 0

# ── 7. 메인 화면 출력 ────────────────────────────────────
st.title("🚀 연금자산 현금흐름 관제탑")
st.markdown(f"### 현재 예상 월 수입: **{total_income:,.0f}원**")

col1, col2 = st.columns(2)
with col1:
    delta_val = achievement - 100
    st.metric(
        "목표 달성률",
        f"{achievement:.1f}%",
        delta=f"{delta_val:+.1f}%p",
        delta_color="normal",
    )
    st.info("💡 8월 알프스 여정 대비 현금 흐름을 점검 중입니다.")

    # 수입 내역 상세
    with st.container(border=True):
        st.markdown("**월 수입 내역**")
        r1, r2 = st.columns(2)
        r1.metric("공적연금",    f"{public_pension:,.0f}원")
        r2.metric("IRP 수익",   f"{irp_income:,.0f}원")
        r3, r4 = st.columns(2)
        r3.metric("ISA 수익",   f"{isa_income:,.0f}원")
        r4.metric("목표 생활비", f"{target_monthly:,.0f}원")

with col2:
    fig_df = pd.DataFrame({
        "구분":  ["공적연금", "IRP 수익", "ISA 수익"],
        "금액":  [public_pension, irp_income, isa_income],
    })
    fig = px.pie(
        fig_df, values="금액", names="구분",
        hole=0.4, title="월 수입 구성",
        color_discrete_sequence=["#87CEEB", "#FFD700", "#FF4B4B"],
    )
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        font_color="white",
    )
    st.plotly_chart(fig, use_container_width=True)
