"""
pension_tax_monitor.py
======================
금융소득 종합과세(연 2,000만원) 및 연금소득 분리과세 한도(연 1,500만원) 관리 모듈.

커버드콜 ETF의 월별 과세표준 변동을 추적하고,
연간 누적 과세 금융소득이 임계치를 초과하지 않도록 경보·조언을 제공합니다.

사용법:
    from pension_tax_monitor import render_tax_monitor_tab
    with _main_tab_tax:
        render_tax_monitor_tab(tax_ctx)
"""
from __future__ import annotations
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
import plotly.express as px

# ── 과세 한도 상수 ─────────────────────────────────────
FINANCIAL_INCOME_LIMIT   = 20_000_000   # 금융소득 종합과세 기준 (연 2,000만원)
PENSION_INCOME_LIMIT     = 15_000_000   # 연금소득 분리과세 한도 (연 1,500만원) ← 핵심
ISA_TAX_FREE_ANNUAL      = 2_000_000    # ISA 비과세 한도 (연 200만원)
DIVIDEND_TAX_RATE        = 0.154        # 일반 배당소득세
ISA_EXCESS_TAX_RATE      = 0.099        # ISA 초과분 분리과세
IRP_PENSION_TAX_RATE     = 0.055        # IRP/연금저축 연금소득세 (55~69세)

# ── 경보 임계치 ────────────────────────────────────────
WARN_RATIO   = 0.80   # 80% 도달 시 주의
DANGER_RATIO = 0.95   # 95% 도달 시 위험


# ════════════════════════════════════════════════════════
# 헬퍼: 상태 색상·라벨
# ════════════════════════════════════════════════════════
def _status(used: float, limit: float) -> tuple[str, str, str]:
    """(색상, 이모지, 라벨) 반환"""
    r = used / limit if limit > 0 else 0
    if r >= 1.0:
        return "#FF4B4B", "🚨", "한도 초과"
    if r >= DANGER_RATIO:
        return "#FF8C00", "⚠️", "위험"
    if r >= WARN_RATIO:
        return "#FFD700", "🔶", "주의"
    return "#7dffb0", "✅", "정상"


def _gauge_fig(value: float, limit: float, title: str, unit: str = "만원") -> go.Figure:
    """미니 게이지 차트"""
    pct = min(value / limit * 100, 100) if limit > 0 else 0
    color, _, _ = _status(value, limit)
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=value / 10000,
        number=dict(suffix=unit, font=dict(size=22, color=color)),
        gauge=dict(
            axis=dict(range=[0, limit / 10000],
                      tickfont=dict(color="rgba(255,255,255,0.4)", size=9)),
            bar=dict(color=color, thickness=0.6),
            bgcolor="rgba(255,255,255,0.05)",
            bordercolor="rgba(0,0,0,0)",
            steps=[
                dict(range=[0, limit * WARN_RATIO / 10000],   color="rgba(125,255,176,0.08)"),
                dict(range=[limit * WARN_RATIO / 10000,
                            limit * DANGER_RATIO / 10000],    color="rgba(255,215,0,0.10)"),
                dict(range=[limit * DANGER_RATIO / 10000,
                            limit / 10000],                   color="rgba(255,75,75,0.12)"),
            ],
            threshold=dict(line=dict(color="#FFD700", width=2),
                           thickness=0.85, value=limit * WARN_RATIO / 10000),
        ),
        title=dict(text=title, font=dict(size=12, color="rgba(255,255,255,0.7)")),
    ))
    fig.update_layout(
        height=200, paper_bgcolor="rgba(0,0,0,0)", font_color="white",
        margin=dict(t=30, b=10, l=20, r=20),
    )
    return fig


# ════════════════════════════════════════════════════════
# 과세 계산 엔진
# ════════════════════════════════════════════════════════
def calc_taxable_income(
    dist_df: pd.DataFrame,   # 월별 분배금 시트 (구조 아래 참고)
    year: int,
) -> dict:
    """
    dist_df 컬럼:
        연월 | 계좌 | 종목명 | 분배금(원) | 과세표준(원) | 비과세(원)

    과세 계좌별 처리:
        IRP·연금저축 → 연금소득 (분리과세 1,500만원 한도)
        ISA          → 200만원 비과세, 초과분 9.9% 분리과세
        일반         → 금융소득 종합과세 대상 (2,000만원 초과 시 종합)
    """
    # ── 필수 컬럼 검증 ───────────────────────────────────
    required = ["연월", "분배금(원)"]
    if any(c not in dist_df.columns for c in required):
        return _empty_tax_result(year)

    yr_df = dist_df[dist_df["연월"].astype(str).str.startswith(str(year))].copy()
    if yr_df.empty:
        return _empty_tax_result(year)

    # 선택 컬럼 없으면 기본값으로 채움
    if "계좌" not in yr_df.columns:
        yr_df["계좌"] = "IRP"
    if "과세표준(원)" not in yr_df.columns:
        yr_df["과세표준(원)"] = yr_df["분배금(원)"]
    if "비과세(원)" not in yr_df.columns:
        yr_df["비과세(원)"] = 0

    yr_df["과세표준(원)"] = pd.to_numeric(yr_df["과세표준(원)"], errors="coerce").fillna(0)
    yr_df["분배금(원)"]   = pd.to_numeric(yr_df["분배금(원)"],   errors="coerce").fillna(0)
    yr_df["비과세(원)"]   = pd.to_numeric(yr_df["비과세(원)"],   errors="coerce").fillna(0)

    # 계좌별 집계
    irp_ps   = yr_df[yr_df["계좌"].isin(["IRP","연금저축"])]["과세표준(원)"].sum()
    isa_dist = yr_df[yr_df["계좌"] == "ISA"]["분배금(원)"].sum()
    isa_taxfree = min(isa_dist, ISA_TAX_FREE_ANNUAL)
    isa_taxable = max(0, isa_dist - ISA_TAX_FREE_ANNUAL)
    gen_taxable = yr_df[yr_df["계좌"] == "일반"]["과세표준(원)"].sum()

    # 세금 계산
    irp_ps_tax  = irp_ps   * IRP_PENSION_TAX_RATE
    isa_tax     = isa_taxable * ISA_EXCESS_TAX_RATE
    gen_tax     = gen_taxable * DIVIDEND_TAX_RATE

    # 종합과세 위험: IRP·연금저축 연금소득 연 1,500만원 초과
    pension_综합위험 = max(0, irp_ps - PENSION_INCOME_LIMIT)
    # 금융소득 종합과세: 일반계좌 연 2,000만원 초과
    financial_종합위험 = max(0, gen_taxable - FINANCIAL_INCOME_LIMIT)

    return {
        "year":                year,
        "irp_ps_annual":       irp_ps,
        "isa_dist_annual":     isa_dist,
        "isa_taxfree":         isa_taxfree,
        "isa_taxable":         isa_taxable,
        "gen_taxable_annual":  gen_taxable,
        "irp_ps_tax":          irp_ps_tax,
        "isa_tax":             isa_tax,
        "gen_tax":             gen_tax,
        "total_tax":           irp_ps_tax + isa_tax + gen_tax,
        "pension_综합위험":     pension_综합위험,
        "financial_종합위험":  financial_종합위험,
        "pension_limit_used_pct":  irp_ps / PENSION_INCOME_LIMIT * 100 if PENSION_INCOME_LIMIT > 0 else 0,
        "financial_limit_used_pct": gen_taxable / FINANCIAL_INCOME_LIMIT * 100 if FINANCIAL_INCOME_LIMIT > 0 else 0,
    }


def _empty_tax_result(year: int) -> dict:
    return {k: 0 for k in [
        "irp_ps_annual","isa_dist_annual","isa_taxfree","isa_taxable",
        "gen_taxable_annual","irp_ps_tax","isa_tax","gen_tax","total_tax",
        "pension_综합위험","financial_종합위험",
        "pension_limit_used_pct","financial_limit_used_pct",
    ]} | {"year": year}


def calc_monthly_cumulative(dist_df: pd.DataFrame, year: int) -> pd.DataFrame:
    """월별 누적 과세표준 DataFrame 반환"""
    # 필수 컬럼 검증
    if "연월" not in dist_df.columns or "분배금(원)" not in dist_df.columns:
        return pd.DataFrame()

    yr_df = dist_df[dist_df["연월"].astype(str).str.startswith(str(year))].copy()
    if yr_df.empty:
        return pd.DataFrame()

    if "계좌" not in yr_df.columns:
        yr_df["계좌"] = "IRP"
    if "과세표준(원)" not in yr_df.columns:
        yr_df["과세표준(원)"] = yr_df["분배금(원)"]

    yr_df["과세표준(원)"] = pd.to_numeric(yr_df["과세표준(원)"], errors="coerce").fillna(0)
    yr_df["분배금(원)"]   = pd.to_numeric(yr_df["분배금(원)"],   errors="coerce").fillna(0)

    monthly = yr_df.groupby(["연월","계좌"]).agg(
        과세표준=("과세표준(원)", "sum"),
        분배금=("분배금(원)", "sum"),
    ).reset_index()

    # IRP+연금저축 합산
    monthly["is_pension"] = monthly["계좌"].isin(["IRP","연금저축"])
    pension_m = monthly[monthly["is_pension"]].groupby("연월")["과세표준"].sum().reset_index()
    pension_m.columns = ["연월", "연금과세표준"]

    # 일반계좌
    gen_m = monthly[monthly["계좌"] == "일반"].groupby("연월")["과세표준"].sum().reset_index()
    gen_m.columns = ["연월", "일반과세표준"]

    # ISA
    isa_m = monthly[monthly["계좌"] == "ISA"].groupby("연월")["분배금"].sum().reset_index()
    isa_m.columns = ["연월", "ISA분배금"]

    all_months = sorted(yr_df["연월"].unique())
    base = pd.DataFrame({"연월": all_months})
    base = base.merge(pension_m, on="연월", how="left").fillna(0)
    base = base.merge(gen_m,     on="연월", how="left").fillna(0)
    base = base.merge(isa_m,     on="연월", how="left").fillna(0)

    base["연금과세_누적"]  = base["연금과세표준"].cumsum()
    base["일반과세_누적"]  = base["일반과세표준"].cumsum()
    base["ISA분배_누적"]   = base["ISA분배금"].cumsum()

    # 잔여 한도
    base["연금_잔여한도"] = (PENSION_INCOME_LIMIT - base["연금과세_누적"]).clip(lower=0)
    base["일반_잔여한도"] = (FINANCIAL_INCOME_LIMIT - base["일반과세_누적"]).clip(lower=0)

    return base


# ════════════════════════════════════════════════════════
# 남은 한도 내 월 인출 가능액 시뮬레이션
# ════════════════════════════════════════════════════════
def simulate_remaining_capacity(
    cumulative_pension: float,
    cumulative_gen: float,
    current_month: int,
) -> dict:
    """이번달 이후 월별 인출 가능 한도 시산"""
    remaining_months = 12 - current_month + 1
    if remaining_months <= 0:
        remaining_months = 1

    pension_remaining = max(0, PENSION_INCOME_LIMIT - cumulative_pension)
    gen_remaining     = max(0, FINANCIAL_INCOME_LIMIT - cumulative_gen)

    return {
        "remaining_months":      remaining_months,
        "pension_remaining":     pension_remaining,
        "gen_remaining":         gen_remaining,
        "pension_monthly_avail": pension_remaining / remaining_months,
        "gen_monthly_avail":     gen_remaining     / remaining_months,
        "pension_limit_pct":     cumulative_pension / PENSION_INCOME_LIMIT * 100,
        "gen_limit_pct":         cumulative_gen     / FINANCIAL_INCOME_LIMIT * 100,
    }


# ════════════════════════════════════════════════════════
# 메인 렌더러
# ════════════════════════════════════════════════════════
def render_tax_monitor_tab(tax_ctx: dict) -> None:
    """
    tax_ctx 키:
        dist_df         : 월별 분배금 DataFrame (구글 시트 로드값)
        year            : 조회 연도 (int)
        current_month   : 현재 월 (int)
        irp_monthly     : IRP 이번달 분배금 (float)
        isa_monthly     : ISA 이번달 분배금 (float)
        gen_monthly     : 일반 이번달 분배금 (float)
        ps_monthly      : 연금저축 이번달 분배금 (float)
        target_monthly  : 목표 생활비 (float)
    """
    dist_df        = tax_ctx.get("dist_df", pd.DataFrame())
    year           = tax_ctx.get("year", 2026)
    current_month  = tax_ctx.get("current_month", 4)
    irp_monthly    = tax_ctx.get("irp_monthly", 0.0)
    isa_monthly    = tax_ctx.get("isa_monthly", 0.0)
    gen_monthly    = tax_ctx.get("gen_monthly", 0.0)
    ps_monthly     = tax_ctx.get("ps_monthly", 0.0)
    target_monthly = tax_ctx.get("target_monthly", 6_600_000)
    sc_tax_data    = tax_ctx.get("sc_tax_data", {})
    sc_df_ctx      = tax_ctx.get("sc_df", pd.DataFrame())
    sc_names_ctx   = tax_ctx.get("sc_names", [])
    # sc_tax_data에 sc_df/sc_names 병합 (두 경로 모두 지원)
    if not sc_tax_data.get("sc_df", pd.DataFrame()).empty == True:
        pass
    else:
        sc_tax_data["sc_df"]    = sc_df_ctx
        sc_tax_data["sc_names"] = sc_names_ctx

    st.markdown(
        "<h3 style='margin-bottom:0.2rem;'>🏦 과세 금융소득 관리 대시보드</h3>"
        "<p style='color:rgba(255,255,255,0.5); font-size:0.83rem; margin-top:0;'>"
        "IRP·연금저축 연금소득 연 1,500만원 / 일반계좌 금융소득 연 2,000만원 한도 실시간 모니터링</p>",
        unsafe_allow_html=True,
    )

    # ── 연도 선택 ────────────────────────────────────────
    sel_year = st.selectbox(
        "조회 연도", [2024, 2025, 2026, 2027], index=[2024,2025,2026,2027].index(year),
        key="tax_year_sel", label_visibility="collapsed",
    )

    # ════════════════════════════════════════════════════
    # A. 시트 데이터 없는 경우: 현재 수치 기반 추산 모드
    # ════════════════════════════════════════════════════
    # 필수 컬럼(연월, 분배금(원)) 없으면 추산 모드로 전환
    _required_cols = ["연월", "분배금(원)"]
    if dist_df.empty or any(c not in dist_df.columns for c in _required_cols):
        dist_df = pd.DataFrame()  # 추산 모드 강제 전환

    if dist_df.empty:
        st.info(
            "📋 **분배금 과세 시트가 연결되지 않았습니다.** 현재 월 수치로 연간 추산합니다.\n\n"
            "정확한 관리를 위해 구글 시트에 `분배금과세` 탭을 추가하세요. (아래 시트 구성 안내 참고)"
        )
        _render_estimation_mode(
            irp_monthly, isa_monthly, gen_monthly, ps_monthly,
            current_month, target_monthly,
            sc_tax_data=sc_tax_data,
        )
        st.divider()
        _render_sheet_guide()
        return

    # ════════════════════════════════════════════════════
    # B. 시트 연동 모드
    # ════════════════════════════════════════════════════
    tax_result = calc_taxable_income(dist_df, sel_year)
    cum_df     = calc_monthly_cumulative(dist_df, sel_year)
    sim        = simulate_remaining_capacity(
        tax_result["irp_ps_annual"],
        tax_result["gen_taxable_annual"],
        current_month,
    )

    # ── 상단 경보 배너 ────────────────────────────────────
    p_color, p_emoji, p_label = _status(tax_result["irp_ps_annual"], PENSION_INCOME_LIMIT)
    g_color, g_emoji, g_label = _status(tax_result["gen_taxable_annual"], FINANCIAL_INCOME_LIMIT)

    alert_html = ""
    if tax_result["pension_综합위험"] > 0:
        alert_html += (
            f"<div style='background:rgba(255,75,75,0.15); border:1px solid #FF4B4B; "
            f"border-radius:8px; padding:10px 14px; margin-bottom:8px; font-size:0.88rem;'>"
            f"🚨 <b>연금소득 한도 초과!</b> IRP·연금저축 연금소득이 연 1,500만원을 "
            f"<b style='color:#FF4B4B;'>{tax_result['pension_综합위험']/10000:.1f}만원</b> 초과했습니다. "
            f"종합과세 대상이 됩니다.</div>"
        )
    if tax_result["financial_종합위험"] > 0:
        alert_html += (
            f"<div style='background:rgba(255,75,75,0.15); border:1px solid #FF4B4B; "
            f"border-radius:8px; padding:10px 14px; margin-bottom:8px; font-size:0.88rem;'>"
            f"🚨 <b>금융소득 종합과세 기준 초과!</b> 일반계좌 금융소득이 연 2,000만원을 "
            f"<b style='color:#FF4B4B;'>{tax_result['financial_종합위험']/10000:.1f}만원</b> 초과했습니다.</div>"
        )
    if alert_html:
        st.markdown(alert_html, unsafe_allow_html=True)

    # ── 1. 핵심 지표 게이지 ─────────────────────────────
    st.markdown("#### 📊 연간 누적 과세 현황")
    g1, g2, g3 = st.columns(3)

    with g1:
        st.plotly_chart(
            _gauge_fig(tax_result["irp_ps_annual"], PENSION_INCOME_LIMIT,
                       "IRP·연금저축 연금소득<br>(한도 1,500만원)"),
            use_container_width=True,
        )
        pct1 = tax_result["pension_limit_used_pct"]
        c, e, l = _status(tax_result["irp_ps_annual"], PENSION_INCOME_LIMIT)
        st.markdown(
            f"<div style='text-align:center; font-size:0.82rem;'>{e} <b style='color:{c};'>{l}</b> "
            f"({pct1:.1f}% 사용) · 잔여 "
            f"<b>{(PENSION_INCOME_LIMIT-tax_result['irp_ps_annual'])/10000:.1f}만원</b></div>",
            unsafe_allow_html=True,
        )

    with g2:
        st.plotly_chart(
            _gauge_fig(tax_result["isa_dist_annual"], ISA_TAX_FREE_ANNUAL * 10,
                       "ISA 분배금 누적<br>(비과세 200만원)"),
            use_container_width=True,
        )
        isa_free_pct = min(tax_result["isa_dist_annual"] / ISA_TAX_FREE_ANNUAL * 100, 999)
        st.markdown(
            f"<div style='text-align:center; font-size:0.82rem;'>"
            f"비과세 <b style='color:#7dffb0;'>{tax_result['isa_taxfree']/10000:.1f}만원</b> · "
            f"과세 <b style='color:#FF4B4B;'>{tax_result['isa_taxable']/10000:.1f}만원</b></div>",
            unsafe_allow_html=True,
        )

    with g3:
        st.plotly_chart(
            _gauge_fig(tax_result["gen_taxable_annual"], FINANCIAL_INCOME_LIMIT,
                       "일반계좌 금융소득<br>(종합과세 기준 2,000만원)"),
            use_container_width=True,
        )
        pct3 = tax_result["financial_limit_used_pct"]
        c3, e3, l3 = _status(tax_result["gen_taxable_annual"], FINANCIAL_INCOME_LIMIT)
        st.markdown(
            f"<div style='text-align:center; font-size:0.82rem;'>{e3} <b style='color:{c3};'>{l3}</b> "
            f"({pct3:.1f}% 사용) · 잔여 "
            f"<b>{(FINANCIAL_INCOME_LIMIT-tax_result['gen_taxable_annual'])/10000:.1f}만원</b></div>",
            unsafe_allow_html=True,
        )

    # ── 2. 남은 기간 월별 인출 가능 한도 ─────────────────
    st.divider()
    st.markdown("#### 📅 잔여 인출 가능 한도 (이번달 이후)")

    rm1, rm2, rm3, rm4 = st.columns(4)
    rm1.metric("남은 개월",      f"{sim['remaining_months']}개월")
    rm2.metric("IRP·연금저축 잔여", f"{sim['pension_remaining']/10000:.1f}만원",
               help="연 1,500만원 기준 남은 한도")
    rm3.metric("월 평균 가능",   f"{sim['pension_monthly_avail']/10000:.1f}만원/월",
               help="남은 기간 균등 배분 시 월 인출 가능액")
    rm4.metric("일반계좌 잔여",  f"{sim['gen_remaining']/10000:.1f}만원",
               help="연 2,000만원 기준 남은 한도")

    # ── 3. 월별 누적 추이 차트 ────────────────────────────
    if not cum_df.empty:
        st.divider()
        st.markdown("#### 📈 월별 누적 과세표준 추이")

        fig_cum = go.Figure()

        # IRP·연금저축 누적
        fig_cum.add_trace(go.Bar(
            x=cum_df["연월"], y=cum_df["연금과세표준"] / 10000,
            name="IRP·연금저축 (월)", marker_color="rgba(255,215,0,0.6)",
        ))
        fig_cum.add_trace(go.Scatter(
            x=cum_df["연월"], y=cum_df["연금과세_누적"] / 10000,
            name="IRP·연금저축 누적", mode="lines+markers",
            line=dict(color="#FFD700", width=2.5),
            marker=dict(size=7),
        ))

        # 일반계좌 누적
        fig_cum.add_trace(go.Bar(
            x=cum_df["연월"], y=cum_df["일반과세표준"] / 10000,
            name="일반계좌 (월)", marker_color="rgba(135,206,235,0.5)",
        ))
        fig_cum.add_trace(go.Scatter(
            x=cum_df["연월"], y=cum_df["일반과세_누적"] / 10000,
            name="일반계좌 누적", mode="lines+markers",
            line=dict(color="#87CEEB", width=2, dash="dot"),
        ))

        # 한도선
        fig_cum.add_hline(
            y=PENSION_INCOME_LIMIT / 10000,
            line_dash="dash", line_color="#FFD700", line_width=1.5,
            annotation_text="연금소득 한도 1,500만원",
            annotation_position="top left",
            annotation_font_color="#FFD700",
        )
        fig_cum.add_hline(
            y=FINANCIAL_INCOME_LIMIT / 10000,
            line_dash="dash", line_color="#87CEEB", line_width=1.5,
            annotation_text="금융소득 한도 2,000만원",
            annotation_position="top right",
            annotation_font_color="#87CEEB",
        )
        # 경고선 (80%)
        fig_cum.add_hline(
            y=PENSION_INCOME_LIMIT * WARN_RATIO / 10000,
            line_dash="dot", line_color="rgba(255,215,0,0.35)", line_width=1,
        )

        fig_cum.update_layout(
            barmode="group", height=360,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(255,255,255,0.02)",
            font_color="white",
            legend=dict(orientation="h", y=-0.25, xanchor="center", x=0.5),
            margin=dict(t=20, b=80, l=10, r=10),
            yaxis=dict(title="만원", tickformat=","),
            xaxis=dict(tickangle=-30),
            hovermode="x unified",
        )
        st.plotly_chart(fig_cum, use_container_width=True)

        # ── 4. 종목별 월별 과세표준 테이블 ─────────────────
        st.divider()
        st.markdown("#### 📋 종목별 월별 과세표준 상세")

        yr_df = dist_df[dist_df["연월"].astype(str).str.startswith(str(sel_year))].copy()
        if "계좌" not in yr_df.columns:
            yr_df["계좌"] = "IRP"
        if "종목명" not in yr_df.columns:
            yr_df["종목명"] = "미입력"
        if "과세표준(원)" not in yr_df.columns:
            yr_df["과세표준(원)"] = yr_df["분배금(원)"] if "분배금(원)" in yr_df.columns else 0
        yr_df["분배금(원)"]   = pd.to_numeric(yr_df.get("분배금(원)", 0),   errors="coerce").fillna(0)
        yr_df["과세표준(원)"] = pd.to_numeric(yr_df["과세표준(원)"],         errors="coerce").fillna(0)
        yr_df["과세비율(%)"]  = (yr_df["과세표준(원)"] / yr_df["분배금(원)"].replace(0, pd.NA) * 100).fillna(0).round(1)

        if not yr_df.empty:
            pivot = yr_df.pivot_table(
                index=["계좌","종목명"],
                columns="연월",
                values="과세표준(원)",
                aggfunc="sum",
                fill_value=0,
            ).reset_index()

            months = sorted(yr_df["연월"].unique())
            col_cfg = {m: st.column_config.NumberColumn(m, format="%,.0f") for m in months}

            # 연간 합계 컬럼 추가
            pivot["연간합계"] = pivot[months].sum(axis=1)
            col_cfg["연간합계"] = st.column_config.NumberColumn("연간합계", format="%,.0f")

            # 한도 대비 색상 강조를 위해 연금계좌만 필터링
            pension_total = pivot[pivot["계좌"].isin(["IRP","연금저축"])]["연간합계"].sum()
            remaining = PENSION_INCOME_LIMIT - pension_total
            if remaining < PENSION_INCOME_LIMIT * (1 - WARN_RATIO):
                st.warning(
                    f"⚠️ IRP·연금저축 연간 과세표준 합계 **{pension_total/10000:.1f}만원** "
                    f"/ 한도 {PENSION_INCOME_LIMIT/10000:.0f}만원 "
                    f"— 잔여 **{remaining/10000:.1f}만원**"
                )

            st.dataframe(pivot, hide_index=True, use_container_width=True, column_config=col_cfg)

        # ── 5. 과세비율 변동 추이 (커버드콜 핵심 지표) ────
        st.divider()
        st.markdown("#### 🎯 종목별 과세비율(%) 월별 변동 추이")
        st.caption("커버드콜 ETF는 옵션프리미엄 vs 주가차익 비중에 따라 월별 과세표준 비율이 달라집니다.")

        if not yr_df.empty and "종목명" in yr_df.columns:
            stocks = yr_df["종목명"].unique()
            fig_tax_rate = go.Figure()
            COLORS = ["#FFD700","#87CEEB","#7dffb0","#FF4B4B","#AFA9EC","#FF8C00"]
            for i, stk in enumerate(stocks):
                s_df = yr_df[yr_df["종목명"] == stk].sort_values("연월")
                if s_df.empty:
                    continue
                fig_tax_rate.add_trace(go.Scatter(
                    x=s_df["연월"], y=s_df["과세비율(%)"],
                    name=stk, mode="lines+markers",
                    line=dict(color=COLORS[i % len(COLORS)], width=2),
                    marker=dict(size=7),
                    hovertemplate=f"{stk}: %{{y:.1f}}%<extra></extra>",
                ))
            fig_tax_rate.add_hline(
                y=100, line_dash="dot", line_color="rgba(255,255,255,0.2)", line_width=1,
                annotation_text="100% (전액 과세)",
            )
            fig_tax_rate.update_layout(
                height=300, paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(255,255,255,0.02)", font_color="white",
                legend=dict(orientation="h", y=-0.3, xanchor="center", x=0.5),
                margin=dict(t=20, b=80, l=10, r=10),
                yaxis=dict(title="과세비율 (%)", ticksuffix="%", range=[0, 110]),
                xaxis=dict(tickangle=-30),
                hovermode="x unified",
            )
            st.plotly_chart(fig_tax_rate, use_container_width=True)

    # ── 6. 절세 전략 조언 ───────────────────────────────
    st.divider()
    _render_tax_strategy(tax_result, sim, target_monthly)

    # ── 7. 시트 구성 안내 ───────────────────────────────
    with st.expander("📋 분배금과세 시트 구성 안내"):
        _render_sheet_guide()


# ════════════════════════════════════════════════════════
# 추산 모드 (시트 미연동 시) — 시나리오 종목별 상세 추산
# ════════════════════════════════════════════════════════
def _classify_source(memo: str) -> str:
    """메모/원천구분 컬럼에서 퇴직금/개인납입 분류"""
    m = str(memo).strip()
    if any(k in m for k in ["개인","납입","세액공제"]):
        return "개인납입"
    return "퇴직금"  # IRP 기본값 퇴직금


def _render_estimation_mode(
    irp_monthly, isa_monthly, gen_monthly, ps_monthly,
    current_month, target_monthly,
    sc_tax_data: dict = None,
):
    """
    시나리오 종목별 수량×과세표준 기반 연간 과세소득 시뮬레이션.

    세법 핵심 (소득세법 §129 ①5호):
      - IRP 퇴직금 원천: 퇴직소득세 감면(0.76%/1.1%) → 연 1,500만원 한도 제외
      - IRP 개인납입+운용수익, 연금저축: 연금소득세(5.5%) → 1,500만원 한도 포함
    """
    st.markdown("#### 📐 연간 과세소득 시뮬레이션 (종목별 수량·과세표준 기준)")
    st.caption(
        "시나리오 시트의 **수량 × 주당 과세표준**으로 계산합니다. "
        "퇴직금 원천 분배금은 퇴직소득세(0.76~1.1%)로 분리과세되므로 "
        "연금소득 1,500만원 한도에 **포함되지 않습니다.**"
    )

    sc_data = sc_tax_data or {}
    sc_df   = sc_data.get("sc_df", pd.DataFrame())
    sc_names= sc_data.get("sc_names", [])
    remaining_months = max(1, 12 - current_month + 1)

    # ── 시나리오 선택 ────────────────────────────────────
    if not sc_df.empty and "시나리오명" in sc_df.columns:
        _opts = sc_names if sc_names else sc_df["시나리오명"].unique().tolist()
        _sel  = st.selectbox("시뮬레이션 시나리오", _opts,
                             key="est_sc_sel",
                             help="시나리오 탭의 포트폴리오로 연간 과세표준을 추산합니다")
        sc_sub = sc_df[sc_df["시나리오명"] == _sel].copy()
    elif not sc_df.empty:
        sc_sub = sc_df.copy()
        _sel   = "기본 포트폴리오"
    else:
        sc_sub = pd.DataFrame()
        _sel   = ""

    # ── 시나리오 데이터 없으면 기본 슬라이더 모드 폴백 ──
    if sc_sub.empty:
        st.info("시나리오 시트 데이터가 없습니다. 현재 분배금 기준 단순 추산을 표시합니다.")
        _irp_tb = irp_monthly * 0.6 * 12
        _ps_tb  = ps_monthly  * 0.8 * 12
        _in_limit = _ps_tb  # 연금저축만 한도 포함 (단순 추산)
        r1,r2,r3,r4 = st.columns(4)
        r1.metric("연금소득 한도 포함 추산", f"{_in_limit/10000:.1f}만원",
                  help="연금저축 분배금의 80% 기준 (과세표준 입력 시 정확해짐)")
        r2.metric("한도 잔여", f"{max(0,PENSION_INCOME_LIMIT-_in_limit)/10000:.1f}만원")
        r3.metric("IRP 퇴직금 추산 (한도 제외)", f"{_irp_tb/10000:.1f}만원",
                  help="퇴직소득세 분리과세 — 1,500만원 한도 무관")
        r4.metric("월 여유", f"{max(0,PENSION_INCOME_LIMIT-_in_limit)/remaining_months/10000:.1f}만원/월")
        return

    # ── 수치 컬럼 정규화 ─────────────────────────────────
    for _nc in ["수량","주당분배금","과세표준","평가액","원금"]:
        if _nc in sc_sub.columns:
            sc_sub[_nc] = pd.to_numeric(
                sc_sub[_nc].astype(str).str.replace(",",""), errors="coerce"
            ).fillna(0)

    # 원금 없으면 평가액 사용
    if "원금" not in sc_sub.columns or sc_sub.get("원금", pd.Series([0])).sum() == 0:
        if "평가액" in sc_sub.columns:
            sc_sub["원금"] = sc_sub["평가액"]

    # 과세표준 컬럼 없으면 0
    if "과세표준" not in sc_sub.columns:
        sc_sub["과세표준"] = 0

    # 분배주기 반영
    if "분배주기" in sc_sub.columns:
        sc_sub["_freq"] = sc_sub["분배주기"].astype(str).apply(
            lambda x: 1 if "년" in x else 12
        )
    else:
        sc_sub["_freq"] = 12

    # 원천 분류 (메모 컬럼 기반)
    src_col = "메모" if "메모" in sc_sub.columns else ("원천구분" if "원천구분" in sc_sub.columns else None)
    if src_col:
        sc_sub["_source"] = sc_sub[src_col].apply(_classify_source)
    else:
        sc_sub["_source"] = sc_sub["계좌"].apply(
            lambda x: "개인납입" if x in ("연금저축","ISA","일반") else "퇴직금"
        )

    # ── 종목별 수량·과세표준 조정 UI ─────────────────────
    st.markdown("**⚙️ 종목별 수량·과세표준 조정**")
    st.caption("수량 또는 주당 과세표준을 변경하면 과세표준이 실시간으로 재계산됩니다.")

    _adj_data = []
    pension_rows = sc_sub[sc_sub["계좌"].isin(["IRP","연금저축"])].copy()
    isa_rows     = sc_sub[sc_sub["계좌"] == "ISA"].copy()
    gen_rows     = sc_sub[sc_sub["계좌"] == "일반"].copy()

    # 조정 테이블 — IRP·연금저축만 표시 (한도 관련)
    if not pension_rows.empty:
        for idx2, row in pension_rows.iterrows():
            nm       = str(row.get("종목명","")).strip()
            acc_nm   = str(row.get("계좌","IRP")).strip()
            src      = str(row.get("_source","퇴직금"))
            qty_orig = int(row.get("수량", 0))
            tb_orig  = int(row.get("과세표준", 0))
            freq     = int(row.get("_freq", 12))
            in_limit = "✅ 한도 포함" if (acc_nm=="연금저축" or src=="개인납입") else "➖ 한도 제외(퇴직금)"
            badge_c  = "#7dffb0" if "포함" in in_limit else "#87CEEB"
            _adj_data.append({
                "nm": nm, "acc": acc_nm, "src": src,
                "qty_orig": qty_orig, "tb_orig": tb_orig,
                "freq": freq, "in_limit": in_limit, "badge_c": badge_c,
            })

    # 각 종목 행 렌더링 (expander 내부)
    _updated_rows = []
    with st.expander("종목별 수량·과세표준 조정 (클릭하여 펼치기)", expanded=False):
        st.caption("수량 조정은 매입·매도 시뮬레이션, 과세표준 조정은 운용사 공시값 변경 시 활용하세요.")
        for _i, d in enumerate(_adj_data):
            col_nm, col_qty, col_tb, col_info = st.columns([3,2,2,3])
            with col_nm:
                st.markdown(
                    f"<div style='padding:6px 0;font-size:0.82rem;'>"
                    f"<span style='font-size:0.7rem;background:rgba(255,255,255,0.08);"
                    f"padding:1px 6px;border-radius:3px;margin-right:4px;'>{d['acc']}</span>"
                    f"{d['nm'][:20]}</div>",
                    unsafe_allow_html=True,
                )
            with col_qty:
                adj_qty = st.number_input(
                    "수량", min_value=0, max_value=100000,
                    value=d["qty_orig"], step=100,
                    key=f"est_qty_{_i}",
                    label_visibility="collapsed",
                )
            with col_tb:
                adj_tb = st.number_input(
                    "주당과세표준", min_value=0, max_value=10000,
                    value=d["tb_orig"], step=1,
                    key=f"est_tb_{_i}",
                    label_visibility="collapsed",
                )
            with col_info:
                st.markdown(
                    f"<div style='padding:6px 0;font-size:0.75rem;color:{d["badge_c"]};'>"
                    f"{d['in_limit']}</div>",
                    unsafe_allow_html=True,
                )
            _updated_rows.append({**d, "qty": adj_qty, "tb": adj_tb})

    # 조정 없으면 원본값 사용
    if not _updated_rows:
        for idx2, row in pension_rows.iterrows():
            _updated_rows.append({
                "nm":  str(row.get("종목명","")),
                "acc": str(row.get("계좌","IRP")),
                "src": str(row.get("_source","퇴직금")),
                "qty": int(row.get("수량",0)),
                "tb":  int(row.get("과세표준",0)),
                "freq":int(row.get("_freq",12)),
                "in_limit": "➖ 한도 제외(퇴직금)" if str(row.get("_source","퇴직금"))=="퇴직금" else "✅ 한도 포함",
                "badge_c": "#87CEEB",
            })

    # ── 과세표준 집계 (원천별 분리) ───────────────────────
    irp_retire_monthly  = 0.0  # 퇴직금 → 한도 제외
    irp_personal_monthly= 0.0  # 개인납입 → 한도 포함
    ps_monthly_tb       = 0.0  # 연금저축 → 한도 포함

    for d in _updated_rows:
        monthly_tb = d["qty"] * d["tb"]  # 월 과세표준
        if d["acc"] == "IRP" and d["src"] == "퇴직금":
            irp_retire_monthly += monthly_tb
        elif d["acc"] == "IRP" and d["src"] == "개인납입":
            irp_personal_monthly += monthly_tb
        elif d["acc"] == "연금저축":
            ps_monthly_tb += monthly_tb

    # ISA·일반은 시나리오 원본값 사용
    isa_monthly_tb = float(isa_rows["수량"].mul(isa_rows["과세표준"]).sum()) if not isa_rows.empty else 0.0
    gen_monthly_tb = float(gen_rows["수량"].mul(gen_rows["과세표준"]).sum()) if not gen_rows.empty else 0.0

    # 연간 환산
    irp_retire_annual   = irp_retire_monthly  * 12
    irp_personal_annual = irp_personal_monthly* 12
    ps_annual_tb        = ps_monthly_tb       * 12
    isa_annual_tb       = isa_monthly_tb      * 12
    gen_annual_tb       = gen_monthly_tb      * 12

    # 한도 포함 합계 (핵심)
    pension_in_limit = irp_personal_annual + ps_annual_tb

    # ISA 비과세·과세 분리
    isa_taxfree  = min(isa_annual_tb, ISA_TAX_FREE_ANNUAL)
    isa_taxable  = max(0, isa_annual_tb - ISA_TAX_FREE_ANNUAL)

    # ── 요약 카드 ────────────────────────────────────────
    st.divider()
    st.markdown("**📊 과세표준 집계 요약**")

    r1, r2, r3, r4 = st.columns(4)
    _pc, _, _ = _status(pension_in_limit, PENSION_INCOME_LIMIT)
    r1.metric(
        "연금소득 한도 포함 (개인납입·연금저축)",
        f"{pension_in_limit/10000:.1f}만원",
        delta=f"한도 대비 {pension_in_limit/PENSION_INCOME_LIMIT*100:.1f}%",
        delta_color="inverse" if pension_in_limit > PENSION_INCOME_LIMIT * WARN_RATIO else "normal",
        help="IRP 개인납입+운용수익 + 연금저축 (소득세법 §129 ①5호 연금소득세 대상)",
    )
    r2.metric(
        "한도 잔여",
        f"{max(0, PENSION_INCOME_LIMIT - pension_in_limit)/10000:.1f}만원",
        delta=f"월 {max(0, PENSION_INCOME_LIMIT - pension_in_limit)/remaining_months/10000:.1f}만원 여유",
        help=f"남은 {remaining_months}개월 균등 기준",
    )
    r3.metric(
        "IRP 퇴직금 연간 과세표준 (한도 제외)",
        f"{irp_retire_annual/10000:.1f}만원",
        help="퇴직소득세 0.76~1.1% 분리과세 — 연금소득 1,500만원 한도와 무관",
    )
    r4.metric(
        "ISA 과세 (비과세 초과분)",
        f"{isa_taxable/10000:.1f}만원",
        delta=f"비과세 {isa_taxfree/10000:.1f}만원 적용 후",
        help="ISA 연 200만원 비과세 초과분 9.9% 분리과세",
    )

    st.divider()

    # ── 종목별 상세 테이블 ──────────────────────────────
    st.markdown("**📋 종목별 과세표준 상세**")

    _acc_badge = {
        "IRP":    "background:rgba(135,206,235,0.15);color:#87CEEB;",
        "ISA":    "background:rgba(125,255,176,0.15);color:#7dffb0;",
        "연금저축": "background:rgba(255,215,0,0.15);color:#FFD700;",
        "일반":   "background:rgba(175,169,236,0.15);color:#AFA9EC;",
    }
    _th = ("background:rgba(255,255,255,0.06);padding:7px 10px;font-size:0.75rem;"
           "font-weight:600;color:rgba(255,255,255,0.5);border-bottom:1px solid rgba(255,255,255,0.1);"
           "white-space:nowrap;")
    _th_r = _th + "text-align:right;"
    _td   = "padding:6px 10px;font-size:0.82rem;border-bottom:0.5px solid rgba(255,255,255,0.06);"
    _td_r = _td + "text-align:right;"

    rows_html = []
    # IRP·연금저축 — 조정 데이터 반영
    for d in _updated_rows:
        monthly_tb = d["qty"] * d["tb"]
        annual_tb  = monthly_tb * 12
        monthly_dist = d["qty"] * next(
            (int(r.get("주당분배금",0)) for _,r in pension_rows.iterrows() if str(r.get("종목명",""))==d["nm"]),
            0
        )
        tax_pct = (d["tb"] / (monthly_dist // d["qty"]) * 100) if d["qty"] > 0 and monthly_dist > 0 else 0
        in_limit_txt = "포함" if "포함" in d.get("in_limit","") else "제외(퇴직금)"
        in_c = "#7dffb0" if "포함" in in_limit_txt else "#87CEEB"
        bst  = _acc_badge.get(d["acc"],"")
        rows_html.append(
            f"<tr><td style='{_td}'><span style='font-size:0.7rem;font-weight:700;padding:1px 6px;border-radius:3px;{bst}'>{d['acc']}</span></td>"
            f"<td style='{_td}'>{d['nm'][:24]}</td>"
            f"<td style='{_td_r}'>{d['qty']:,}</td>"
            f"<td style='{_td_r}'>{d['tb']:,}</td>"
            f"<td style='{_td_r}'>{monthly_tb:,.0f}</td>"
            f"<td style='{_td_r}'>{annual_tb/10000:.2f}만</td>"
            f"<td style='{_td}color:{in_c};font-weight:600;'>{in_limit_txt}</td></tr>"
        )

    # ISA·일반 행 추가 (조정 없이 원본)
    for _df2, _acc2 in [(isa_rows, "ISA"), (gen_rows, "일반")]:
        for _, row in _df2.iterrows():
            nm2 = str(row.get("종목명",""))
            qty2 = int(row.get("수량",0))
            tb2  = int(row.get("과세표준",0))
            bst2 = _acc_badge.get(_acc2,"")
            rows_html.append(
                f"<tr><td style='{_td}'><span style='font-size:0.7rem;font-weight:700;padding:1px 6px;border-radius:3px;{bst2}'>{_acc2}</span></td>"
                f"<td style='{_td}'>{nm2[:24]}</td>"
                f"<td style='{_td_r}'>{qty2:,}</td>"
                f"<td style='{_td_r}'>{tb2:,}</td>"
                f"<td style='{_td_r}'>{qty2*tb2:,.0f}</td>"
                f"<td style='{_td_r}'>{qty2*tb2*12/10000:.2f}만</td>"
                f"<td style='{_td}color:#7dffb0;'>비과세200만</td></tr>"
            )

    tbl = (
        "<div style='overflow-x:auto;border:1px solid rgba(255,255,255,0.1);border-radius:8px;'>"
        "<table style='width:100%;border-collapse:collapse;'><thead><tr>"
        f"<th style='{_th}'>계좌</th>"
        f"<th style='{_th}'>종목명</th>"
        f"<th style='{_th_r}'>수량</th>"
        f"<th style='{_th_r}'>주당과세표준</th>"
        f"<th style='{_th_r}'>월 과세표준</th>"
        f"<th style='{_th_r}'>연 과세표준</th>"
        f"<th style='{_th}'>한도 구분</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows_html)}</tbody></table></div>"
    )
    st.markdown(tbl, unsafe_allow_html=True)

    st.divider()

    # ── 한도 시각화 ─────────────────────────────────────
    col_g1, col_g2 = st.columns(2)
    with col_g1:
        st.plotly_chart(
            _gauge_fig(pension_in_limit, PENSION_INCOME_LIMIT,
                       "연금소득 한도 포함<br>(IRP개인납입+연금저축)"),
            use_container_width=True,
        )
        _pc2, _pe2, _pl2 = _status(pension_in_limit, PENSION_INCOME_LIMIT)
        st.markdown(
            f"<div style='text-align:center;font-size:0.82rem;'>"
            f"{_pe2} <b style='color:{_pc2};'>{_pl2}</b> — "
            f"잔여 <b>{max(0,PENSION_INCOME_LIMIT-pension_in_limit)/10000:.0f}만원</b> "
            f"/ 월 <b>{max(0,PENSION_INCOME_LIMIT-pension_in_limit)/remaining_months/10000:.1f}만원</b></div>",
            unsafe_allow_html=True,
        )
    with col_g2:
        fig_bar = go.Figure()
        cats   = ["IRP 퇴직금(한도 제외)", "IRP 개인납입(한도 포함)", "연금저축(한도 포함)"]
        vals   = [irp_retire_annual/10000, irp_personal_annual/10000, ps_annual_tb/10000]
        colors = ["#87CEEB", "#FFD700", "#FFD700"]
        fig_bar.add_trace(go.Bar(
            x=cats, y=vals, marker_color=colors, text=[f"{v:.1f}만" for v in vals],
            textposition="outside",
        ))
        fig_bar.add_hline(
            y=PENSION_INCOME_LIMIT/10000,
            line_dash="dash", line_color="#FF4B4B", line_width=2,
            annotation_text="한도 1,500만원",
            annotation_font_color="#FF4B4B",
        )
        fig_bar.update_layout(
            height=230, paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(255,255,255,0.02)", font_color="white",
            margin=dict(t=30,b=10,l=10,r=10),
            yaxis=dict(title="만원", tickformat=","),
            showlegend=False,
        )
        st.plotly_chart(fig_bar, use_container_width=True)

    # ── 경고 및 절세 조언 ───────────────────────────────
    if pension_in_limit > PENSION_INCOME_LIMIT:
        over = pension_in_limit - PENSION_INCOME_LIMIT
        st.error(
            f"🚨 한도 초과 예측: 개인납입·연금저축 연간 과세표준이 "
            f"**{pension_in_limit/10000:.1f}만원**으로 1,500만원을 "
            f"**{over/10000:.1f}만원** 초과합니다. "
            f"과세비율 낮은 종목으로 교체하거나 수량을 줄이세요."
        )
        # 어떤 종목을 줄여야 하는가
        if _updated_rows:
            candidates = [d for d in _updated_rows if d["acc"] in ("IRP","연금저축")
                          and d["tb"] > 0 and d.get("in_limit","") and "포함" in d.get("in_limit","")]
            if candidates:
                reduce_per = over / len(candidates) / 12
                c_names = ", ".join(d["nm"][:10] for d in candidates[:3])
                st.warning(
                    f"💡 한도 포함 종목 균등 감축 시: {c_names} 등 각 "
                    f"약 {reduce_per:.0f}원/월({reduce_per/candidates[0]['tb']:.0f}주) 수량 축소 필요"
                )
    elif pension_in_limit > PENSION_INCOME_LIMIT * WARN_RATIO:
        st.warning(
            f"⚠️ 한도의 {pension_in_limit/PENSION_INCOME_LIMIT*100:.1f}% 수준 — "
            f"잔여 **{(PENSION_INCOME_LIMIT-pension_in_limit)/10000:.1f}만원** "
            f"({remaining_months}개월 기준 월 "
            f"{(PENSION_INCOME_LIMIT-pension_in_limit)/remaining_months/10000:.1f}만원 여유)"
        )
    else:
        st.success(
            f"✅ 연금소득 한도 정상 — "
            f"잔여 {(PENSION_INCOME_LIMIT-pension_in_limit)/10000:.0f}만원 "
            f"(월 {(PENSION_INCOME_LIMIT-pension_in_limit)/remaining_months/10000:.1f}만원 여유)"
        )

    # IRP 퇴직금 세금 별도 표시
    if irp_retire_annual > 0:
        irp_total_asset = sc_sub[sc_sub["계좌"]=="IRP"]["원금"].sum() if "원금" in sc_sub.columns else 0
        if irp_total_asset == 0 and "평가액" in sc_sub.columns:
            irp_total_asset = sc_sub[sc_sub["계좌"]=="IRP"]["평가액"].sum()
        limit_m = (irp_total_asset / 10 * 1.2 / 12) if irp_total_asset > 0 else 1_500_000
        within_m = min(irp_retire_monthly, limit_m)
        excess_m = max(0, irp_retire_monthly - limit_m)
        retire_tax_m = within_m * 0.0076 + excess_m * 0.011
        st.info(
            f"📌 IRP 퇴직금 원천 월 과세표준 {irp_retire_monthly:,.0f}원 → "
            f"퇴직소득세 {retire_tax_m:,.0f}원/월 (한도내 0.76% + 초과 1.1%) — "
            f"연금소득 1,500만원 한도와 **무관**하게 분리과세"
        )


# ════════════════════════════════════════════════════════
# 절세 전략 조언 섹션
# ════════════════════════════════════════════════════════
def _render_tax_strategy(tax_result: dict, sim: dict, target_monthly: float):
    st.markdown("#### 💡 절세 전략 조언")

    pension_used_pct = tax_result["pension_limit_used_pct"]
    gen_used_pct     = tax_result["financial_limit_used_pct"]

    strategies = []

    # IRP·연금저축 한도 관리
    if pension_used_pct >= 95:
        strategies.append(("🚨 즉시 조치", "#FF4B4B",
            "IRP·연금저축 이번달 인출을 중단하거나 대폭 축소하세요. "
            "연금소득 1,500만원 초과분은 종합과세 대상이 되어 실효세율이 급격히 높아집니다."))
    elif pension_used_pct >= 80:
        monthly_safe = sim["pension_monthly_avail"]
        strategies.append(("⚠️ 한도 주의", "#FFD700",
            f"남은 {sim['remaining_months']}개월 동안 IRP·연금저축 월 인출을 "
            f"**{monthly_safe/10000:.1f}만원 이하**로 제한하세요. "
            f"초과 시 종합과세 위험이 있습니다."))
    else:
        strategies.append(("✅ IRP 한도 양호", "#7dffb0",
            f"IRP·연금저축 잔여 한도 {sim['pension_remaining']/10000:.0f}만원. "
            f"월 {sim['pension_monthly_avail']/10000:.1f}만원 페이스 유지 가능합니다."))

    # 커버드콜 과세비율 변동 대응
    strategies.append(("📊 커버드콜 과세비율 모니터링", "#87CEEB",
        "커버드콜 ETF는 옵션프리미엄 회수 시 과세비율이 낮고, 주가 하락 후 반등 시 높아집니다. "
        "매월 운용사 홈페이지에서 과세표준 비율을 확인하고 시트에 입력하면 자동으로 누적 추적됩니다."))

    # ISA 활용 극대화
    isa_taxable = tax_result.get("isa_taxable", 0)
    if isa_taxable > 0:
        strategies.append(("💰 ISA 비과세 소진됨", "#FFD700",
            f"ISA 비과세 200만원 한도가 소진되어 {isa_taxable/10000:.1f}만원이 9.9% 분리과세 됩니다. "
            "추가 납입 여력이 있으면 ISA 납입한도(연 2,000만원)를 최대한 활용하세요."))
    else:
        strategies.append(("✅ ISA 비과세 범위 내", "#7dffb0",
            "ISA 분배금이 연 200만원 비과세 한도 이내입니다. "
            "일반계좌 종목은 ISA로 이전하는 것이 절세에 유리합니다."))

    # 일반계좌 금융소득 관리
    if gen_used_pct >= 80:
        strategies.append(("⚠️ 일반계좌 금융소득 주의", "#FF8C00",
            f"일반계좌 금융소득이 2,000만원 한도의 {gen_used_pct:.0f}%에 달했습니다. "
            "한도 초과 시 모든 금융소득이 다른 종합소득에 합산되어 최고 45% 세율이 적용될 수 있습니다. "
            "초과 예상 종목은 IRP·ISA로 교체를 검토하세요."))

    for title, color, content in strategies:
        st.markdown(
            f"<div style='background:rgba(255,255,255,0.03); border-left:4px solid {color}; "
            f"border-radius:0 8px 8px 0; padding:10px 14px; margin-bottom:8px;'>"
            f"<div style='font-size:0.82rem; font-weight:700; color:{color}; margin-bottom:4px;'>"
            f"{title}</div>"
            f"<div style='font-size:0.85rem; color:rgba(255,255,255,0.8); line-height:1.6;'>"
            f"{content}</div></div>",
            unsafe_allow_html=True,
        )


# ════════════════════════════════════════════════════════
# 시트 구성 안내
# ════════════════════════════════════════════════════════
def _render_sheet_guide():
    st.markdown("""
**구글 시트에 `분배금과세` 탭을 추가하고 아래 형식으로 입력하세요.**

| 연월 | 계좌 | 종목명 | 분배금(원) | 과세표준(원) | 비과세(원) | 비고 |
|---|---|---|---|---|---|---|
| 2026-01 | IRP | SOL팔란티어커버드콜 | 2364600 | 1891680 | 0 | 과세비율 80% |
| 2026-01 | ISA | KODEX200위클리커버드콜 | 556200 | 356200 | 200000 | |
| 2026-02 | IRP | SOL팔란티어커버드콜 | 2364600 | 2128140 | 0 | 과세비율 90% |
| 2026-03 | IRP | SOL팔란티어커버드콜 | 2364600 | 1655220 | 0 | 과세비율 70% |

**과세표준 확인 방법:**
1. 운용사 홈페이지(삼성자산운용·신한자산운용 등) → ETF 상세 → 분배금 내역
2. 또는 증권사 HTS → 연금계좌 → 분배금 내역 → 과세표준 확인
3. 매월 분배락일 이후 1~2영업일 내 확정 공시

**탭 생성 후 gid를 Streamlit Secrets에 추가:**
```toml
DIST_TAX_SHEET_GID = "여기에_gid"
```
""")


# ════════════════════════════════════════════════════════
# 데이터 로더 (pension_app.py 에서 호출)
# ════════════════════════════════════════════════════════
def load_dist_tax_sheet(url: str, gid: str) -> pd.DataFrame:
    """분배금과세 시트 로드"""
    if not gid:
        return pd.DataFrame()
    try:
        import re
        sid = re.search(r"/d/([a-zA-Z0-9_-]+)", url).group(1)
        df  = pd.read_csv(
            f"https://docs.google.com/spreadsheets/d/{sid}"
            f"/export?format=csv&gid={gid}"
        )
        for col in ["분배금(원)","과세표준(원)","비과세(원)"]:
            if col in df.columns:
                df[col] = pd.to_numeric(
                    df[col].astype(str).str.replace(",",""), errors="coerce"
                ).fillna(0)

        # ── 연월 형식 정규화: 날짜/Timestamp → "YYYY-MM" 문자열 ──
        # 시트에 "2026-04-01", datetime 등 다양한 형식이 올 수 있음
        if "연월" in df.columns:
            import pandas as _pd
            def _to_ym(v):
                try:
                    return _pd.to_datetime(v).strftime("%Y-%m")
                except Exception:
                    s = str(v).strip()
                    # 이미 YYYY-MM 형식이면 그대로
                    if len(s) >= 7:
                        return s[:7]
                    return s
            df["연월"] = df["연월"].apply(_to_ym)

        return df
    except Exception:
        return pd.DataFrame()
