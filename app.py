from __future__ import annotations

import re
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

# ════════════════════════════════════════════════════════
# 설정 상수
# ════════════════════════════════════════════════════════
SHEET_URL      = "https://docs.google.com/spreadsheets/d/14e_0SQaBFbyEC-16hEEqvrJfdVXob20b3MLJ2Cn60Do"
WORKSHEET_NAME = "연금현황"
DATA_TTL       = "5m"
REQUIRED_ITEMS = ["공적연금", "IRP", "ISA", "목표생활비"]

# ── 세금 상수 ─────────────────────────────────────────
# 연금소득세: 연 1,200만원 이하 → 5.5% / 초과분 → 16.5%
PENSION_TAX_LOW        = 0.055   # 분리과세 저율 (지방세 포함)
PENSION_TAX_HIGH       = 0.165   # 종합과세 고율 (지방세 포함)
PENSION_TAX_THRESHOLD  = 12_000_000 / 12   # 월 100만원 기준
# 건강보험료: 연금소득의 약 7.09% (지역가입자 기준, 장기요양 포함)
HEALTH_INS_RATE        = 0.0709
# ISA 비과세 한도: 연 200만원 → 월 환산
ISA_TAX_FREE_MONTHLY   = 2_000_000 / 12
# IRP·퇴직연금 분리과세: 5.5%
IRP_TAX_RATE           = 0.055


# ════════════════════════════════════════════════════════
# 헬퍼 함수
# ════════════════════════════════════════════════════════

def safe_get(df: pd.DataFrame, item: str, default: float = 0.0) -> float:
    rows = df.loc[df["항목"] == item, "금액"]
    if rows.empty:
        return default
    try:
        return float(rows.values[0])
    except (ValueError, TypeError):
        return default


def validate_df(df: pd.DataFrame) -> list[str]:
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
    missing = [i for i in REQUIRED_ITEMS if i not in df["항목"].values]
    if missing:
        errors.append(f"다음 항목이 없습니다: {', '.join(missing)}")
    return errors


def calc_after_tax(
    public_pension: float,
    irp_income: float,
    isa_income: float,
) -> dict:
    """
    세목별 공제 후 실수령액 계산.

    공적연금 (공무원연금)
    ─ 연금소득세: 월 100만원 이하 5.5% / 초과분 16.5%
    ─ 건강보험료: 연금소득 × 7.09% (공무원연금 수령자 지역가입자 기준)

    IRP / 퇴직연금
    ─ 연금소득세 분리과세 5.5% 적용

    ISA (KODEX 월배당)
    ─ 연 200만원 비과세 한도 내: 세금 0
    ─ 초과분: 9.9% (분리과세)
    """
    # ── 공적연금 ──
    if public_pension <= PENSION_TAX_THRESHOLD:
        pub_tax = public_pension * PENSION_TAX_LOW
    else:
        pub_tax = (PENSION_TAX_THRESHOLD * PENSION_TAX_LOW
                   + (public_pension - PENSION_TAX_THRESHOLD) * PENSION_TAX_HIGH)
    pub_health  = public_pension * HEALTH_INS_RATE
    pub_net     = public_pension - pub_tax - pub_health

    # ── IRP ──
    irp_tax = irp_income * IRP_TAX_RATE
    irp_net = irp_income - irp_tax

    # ── ISA ──
    isa_taxable = max(0, isa_income - ISA_TAX_FREE_MONTHLY)
    isa_tax     = isa_taxable * 0.099   # 9.9% 분리과세
    isa_net     = isa_income - isa_tax

    total_gross = public_pension + irp_income + isa_income
    total_tax   = pub_tax + pub_health + irp_tax + isa_tax
    total_net   = pub_net + irp_net + isa_net

    return {
        "공적연금_세전":   public_pension,
        "공적연금_소득세": pub_tax,
        "공적연금_건보료": pub_health,
        "공적연금_세후":   pub_net,
        "IRP_세전":        irp_income,
        "IRP_세금":        irp_tax,
        "IRP_세후":        irp_net,
        "ISA_세전":        isa_income,
        "ISA_세금":        isa_tax,
        "ISA_세후":        isa_net,
        "총_세전":         total_gross,
        "총_공제액":       total_tax,
        "총_세후":         total_net,
        "실효세율":        (total_tax / total_gross * 100) if total_gross > 0 else 0,
    }


# ════════════════════════════════════════════════════════
# 1. 페이지 설정
# ════════════════════════════════════════════════════════
st.set_page_config(page_title="연금 현금흐름 관제탑", layout="wide")

st.markdown("""
<style>
[data-testid="stMetricValue"] { font-size:1.5rem !important; font-weight:700 !important; }
.tax-row { display:flex; justify-content:space-between; padding:6px 0;
           border-bottom:1px solid rgba(255,255,255,0.06); font-size:0.88rem; }
.tax-label { color:rgba(255,255,255,0.6); }
.tax-val   { font-weight:600; }
.tax-neg   { color:#FF4B4B; }
.tax-pos   { color:#7dffb0; }
</style>
""", unsafe_allow_html=True)


# ════════════════════════════════════════════════════════
# 2. 구글 시트 로드
# ════════════════════════════════════════════════════════
@st.cache_data(ttl=DATA_TTL)
def load_sheet(url: str, gid: str = "919720494") -> pd.DataFrame:
    match = re.search(r"/d/([a-zA-Z0-9_-]+)", url)
    if not match:
        raise ValueError("올바른 구글 시트 URL이 아닙니다.")
    sheet_id = match.group(1)
    csv_url  = (
        f"https://docs.google.com/spreadsheets/d/{sheet_id}"
        f"/export?format=csv&gid={gid}"
    )
    return pd.read_csv(csv_url)

with st.status("📋 구글 시트 연결 중...", expanded=False) as status:
    try:
        df = load_sheet(SHEET_URL)
        status.update(label="✅ 데이터 로드 완료", state="complete")
    except Exception as e:
        status.update(label="❌ 연결 실패", state="error")
        st.error(f"구글 시트 읽기 오류: {e}")
        st.stop()

errors = validate_df(df)
if errors:
    st.error("📋 시트 데이터 오류")
    for err in errors:
        st.warning(f"• {err}")
    with st.expander("현재 시트 미리보기"):
        st.dataframe(df)
    st.stop()


# ════════════════════════════════════════════════════════
# 3. 값 추출
# ════════════════════════════════════════════════════════
public_pension   = safe_get(df, "공적연금")
irp_total        = safe_get(df, "IRP")
isa_total        = safe_get(df, "ISA")
target_monthly   = safe_get(df, "목표생활비", default=1.0)
default_palantir = safe_get(df, "IRP기본분배율", default=1.2)
default_kodex    = safe_get(df, "ISA기본분배율", default=0.8)


# ════════════════════════════════════════════════════════
# 4. 사이드바
# ════════════════════════════════════════════════════════
with st.sidebar:
    st.header("📈 수익률 시뮬레이션")
    palantir_rate = st.slider(
        "IRP(팔란티어) 월 분배율 (%)",
        min_value=0.5, max_value=2.0,
        value=float(default_palantir), step=0.1,
    ) / 100
    kodex_rate = st.slider(
        "ISA(KODEX) 월 분배율 (%)",
        min_value=0.3, max_value=1.5,
        value=float(default_kodex), step=0.1,
    ) / 100

    st.divider()
    st.subheader("⚙️ 세금 옵션")
    show_tax = st.toggle("세후 실수령액 표시", value=True)
    use_health_ins = st.toggle("건강보험료 포함", value=True)

    st.divider()
    if st.button("🔄 데이터 갱신", use_container_width=True):
        load_sheet.clear()
        st.rerun()
    st.caption(f"워크시트: {WORKSHEET_NAME} · 캐시: {DATA_TTL}")


# ════════════════════════════════════════════════════════
# 5. 계산
# ════════════════════════════════════════════════════════
irp_income   = irp_total * palantir_rate
isa_income   = isa_total * kodex_rate
total_income = public_pension + irp_income + isa_income

# 세후 계산
tax_result = calc_after_tax(public_pension, irp_income, isa_income)
if not use_health_ins:
    # 건보료 제외 옵션
    tax_result["공적연금_세후"]  += tax_result["공적연금_건보료"]
    tax_result["총_세후"]        += tax_result["공적연금_건보료"]
    tax_result["총_공제액"]      -= tax_result["공적연금_건보료"]
    tax_result["실효세율"]        = (
        tax_result["총_공제액"] / tax_result["총_세전"] * 100
        if tax_result["총_세전"] > 0 else 0
    )

display_income = tax_result["총_세후"] if show_tax else total_income
achievement    = (display_income / target_monthly) * 100 if target_monthly > 0 else 0


# ════════════════════════════════════════════════════════
# 6. 메인 화면
# ════════════════════════════════════════════════════════
st.title("🚀 연금자산 현금흐름 관제탑")

tax_label = "세후 " if show_tax else "세전 "
st.markdown(
    f"### 현재 예상 월 수입 ({tax_label}): "
    f"**{display_income:,.0f}원**"
    + (f"  <span style='font-size:0.9rem; color:rgba(255,255,255,0.4);'>"
       f"(세전 {total_income:,.0f}원)</span>" if show_tax else ""),
    unsafe_allow_html=True,
)

# ── 상단 메트릭 4개 ──
m1, m2, m3, m4 = st.columns(4)
m1.metric("목표 달성률",
          f"{achievement:.1f}%",
          delta=f"{achievement-100:+.1f}%p")
m2.metric(f"총 {'세후' if show_tax else '세전'} 월 수입",
          f"{display_income:,.0f}원")
m3.metric("총 공제액",
          f"{tax_result['총_공제액']:,.0f}원",
          delta=f"-{tax_result['실효세율']:.1f}%",
          delta_color="inverse")
m4.metric("목표 생활비",
          f"{target_monthly:,.0f}원",
          delta=f"{display_income - target_monthly:+,.0f}원")

st.info("💡 8월 알프스 여정 대비 현금 흐름을 점검 중입니다.")
st.divider()

# ── 세후 상세 내역 + 파이차트 ──
col1, col2 = st.columns([1, 1])

with col1:
    st.markdown("#### 💸 수입원별 세후 실수령액")

    # 공적연금 카드
    with st.container(border=True):
        st.markdown("**🏛️ 공적연금 (공무원연금)**")
        ca, cb, cc = st.columns(3)
        ca.metric("세전", f"{tax_result['공적연금_세전']:,.0f}원")
        cb.metric("소득세", f"-{tax_result['공적연금_소득세']:,.0f}원",
                  delta_color="inverse",
                  delta=f"{tax_result['공적연금_소득세']/max(tax_result['공적연금_세전'],1)*100:.1f}%")
        cc.metric("건보료" if use_health_ins else "건보료(미적용)",
                  f"-{tax_result['공적연금_건보료']:,.0f}원" if use_health_ins else "0원",
                  delta_color="inverse")
        st.markdown(
            f"<div style='text-align:right; font-size:1.1rem; font-weight:700; color:#7dffb0;'>"
            f"실수령 {tax_result['공적연금_세후']:,.0f}원</div>",
            unsafe_allow_html=True
        )

    # IRP 카드
    with st.container(border=True):
        st.markdown("**💼 IRP (팔란티어 커버드콜)**")
        da, db = st.columns(2)
        da.metric("세전", f"{tax_result['IRP_세전']:,.0f}원")
        db.metric("연금소득세 5.5%", f"-{tax_result['IRP_세금']:,.0f}원",
                  delta_color="inverse")
        st.markdown(
            f"<div style='text-align:right; font-size:1.1rem; font-weight:700; color:#7dffb0;'>"
            f"실수령 {tax_result['IRP_세후']:,.0f}원</div>",
            unsafe_allow_html=True
        )

    # ISA 카드
    with st.container(border=True):
        st.markdown("**📦 ISA (KODEX 위클리커버드콜)**")
        ea, eb = st.columns(2)
        ea.metric("세전", f"{tax_result['ISA_세전']:,.0f}원")
        eb.metric("분리과세 9.9%", f"-{tax_result['ISA_세금']:,.0f}원",
                  delta_color="inverse",
                  delta=f"비과세 {ISA_TAX_FREE_MONTHLY:,.0f}원/월 적용")
        st.markdown(
            f"<div style='text-align:right; font-size:1.1rem; font-weight:700; color:#7dffb0;'>"
            f"실수령 {tax_result['ISA_세후']:,.0f}원</div>",
            unsafe_allow_html=True
        )

    # 합계
    with st.container(border=True):
        st.markdown(
            f"<div class='tax-row'>"
            f"<span class='tax-label'>총 세전</span>"
            f"<span class='tax-val'>{tax_result['총_세전']:,.0f}원</span></div>"
            f"<div class='tax-row'>"
            f"<span class='tax-label'>총 공제액</span>"
            f"<span class='tax-val tax-neg'>-{tax_result['총_공제액']:,.0f}원</span></div>"
            f"<div class='tax-row' style='border:none; margin-top:6px;'>"
            f"<span style='font-weight:700;'>총 세후 실수령</span>"
            f"<span class='tax-val tax-pos' style='font-size:1.1rem;'>"
            f"{tax_result['총_세후']:,.0f}원</span></div>"
            f"<div class='tax-row' style='border:none;'>"
            f"<span class='tax-label'>실효 세율</span>"
            f"<span class='tax-val'>{tax_result['실효세율']:.1f}%</span></div>",
            unsafe_allow_html=True
        )

with col2:
    st.markdown("#### 📊 세전 vs 세후 비교")

    # 세전·세후 비교 막대차트
    bar_df = pd.DataFrame({
        "구분":   ["공적연금", "IRP", "ISA"],
        "세전":   [tax_result["공적연금_세전"], tax_result["IRP_세전"], tax_result["ISA_세전"]],
        "세후":   [tax_result["공적연금_세후"], tax_result["IRP_세후"], tax_result["ISA_세후"]],
    })
    fig_bar = go.Figure()
    fig_bar.add_trace(go.Bar(
        name="세전", x=bar_df["구분"], y=bar_df["세전"],
        marker_color="rgba(135,206,235,0.4)",
        text=[f"{v/10000:.0f}만" for v in bar_df["세전"]],
        textposition="outside",
    ))
    fig_bar.add_trace(go.Bar(
        name="세후", x=bar_df["구분"], y=bar_df["세후"],
        marker_color=["#87CEEB", "#FFD700", "#FF4B4B"],
        text=[f"{v/10000:.0f}만" for v in bar_df["세후"]],
        textposition="outside",
    ))
    fig_bar.update_layout(
        barmode="group", height=280,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(255,255,255,0.02)",
        font_color="white",
        legend=dict(orientation="h", y=-0.2),
        margin=dict(t=20, b=60, l=10, r=10),
        yaxis=dict(tickformat=","),
    )
    st.plotly_chart(fig_bar, use_container_width=True)

    # 월 수입 구성 파이차트 (세후 기준)
    pie_df = pd.DataFrame({
        "구분": ["공적연금", "IRP 수익", "ISA 수익"],
        "금액": [
            tax_result["공적연금_세후"],
            tax_result["IRP_세후"],
            tax_result["ISA_세후"],
        ],
    })
    fig_pie = px.pie(
        pie_df, values="금액", names="구분",
        hole=0.4,
        title="세후 월 수입 구성",
        color_discrete_sequence=["#87CEEB", "#FFD700", "#FF4B4B"],
    )
    fig_pie.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        font_color="white", height=280,
        margin=dict(t=40, b=0, l=0, r=0),
    )
    st.plotly_chart(fig_pie, use_container_width=True)

    # 세금 구성 요약
    with st.container(border=True):
        st.markdown("**🔍 세금 구성 요약**")
        tax_items = [
            ("공적연금 소득세 (5.5%)",  tax_result["공적연금_소득세"]),
            ("공적연금 건보료 (7.09%)", tax_result["공적연금_건보료"] if use_health_ins else 0),
            ("IRP 연금소득세 (5.5%)",   tax_result["IRP_세금"]),
            ("ISA 분리과세 (9.9%)",     tax_result["ISA_세금"]),
        ]
        for label, val in tax_items:
            if val > 0:
                st.markdown(
                    f"<div class='tax-row'>"
                    f"<span class='tax-label'>{label}</span>"
                    f"<span class='tax-val tax-neg'>-{val:,.0f}원</span>"
                    f"</div>",
                    unsafe_allow_html=True
                )


# ════════════════════════════════════════════════════════
# 수령 타임라인
# ════════════════════════════════════════════════════════
st.divider()
st.markdown("## 📅 연도별 수령 타임라인")
st.caption("은퇴부터 기대수명까지 수입원이 어떻게 바뀌는지 한눈에 확인합니다.")

# ── 타임라인 파라미터 (사이드바) ─────────────────────
with st.sidebar:
    st.divider()
    st.subheader("📅 타임라인 설정")
    birth_year     = st.number_input("출생 연도", value=1971, min_value=1950, max_value=1985, step=1)
    retire_age     = st.number_input("은퇴 나이",  value=55,  min_value=50,   max_value=75,   step=1)
    pension_age    = st.number_input("공무원연금 개시 나이", value=55, min_value=50, max_value=70, step=1)
    life_exp       = st.number_input("기대 수명",  value=90,  min_value=70,   max_value=100,  step=1)
    inflation_rate = st.slider("물가상승률 (%)", min_value=0.0, max_value=5.0, value=2.0, step=0.1) / 100

# ── 기본 연도 계산 ────────────────────────────────────
current_year   = 2026
retire_year    = birth_year + retire_age
pension_year   = birth_year + pension_age
end_year       = birth_year + life_exp
years          = list(range(retire_year, end_year + 1))

# ── 연도별 현금흐름 시뮬레이션 ───────────────────────
def simulate_timeline(
    years: list[int],
    retire_year: int,
    pension_year: int,
    birth_year: int,
    irp_total: float,
    isa_total: float,
    irp_rate: float,
    isa_rate: float,
    public_pension_monthly: float,
    target_monthly: float,
    inflation_rate: float,
    use_after_tax: bool,
) -> pd.DataFrame:
    rows = []
    irp_balance = irp_total
    isa_balance = isa_total

    for yr in years:
        age = yr - birth_year
        elapsed = yr - retire_year   # 은퇴 후 경과 연수

        # 물가 반영 목표 생활비 (실질)
        target_real = target_monthly * ((1 + inflation_rate) ** elapsed)

        # 공적연금: 개시 연도부터 수령
        pub = public_pension_monthly if yr >= pension_year else 0.0

        # IRP: 은퇴 즉시 인출 (잔액 있을 때만)
        irp_m = irp_balance * irp_rate if irp_balance > 0 else 0.0
        irp_balance = max(0, irp_balance - irp_m * 12)  # 연간 인출 후 잔액

        # ISA: 은퇴 즉시 인출 (잔액 있을 때만)
        isa_m = isa_balance * isa_rate if isa_balance > 0 else 0.0
        isa_balance = max(0, isa_balance - isa_m * 12)

        gross_m = pub + irp_m + isa_m

        # 세후 적용
        if use_after_tax and gross_m > 0:
            tr = calc_after_tax(pub, irp_m, isa_m)
            net_m = tr["총_세후"]
        else:
            net_m = gross_m

        gap = net_m - target_real   # 양수=여유, 음수=부족

        rows.append({
            "연도": yr,
            "나이": age,
            "공적연금": pub,
            "IRP수익":  irp_m,
            "ISA수익":  isa_m,
            "세전합계": gross_m,
            "세후합계": net_m if use_after_tax else gross_m,
            "목표생활비(실질)": target_real,
            "잉여/부족": gap,
            "IRP잔액": irp_balance,
            "ISA잔액": isa_balance,
            "단계": (
                "공무원연금 + IRP·ISA 병행" if irp_balance > 0 or isa_balance > 0
                else "공무원연금 단독"
            ),
        })
    return pd.DataFrame(rows)

tl_df = simulate_timeline(
    years        = years,
    retire_year  = retire_year,
    pension_year = pension_year,
    birth_year   = birth_year,
    irp_total    = irp_total,
    isa_total    = isa_total,
    irp_rate     = palantir_rate,
    isa_rate     = kodex_rate,
    public_pension_monthly = public_pension,
    target_monthly = target_monthly,
    inflation_rate = inflation_rate,
    use_after_tax  = show_tax,
)

# ── 핵심 이벤트 요약 카드 ─────────────────────────────
ev1, ev2, ev3, ev4 = st.columns(4)

irp_exhaust = tl_df[tl_df["IRP잔액"] <= 0]["연도"].min() if (tl_df["IRP잔액"] <= 0).any() else None
isa_exhaust = tl_df[tl_df["ISA잔액"] <= 0]["연도"].min() if (tl_df["ISA잔액"] <= 0).any() else None
shortage_yrs = tl_df[tl_df["잉여/부족"] < 0]

ev1.metric("🏖️ 은퇴 연도", f"{retire_year}년", delta=f"{retire_age}세")
ev2.metric("🏛️ 공무원연금 개시", f"{pension_year}년",
           delta=f"은퇴와 동시" if pension_year == retire_year else f"{pension_year - retire_year}년 후")
ev3.metric("💼 IRP 고갈",
           f"{irp_exhaust}년" if irp_exhaust else "고갈 없음",
           delta=f"{irp_exhaust - retire_year}년 후" if irp_exhaust else "✅ 충분",
           delta_color="inverse" if irp_exhaust else "normal")
ev4.metric("📦 ISA 고갈",
           f"{isa_exhaust}년" if isa_exhaust else "고갈 없음",
           delta=f"{isa_exhaust - retire_year}년 후" if isa_exhaust else "✅ 충분",
           delta_color="inverse" if isa_exhaust else "normal")

# ── 누적 현금흐름 영역 차트 ──────────────────────────
st.markdown("#### 💰 연도별 월 수입 구성 추이")

fig_tl = go.Figure()

# 단계 구분 배경
phase_colors = {
    "공무원연금 + IRP·ISA 병행": "rgba(255,215,0,0.05)",
    "공무원연금 단독":            "rgba(135,206,235,0.05)",
}
prev_phase = None
phase_start = tl_df["연도"].iloc[0]
for _, row in tl_df.iterrows():
    if row["단계"] != prev_phase:
        if prev_phase is not None:
            fig_tl.add_vrect(
                x0=phase_start, x1=row["연도"],
                fillcolor=phase_colors.get(prev_phase, "rgba(0,0,0,0)"),
                layer="below", line_width=0,
            )
        phase_start = row["연도"]
        prev_phase  = row["단계"]
# 마지막 구간
fig_tl.add_vrect(
    x0=phase_start, x1=tl_df["연도"].iloc[-1],
    fillcolor=phase_colors.get(prev_phase, "rgba(0,0,0,0)"),
    layer="below", line_width=0,
)

# 수입 구성 누적 막대
income_col = "세후합계" if show_tax else "세전합계"
fig_tl.add_trace(go.Bar(
    x=tl_df["연도"], y=tl_df["공적연금"] / 10000,
    name="공적연금", marker_color="#87CEEB",
))
fig_tl.add_trace(go.Bar(
    x=tl_df["연도"], y=tl_df["IRP수익"] / 10000,
    name="IRP 수익", marker_color="#FFD700",
))
fig_tl.add_trace(go.Bar(
    x=tl_df["연도"], y=tl_df["ISA수익"] / 10000,
    name="ISA 수익", marker_color="#FF4B4B",
))

# 목표 생활비 라인
fig_tl.add_trace(go.Scatter(
    x=tl_df["연도"], y=tl_df["목표생활비(실질)"] / 10000,
    name=f"목표생활비 (물가{inflation_rate*100:.1f}% 반영)",
    line=dict(color="white", width=2, dash="dot"),
    mode="lines",
))

# 공무원연금 개시 수직선
fig_tl.add_vline(
    x=pension_year, line_dash="dash",
    line_color="rgba(135,206,235,0.6)", line_width=1.5,
    annotation_text=f"공무원연금 개시 ({pension_year}년, {pension_age}세)",
    annotation_position="top right",
    annotation_font_color="#87CEEB",
)

fig_tl.update_layout(
    barmode="stack",
    height=400,
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(255,255,255,0.02)",
    font_color="white",
    legend=dict(orientation="h", yanchor="bottom", y=-0.25, xanchor="center", x=0.5),
    margin=dict(t=20, b=80, l=10, r=10),
    yaxis=dict(title="월 수입 (만원)", tickformat=","),
    xaxis=dict(title="연도", dtick=2),
    hovermode="x unified",
)
st.plotly_chart(fig_tl, use_container_width=True)

# 단계 범례 설명
lc1, lc2, lc3 = st.columns(3)
lc2.markdown(
    "<div style='background:rgba(255,215,0,0.1); padding:8px 12px; border-radius:8px;"
    " border-left:3px solid #FFD700; font-size:0.85rem;'>"
    "🟡 <b>병행 구간</b><br>공무원연금 + IRP·ISA 동시 수령</div>",
    unsafe_allow_html=True
)
lc3.markdown(
    "<div style='background:rgba(135,206,235,0.1); padding:8px 12px; border-radius:8px;"
    " border-left:3px solid #87CEEB; font-size:0.85rem;'>"
    "🔵 <b>안정 구간</b><br>공무원연금 단독 수령</div>",
    unsafe_allow_html=True
)

# ── 잉여/부족 차트 ────────────────────────────────────
st.markdown("#### 📊 연도별 목표 대비 잉여 / 부족액")

colors_gap = [
    "#7dffb0" if v >= 0 else "#FF4B4B"
    for v in tl_df["잉여/부족"]
]
fig_gap = go.Figure(go.Bar(
    x=tl_df["연도"],
    y=tl_df["잉여/부족"] / 10000,
    marker_color=colors_gap,
    text=[f"{v/10000:+.0f}만" for v in tl_df["잉여/부족"]],
    textposition="outside",
    hovertemplate="%{x}년: %{y:.1f}만원<extra></extra>",
))
fig_gap.add_hline(y=0, line_color="rgba(255,255,255,0.3)", line_width=1)
fig_gap.update_layout(
    height=300,
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(255,255,255,0.02)",
    font_color="white",
    margin=dict(t=10, b=40, l=10, r=10),
    yaxis=dict(title="잉여/부족 (만원)", tickformat=","),
    xaxis=dict(dtick=2),
)
st.plotly_chart(fig_gap, use_container_width=True)

if not shortage_yrs.empty:
    first_shortage = shortage_yrs.iloc[0]
    st.warning(
        f"⚠️ **{int(first_shortage['연도'])}년 ({int(first_shortage['나이'])}세)**부터 "
        f"목표 생활비 대비 월 **{abs(first_shortage['잉여/부족']/10000):.1f}만원** 부족 예상 — "
        f"추가 적립 또는 지출 조정을 검토하세요."
    )
else:
    st.success("✅ 기대수명까지 목표 생활비를 충분히 충당할 수 있습니다.")

# ── 연도별 상세 테이블 ────────────────────────────────
with st.expander("📋 연도별 상세 데이터 보기"):
    display_tl = tl_df[[
        "연도", "나이", "단계",
        "공적연금", "IRP수익", "ISA수익",
        "세후합계" if show_tax else "세전합계",
        "목표생활비(실질)", "잉여/부족",
        "IRP잔액", "ISA잔액",
    ]].copy()
    # 만원 단위 변환
    for c in ["공적연금","IRP수익","ISA수익","세후합계","세전합계",
              "목표생활비(실질)","잉여/부족","IRP잔액","ISA잔액"]:
        if c in display_tl.columns:
            display_tl[c] = display_tl[c].apply(lambda v: round(v/10000, 1))
    display_tl = display_tl.rename(columns={
        "세후합계": "월수입(세후,만원)" if show_tax else "월수입(세전,만원)",
        "세전합계": "월수입(세전,만원)",
        "목표생활비(실질)": "목표(만원)",
        "잉여/부족": "잉여/부족(만원)",
        "IRP잔액":  "IRP잔액(만원)",
        "ISA잔액":  "ISA잔액(만원)",
        "공적연금": "공적연금(만원)",
        "IRP수익":  "IRP수익(만원)",
        "ISA수익":  "ISA수익(만원)",
    })
    st.dataframe(
        display_tl,
        hide_index=True,
        use_container_width=True,
        column_config={
            "잉여/부족(만원)": st.column_config.NumberColumn(
                "잉여/부족(만원)", format="%+.1f"
            ),
        },
    )
