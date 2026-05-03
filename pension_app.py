from __future__ import annotations

import re
from datetime import datetime
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

# ════════════════════════════════════════════════════════
# 설정 상수
# ════════════════════════════════════════════════════════
SHEET_URL      = "https://docs.google.com/spreadsheets/d/14e_0SQaBFbyEC-16hEEqvrJfdVXob20b3MLJ2Cn60Do"
WORKSHEET_NAME = "연금현황"
DATA_TTL            = "5m"
REQUIRED_ITEMS      = ["공적연금", "IRP", "ISA", "목표생활비"]  # 연금저축은 선택(없으면 0)
SCENARIO_SHEET_GID  = "961920932"   # ← 시나리오 탭 gid(숫자) 입력. 탭이 없으면 직접 작성 모드만 활성화
HOUSEHOLD_SHEET_GID = "122998571"   # ← 가계부 탭 gid(숫자) 입력
WATCHLIST_SHEET_GID = "142238543"
CONTRIBUTION_SHEET_GID  = "1969242299"   # ← 납입현황 탭 gid(숫자) 입력

# ── 세액공제 한도 (소득세법 §59의3) ─────────────────
IRP_TAX_DEDUCT_LIMIT    = 3_000_000   # IRP 단독 세액공제 한도 (연 300만원)
PS_TAX_DEDUCT_LIMIT     = 6_000_000   # 연금저축 세액공제 한도 (연 600만원)
COMBINED_TAX_DEDUCT_LIMIT = 9_000_000 # IRP+연금저축 합산 한도 (연 900만원)
ANNUAL_CONTRIBUTION_LIMIT = 18_000_000 # IRP+연금저축 합산 납입 한도 (연 1,800만원)
TAX_DEDUCT_RATE_GENERAL = 0.132        # 세액공제율 13.2% (지방세 포함, 총급여 5,500만 이하)
TAX_DEDUCT_RATE_HIGH    = 0.099        # 세액공제율 9.9% (총급여 5,500만 초과)

# ── 세금 상수 ─────────────────────────────────────────
# 건강보험료: 지역가입자 기준 (건보 6.99% + 장기요양 0.9182% ≈ 7.09%)
# 단, 공무원연금 수령자는 연금소득의 50%를 소득월액 기준으로 산정
HEALTH_INS_RATE        = 0.0709
# ISA 비과세 한도: 연 200만원 → 월 환산
ISA_TAX_FREE_MONTHLY   = 2_000_000 / 12
ISA_ANNUAL_LIMIT       = 20_000_000        # 연간 납입 한도 (소득세법 §91의18)
ISA_LIMIT              = 40_000_000        # 현재 누적 한도 (가입 2년차 기준 — 매년 갱신)
# IRP·퇴직연금·연금저축 분리과세: 5.5% (소득세법 §129 ①5호)
IRP_TAX_RATE           = 0.055
PS_TAX_RATE            = 0.055   # 연금저축 (pension saving)

# ── IRP 퇴직연금 실제 세율 (소득세법 시행령 §40의2) ────
# 퇴직금을 IRP에서 연금으로 수령 시 퇴직소득세 감면 적용
# 연금수령 한도(연차별 계산) 이내: 퇴직소득세 × 60% 감면
# 연금수령 한도 초과: 퇴직소득세 100% (감면 없음)
# ※ 아래 세율은 2026년 기준 실효세율 (개인 퇴직소득 과세표준 기반)
# ── 연금수령한도 공식 (소득세법 시행령 §40의2) ─────────
# 연간 한도 = 평가액 ÷ (11 - 수령연차) × 120%  연차: 개시=1, 매년+1
IRP_PENSION_TAX_WITHIN   = 0.0076      # 한도 내 실효세율 (퇴직소득세 × 60% 감면)
IRP_PENSION_TAX_EXCESS   = 0.011       # 한도 초과 실효세율 (퇴직소득세 100%)
IRP_PENSION_COMPREHENSIVE_LIMIT = 15_000_000  # 연 1,500만원 초과 시 종합과세 위험
# ── IRP 개인납입금·운용수익 원천 연금소득세 (소득세법 §129 ①5호) ─
# 나이별 세율: ~69세 5.5% / 70~79세 4.4% / 80세~ 3.3%
IRP_PENSION_PERSONAL_TAX = {  # 나이 → 세율(지방세 포함)
    "60s": 0.055,   # 55~69세
    "70s": 0.044,   # 70~79세
    "80s": 0.033,   # 80세 이상
}
# ※ 연금저축은 동일 연금소득세율 적용 (퇴직금 아닌 개인 납입금)





def _render_household_tab(
    hh_df,
    display_income: float,
    target_monthly: float,
    public_pension: float,
    irp_income: float,
    isa_income: float,
    now_kst,
):
    """월별 가계부 탭 렌더링 — 카테고리 월별 증감 모니터링 포함"""
    import calendar as _cal
    from datetime import datetime as _dt
    import pandas as pd
    import plotly.graph_objects as _go
    import plotly.express as _px
    import streamlit as st

    st.markdown("#### 📒 월별 가계부")

    # ── 시트 미연동 안내 ────────────────────────────────
    if hh_df.empty:
        st.info(
            "**구글 시트에 `가계부` 탭을 추가하고 아래 헤더로 구성하세요.**\n\n"
            "```\n연월 | 구분 | 카테고리 | 항목 | 금액 | 비고\n```\n\n"
            "| 연월 | 구분 | 카테고리 | 항목 | 금액 | 비고 |\n"
            "|---|---|---|---|---|---|\n"
            "| 2026-04 | 수입 | 공무원연금 | 공무원연금 | 3624210 | |\n"
            "| 2026-04 | 수입 | IRP분배금 | IRP분배금 | 3827200 | |\n"
            "| 2026-04 | 지출 | 식비 | 마트/외식 | 650000 | |\n"
            "| 2026-04 | 지출 | 여행/여가 | 알프스 준비 | 500000 | |\n\n"
            "탭 생성 후 gid를 `HOUSEHOLD_SHEET_GID`에 입력하세요."
        )
        st.divider()
        st.markdown("##### 📊 이번달 예상 수입 (시뮬레이션 기준)")
        _c1, _c2, _c3, _c4 = st.columns(4)
        _c1.metric("공무원연금", f"{public_pension:,.0f}원")
        _c2.metric("IRP 분배금", f"{irp_income:,.0f}원")
        _c3.metric("ISA 분배금", f"{isa_income:,.0f}원")
        _c4.metric("합계", f"{display_income:,.0f}원")
        return

    # ════════════════════════════════════════════════════
    # 공통 데이터 준비
    # ════════════════════════════════════════════════════
    hh_df = hh_df.copy()
    hh_df["금액"] = pd.to_numeric(hh_df["금액"], errors="coerce").fillna(0)
    hh_df["연월"] = hh_df["연월"].astype(str)

    all_ym = sorted(hh_df["연월"].unique(), reverse=True)
    cur_ym = f"{_dt.now().year}-{_dt.now().month:02d}"
    default_idx = all_ym.index(cur_ym) if cur_ym in all_ym else 0

    # 전월 계산
    def _prev_ym(ym: str) -> str:
        y, m = int(ym[:4]), int(ym[5:7])
        m -= 1
        if m == 0:
            m, y = 12, y - 1
        return f"{y}-{m:02d}"

    # ── 연월 선택 ────────────────────────────────────────
    sel_col, _, _ = st.columns([2, 3, 3])
    sel_ym = sel_col.selectbox(
        "조회 연월", all_ym, index=default_idx, key="hh_ym_sel",
        label_visibility="collapsed",
    )
    prev_ym = _prev_ym(sel_ym)

    month_df   = hh_df[hh_df["연월"] == sel_ym]
    prev_df    = hh_df[hh_df["연월"] == prev_ym]
    income_df  = month_df[month_df["구분"] == "수입"]
    expense_df = month_df[month_df["구분"] == "지출"]
    prev_inc   = prev_df[prev_df["구분"] == "수입"]
    prev_exp   = prev_df[prev_df["구분"] == "지출"]

    total_income_hh  = income_df["금액"].sum()
    total_expense_hh = expense_df["금액"].sum()
    balance          = total_income_hh - total_expense_hh
    prev_income_tot  = prev_inc["금액"].sum()
    prev_expense_tot = prev_exp["금액"].sum()
    prev_balance     = prev_income_tot - prev_expense_tot

    # ════════════════════════════════════════════════════
    # 1. 월별 요약 카드 (전월 대비 delta 포함)
    # ════════════════════════════════════════════════════
    mc1, mc2, mc3, mc4 = st.columns(4)
    mc1.metric(
        "총 수입", f"{total_income_hh:,.0f}원",
        delta=f"{total_income_hh - prev_income_tot:+,.0f}원" if prev_income_tot else None,
    )
    mc2.metric(
        "총 지출", f"{total_expense_hh:,.0f}원",
        delta=f"{total_expense_hh - prev_expense_tot:+,.0f}원" if prev_expense_tot else None,
        delta_color="inverse",
    )
    mc3.metric(
        "잉여/부족", f"{balance:+,.0f}원",
        delta=f"{balance - prev_balance:+,.0f}원" if prev_balance else None,
        delta_color="normal" if balance >= 0 else "inverse",
    )
    mc4.metric(
        "목표 대비",
        f"{(total_income_hh / target_monthly * 100) if target_monthly > 0 else 0:.0f}%",
        help="이번달 총 수입 ÷ 목표 생활비",
    )

    st.divider()

    # ════════════════════════════════════════════════════
    # 2. 수입·지출 카테고리 내역 (전월 대비 증감 포함)
    # ════════════════════════════════════════════════════
    left_col, right_col = st.columns(2)

    def _delta_badge(cur_val: float, prev_val: float, is_expense: bool = False) -> str:
        """전월 대비 증감 뱃지 HTML"""
        if prev_val == 0:
            return ""
        diff = cur_val - prev_val
        pct  = diff / prev_val * 100
        # 지출은 증가가 나쁨(빨강), 수입은 증가가 좋음(초록)
        if is_expense:
            color = "#FF4B4B" if diff > 0 else "#7dffb0"
        else:
            color = "#7dffb0" if diff > 0 else "#FF4B4B"
        arrow = "▲" if diff > 0 else "▼"
        return (
            f"<span style='font-size:0.75rem; color:{color}; margin-left:6px;'>"
            f"{arrow} {abs(diff)/10000:.1f}만 ({pct:+.1f}%)</span>"
        )

    # ── 수입 내역 ────────────────────────────────────────
    with left_col:
        st.markdown("**💰 수입 내역**")
        if not income_df.empty:
            inc_by_cat  = income_df.groupby("카테고리")["금액"].sum().reset_index()
            prev_inc_cat = prev_inc.groupby("카테고리")["금액"].sum().to_dict() if not prev_inc.empty else {}
            for _, row in inc_by_cat.iterrows():
                badge = _delta_badge(row["금액"], prev_inc_cat.get(row["카테고리"], 0), False)
                st.markdown(
                    f"<div style='display:flex; justify-content:space-between; align-items:center;"
                    f"padding:5px 0; border-bottom:1px solid rgba(255,255,255,0.05); font-size:0.88rem;'>"
                    f"<span style='color:rgba(255,255,255,0.7);'>{row['카테고리']}</span>"
                    f"<span><span style='color:#7dffb0; font-weight:600;'>{row['금액']:,.0f}원</span>"
                    f"{badge}</span></div>",
                    unsafe_allow_html=True,
                )
            st.markdown(
                f"<div style='display:flex; justify-content:space-between; padding:6px 0;"
                f"font-size:0.9rem; font-weight:700; margin-top:4px;'>"
                f"<span>합계</span>"
                f"<span style='color:#7dffb0;'>{total_income_hh:,.0f}원</span></div>",
                unsafe_allow_html=True,
            )
            fig_inc = _px.pie(
                inc_by_cat, values="금액", names="카테고리", hole=0.45,
                color_discrete_sequence=["#7dffb0","#87CEEB","#FFD700","#AFA9EC","#FF8C00","#5DCAA5"],
            )
            fig_inc.update_layout(
                height=220, paper_bgcolor="rgba(0,0,0,0)", font_color="white",
                legend=dict(orientation="h", y=-0.2, xanchor="center", x=0.5),
                margin=dict(t=10, b=60, l=0, r=0),
            )
            st.plotly_chart(fig_inc, use_container_width=True)
        else:
            st.caption("수입 내역 없음")

    # ── 지출 내역 ────────────────────────────────────────
    with right_col:
        st.markdown("**💸 지출 내역**")
        if not expense_df.empty:
            exp_by_cat  = expense_df.groupby("카테고리")["금액"].sum().reset_index().sort_values("금액", ascending=False)
            prev_exp_cat = prev_exp.groupby("카테고리")["금액"].sum().to_dict() if not prev_exp.empty else {}
            for _, row in exp_by_cat.iterrows():
                badge = _delta_badge(row["금액"], prev_exp_cat.get(row["카테고리"], 0), True)
                st.markdown(
                    f"<div style='display:flex; justify-content:space-between; align-items:center;"
                    f"padding:5px 0; border-bottom:1px solid rgba(255,255,255,0.05); font-size:0.88rem;'>"
                    f"<span style='color:rgba(255,255,255,0.7);'>{row['카테고리']}</span>"
                    f"<span><span style='color:#FF4B4B; font-weight:600;'>{row['금액']:,.0f}원</span>"
                    f"{badge}</span></div>",
                    unsafe_allow_html=True,
                )
            st.markdown(
                f"<div style='display:flex; justify-content:space-between; padding:6px 0;"
                f"font-size:0.9rem; font-weight:700; margin-top:4px;'>"
                f"<span>합계</span>"
                f"<span style='color:#FF4B4B;'>{total_expense_hh:,.0f}원</span></div>",
                unsafe_allow_html=True,
            )
            fig_d = _px.pie(
                exp_by_cat, values="금액", names="카테고리", hole=0.45,
                color_discrete_sequence=["#87CEEB","#FFD700","#FF4B4B","#7dffb0","#AFA9EC","#FF8C00"],
            )
            fig_d.update_layout(
                height=220, paper_bgcolor="rgba(0,0,0,0)", font_color="white",
                legend=dict(orientation="h", y=-0.2, xanchor="center", x=0.5),
                margin=dict(t=10, b=60, l=0, r=0),
            )
            st.plotly_chart(fig_d, use_container_width=True)
        else:
            st.caption("지출 내역 없음")

    # ════════════════════════════════════════════════════
    # 3. 카테고리별 월별 증감 추이 차트 [NEW]
    # ════════════════════════════════════════════════════
    st.divider()
    st.markdown("#### 📊 카테고리별 월별 증감 추이")

    # 최근 N개월 선택
    n_months = st.select_slider(
        "조회 기간", options=[3, 6, 9, 12], value=6, key="hh_trend_months",
        format_func=lambda x: f"최근 {x}개월",
    )
    recent_yms = sorted(hh_df["연월"].unique())[-n_months:]
    trend_df   = hh_df[hh_df["연월"].isin(recent_yms)]

    trend_tab_inc, trend_tab_exp = st.tabs(["💰 수입 카테고리 추이", "💸 지출 카테고리 추이"])

    # ── 수입 카테고리 추이 ────────────────────────────────
    with trend_tab_inc:
        inc_trend = (
            trend_df[trend_df["구분"] == "수입"]
            .groupby(["연월", "카테고리"])["금액"]
            .sum()
            .reset_index()
        )
        if not inc_trend.empty:
            cats = inc_trend["카테고리"].unique()
            COLORS = ["#7dffb0","#87CEEB","#FFD700","#AFA9EC","#FF8C00","#5DCAA5","#C8A8E9"]
            fig_it = _go.Figure()
            for i, cat in enumerate(cats):
                d = inc_trend[inc_trend["카테고리"] == cat].set_index("연월").reindex(recent_yms).fillna(0).reset_index()
                fig_it.add_trace(_go.Scatter(
                    x=d["연월"], y=d["금액"] / 10000,
                    name=cat, mode="lines+markers",
                    line=dict(color=COLORS[i % len(COLORS)], width=2),
                    marker=dict(size=6),
                    hovertemplate=f"{cat}: %{{y:.1f}}만원<extra></extra>",
                ))
            fig_it.update_layout(
                height=300, paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(255,255,255,0.02)", font_color="white",
                legend=dict(orientation="h", y=-0.3, xanchor="center", x=0.5),
                margin=dict(t=10, b=80, l=10, r=10),
                yaxis=dict(title="금액 (만원)", tickformat=","),
                xaxis=dict(tickangle=-30), hovermode="x unified",
            )
            st.plotly_chart(fig_it, use_container_width=True)

            # 전월 대비 증감 테이블
            inc_pivot = inc_trend.pivot_table(index="카테고리", columns="연월", values="금액", aggfunc="sum", fill_value=0)
            _show_mom_table(inc_pivot, recent_yms, is_expense=False)
        else:
            st.caption("수입 데이터 없음")

    # ── 지출 카테고리 추이 ────────────────────────────────
    with trend_tab_exp:
        exp_trend = (
            trend_df[trend_df["구분"] == "지출"]
            .groupby(["연월", "카테고리"])["금액"]
            .sum()
            .reset_index()
        )
        if not exp_trend.empty:
            cats_e = exp_trend["카테고리"].unique()
            COLORS_E = ["#FF4B4B","#FFD700","#87CEEB","#AFA9EC","#FF8C00","#7dffb0","#C8A8E9"]
            fig_et = _go.Figure()
            for i, cat in enumerate(cats_e):
                d = exp_trend[exp_trend["카테고리"] == cat].set_index("연월").reindex(recent_yms).fillna(0).reset_index()
                fig_et.add_trace(_go.Scatter(
                    x=d["연월"], y=d["금액"] / 10000,
                    name=cat, mode="lines+markers",
                    line=dict(color=COLORS_E[i % len(COLORS_E)], width=2),
                    marker=dict(size=6),
                    hovertemplate=f"{cat}: %{{y:.1f}}만원<extra></extra>",
                ))
            fig_et.update_layout(
                height=300, paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(255,255,255,0.02)", font_color="white",
                legend=dict(orientation="h", y=-0.3, xanchor="center", x=0.5),
                margin=dict(t=10, b=80, l=10, r=10),
                yaxis=dict(title="금액 (만원)", tickformat=","),
                xaxis=dict(tickangle=-30), hovermode="x unified",
            )
            st.plotly_chart(fig_et, use_container_width=True)

            exp_pivot = exp_trend.pivot_table(index="카테고리", columns="연월", values="금액", aggfunc="sum", fill_value=0)
            _show_mom_table(exp_pivot, recent_yms, is_expense=True)
        else:
            st.caption("지출 데이터 없음")

    # ════════════════════════════════════════════════════
    # 4. 지출 카테고리 히트맵 [NEW]
    # ════════════════════════════════════════════════════
    st.divider()
    st.markdown("#### 🗓️ 지출 카테고리 히트맵")
    st.caption("월×카테고리 지출 패턴 — 색이 진할수록 지출 多")

    hm_exp = (
        hh_df[hh_df["구분"] == "지출"]
        .groupby(["연월", "카테고리"])["금액"]
        .sum()
        .reset_index()
    )
    if not hm_exp.empty:
        hm_pivot = hm_exp.pivot_table(index="연월", columns="카테고리", values="금액", fill_value=0)
        hm_pivot = hm_pivot.sort_index()

        fig_hm = _go.Figure(_go.Heatmap(
            z=hm_pivot.values / 10000,
            x=hm_pivot.columns.tolist(),
            y=hm_pivot.index.tolist(),
            colorscale=[
                [0.0, "#1a1a2e"], [0.3, "#16213e"],
                [0.6, "#c0392b"], [1.0, "#FF4B4B"],
            ],
            hovertemplate="%{y} %{x}: %{z:.1f}만원<extra></extra>",
            showscale=True,
            colorbar=dict(title=dict(text="만원"), tickfont=dict(color="rgba(255,255,255,0.6)", size=10), thickness=12),
            xgap=2, ygap=2,
        ))
        fig_hm.update_layout(
            height=max(250, len(hm_pivot) * 28 + 80),
            paper_bgcolor="rgba(0,0,0,0)", font_color="white",
            margin=dict(t=20, b=40, l=10, r=80),
            xaxis=dict(tickfont=dict(size=11), side="top", tickangle=-30),
            yaxis=dict(tickfont=dict(size=11), autorange="reversed"),
        )
        st.plotly_chart(fig_hm, use_container_width=True)
    else:
        st.caption("지출 데이터 없음")

    # ════════════════════════════════════════════════════
    # 5. 기존: 월별 수입·지출 추이 바 차트
    # ════════════════════════════════════════════════════
    st.divider()
    st.markdown("**📈 월별 수입·지출 추이**")
    monthly_hh = hh_df.groupby(["연월","구분"])["금액"].sum().unstack(fill_value=0).reset_index()
    if "수입" not in monthly_hh.columns: monthly_hh["수입"] = 0
    if "지출" not in monthly_hh.columns: monthly_hh["지출"] = 0
    monthly_hh["잉여"] = monthly_hh["수입"] - monthly_hh["지출"]

    fig_trend = _go.Figure()
    fig_trend.add_trace(_go.Bar(x=monthly_hh["연월"], y=monthly_hh["수입"]/10000, name="수입", marker_color="rgba(125,255,176,0.7)"))
    fig_trend.add_trace(_go.Bar(x=monthly_hh["연월"], y=monthly_hh["지출"]/10000, name="지출", marker_color="rgba(255,75,75,0.7)"))
    fig_trend.add_trace(_go.Scatter(x=monthly_hh["연월"], y=monthly_hh["잉여"]/10000, name="잉여/부족", mode="lines+markers", line=dict(color="#FFD700", width=2)))
    fig_trend.add_hline(y=target_monthly/10000, line_dash="dot", line_color="rgba(255,255,255,0.3)", line_width=1,
                        annotation_text=f"목표 {target_monthly/10000:.0f}만", annotation_font_color="rgba(255,255,255,0.4)")
    fig_trend.update_layout(
        barmode="group", height=300, paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(255,255,255,0.02)", font_color="white",
        legend=dict(orientation="h", y=-0.25, xanchor="center", x=0.5),
        margin=dict(t=10, b=70, l=10, r=10),
        yaxis=dict(title="만원", tickformat=","), xaxis=dict(tickangle=-30), hovermode="x unified",
    )
    st.plotly_chart(fig_trend, use_container_width=True)

    # ════════════════════════════════════════════════════
    # 6. 기존: 연간 누계 + 상세 내역
    # ════════════════════════════════════════════════════
    st.divider()
    st.markdown("**📆 연간 누계**")
    hh_df["연도"] = hh_df["연월"].str[:4]
    annual_hh = hh_df.groupby(["연도","구분"])["금액"].sum().unstack(fill_value=0).reset_index()
    if "수입" not in annual_hh.columns: annual_hh["수입"] = 0
    if "지출" not in annual_hh.columns: annual_hh["지출"] = 0
    annual_hh["잉여"] = annual_hh["수입"] - annual_hh["지출"]
    for col in ["수입","지출","잉여"]:
        annual_hh[col] = (annual_hh[col]/10000).round(1)
    import streamlit as st
    st.dataframe(
        annual_hh.rename(columns={"수입":"수입(만원)","지출":"지출(만원)","잉여":"잉여(만원)"}),
        hide_index=True, use_container_width=True,
        column_config={
            "수입(만원)": st.column_config.NumberColumn(format="%,.1f"),
            "지출(만원)": st.column_config.NumberColumn(format="%,.1f"),
            "잉여(만원)": st.column_config.NumberColumn(format="%+.1f"),
        },
    )

    with st.expander("📋 이번달 상세 내역"):
        disp = month_df[["구분","카테고리","항목","금액"] +
                        (["비고"] if "비고" in month_df.columns else [])].copy()
        disp["금액"] = disp["금액"].apply(lambda x: f"{x:,.0f}")
        st.dataframe(disp, hide_index=True, use_container_width=True)

    st.divider()
    _render_actual_section(url=_SHEET_URL_REF, target_monthly=target_monthly)


# ════════════════════════════════════════════════════════
# 헬퍼: 전월 대비 증감 테이블
# ════════════════════════════════════════════════════════
def _show_mom_table(pivot, recent_yms, is_expense: bool = False):
    """카테고리 × 연월 피벗 → 전월 대비 증감 포함 st.dataframe 출력"""
    import pandas as pd
    import streamlit as st

    cols = [c for c in recent_yms if c in pivot.columns]
    if len(cols) < 2:
        return

    rows = []
    for cat in pivot.index:
        row = {"카테고리": cat}
        for i, ym in enumerate(cols):
            val  = pivot.loc[cat, ym]
            row[ym] = val / 10000  # 만원 단위
            if i > 0:
                prev_val = pivot.loc[cat, cols[i-1]]
                diff = val - prev_val
                row[f"{ym}_증감"] = diff / 10000
        rows.append(row)

    df_out = pd.DataFrame(rows)

    # 컬럼 설정
    col_cfg = {"카테고리": st.column_config.TextColumn("카테고리", width="medium")}
    for ym in cols:
        col_cfg[ym] = st.column_config.NumberColumn(ym, format="%.1f만")
        if f"{ym}_증감" in df_out.columns:
            col_cfg[f"{ym}_증감"] = st.column_config.NumberColumn(
                f"▲▼ {ym}", format="%+.1f만",
                help="전월 대비 증감 (만원)",
            )

    display_cols = ["카테고리"]
    for ym in cols:
        display_cols.append(ym)
        if f"{ym}_증감" in df_out.columns:
            display_cols.append(f"{ym}_증감")

    st.dataframe(
        df_out[display_cols],
        hide_index=True,
        use_container_width=True,
        column_config=col_cfg,
    )
# 실적 탭 URL 참조용 (함수 내부에서 SHEET_URL 접근)
_SHEET_URL_REF = SHEET_URL


def _render_actual_section(url: str, target_monthly: float):
    """실지급 & 생활비 실적 관리 섹션 (가계부 탭 하단에 표시)"""
    st.markdown("#### 📋 실지급 & 생활비 실적 관리")
    st.caption(
        "구글 시트 **실적** 탭(연월|공무원연금|IRP분배금|ISA분배금|일반분배금|생활비|비고) "
        "데이터를 입력하면 자동 반영됩니다."
    )

    ACTUAL_SHEET_GID = st.secrets.get("actual_gid", "")

    @st.cache_data(ttl=DATA_TTL, show_spinner=False)
    def _load_actual(u: str, gid: str) -> pd.DataFrame:
        if not gid:
            return pd.DataFrame()
        try:
            import re as _re
            sid = _re.search(r"/d/([a-zA-Z0-9_-]+)", u).group(1)
            df  = pd.read_csv(
                f"https://docs.google.com/spreadsheets/d/{sid}"
                f"/export?format=csv&gid={gid}"
            )
            return df
        except Exception:
            return pd.DataFrame()

    actual_df = _load_actual(url, ACTUAL_SHEET_GID)

    if actual_df.empty:
        with st.expander("📋 실적 시트 설정 방법", expanded=False):
            st.markdown("""
**구글 시트에 `실적` 탭을 추가하고 아래 헤더로 구성하세요.**

| 연월 | 공무원연금 | IRP분배금 | ISA분배금 | 일반분배금 | 생활비 | 비고 |
|---|---|---|---|---|---|---|
| 2026-03 | 3624210 | 3827200 | 556200 | 0 | 3250000 | |

**설정 순서**
1. 구글 시트에서 `실적` 탭 생성 후 위 형식으로 입력
2. 탭 우클릭 → 시트 ID(gid=XXXXXX) 확인
3. Streamlit Cloud → Manage app → Settings → Secrets 에 추가:
```
actual_gid = "여기에_실적탭_gid"
```
""")
        return

    required_cols = ["연월", "공무원연금", "IRP분배금", "ISA분배금", "생활비"]
    if any(col not in actual_df.columns for col in required_cols):
        st.warning("실적 시트 컬럼을 확인하세요: 연월|공무원연금|IRP분배금|ISA분배금|생활비")
        return

    for col in ["공무원연금","IRP분배금","ISA분배금","일반분배금","생활비"]:
        if col in actual_df.columns:
            actual_df[col] = pd.to_numeric(
                actual_df[col].astype(str).str.replace(",",""), errors="coerce"
            ).fillna(0)

    actual_df["총수입"] = (
        actual_df["공무원연금"] + actual_df["IRP분배금"]
        + actual_df["ISA분배금"]
        + actual_df.get("일반분배금", pd.Series([0]*len(actual_df)))
    )
    actual_df["잉여/부족"] = actual_df["총수입"] - actual_df["생활비"]

    # 최근 3개월 카드
    recent = actual_df.tail(3)
    r_cols = st.columns(len(recent))
    for i, (_, row) in enumerate(recent.iterrows()):
        gc       = "#7dffb0" if row["잉여/부족"] >= 0 else "#FF4B4B"
        ym_label = str(row["연월"])
        with r_cols[i]:
            with st.container(border=True):
                st.markdown(
                    "<div style='font-size:0.82rem; font-weight:700; "
                    "color:rgba(255,255,255,0.6);'>" + ym_label + "</div>",
                    unsafe_allow_html=True,
                )
                st.metric("총 수입",  f"{row['총수입']:,.0f}원")
                st.metric("생활비",   f"{row['생활비']:,.0f}원")
                st.metric("잉여/부족", f"{row['잉여/부족']:+,.0f}원",
                          delta_color="normal" if row["잉여/부족"] >= 0 else "inverse")

    # 예측 vs 실적 차트
    fig_act = go.Figure()
    fig_act.add_trace(go.Bar(
        x=actual_df["연월"], y=actual_df["총수입"]/10000,
        name="실제 수입", marker_color="#87CEEB",
        text=[f"{v/10000:.0f}만" for v in actual_df["총수입"]],
        textposition="outside",
    ))
    fig_act.add_trace(go.Bar(
        x=actual_df["연월"], y=actual_df["생활비"]/10000,
        name="실제 생활비", marker_color="rgba(255,75,75,0.6)",
        text=[f"{v/10000:.0f}만" for v in actual_df["생활비"]],
        textposition="outside",
    ))
    fig_act.add_hline(
        y=target_monthly/10000, line_dash="dot",
        line_color="#FFD700", line_width=1.5,
        annotation_text=f"목표 {target_monthly/10000:.0f}만원",
        annotation_font_color="#FFD700",
    )
    fig_act.update_layout(
        barmode="group", height=280,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(255,255,255,0.02)",
        font_color="white",
        legend=dict(orientation="h", y=-0.25, xanchor="center", x=0.5),
        margin=dict(t=20, b=70, l=10, r=10),
        yaxis=dict(title="만원", tickformat=","),
        xaxis=dict(tickangle=-30), hovermode="x unified",
    )
    st.plotly_chart(fig_act, use_container_width=True)

    # 누적 성과 요약
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("총 수입 누계",   f"{actual_df['총수입'].sum():,.0f}원")
    s2.metric("총 생활비 누계", f"{actual_df['생활비'].sum():,.0f}원")
    s3.metric("누적 잉여/부족", f"{actual_df['잉여/부족'].sum():+,.0f}원",
              delta_color="normal" if actual_df["잉여/부족"].sum() >= 0 else "inverse")
    s4.metric("월 평균 수입",   f"{actual_df['총수입'].mean():,.0f}원")


# ──────────────────────────────────────────────────────

# ── 관심종목 실시간 주가 수집 ─────────────────────────
def _normalize_code(code: str) -> str:
    """
    종목코드 → Yahoo Finance 형식 정규화.
    - 숫자 6자리 (KRX): 000660 → 000660.KS
    - 이미 점 포함: 0040Y0.KS → 그대로
    - 영문자 포함 (미국 주식): SNDK, PLTR, AAPL → 그대로 (USD 자동 환산)
    """
    code = str(code).strip().upper()
    if not code or code in ("NAN", "0", ""):
        return ""
    if "." in code:
        return code
    if code.isdigit():
        return code + ".KS"
    return code  # 영문 티커 → 미국 주식 (Yahoo에서 USD로 조회, 자동 환산)


@st.cache_data(ttl="30m", show_spinner=False)
def _fetch_usd_krw() -> float:
    """USD/KRW 환율 조회 (USDKRW=X). 실패 시 1,380원 반환."""
    try:
        import requests as _r
        res  = _r.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/USDKRW=X",
            headers={"User-Agent": "Mozilla/5.0"},
            params={"interval":"1d","range":"2d"}, timeout=6,
        )
        rate = float(res.json()["chart"]["result"][0]["meta"].get("regularMarketPrice",0))
        return rate if rate > 100 else 1380.0
    except Exception:
        return 1380.0


def _fetch_price_by_code(code: str) -> tuple[int, float, float]:
    """
    종목코드 → (현재가(원), 전일대비%, 전일대비금액) 반환.

    조회 순서:
      1. Yahoo Finance (KRX 순수숫자 코드 / 미국 주식 USD→원화 자동환산)
      2. Yahoo 실패 시 → 네이버 증권 polling API 폴백
         (KRX 혼합코드 0040Y0, 0018C0, 0177R0 등 Yahoo 미등록 종목 지원)
    """
    ycode = _normalize_code(code)
    if not ycode:
        return 0, 0.0, 0.0

    # ── 1. Yahoo Finance 시도 ────────────────────────────
    try:
        import requests as _req
        import datetime as _dt

        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ycode}"
        res = _req.get(
            url, headers={"User-Agent": "Mozilla/5.0"},
            params={"interval": "1d", "range": "5d"}, timeout=8,
        )
        data   = res.json()
        result = data["chart"]["result"][0]
        meta   = result["meta"]

        now_p_raw = float(meta.get("regularMarketPrice", 0))
        if now_p_raw > 0:
            # 통화 감지 → USD면 원화 환산
            currency = str(meta.get("currency", "KRW")).upper()
            fx = _fetch_usd_krw() if currency == "USD" else 1.0
            now_p = int(round(now_p_raw * fx))

            # 직전 거래일 종가
            now_kst = _dt.datetime.utcnow() + _dt.timedelta(hours=9)
            today_start_utc = int(
                _dt.datetime(now_kst.year, now_kst.month, now_kst.day)
                .replace(tzinfo=_dt.timezone.utc).timestamp()
            ) - 9 * 3600

            ts_list  = result.get("timestamp", [])
            cls_list = result["indicators"]["quote"][0].get("close", [])

            prev_p_raw = 0.0
            for ts, cl in zip(reversed(ts_list), reversed(cls_list)):
                if cl is None or cl <= 0: continue
                if ts < today_start_utc:
                    prev_p_raw = cl; break
            if prev_p_raw == 0:
                valid = [(t,cl) for t,cl in zip(ts_list,cls_list) if cl and cl>0]
                if len(valid) >= 2:
                    prev_p_raw = valid[-2][1]
            if prev_p_raw == 0:
                prev_p_raw = now_p_raw

            prev_p  = int(round(prev_p_raw * fx))
            chg_amt = now_p - prev_p
            chg_pct = (chg_amt / prev_p * 100) if prev_p > 0 else 0.0
            return now_p, round(chg_pct, 2), chg_amt
    except Exception:
        pass

    # ── 2. Yahoo 실패 → 네이버 증권 폴백 ────────────────
    # 순수 영문 티커(미국 주식)는 네이버 미지원이므로 건너뜀
    naver_code = _krx_to_naver_code(ycode)
    if naver_code:
        return _fetch_naver_price(naver_code)

    return 0, 0.0, 0.0


def _krx_to_naver_code(code: str) -> str:
    """
    KRX/Yahoo 코드 → 네이버 증권 코드 변환.
    네이버는 KRX 원본 코드 그대로 사용 (혼합코드 포함).
    예: 0018C0.KS → 0018C0 / 0040Y0.KS → 0040Y0
    순수 영문 티커(미국 주식) → "" (네이버 미지원)
    """
    base = str(code).strip().upper()
    base = base.replace(".KS", "").replace(".KQ", "")
    if not base or base in ("NAN", "0", ""):
        return ""
    if base.isalpha():   # 순수 영문 = 미국 주식 → 네이버 미지원
        return ""
    return base          # 숫자 단독 or 혼합코드 모두 OK


def _fetch_naver_price(naver_code: str) -> tuple[int, float, float]:
    """
    네이버 증권 polling API → (현재가, 전일대비%, 전일대비금액).
    Yahoo 미등록 KRX 혼합코드(0040Y0, 0018C0, 0177R0 등) 폴백 소스.
    """
    if not naver_code:
        return 0, 0.0, 0.0
    try:
        import requests as _req
        url = (
            f"https://polling.finance.naver.com/api/realtime"
            f"/domestic/stock/{naver_code}"
        )
        res = _req.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer":    "https://finance.naver.com",
            },
            timeout=6,
        )
        if res.status_code != 200:
            return 0, 0.0, 0.0

        d = res.json().get("datas", [{}])[0]
        now_p = int(str(d.get("closePrice", "0")).replace(",", "") or 0)
        if now_p == 0:
            return 0, 0.0, 0.0

        ctp     = d.get("compareToPreviousPrice", {})
        pct_str = str(ctp.get("fluctuationsRatio", "0")).replace(",", "").replace("%", "")
        amt_str = str(ctp.get("diff", "0")).replace(",", "").replace("+", "").replace("-", "")
        try:
            raw_pct = float(pct_str)
            raw_amt = int(amt_str)
        except ValueError:
            raw_pct, raw_amt = 0.0, 0

        # 등락 부호: code "2"=상승 "5"=하락 "3"=보합
        sign = str(ctp.get("code", "") or ctp.get("fluctuationType", "")).upper()
        if sign in ("5", "LOWER", "하락"):
            raw_pct, raw_amt = -abs(raw_pct), -abs(raw_amt)
        else:
            raw_pct, raw_amt = abs(raw_pct), abs(raw_amt)

        return now_p, round(raw_pct, 2), raw_amt
    except Exception:
        return 0, 0.0, 0.0


@st.cache_data(ttl="3m", show_spinner=False)
def fetch_watchlist_prices(codes: tuple) -> dict:
    """
    관심종목 코드 리스트 → {코드: (현재가, 전일대비%, 전일대비금액)} 딕셔너리.
    캐시 3분. 병렬 수집.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed as _asc
    results = {}
    with ThreadPoolExecutor(max_workers=min(len(codes), 8)) as ex:
        futures = {ex.submit(_fetch_price_by_code, c): c for c in codes}
        for fut in _asc(futures):
            c = futures[fut]
            try:
                results[c] = fut.result()
            except Exception:
                results[c] = (0, 0.0, 0.0)
    return results


@st.cache_data(ttl="10m", show_spinner=False)
def fetch_price_history(code: str, pages: int = 5) -> pd.DataFrame:
    """
    Yahoo Finance API → 일별 OHLCV DataFrame 반환.
    pages: 1=1개월, 5=3개월, 13=6개월 (하위 호환 매핑)
    """
    ycode = _normalize_code(code)
    if not ycode:
        return pd.DataFrame()

    # pages → 기간 매핑
    range_map = {1: "1mo", 2: "1mo", 3: "3mo", 4: "3mo",
                 5: "3mo", 6: "6mo", 13: "6mo"}
    yrange = range_map.get(pages, "3mo")

    try:
        import requests as _req
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ycode}"
        res = _req.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            params={"interval": "1d", "range": yrange},
            timeout=6,
        )
        data   = res.json()
        result = data["chart"]["result"][0]
        times  = result["timestamp"]
        ohlcv  = result["indicators"]["quote"][0]

        rows = []
        for i, ts in enumerate(times):
            try:
                rows.append({
                    "날짜":   pd.Timestamp(ts, unit="s", tz="Asia/Seoul").tz_localize(None),
                    "시가":   int(ohlcv["open"][i]  or 0),
                    "고가":   int(ohlcv["high"][i]  or 0),
                    "저가":   int(ohlcv["low"][i]   or 0),
                    "종가":   int(ohlcv["close"][i] or 0),
                    "거래량": int(ohlcv["volume"][i] or 0),
                })
            except Exception:
                continue

        df = pd.DataFrame(rows)
        df = df[df["종가"] > 0].sort_values("날짜").reset_index(drop=True)
        # 저가·고가가 0이면 종가로 대체 (데이터 누락 방지)
        if "저가" in df.columns:
            df["저가"] = df.apply(lambda r: r["종가"] if r["저가"] <= 0 else r["저가"], axis=1)
        if "고가" in df.columns:
            df["고가"] = df.apply(lambda r: r["종가"] if r["고가"] <= 0 else r["고가"], axis=1)
        return df
    except Exception:
        return pd.DataFrame()

# 관심종목 연금 특화 분석 데이터
# ──────────────────────────────────────────────────────
# ── 관심종목 분석 데이터 ─────────────────────────────────
# 키: 종목명 공백·특수문자 제거 소문자로 자동 매칭
# 동일 종목 다양한 이름 변형을 모두 등록
WATCHLIST_RESEARCH = {
    # KODEX 200 타겟위클리커버드콜
    "KODEX200타겟위클리커버드콜": {
        "유형": "커버드콜 ETF", "기초자산": "KOSPI 200",
        "분배주기": "월(17일)", "과세": "ISA 비과세 한도",
        "특징": ["KOSPI200 지수 추종으로 안정적 기반", "위클리 콜옵션 매도로 매월 분배금 창출", "횡보·완만한 상승장에서 KOSPI 대비 초과 수익"],
        "적합계좌": "ISA", "위험등급": "낮음",
    },
    # SOL 팔란티어 커버드콜 (다양한 이름 변형)
    "SOL팔란티어커버드콜OTM": {
        "유형": "커버드콜 ETF", "기초자산": "팔란티어(PLTR)",
        "분배주기": "월", "과세": "IRP 5.5%",
        "특징": ["AI·방산 테마 직접 노출", "OTM 커버드콜로 상승 여지 확보", "월분배금 안정성과 성장성 균형"],
        "적합계좌": "IRP", "위험등급": "중간",
    },
    "SOL팔란티어커버드콜OTM채권혼합": {
        "유형": "커버드콜+채권 혼합 ETF", "기초자산": "팔란티어(PLTR) + 채권",
        "분배주기": "월", "과세": "IRP 5.5%",
        "특징": ["팔란티어 커버드콜 + 채권 혼합으로 변동성 완화", "채권 비중으로 하방 방어력 강화", "IRP 내 위험자산 한도 절감 효과"],
        "적합계좌": "IRP", "위험등급": "낮음~중간",
    },
    # RISE 삼성전자SK하이닉스채권혼합50
    "RISE삼성전자SK하이닉스채권혼합50": {
        "유형": "주식+채권 혼합 ETF", "기초자산": "삼성전자+SK하이닉스+채권",
        "분배주기": "월", "과세": "ISA 비과세 한도",
        "특징": ["반도체 대표주 + 채권 50:50 혼합", "HBM·AI 반도체 수혜 노출", "채권으로 변동성 절반 축소"],
        "적합계좌": "ISA", "위험등급": "낮음~중간",
    },
    # SOL AI반도체TOP2플러스
    "SOLAI반도체TOP2플러스": {
        "유형": "테마 ETF", "기초자산": "엔비디아+TSMC 등 AI반도체",
        "분배주기": "월", "과세": "ISA 비과세 한도",
        "특징": ["AI 인프라 핵심 반도체 집중 투자", "글로벌 AI 수요 성장 직접 수혜", "고성장·고변동성 테마"],
        "적합계좌": "ISA", "위험등급": "높음",
    },
    # SOL 미국배당미국채혼합50
    "SOL미국배당미국채혼합50": {
        "유형": "배당+채권 혼합 ETF", "기초자산": "미국배당주 + 미국채",
        "분배주기": "월", "과세": "ISA 비과세 한도",
        "특징": ["미국 고배당주 + 미국채 50:50", "달러 자산 분산 효과", "금리 인하 시 채권 평가익 기대"],
        "적합계좌": "ISA", "위험등급": "낮음",
    },
    # TIGER 미국배당다우존스
    "TIGER미국배당다우존스": {
        "유형": "배당 ETF", "기초자산": "다우존스 배당지수",
        "분배주기": "월", "과세": "IRP 5.5%",
        "특징": ["다우존스 고배당 우량주 집중", "달러 환노출로 환헤지 비용 없음", "안정적 배당 성장 기업 선별"],
        "적합계좌": "IRP/ISA", "위험등급": "낮음",
    },
    # ACE 미국10년국채액티브
    "ACE미국10년국채액티브": {
        "유형": "채권 ETF", "기초자산": "미국 10년 국채",
        "분배주기": "월", "과세": "IRP 5.5%",
        "특징": ["미국 10년물 국채 액티브 운용", "금리 인하 사이클 수혜", "포트폴리오 안전자산 역할"],
        "적합계좌": "IRP", "위험등급": "낮음",
    },
    "ACE미국30년국채액티브": {
        "유형": "채권 ETF", "기초자산": "미국 30년 국채",
        "분배주기": "월", "과세": "ISA 비과세 한도",
        "특징": ["30년 장기채로 금리 인하 시 높은 평가익", "포트폴리오 방어 역할", "높은 듀레이션·높은 변동성"],
        "적합계좌": "ISA", "위험등급": "중간",
    },
    # KODEX 머니마켓액티브
    "KODEX머니마켓액티브": {
        "유형": "머니마켓 ETF", "기초자산": "단기 채권·RP",
        "분배주기": "년", "과세": "일반 15.4%",
        "특징": ["원금 보존 우선 단기 운용", "시장 대기 자금 운용 최적화", "낮은 수익률·낮은 위험"],
        "적합계좌": "일반", "위험등급": "매우 낮음",
    },
}


def _match_watchlist_research(name: str) -> dict | None:
    """
    종목명을 공백·특수문자 제거 후 WATCHLIST_RESEARCH와 매칭.
    완전 일치 → 부분 일치 순으로 탐색.
    """
    if not name:
        return None
    # 정규화: 공백·특수문자 제거
    norm = str(name).replace(" ", "").replace("　", "").upper()

    # 완전 일치
    for key, val in WATCHLIST_RESEARCH.items():
        if norm == key.upper():
            return val

    # 부분 일치 (키가 정규화 이름에 포함되거나 반대)
    for key, val in WATCHLIST_RESEARCH.items():
        k = key.upper()
        if k in norm or norm in k:
            return val

    return None



def _render_holdings_tab(
    pension_items: dict,
    sc_df: pd.DataFrame,
    sc_choice: str,
    wl_df: pd.DataFrame,
    irp_total: float,
    isa_total: float,
    gen_total: float,
    ps_total: float = 0.0,
):
    """📈 보유종목 탭 — 연금계좌 실시간 현황"""
    import plotly.graph_objects as go
    import plotly.express as px

    # ── 데이터 준비 ──────────────────────────────────────
    all_items = []
    for acc_kr, items in pension_items.items():
        for it in items:
            nm   = str(it.get("종목명","")).strip()
            code = str(it.get("종목코드","")).strip()
            qty  = float(it.get("수량", 0) or 0)
            dps  = float(it.get("주당분배금", 0) or 0)
            amt  = float(it.get("원금", 0) or 0)
            rate = float(it.get("분배율(%)", 0) or 0)
            if not nm or nm in ("nan",""):
                continue
            monthly = qty * dps if qty > 0 and dps > 0 else amt * rate / 100
            all_items.append({
                "계좌": acc_kr, "종목명": nm, "종목코드": code,
                "수량": int(qty), "주당분배금": int(dps),
                "매입금액": amt, "분배율(%)": rate, "월분배금": monthly,
            })

    if not all_items:
        st.info("연금현황 시트에 보유종목을 입력하면 여기에 표시됩니다.")
        return

    port_df = pd.DataFrame(all_items)

    # ── 실시간 주가 조회 (fetch_watchlist_prices 재사용) ─
    codes = []
    for r in all_items:
        raw_c = str(r["종목코드"]).strip()
        if raw_c and raw_c not in ("nan","0",""):
            norm_c = _normalize_code(raw_c)
            if norm_c:
                codes.append(norm_c)
    codes = list(set(codes))

    prices_raw = {}
    if codes:
        with st.spinner(f"실시간 주가 조회 중... ({len(codes)}종목)"):
            try:
                prices_raw = fetch_watchlist_prices(tuple(codes))
            except Exception as _e:
                st.caption(f"주가 조회 오류: {_e}")

    # prices_raw: {코드: (현재가, 전일대비%, 전일대비금액)}
    prices_map   = {k: v[0] for k, v in prices_raw.items() if v[0] > 0}
    prev_pct_map = {k: v[1] for k, v in prices_raw.items()}
    prev_amt_map = {k: v[2] for k, v in prices_raw.items()}

    # 조회 결과 캡션 표시
    _ok_cnt = len([v for v in prices_map.values() if v > 0])
    if codes:
        st.caption(f"📡 실시간 주가 연동: {_ok_cnt}/{len(codes)}종목 "
                   f"{'✅' if _ok_cnt == len(codes) else '⚠️ 일부 실패'}")

    # 주가 반영 → 평가금액·손익 계산
    rows = []
    for it in all_items:
        raw_code = it["종목코드"]
        code     = _normalize_code(raw_code)
        price    = prices_map.get(code, 0) or prices_map.get(raw_code, 0)
        day_pct  = prev_pct_map.get(code, 0.0) or prev_pct_map.get(raw_code, 0.0)
        day_amt  = prev_amt_map.get(code, 0) or prev_amt_map.get(raw_code, 0)
        qty      = it["수량"]
        dps      = it["주당분배금"]
        amt      = it["매입금액"]
        rate     = it["분배율(%)"]
        monthly  = it["월분배금"]
        eval_amt = qty * price if qty > 0 and price > 0 else amt
        gain     = eval_amt - amt if price > 0 and qty > 0 else 0
        gain_pct = (gain / amt * 100) if amt > 0 else 0
        day_diff = day_amt * qty if day_amt != 0 and qty > 0 else 0
        rows.append({
            **it,
            "매입단가":      int(amt / qty) if qty > 0 else 0,
            "현재가":        int(price) if price > 0 else 0,
            "평가금액":      int(eval_amt),
            "손익":          int(gain),
            "전일대비(원)":  int(day_diff),
            "전일대비(%)":   round(day_pct, 2),
            "누적수익률(%)": round(gain_pct, 2),
            "월분배금":      int(monthly),
            "분배율(%)":     rate,
        })
    disp_df = pd.DataFrame(rows)

    # ══════════════════════════════════════════════════════
    # 1-A. 전체 합계 카드 (1행)
    # ══════════════════════════════════════════════════════
    _total_eval = disp_df["평가금액"].sum()
    _total_buy  = disp_df["매입금액"].sum()
    _total_gain = disp_df["손익"].sum()
    _total_ret  = (_total_gain / _total_buy * 100) if _total_buy > 0 else 0
    _total_day  = disp_df["전일대비(원)"].sum()
    _total_day_pct = (
        _total_day / (_total_eval - _total_day) * 100
        if (_total_eval - _total_day) > 0 else 0
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("계좌 평가액", f"{_total_eval:,.0f}원",
              delta=f"{_total_day:+,.0f}원 ({_total_day_pct:+.2f}%)",
              delta_color="normal" if _total_day >= 0 else "inverse")
    c2.metric("계좌 매입액", f"{_total_buy:,.0f}원")
    c3.metric("계좌 손익",   f"{_total_gain:+,.0f}원",
              delta_color="normal" if _total_gain >= 0 else "inverse")
    c4.metric("계좌 수익률", f"{_total_ret:+.2f}%",
              delta_color="normal" if _total_ret >= 0 else "inverse")

    # ══════════════════════════════════════════════════════
    # 1-B. 계좌별 소계 카드 (2행 — IRP→연금저축→ISA→일반)
    # ══════════════════════════════════════════════════════
    _ACC_ORDER = ["IRP", "연금저축", "ISA", "일반"]
    _ACC_COLOR = {
        "IRP":    "#87CEEB",
        "연금저축": "#FFD700",
        "ISA":    "#7dffb0",
        "일반":   "#AFA9EC",
    }
    _ACC_BG = {
        "IRP":    "rgba(135,206,235,0.12)",
        "연금저축": "rgba(255,215,0,0.10)",
        "ISA":    "rgba(125,255,176,0.10)",
        "일반":   "rgba(175,169,236,0.10)",
    }

    # 계좌별 소계 집계
    _acc_grp = (
        disp_df.groupby("계좌")
        .agg(평가금액=("평가금액","sum"), 매입금액=("매입금액","sum"), 손익=("손익","sum"))
        .reset_index()
    )
    _sorted_accs = sorted(
        _acc_grp.to_dict("records"),
        key=lambda r: _ACC_ORDER.index(r["계좌"]) if r["계좌"] in _ACC_ORDER else 99,
    )

    if _sorted_accs:
        _acols = st.columns(len(_sorted_accs))
        for _ai, _ar in enumerate(_sorted_accs):
            _an = _ar["계좌"]
            _ae, _ab, _ag = _ar["평가금액"], _ar["매입금액"], _ar["손익"]
            _ar2 = (_ag / _ab * 100) if _ab > 0 else 0
            _bc  = _ACC_COLOR.get(_an, "#AFA9EC")
            _bg  = _ACC_BG.get(_an, "rgba(175,169,236,0.10)")
            _gc  = "#7dffb0" if _ag >= 0 else "#FF4B4B"
            with _acols[_ai]:
                st.markdown(
                    f"<div style='background:{_bg};border:1px solid rgba(255,255,255,0.08);"
                    f"border-top:3px solid {_bc};border-radius:8px;"
                    f"padding:10px 12px;margin-top:10px;'>"
                    f"<div style='font-size:0.72rem;font-weight:700;color:{_bc};"
                    f"margin-bottom:6px;'>{_an}</div>"
                    f"<div style='font-size:0.78rem;color:rgba(255,255,255,0.45);"
                    f"margin-bottom:1px;'>평가액</div>"
                    f"<div style='font-size:0.92rem;font-weight:600;margin-bottom:3px;'>"
                    f"{_ae:,.0f}원</div>"
                    f"<div style='font-size:0.72rem;color:rgba(255,255,255,0.35);'>"
                    f"매입 {_ab:,.0f}원</div>"
                    f"<div style='font-size:0.80rem;color:{_gc};margin-top:4px;"
                    f"font-weight:600;'>{_ag:+,.0f}원 ({_ar2:+.2f}%)</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

    # ══════════════════════════════════════════════════════
    # 2-A. 계좌별 구분행 포함 HTML 테이블
    #       IRP→연금저축→ISA→일반 고정 정렬
    #       계좌 배지 컬럼 + 구분 헤더행(소계)
    # ══════════════════════════════════════════════════════
    st.divider()

    # 계좌 순서 고정 정렬
    disp_df["_acc_ord"] = disp_df["계좌"].apply(
        lambda x: _ACC_ORDER.index(x) if x in _ACC_ORDER else 99
    )
    disp_df = disp_df.sort_values(["_acc_ord", "종목명"]).drop(columns=["_acc_ord"])

    _NUM_C = {"수량","매입단가","매입금액","현재가","평가금액",
              "손익","전일대비(원)","전일대비(%)","누적수익률(%)",
              "주당분배금","월분배금","분배율(%)"}
    _CLR_C = {"손익","전일대비(원)","전일대비(%)","누적수익률(%)"}

    def _cv(v, col):
        if col in _CLR_C and isinstance(v, (int, float)):
            if v > 0: return "color:#FF4B4B;font-weight:600"
            if v < 0: return "color:#4B9EFF;font-weight:600"
        return ""

    def _fv(v, col):
        if not isinstance(v, (int, float)):
            return str(v)
        if col in ("손익","전일대비(원)"):          return f"{v:+,.0f}"
        if col in ("전일대비(%)","누적수익률(%)","분배율(%)"): return f"{v:+.2f}%"
        if col in ("수량","매입단가","매입금액","현재가","평가금액","주당분배금","월분배금"):
            return f"{v:,.0f}"
        return str(v)

    _COLS  = ["계좌","종목명","수량","매입단가","매입금액","현재가",
              "평가금액","손익","전일대비(원)","전일대비(%)","누적수익률(%)",
              "주당분배금","월분배금","분배율(%)"]
    _HEADS = ["계좌","종목명","수량","매입단가","매입금액","현재가",
              "평가금액","손익","전일대비(원)","전일대비(%)","수익률(%)",
              "주당분배금","월분배금","분배율(%)"]

    _TH  = ("background:rgba(255,255,255,0.06);padding:7px 10px;"
            "font-size:0.75rem;font-weight:600;color:rgba(255,255,255,0.5);"
            "border-bottom:1px solid rgba(255,255,255,0.1);white-space:nowrap;")
    _TR  = "border-bottom:0.5px solid rgba(255,255,255,0.06);"
    _SEP = ("background:rgba(255,255,255,0.03);padding:5px 10px;"
            "font-size:0.74rem;color:rgba(255,255,255,0.5);"
            "border-bottom:1px solid rgba(255,255,255,0.18);")

    _rows_html = []
    _prev_acc  = None
    for _, _row in disp_df.iterrows():
        _acc = str(_row.get("계좌", ""))

        # 계좌 구분 헤더행 삽입
        if _acc != _prev_acc:
            _rg = _acc_grp[_acc_grp["계좌"] == _acc]
            if not _rg.empty:
                _ge = int(_rg["평가금액"].iloc[0])
                _gb = int(_rg["매입금액"].iloc[0])
                _gg = int(_rg["손익"].iloc[0])
                _gr = (_gg / _gb * 100) if _gb > 0 else 0
                _gc2 = "#7dffb0" if _gg >= 0 else "#FF4B4B"
                _bc2 = _ACC_COLOR.get(_acc, "#AFA9EC")
                _bg2 = _ACC_BG.get(_acc, "rgba(175,169,236,0.10)")
                _rows_html.append(
                    f'<tr><td colspan="{len(_COLS)}" style="{_SEP}">'
                    f'<span style="font-size:0.70rem;font-weight:700;padding:2px 8px;'
                    f'border-radius:4px;background:{_bg2};color:{_bc2};">{_acc}</span>'
                    f'&nbsp;&nbsp;평가 {_ge:,.0f}원&nbsp;·&nbsp;매입 {_gb:,.0f}원&nbsp;·&nbsp;'
                    f'손익 <span style="color:{_gc2};font-weight:600;">'
                    f'{_gg:+,.0f}원 ({_gr:+.2f}%)</span>'
                    f'</td></tr>'
                )
            _prev_acc = _acc

        # 종목 행
        cells = []
        for col in _COLS:
            v  = _row.get(col, "")
            al = "right" if col in _NUM_C else "left"
            td = f"padding:6px 10px;font-size:0.82rem;text-align:{al};white-space:nowrap;"
            if col == "계좌":
                _bc3 = _ACC_COLOR.get(_acc, "#AFA9EC")
                _bg3 = _ACC_BG.get(_acc, "rgba(175,169,236,0.10)")
                cells.append(
                    f'<td style="{td}">'
                    f'<span style="font-size:0.69rem;font-weight:700;padding:2px 7px;'
                    f'border-radius:4px;background:{_bg3};color:{_bc3};">{_acc}</span>'
                    f'</td>'
                )
            else:
                cells.append(f'<td style="{td}{_cv(v, col)}">{_fv(v, col)}</td>')
        _rows_html.append(f'<tr style="{_TR}">{"".join(cells)}</tr>')

    _hdr_html = "".join(
        f'<th style="{_TH}text-align:{"right" if h in _NUM_C else "left"}">{h}</th>'
        for h in _HEADS
    )
    st.markdown(
        f'<div style="overflow-x:auto;border:1px solid rgba(255,255,255,0.1);'
        f'border-radius:8px;margin-bottom:12px;">'
        f'<table style="width:100%;border-collapse:collapse;">'
        f'<thead><tr>{_hdr_html}</tr></thead>'
        f'<tbody>{"".join(_rows_html)}</tbody>'
        f'</table></div>',
        unsafe_allow_html=True,
    )

    # ══════════════════════════════════════════════════════
    # 2-B. 소팅 가능한 st.dataframe (컬럼 헤더 클릭 정렬)
    # ══════════════════════════════════════════════════════
    st.caption("💡 아래 테이블은 컬럼 헤더 클릭으로 오름/내림차순 정렬이 가능합니다.")

    _color_cols = list(_CLR_C)

    def _style_pnl(df):
        styles = pd.DataFrame("", index=df.index, columns=df.columns)
        for col in _color_cols:
            if col in df.columns:
                styles[col] = df[col].apply(
                    lambda v: "color: #FF4B4B; font-weight:600"
                    if isinstance(v, (int, float)) and v > 0
                    else ("color: #4B9EFF; font-weight:600"
                          if isinstance(v, (int, float)) and v < 0
                          else "color: rgba(255,255,255,0.4)")
                )
        return styles

    def _fmt(df):
        fmt = {}
        for col in df.columns:
            if col in ("손익","전일대비(원)"):
                fmt[col] = lambda v: f"{v:+,.0f}" if isinstance(v,(int,float)) else v
            elif col in ("전일대비(%)","누적수익률(%)","분배율(%)"):
                fmt[col] = lambda v: f"{v:+.2f}%" if isinstance(v,(int,float)) else v
            elif col in ("수량","매입단가","매입금액","현재가","평가금액","주당분배금","월분배금"):
                fmt[col] = lambda v: f"{v:,.0f}" if isinstance(v,(int,float)) else v
        return fmt

    tbl_cols = list(_COLS)
    _styled = (
        disp_df[tbl_cols]
        .style
        .apply(_style_pnl, axis=None)
        .format(_fmt(disp_df[tbl_cols]))
    )
    st.dataframe(
        _styled,
        hide_index=True,
        use_container_width=True,
        column_config={
            "계좌":          st.column_config.TextColumn("계좌",      width="small"),
            "수량":          st.column_config.NumberColumn("수량",     format="%,.0f"),
            "매입단가":      st.column_config.NumberColumn("매입단가", format="%,.0f"),
            "매입금액":      st.column_config.NumberColumn("매입금액", format="%,.0f"),
            "현재가":        st.column_config.NumberColumn("현재가",   format="%,.0f"),
            "평가금액":      st.column_config.NumberColumn("평가금액", format="%,.0f"),
            "손익":          st.column_config.NumberColumn("손익",     format="%+,.0f"),
            "전일대비(원)":  st.column_config.NumberColumn("전일대비(원)", format="%+,.0f"),
            "전일대비(%)":   st.column_config.NumberColumn("전일대비(%)",  format="%+.2f%%"),
            "누적수익률(%)": st.column_config.NumberColumn("수익률(%)",    format="%+.2f%%"),
            "주당분배금":    st.column_config.NumberColumn("주당분배금", format="%,.0f"),
            "월분배금":      st.column_config.NumberColumn("월분배금",  format="%,.0f"),
            "분배율(%)":     st.column_config.NumberColumn("분배율(%)", format="%.2f%%"),
        },
    )

    # 종목명 목록 (selectbox는 종목 상세 섹션 제목 옆으로 이동)
    _nm_list    = disp_df["종목명"].tolist()
    _sel_nm_key = st.session_state.get("hld_sel_nm", "")
    if _sel_nm_key not in _nm_list:
        _sel_nm_key = _nm_list[0] if _nm_list else ""

    # ══════════════════════════════════════════════════════
    # 3. 분배금 요약 카드
    # ══════════════════════════════════════════════════════
    st.divider()
    _total_monthly = port_df["월분배금"].sum()
    _avg_rate      = (_total_monthly / _total_buy * 100) if _total_buy > 0 else 0

    d1, d2, d3, d4 = st.columns(4)
    d1.metric("총 투자원금",    f"{_total_buy/100_000_000:.2f}억원")
    d2.metric("월 예상 분배금", f"{_total_monthly:,.0f}원")
    d3.metric("연 예상 분배금", f"{_total_monthly*12:,.0f}원")
    d4.metric("평균 월 분배율", f"{_avg_rate:.2f}%")

    # ══════════════════════════════════════════════════════
    # 4. 차트
    # ══════════════════════════════════════════════════════
    st.divider()
    ch1, ch2 = st.columns(2)
    with ch1:
        st.markdown("**계좌별 원금 배분**")
        acc_grp = port_df.groupby("계좌")["매입금액"].sum().reset_index()
        fig_pie = px.pie(
            acc_grp, values="매입금액", names="계좌", hole=0.4,
            color_discrete_sequence=["#FFD700","#FF4B4B","#87CEEB","#5DCAA5"],
        )
        fig_pie.update_layout(
            height=240, paper_bgcolor="rgba(0,0,0,0)",
            font_color="white", margin=dict(t=10,b=0,l=0,r=0),
        )
        st.plotly_chart(fig_pie, use_container_width=True)

    with ch2:
        st.markdown("**종목별 월 분배금**")
        dist_s = port_df.sort_values("월분배금", ascending=True)
        fig_bar = go.Figure(go.Bar(
            x=dist_s["월분배금"] / 10000,
            y=dist_s["종목명"].str[:12],
            orientation="h",
            marker_color="#FFD700",
            text=[f"{v/10000:.1f}만" for v in dist_s["월분배금"]],
            textposition="outside",
        ))
        fig_bar.update_layout(
            height=240, paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(255,255,255,0.02)",
            font_color="white", margin=dict(t=10,b=10,l=10,r=60),
        )
        st.plotly_chart(fig_bar, use_container_width=True)

    # ══════════════════════════════════════════════════════
    # 5. 종목 상세 분석 — 제목 옆에 selectbox 배치
    # ══════════════════════════════════════════════════════
    st.divider()
    _hdr_col, _sel_col = st.columns([2, 3])
    _hdr_col.markdown("**🔍 종목 상세**")
    _sel_nm = _sel_col.selectbox(
        "종목 선택",
        _nm_list,
        index=_nm_list.index(_sel_nm_key) if _sel_nm_key in _nm_list else 0,
        key="hld_sel_nm",
        label_visibility="collapsed",
        placeholder="종목을 선택하세요...",
    )

    if _sel_nm and len(disp_df) > 0:
        _row     = disp_df[disp_df["종목명"] == _sel_nm].iloc[0]
        _code    = _normalize_code(str(_row.get("종목코드","")))
        res_data = _match_watchlist_research(_sel_nm)
        _acc_kr  = str(_row.get("계좌","IRP"))
        _acc_tot = {"IRP": irp_total,"ISA": isa_total,"일반": gen_total,"연금저축": ps_total}.get(_acc_kr, 0)
        _tax_r   = IRP_TAX_RATE if _acc_kr in ["IRP","연금저축"] else 0.099
        _qty     = float(_row.get("수량", 0))
        _dps     = float(_row.get("주당분배금", 0))
        _rate    = float(_row.get("분배율(%)", 0))
        _monthly_gross = _qty * _dps if _qty > 0 and _dps > 0 else _acc_tot * _rate / 100
        _monthly_net   = _monthly_gross * (1 - _tax_r)

        e1, e2 = st.columns(2)
        with e1:
            with st.container(border=True):
                st.markdown("**📋 종목 특성**")
                if res_data:
                    st.markdown(
                        f"<div style='font-size:0.85rem; line-height:1.8;'>"
                        f"유형: <b>{res_data['유형']}</b><br>"
                        f"기초자산: <b>{res_data['기초자산']}</b><br>"
                        f"분배주기: <b>{res_data['분배주기']}</b><br>"
                        f"과세: <b>{res_data['과세']}</b><br>"
                        f"추천계좌: <b style='color:#FFD700;'>{res_data['적합계좌']}</b><br>"
                        f"위험등급: <b>{res_data['위험등급']}</b>"
                        f"</div>", unsafe_allow_html=True,
                    )
                    st.divider()
                    st.markdown("**💡 주요 특징**")
                    for feat in res_data.get("특징",[]):
                        st.markdown(f"- {feat}")
                else:
                    st.caption("등록된 분석 데이터가 없습니다.")
                    st.markdown(
                        f"계좌: **{_acc_kr}**  |  "
                        f"분배율: **{_rate:.2f}%**  |  "
                        f"주당분배금: **{int(_dps):,}원**"
                    )

        with e2:
            with st.container(border=True):
                st.markdown("**💰 수익 분석**")
                st.markdown(
                    f"<div style='font-size:0.85rem; line-height:2.0;'>"
                    f"계좌: <b>{_acc_kr}</b> (잔액 {_acc_tot/100_000_000:.2f}억원)<br>"
                    f"보유수량: <b>{int(_qty):,}주</b>  ×  "
                    f"주당분배금: <b>{int(_dps):,}원</b><br>"
                    f"월 세전 분배금: <b>{_monthly_gross:,.0f}원</b><br>"
                    f"세금(-{_tax_r*100:.1f}%): <b>-{_monthly_gross*_tax_r:,.0f}원</b><br>"
                    f"월 세후 분배금: <b style='color:#7dffb0;'>{_monthly_net:,.0f}원</b><br>"
                    f"연 세후 분배금: <b style='color:#7dffb0;'>{_monthly_net*12:,.0f}원</b>"
                    f"</div>", unsafe_allow_html=True,
                )
                # 현재 보유 손익
                _eval = int(_row.get("평가금액", 0))
                _buy  = int(_row.get("매입금액", 0))
                _gain = int(_row.get("손익", 0))
                _ret  = float(_row.get("누적수익률(%)", 0))
                _gc   = "#7dffb0" if _gain >= 0 else "#FF4B4B"
                st.divider()
                st.markdown(
                    f"<div style='font-size:0.85rem; line-height:1.8;'>"
                    f"매입금액: <b>{_buy:,.0f}원</b><br>"
                    f"평가금액: <b>{_eval:,.0f}원</b><br>"
                    f"평가손익: <b style='color:{_gc};'>{_gain:+,.0f}원 "
                    f"({_ret:+.2f}%)</b>"
                    f"</div>", unsafe_allow_html=True,
                )

        # ── 주가 추이 차트 ──────────────────────────────
        if _code and _code not in ("","nan","0"):
            with st.container(border=True):
                _chart_cols = st.columns([4,1])
                with _chart_cols[1]:
                    _period = st.radio(
                        "기간", ["1개월","3개월","6개월"],
                        index=1, key="hld_chart_period", horizontal=False,
                    )
                _pages_map = {"1개월":2,"3개월":5,"6개월":13}
                hist_df = fetch_price_history(_code, pages=_pages_map.get(_period,5))

                with _chart_cols[0]:
                    if hist_df.empty:
                        st.caption(f"주가 데이터 조회 실패 — 종목코드: {_code}")
                    else:
                        _curr_p = int(_row.get("현재가", 0))
                        _tgt_p  = 0
                        # 관심종목 탭에 목표가 있으면 가져오기
                        if not wl_df.empty and "목표가" in wl_df.columns:
                            _wl_r = wl_df[wl_df["종목명"]==_sel_nm]
                            if not _wl_r.empty:
                                _tgt_p = float(_wl_r.iloc[0].get("목표가",0) or 0)

                        _last_p  = hist_df["종가"].iloc[-1]
                        _first_p = hist_df["종가"].iloc[0]
                        _chg_pct = (_last_p/_first_p-1)*100 if _first_p>0 else 0
                        _lc      = "#7dffb0" if _chg_pct>=0 else "#FF4B4B"

                        fig_h = go.Figure()
                        fig_h.add_trace(go.Scatter(
                            x=hist_df["날짜"], y=hist_df["종가"],
                            mode="lines", name="종가",
                            line=dict(color=_lc, width=2),
                            fill="tozeroy",
                            fillcolor=f"rgba({','.join(str(int(_lc[i:i+2],16)) for i in [1,3,5])},0.08)",
                        ))
                        if _tgt_p > 0:
                            fig_h.add_hline(
                                y=_tgt_p, line_dash="dot",
                                line_color="#FFD700", line_width=1.5,
                                annotation_text=f"목표 {_tgt_p:,.0f}원",
                                annotation_position="top right",
                                annotation_font_color="#FFD700",
                            )
                        if _curr_p > 0 and _curr_p != int(_last_p):
                            fig_h.add_hline(
                                y=_curr_p, line_dash="dash",
                                line_color="rgba(255,255,255,0.4)", line_width=1,
                                annotation_text=f"현재 {_curr_p:,.0f}원",
                                annotation_position="bottom right",
                                annotation_font_color="rgba(255,255,255,0.5)",
                            )
                        _low_s  = hist_df["저가"][hist_df["저가"] > 0] if "저가" in hist_df.columns else hist_df["종가"]
                        _high_s = hist_df["고가"][hist_df["고가"] > 0] if "고가" in hist_df.columns else hist_df["종가"]
                        _y_min  = float(_low_s.min())  if len(_low_s)  > 0 else float(hist_df["종가"].min())
                        _y_max  = float(_high_s.max()) if len(_high_s) > 0 else float(hist_df["종가"].max())
                        # 목표가는 Y축 범위에서 제외 (수평선으로만 표시)
                        _y_range = _y_max - _y_min
                        _pad = max(_y_range * 0.08, _y_max * 0.005)

                        fig_h.update_layout(
                            title=dict(
                                text=f"{_sel_nm[:20]}  "
                                     f"<span style='font-size:0.9rem; color:{_lc};'>"
                                     f"{_chg_pct:+.1f}% ({_period})</span>",
                                font_size=14,
                            ),
                            height=280,
                            paper_bgcolor="rgba(0,0,0,0)",
                            plot_bgcolor="rgba(255,255,255,0.02)",
                            font_color="white",
                            margin=dict(t=40,b=30,l=10,r=90),
                            yaxis=dict(
                                range=[max(0,_y_min-_pad), _y_max+_pad],
                                tickformat=",",
                            ),
                            xaxis=dict(showgrid=False),
                        )
                        st.plotly_chart(fig_h, use_container_width=True)

                        # 시작가·현재가·고가·저가
                        p1,p2,p3,p4 = st.columns(4)
                        p1.metric("시작가", f"{int(_first_p):,}원")
                        p2.metric("현재가", f"{int(_last_p):,}원",
                                  delta=f"{_chg_pct:+.1f}%",
                                  delta_color="normal" if _chg_pct>=0 else "inverse")
                        if "고가" in hist_df.columns:
                            p3.metric("고가", f"{int(hist_df['고가'].max()):,}원")
                            p4.metric("저가", f"{int(hist_df['저가'].min()):,}원")


def _calc_net_dist(row, dist_tax_df) -> float:
    """
    종목·계좌별 세후분배금 계산 — 과세표준 기반.

    과세 원칙:
      IRP / 연금저축 : 과세표준 × 5.5% (연금소득세, 과세이연 후 수령 시)
      ISA           : 과세표준 × 9.9% (비과세한도 초과분 분리과세)
      일반계좌       : 과세표준 × 15.4% (배당소득세)

    과세표준 조회 우선순위:
      1순위: 분배금과세 시트의 '과세표준(원)' — 종목명 매칭
      2순위: 시트 없거나 미입력 → 분배금 전액을 과세표준으로 보수적 처리

    ETF 유형별 특성:
      - 커버드콜 ETF: 옵션 프리미엄 부분 비과세 → 과세표준 < 분배금
      - 채권혼합형:   채권이자 과세 → 과세표준 ≤ 분배금
      - 국내주식형:   배당소득 전액 과세표준
    """
    import pandas as pd

    dist_amt = float(row.get("월분배금", 0) or 0)
    if dist_amt <= 0:
        return 0.0

    acc     = str(row.get("계좌", "") or "")
    nm      = str(row.get("종목명", "") or "")
    qty     = float(row.get("수량", 0) or 0)

    # ── 과세표준 조회 ──────────────────────────────────────
    # 주당과세표준 × 수량 = 과세표준 합계
    taxbase_amt = dist_amt  # 기본값: 전액 과세 (보수적)

    if dist_tax_df is not None and not dist_tax_df.empty and qty > 0:
        def _n(s): return str(s).strip().replace(" ", "").upper()
        nm_n = _n(nm)
        # 계좌 조건도 매칭 (같은 종목이 복수 계좌에 있을 수 있음)
        mask = dist_tax_df["종목명"].astype(str).apply(_n) == nm_n
        if "계좌" in dist_tax_df.columns:
            acc_mask = dist_tax_df["계좌"].astype(str).str.strip() == acc
            if (mask & acc_mask).any():
                mask = mask & acc_mask
        if mask.any():
            row_sh = dist_tax_df[mask].iloc[-1]  # 최신 행 사용
            dps      = float(row_sh.get("분배금(원)",  0) or 0)
            tax_dps  = float(row_sh.get("과세표준(원)", 0) or 0)
            if dps > 0:
                # 과세표준 비율을 실제 분배금에 적용
                tax_ratio   = tax_dps / dps          # 예: 60/350 = 0.171
                taxbase_amt = dist_amt * tax_ratio   # 실제분배금 × 과세비율

    # ── 계좌별 세율 적용 ──────────────────────────────────
    if acc in ("IRP", "연금저축"):
        # 연금소득세 5.5% (과세이연 후 수령 시)
        tax = taxbase_amt * IRP_TAX_RATE
    elif acc == "ISA":
        # 9.9% 분리과세 (비과세한도 초과분)
        tax = taxbase_amt * 0.099
    else:
        # 일반계좌: 배당소득세 15.4%
        tax = taxbase_amt * 0.154

    return dist_amt - tax


def _render_watchlist_tab(
    wl_df: pd.DataFrame,
    irp_total: float,
    isa_total: float,
    general_total: float,
    ps_total: float,
    public_pension: float,
    target_monthly: float,
    show_tax: bool,
    sc_df: pd.DataFrame,
    sc_names: list,
    dist_tax_df: pd.DataFrame = None,   # 분배금과세 시트 — 과세표준 기반 세금 계산용
):
    """🔍 관심종목 탭 — 5개 섹션"""
    st.markdown("#### 🔍 관심종목 연금 포트폴리오 분석")

    # ── 실시간 주가 수집 ─────────────────────────────────
    _has_code = (not wl_df.empty and "종목코드" in wl_df.columns
                 and wl_df["종목코드"].astype(str).str.strip().ne("").any())
    price_map = {}
    if _has_code:
        _codes = tuple(
            _normalize_code(c)
            for c in wl_df["종목코드"].dropna()
            if _normalize_code(str(c)) not in ("", "nan", "0")
        )
        if _codes:
            with st.spinner("실시간 주가 수집 중..."):
                price_map = fetch_watchlist_prices(_codes)

    # 현재가·평가액 업데이트 (시트값 우선, 없으면 실시간 크롤링값 사용)
    if price_map and not wl_df.empty:
        def _get_code(row):
            """종목코드를 정규화해서 price_map 키와 일치시킴"""
            raw = str(row["종목코드"]) if "종목코드" in row.index else ""
            return _normalize_code(raw)

        def _apply_price(row):
            code = _get_code(row)
            val  = price_map.get(code, (0, 0.0, 0.0))
            p    = val[0]
            # 실시간 크롤링값 우선 (시트값 무시)
            sheet_p = float(row["현재가"]) if "현재가" in row.index else 0
            return p if p > 0 else sheet_p

        wl_df["현재가_실시간"] = wl_df.apply(_apply_price, axis=1)
        wl_df["전일대비(%)"]   = wl_df.apply(
            lambda r: price_map.get(_get_code(r), (0, 0.0, 0.0))[1], axis=1
        )
        wl_df["전일대비(원)"]  = wl_df.apply(
            lambda r: price_map.get(_get_code(r), (0, 0.0, 0.0))[2], axis=1
        )
        wl_df["평가액_실시간"] = (
            wl_df["현재가_실시간"] *
            wl_df["수량"].fillna(0) if "수량" in wl_df.columns
            else pd.Series([0]*len(wl_df))
        )
    else:
        wl_df["현재가_실시간"] = wl_df["현재가"].fillna(0) if "현재가" in wl_df.columns                                  else pd.Series([0]*len(wl_df))
        wl_df["전일대비(%)"]   = 0.0
        wl_df["전일대비(원)"]  = 0.0
        wl_df["평가액_실시간"] = (
            wl_df["현재가_실시간"] *
            wl_df["수량"].fillna(0) if "수량" in wl_df.columns
            else pd.Series([0]*len(wl_df))
        )

    # ── 시트 미연동 안내 ────────────────────────────────
    if wl_df.empty:
        st.info(
            "**구글 시트에 `관심종목` 탭을 추가하고 아래 헤더로 구성하세요.**\\n\\n"
            "```\\n종목명 | 계좌 | 목표가 | 월분배율(%) | 수량 | 주당분배금 | 분배주기 | 메모\\n```\\n\\n"
            "| 종목명 | 계좌 | 목표가 | 월분배율(%) | 수량 | 주당분배금 | 분배주기 | 메모 |\\n"
            "|---|---|---|---|---|---|---|---|\\n"
            "| SOL팔란티어커버드콜OTM | IRP | 25000 | 2.08 | 20000 | 191 | 월 | 현 보유 |\\n"
            "| TIGER미국배당다우존스 | IRP | 18000 | 1.50 | 0 | 0 | 월 | 교체 검토 |\\n"
            "| ACE미국30년국채액티브 | ISA | 12000 | 0.80 | 0 | 0 | 월 | 방어용 |\\n\\n"
            "탭 gid를 `WATCHLIST_SHEET_GID`에 입력하세요."
        )
        # 앱 직접 입력 모드 제공
        st.divider()
        st.markdown("**✏️ 임시 관심종목 직접 입력**")
        st.caption("시트 연동 전 임시로 종목을 추가해서 분석해 보세요.")
        _n = st.number_input("종목 수", 1, 5, 3, key="wl_n_temp")
        temp_rows = []
        for i in range(int(_n)):
            c1, c2, c3, c4, c5 = st.columns([3,2,2,2,3])
            nm   = c1.text_input(f"종목명{i+1}", key=f"wl_nm{i}", label_visibility="collapsed",
                                  placeholder="종목명")
            acc  = c2.selectbox("계좌", ["IRP","ISA","일반"], key=f"wl_acc{i}",
                                 label_visibility="collapsed")
            amt  = c3.number_input("원금(만원)", 0, value=0, step=100,
                                    key=f"wl_amt{i}", label_visibility="collapsed")
            rate = c4.number_input("분배율(%)", 0.0, 5.0, 0.0, 0.01,
                                    key=f"wl_rate{i}", label_visibility="collapsed",
                                    format="%.2f")
            memo = c5.text_input("메모", key=f"wl_memo{i}", label_visibility="collapsed",
                                  placeholder="메모")
            if nm.strip():
                temp_rows.append({
                    "종목명": nm, "계좌": acc,
                    "원금": amt * 10_000, "월분배율(%)": rate,
                    "월분배금": amt * 10_000 * rate / 100,
                    "메모": memo,
                })
        if temp_rows:
            wl_df = pd.DataFrame(temp_rows)
            wl_df["수량"] = 0
            wl_df["주당분배금"] = 0
            wl_df["목표가"] = 0
            st.caption("임시 입력 데이터로 분석 중입니다.")
        else:
            return

    # ══════════════════════════════════════════════════
    # ── 계좌별 잔액 맵 (모든 섹션에서 공통 사용) ──────────
    _acc_map = {"IRP": irp_total, "ISA": isa_total, "일반": general_total, "연금저축": ps_total}

    # 섹션 1: 관심종목 현황 테이블
    # ══════════════════════════════════════════════════
    st.markdown("**① 관심종목 현황**")

    # 월분배금 계산
    if "원금" not in wl_df.columns:
        wl_df["원금"] = wl_df.apply(
            lambda r: r.get("수량", 0) * r.get("주당분배금", 0) / (r.get("월분배율(%)", 1) / 100)
            if r.get("월분배율(%)", 0) > 0 else 0,
            axis=1
        )
    if "월분배금" not in wl_df.columns:
        wl_df["월분배금"] = wl_df.apply(
            lambda r: (r.get("주당분배금", 0) * r.get("수량", 0))
                      if r.get("수량", 0) > 0
                      else (r.get("원금", 0) * r.get("월분배율(%)", 0) / 100),
            axis=1
        )
    # dist_tax_df가 None이면 빈 DataFrame 사용
    import pandas as _pd_tax
    _dtdf = dist_tax_df if (dist_tax_df is not None and not dist_tax_df.empty) else _pd_tax.DataFrame()

    wl_df["세후분배금"] = wl_df.apply(
        lambda r: _calc_net_dist(r, _dtdf),
        axis=1
    )

    # 테이블 컬럼 구성 — 현재가·전일대비·평가액 포함
    disp_wl = wl_df.copy()
    disp_wl["현재가"] = disp_wl["현재가_실시간"]
    disp_wl["평가액"] = disp_wl["평가액_실시간"]

    _show_cols = ["종목명","계좌"]
    if "목표가" in disp_wl.columns:   _show_cols.append("목표가")
    _show_cols += ["현재가","전일대비(%)","전일대비(원)"]
    if "수량" in disp_wl.columns:     _show_cols.append("수량")
    _show_cols += ["평가액","월분배율(%)","세후분배금"]
    if "메모" in disp_wl.columns:     _show_cols.append("메모")

    _disp = disp_wl[[c for c in _show_cols if c in disp_wl.columns]].copy()

    # 갱신 버튼 + 상태 캡션 한 줄 배치
    _info_txt = (f"실시간 주가 반영 (캐시 3분) | {len(price_map)}종목"
                 if price_map else "시트에 종목코드를 추가하면 실시간 주가가 연동됩니다.")
    _rcol1, _rcol2 = st.columns([8, 1])
    _rcol1.caption(_info_txt)
    with _rcol2:
        if st.button("🔄", key="wl_price_refresh", help="주가 새로고침",
                     use_container_width=True):
            fetch_watchlist_prices.clear()
            st.rerun()

    _col_cfg = {
        "목표가":       st.column_config.NumberColumn("목표가(원)",     format="%,.0f"),
        "현재가":       st.column_config.NumberColumn("현재가(원)",     format="%,.0f"),
        "전일대비(%)":  st.column_config.NumberColumn("전일대비(%)",   format="%+.2f%%"),
        "전일대비(원)": st.column_config.NumberColumn("전일대비(원)",   format="%+,.0f"),
        "수량":         st.column_config.NumberColumn("수량(주)",       format="%,.0f"),
        "평가액":       st.column_config.NumberColumn("평가액(원)",     format="%,.0f"),
        "월분배율(%)":  st.column_config.NumberColumn("분배율(%)",     format="%.2f%%"),
        "세후분배금":   st.column_config.NumberColumn("세후분배금(원)", format="%,.0f"),
    }
    # 행 선택 활성화 → 선택된 행 인덱스로 직접 종목 결정
    _sel_event = st.dataframe(
        _disp, hide_index=True, use_container_width=True,
        column_config=_col_cfg,
        on_select="rerun",
        selection_mode="single-row",
        key="wl_table_sel",
    )
    # 선택된 행 인덱스 추출 → 종목명 즉시 결정
    _sel_rows = (_sel_event.selection.get("rows", [])
                 if hasattr(_sel_event, "selection") else [])
    _stock_list = wl_df["종목명"].tolist()
    if _sel_rows and _sel_rows[0] < len(_disp):
        _tbl_selected = _disp.iloc[_sel_rows[0]]["종목명"]
    else:
        _tbl_selected = st.session_state.get("wl_tbl_stock", _stock_list[0] if _stock_list else "")
    # 선택 유지용 저장
    if _sel_rows:
        st.session_state["wl_tbl_stock"] = _tbl_selected
        st.caption(f"🔍 **{_tbl_selected}** 선택됨")

    # 계좌별 월분배금 합계 요약 — 컬럼 없을 때 방어 처리
    if "계좌" not in wl_df.columns:
        wl_df["계좌"] = "기타"
    acc_sum      = wl_df.groupby("계좌")["세후분배금"].sum()
    total_wl_net = wl_df["세후분배금"].sum()
    total_eval   = wl_df["평가액_실시간"].sum() if "평가액_실시간" in wl_df.columns else 0.0

    _sm_cols = st.columns(len(acc_sum) + 2)
    for i, (acc, val) in enumerate(acc_sum.items()):
        _sm_cols[i].metric(f"{acc} 세후합계", f"{val:,.0f}원")
    _sm_cols[-2].metric("전체 세후합계", f"{total_wl_net:,.0f}원",
                        delta=f"목표 대비 {total_wl_net/target_monthly*100:.0f}%"
                        if target_monthly > 0 else None)
    _sm_cols[-1].metric("총 평가액", f"{total_eval:,.0f}원",
                        help="현재가 × 수량 합계 (수량 입력 종목만)")

    # ══════════════════════════════════════════════════
    # 섹션 2: 종목 상세 분석
    # ══════════════════════════════════════════════════
    st.divider()
    # 선택된 종목 표시 (테이블 행 체크 → 자동 반영)
    sel_stock = _tbl_selected if _tbl_selected in _stock_list else (
        _stock_list[0] if _stock_list else "")
    st.markdown(f"**② 종목 상세 분석 — {sel_stock}**")
    st.caption("위 테이블에서 행을 체크하면 자동으로 해당 종목 상세 정보가 표시됩니다.")

    sel_row  = wl_df[wl_df["종목명"] == sel_stock].iloc[0]
    res_data = _match_watchlist_research(str(sel_stock))

    d1, d2 = st.columns(2)
    with d1:
        with st.container(border=True):
            st.markdown("**📋 종목 특성**")
            if res_data:
                st.markdown(
                    f"<div style='font-size:0.85rem; line-height:1.8;'>"
                    f"유형: <b>{res_data['유형']}</b><br>"
                    f"기초자산: <b>{res_data['기초자산']}</b><br>"
                    f"분배주기: <b>{res_data['분배주기']}</b><br>"
                    f"과세: <b>{res_data['과세']}</b><br>"
                    f"추천계좌: <b style='color:#FFD700;'>{res_data['적합계좌']}</b><br>"
                    f"위험등급: <b>{res_data['위험등급']}</b>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                st.divider()
                st.markdown("**💡 주요 특징**")
                for feat in res_data["특징"]:
                    st.markdown(f"- {feat}")
            else:
                st.caption("등록된 분석 데이터가 없습니다.")
                st.markdown(
                    f"월분배율: **{sel_row.get('월분배율(%)',0):.2f}%**  |  "
                    f"계좌: **{sel_row.get('계좌','')}**"
                )

    with d2:
        with st.container(border=True):
            st.markdown("**💰 연금 적합성 분석**")
            _sel_acc    = sel_row.get("계좌", "IRP")
            _sel_total  = _acc_map.get(_sel_acc, irp_total)
            _sel_rate   = float(sel_row.get("월분배율(%)", 0)) / 100
            _sel_tax_r  = IRP_TAX_RATE if _sel_acc in ["IRP","연금저축"] else 0.099
            # 계산 우선순위:
            # 1. 수량×주당분배금 (가장 정확)
            # 2. 계좌 잔액×분배율
            # 3. 시트 월분배금 직접 사용
            _sel_qty    = float(sel_row.get("수량", 0))
            _sel_dps    = float(sel_row.get("주당분배금", 0))
            if _sel_qty > 0 and _sel_dps > 0:
                _sel_gross = _sel_qty * _sel_dps
            elif _sel_total > 0 and _sel_rate > 0:
                _sel_gross = _sel_total * _sel_rate
            else:
                _sel_gross = float(sel_row.get("월분배금", 0))
            _sel_net    = _sel_gross * (1 - _sel_tax_r)
            _total_sim  = public_pension + _sel_net
            _ach_sim    = (_total_sim / target_monthly * 100) if target_monthly > 0 else 0
            _ach_c      = "#7dffb0" if _ach_sim >= 100 else "#FFD700" if _ach_sim >= 80 else "#FF4B4B"

            st.markdown(
                f"<div style='font-size:0.85rem; line-height:2.0;'>"
                f"편입 계좌: <b>{_sel_acc}</b> "
                f"(잔액 {_sel_total/100_000_000:.2f}억원)<br>"
                f"세전 월분배금: <b>{_sel_gross:,.0f}원</b><br>"
                f"세금: <b>-{_sel_gross*_sel_tax_r:,.0f}원</b> "
                f"({_sel_tax_r*100:.1f}%)<br>"
                f"세후 월분배금: <b style='color:#7dffb0;'>{_sel_net:,.0f}원</b><br>"
                f"</div>",
                unsafe_allow_html=True,
            )
            st.divider()
            st.markdown(
                f"<div style='text-align:center; padding:8px;'>"
                f"<div style='font-size:0.78rem; color:rgba(255,255,255,0.5);'>"
                f"공무원연금 + 이 종목만 편입 시</div>"
                f"<div style='font-size:1.4rem; font-weight:700; "
                f"color:{_ach_c};'>{_total_sim/10000:.0f}만원</div>"
                f"<div style='font-size:0.9rem; color:{_ach_c};'>"
                f"목표 달성률 {_ach_sim:.0f}%</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

    # ── 주가 추이 차트 ──────────────────────────────────
    _sel_code = _normalize_code(str(sel_row.get("종목코드", "")))
    if _sel_code and _sel_code not in ("", "nan", "0"):
        with st.container(border=True):
            _chart_cols = st.columns([4, 1])
            with _chart_cols[1]:
                _period = st.radio(
                    "기간",
                    ["1개월", "3개월", "6개월"],
                    index=1,
                    key="wl_chart_period",
                    horizontal=False,
                )
            _pages_map = {"1개월": 2, "3개월": 5, "6개월": 13}
            _pages = _pages_map.get(_period, 5)

            hist_df = fetch_price_history(_sel_code, pages=_pages)

            with _chart_cols[0]:
                if hist_df.empty:
                    st.caption("주가 데이터를 불러올 수 없습니다.")
                else:
                    _curr_p  = int(sel_row.get("현재가_실시간", 0))
                    _tgt_p   = float(sel_row.get("목표가", 0))
                    _last_p  = hist_df["종가"].iloc[-1]
                    _first_p = hist_df["종가"].iloc[0]
                    _chg_pct = (_last_p / _first_p - 1) * 100 if _first_p > 0 else 0
                    _line_c  = "#7dffb0" if _chg_pct >= 0 else "#FF4B4B"

                    fig_hist = go.Figure()

                    # 종가 라인
                    fig_hist.add_trace(go.Scatter(
                        x=hist_df["날짜"], y=hist_df["종가"],
                        mode="lines",
                        name="종가",
                        line=dict(color=_line_c, width=2),
                        fill="tozeroy",
                        fillcolor=f"rgba({','.join(str(int(c,16)) for c in [_line_c[1:3],_line_c[3:5],_line_c[5:7]])+',0.08'})",
                    ))

                    # 목표가 수평선
                    if _tgt_p > 0:
                        fig_hist.add_hline(
                            y=_tgt_p,
                            line_dash="dot", line_color="#FFD700", line_width=1.5,
                            annotation_text=f"목표 {_tgt_p:,.0f}원",
                            annotation_position="top right",
                            annotation_font_color="#FFD700",
                            annotation_font_size=11,
                        )

                    # 현재가 수평선 (크롤링값 있을 때)
                    if _curr_p > 0 and _curr_p != _last_p:
                        fig_hist.add_hline(
                            y=_curr_p,
                            line_dash="dash", line_color="rgba(255,255,255,0.4)",
                            line_width=1,
                            annotation_text=f"현재 {_curr_p:,.0f}원",
                            annotation_position="bottom right",
                            annotation_font_color="rgba(255,255,255,0.5)",
                            annotation_font_size=10,
                        )

                    # ── Y축 범위: 데이터 변동폭 기준으로 적정 범위 설정 ──
                    _low_s   = hist_df["저가"][hist_df["저가"] > 0] if "저가" in hist_df.columns else hist_df["종가"]
                    _high_s  = hist_df["고가"][hist_df["고가"] > 0] if "고가" in hist_df.columns else hist_df["종가"]
                    _y_min   = float(_low_s.min())  if len(_low_s)  > 0 else float(hist_df["종가"].min())
                    _y_max   = float(_high_s.max()) if len(_high_s) > 0 else float(hist_df["종가"].max())
                    _y_range = _y_max - _y_min
                    # 목표가는 Y축 범위에서 제외 (수평선으로만 표시)
                    # 패딩 8% 추가
                    _pad     = max(_y_range * 0.08, _y_max * 0.005)
                    _y_lo    = _y_min - _pad
                    _y_hi    = _y_max + _pad

                    # 틱 간격: 범위를 5~7개 구간으로 나눔
                    _raw_tick = _y_range / 5
                    # 10의 거듭제곱 단위로 반올림 (예: 312 → 500, 85 → 100)
                    import math as _math
                    _mag      = 10 ** _math.floor(_math.log10(max(_raw_tick, 1)))
                    _dtick    = round(_raw_tick / _mag) * _mag
                    _dtick    = max(_dtick, 1)

                    fig_hist.update_layout(
                        title=dict(
                            text=f"📈 {sel_stock} 주가 추이  "
                                 f"<span style='font-size:13px; color:{'#7dffb0' if _chg_pct>=0 else '#FF4B4B'};'>"
                                 f"{_chg_pct:+.1f}% ({_period})</span>",
                            x=0.01, font_size=14,
                        ),
                        height=320,
                        paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(255,255,255,0.02)",
                        font_color="white",
                        margin=dict(t=50, b=20, l=60, r=10),
                        xaxis=dict(
                            tickformat="%m/%d",
                            tickangle=-30,
                            showgrid=False,
                        ),
                        yaxis=dict(
                            range=[_y_lo, _y_hi],    # ← 핵심: 적정 범위 명시
                            dtick=_dtick,             # ← 균등 틱 간격
                            tickformat=",",
                            showgrid=True,
                            gridcolor="rgba(255,255,255,0.07)",
                            zeroline=False,
                        ),
                        showlegend=False,
                        hovermode="x unified",
                    )
                    st.plotly_chart(fig_hist, use_container_width=True)

                    # 기간 요약 메트릭
                    _m1, _m2, _m3, _m4 = st.columns(4)
                    _m1.metric("시작가", f"{_first_p:,.0f}원")
                    _m2.metric("현재가", f"{_last_p:,.0f}원",
                               delta=f"{_chg_pct:+.1f}%",
                               delta_color="normal" if _chg_pct >= 0 else "inverse")
                    _m3.metric("고가", f"{hist_df['종가'].max():,.0f}원")
                    _m4.metric("저가", f"{hist_df['종가'].min():,.0f}원")
    else:
        st.caption("💡 시트에 종목코드를 입력하면 주가 추이 차트가 표시됩니다.")

    # ══════════════════════════════════════════════════




def calc_target_expense(
    age: int,
    base: float,
    retire_age: int,
    life_exp: int,
    mode: str = "peak_converge",
    peak_age: int = 70,
    peak_amount: float = 7_000_000,
    end_amount: float | None = None,
    inflation_rate: float = 0.02,
) -> float:
    """
    연령별 목표 생활비 계산 — 피크-수렴형 + 상한캡 혼합 (방안 B+C).

    mode="peak_converge"
    ────────────────────
    은퇴(base) → 피크 연령(peak_amount) → 기대수명(end_amount) 구간을 선형 보간.
    물가 상승분은 base에 이미 반영되어 있다고 가정 (외부에서 물가 적용 후 전달).

    Parameters
    ──────────
    age          : 현재 나이
    base         : 은퇴 시 목표 생활비 (원)
    retire_age   : 은퇴 나이
    life_exp     : 기대 수명
    peak_age     : 생활비 최대 연령 (기본 70세)
    peak_amount  : 피크 시 생활비 (기본 700만원)
    end_amount   : 기대수명 시 생활비 (None이면 base와 동일)
    inflation_rate: 물가상승률 (공적연금 연동용, 목표생활비 직접 적용 안 함)
    """
    if end_amount is None:
        end_amount = base

    # 상한 캡 적용
    peak_amount = min(peak_amount, 7_000_000)

    if age <= retire_age:
        return base
    elif age <= peak_age:
        # 은퇴 → 피크: 선형 증가
        ratio = (age - retire_age) / max(peak_age - retire_age, 1)
        return base + (peak_amount - base) * ratio
    else:
        # 피크 → 기대수명: 선형 감소
        ratio = (age - peak_age) / max(life_exp - peak_age, 1)
        return peak_amount - (peak_amount - end_amount) * ratio


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


def _is_new_format(df: pd.DataFrame) -> bool:
    """연금현황 탭이 신규 행 구조인지 판별 (계좌 컬럼 존재 여부)"""
    return "계좌" in df.columns


def validate_df(df: pd.DataFrame) -> list[str]:
    # 신규 포맷은 별도 검증
    if _is_new_format(df):
        errors = []
        acc_vals = df["계좌"].astype(str).str.strip().tolist()
        if "공적연금" not in acc_vals:
            errors.append("'공적연금' 행이 없습니다.")
        if "목표생활비" not in acc_vals:
            errors.append("'목표생활비' 행이 없습니다.")
        if not any(a in acc_vals for a in ["IRP","ISA","일반","연금저축"]):
            errors.append("IRP/ISA/일반/연금저축 계좌 행이 없습니다.")
        return errors
    # 기존 포맷 검증 (아래 원래 코드 계속)

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


def _pension_income_deduction(annual: float) -> float:
    """연금소득공제 계산 (소득세법 제47조의2)"""
    if annual <= 7_700_000:
        return annual
    elif annual <= 14_000_000:
        return 7_700_000 + (annual - 7_700_000) * 0.40
    elif annual <= 25_000_000:
        return 10_220_000 + (annual - 14_000_000) * 0.20
    elif annual <= 35_000_000:
        return 12_420_000 + (annual - 25_000_000) * 0.10
    else:
        return 13_420_000  # 공제 한도


def _income_tax_rate(taxable: float) -> float:
    """종합소득세 기본세율 (소득세법 제55조, 2024년 기준)"""
    if taxable <= 14_000_000:
        return taxable * 0.06
    elif taxable <= 50_000_000:
        return 840_000 + (taxable - 14_000_000) * 0.15
    elif taxable <= 88_000_000:
        return 6_240_000 + (taxable - 50_000_000) * 0.24
    elif taxable <= 150_000_000:
        return 15_360_000 + (taxable - 88_000_000) * 0.35
    elif taxable <= 300_000_000:
        return 37_060_000 + (taxable - 150_000_000) * 0.38
    else:
        return 94_060_000 + (taxable - 300_000_000) * 0.40




def calc_irp_taxbase_from_sheet(
    dist_tax_df,
    year_month: str,
    pension_items: list,
) -> dict:
    """
    분배금과세 시트 + 연금현황 수량 → IRP 실제 과세표준 계산.
    반환: has_data, irp_taxbase_monthly, irp_dist_monthly, items
    """
    _empty = {"has_data": False, "irp_taxbase_monthly": 0.0,
              "irp_dist_monthly": 0.0, "items": []}

    if dist_tax_df is None or not hasattr(dist_tax_df, "empty") or dist_tax_df.empty:
        return _empty
    if "연월" not in dist_tax_df.columns or "분배금(원)" not in dist_tax_df.columns:
        return _empty

    _ym = str(year_month)[:7]
    _df = dist_tax_df[dist_tax_df["연월"].astype(str).str.startswith(_ym)].copy()
    if _df.empty:
        return _empty

    def _n(s): return str(s).strip().replace(" ", "").upper()
    qty_map = {}
    for it in pension_items:
        nm, qty = it.get("종목명", ""), float(it.get("수량", 0) or 0)
        if nm and qty > 0:
            qty_map[_n(nm)] = qty

    total_dist = total_taxbase = 0.0
    items_out = []
    for _, row in _df.iterrows():
        acc = str(row.get("계좌", "")).strip()
        if acc not in ("IRP", "연금저축"):
            continue
        nm      = str(row.get("종목명", "")).strip()
        dps     = float(row.get("분배금(원)", 0) or 0)
        tax_dps = float(row.get("과세표준(원)", 0) or 0)
        qty = qty_map.get(_n(nm), 0.0)
        if qty == 0:
            for k, v in qty_map.items():
                if _n(nm) in k or k in _n(nm):
                    qty = v; break
        dist_t    = dps * qty
        taxbase_t = tax_dps * qty
        total_dist    += dist_t
        total_taxbase += taxbase_t
        items_out.append({
            "종목명": nm, "계좌": acc,
            "주당분배금": dps, "주당과세표준": tax_dps, "수량": qty,
            "실제분배금": dist_t, "실제과세표준": taxbase_t,
            "과세비율(%)": round(tax_dps / dps * 100, 1) if dps > 0 else 0,
        })

    return {
        "has_data": len(items_out) > 0,
        "irp_taxbase_monthly": total_taxbase,
        "irp_dist_monthly":    total_dist,
        "items":               items_out,
    }

def calc_after_tax(
    public_pension: float,
    irp_income: float,
    isa_income: float,
    ps_income: float = 0.0,
    irp_total: float = 0.0,
    irp_pension_year: int = 1,
    irp_personal_ratio: float = 0.0,   # 개인납입금+운용수익 비율 (0~1)
    age: int = 55,                      # 수령 시점 나이 (연금소득세율 결정)
    pub_taxable_ratio: float = 1.0,    # 공무원연금 과세비율 (2002년 이후 납부분 비율)
) -> dict:
    """
    세목별 공제 후 실수령액 계산 (소득세법 정확 적용).

    공적연금 (공무원연금)
    ─ pub_taxable_ratio: 2002년 이전 납부분 비과세(소득세법 §12①)
      → 과세대상 = 연금월액 × pub_taxable_ratio
    ─ 연금소득공제(소득세법 §47의2) → 과세표준 → 기본세율(§55)
    ─ 지방소득세 10% 가산
    ─ 건강보험료: 연금소득 × 7.09% (지역가입자, 장기요양 포함)

    IRP (퇴직금 + 개인납입금·운용수익 복합)
    ─ 퇴직금 원천: 한도 내 0.76%, 한도 초과 1.1% (퇴직소득세 감면)
    ─ 개인납입금+운용수익 원천: 나이별 연금소득세 5.5%(~69세)/4.4%(70대)/3.3%(80대~)
    ─ 연 1,500만원(개인납입금+운용수익 기준) 초과 시 종합과세 위험
    ─ 근거: 소득세법 §129 ①5호, 시행령 §40의2

    연금저축
    ─ 연금소득세 분리과세 5.5% (IRP와 동일, 소득세법 §129 ①5호)

    ISA (KODEX 월배당)
    ─ 연 200만원 비과세 한도 내: 세금 0
    ─ 초과분: 9.9% 분리과세

    검증: 세전 3,896,740원, 과세비율 93.13% → 세금 216,270원 (공무원연금공단 기준)
    """
    # ── 공적연금: 과세비율 적용 후 소득세법 기준 계산 ──
    # pub_taxable_ratio: 2002년 이후 납부분 비율 (기본 100%)
    _taxable_pension  = public_pension * max(0.0, min(1.0, pub_taxable_ratio))
    annual_pub        = _taxable_pension * 12
    deduction         = _pension_income_deduction(annual_pub)
    taxable           = max(0.0, annual_pub - deduction)
    income_tax_a      = _income_tax_rate(taxable)
    # 연금소득 세액공제 (소득세법 §59의3): 연 900,000원 한도
    PENSION_TAX_CREDIT = 900_000
    income_tax_a  = max(0.0, income_tax_a - PENSION_TAX_CREDIT)
    local_tax_a   = income_tax_a * 0.10        # 지방소득세 10%
    pub_tax       = (income_tax_a + local_tax_a) / 12   # 월 환산
    # 건강보험료: 실제 연금월액 기준 (과세비율 무관)
    pub_health    = public_pension * HEALTH_INS_RATE
    pub_net       = public_pension - pub_tax - pub_health

    # ── IRP (퇴직금 + 개인납입금 원천별 분리 과세) ────────
    _irp_yr       = max(1, min(irp_pension_year, 10))
    _irp_limit_annual  = (irp_total / (11 - _irp_yr) * 1.2
                          if irp_total > 0 else 20_900_000)
    _irp_limit_monthly = _irp_limit_annual / 12

    # 원천별 월 수령액 분리
    _personal_ratio  = max(0.0, min(1.0, irp_personal_ratio))
    _irp_personal    = irp_income * _personal_ratio        # 개인납입금+운용수익 원천
    _irp_retirement  = irp_income * (1 - _personal_ratio)  # 퇴직금 원천

    # 퇴직금 원천: 한도 내/초과 구분
    _irp_within  = min(_irp_retirement, _irp_limit_monthly)
    _irp_excess  = max(0.0, _irp_retirement - _irp_limit_monthly)
    _irp_ret_tax = (_irp_within * IRP_PENSION_TAX_WITHIN
                    + _irp_excess * IRP_PENSION_TAX_EXCESS)

    # 개인납입금+운용수익 원천: 나이별 연금소득세
    if age >= 80:
        _personal_tax_rate = IRP_PENSION_PERSONAL_TAX["80s"]
    elif age >= 70:
        _personal_tax_rate = IRP_PENSION_PERSONAL_TAX["70s"]
    else:
        _personal_tax_rate = IRP_PENSION_PERSONAL_TAX["60s"]
    _irp_personal_tax = _irp_personal * _personal_tax_rate

    irp_tax = _irp_ret_tax + _irp_personal_tax
    irp_net = irp_income - irp_tax

    # 종합과세 기준: 개인납입금+운용수익 연간 수령액
    _irp_personal_annual = _irp_personal * 12

    # ── 연금저축 ──
    ps_tax  = ps_income * PS_TAX_RATE
    ps_net  = ps_income - ps_tax

    # ── ISA ──
    isa_taxable = max(0, isa_income - ISA_TAX_FREE_MONTHLY)
    isa_tax     = isa_taxable * 0.099   # 9.9% 분리과세
    isa_net     = isa_income - isa_tax

    total_gross = public_pension + irp_income + ps_income + isa_income
    total_tax   = pub_tax + pub_health + irp_tax + ps_tax + isa_tax
    total_net   = pub_net + irp_net + ps_net + isa_net

    return {
        "공적연금_세전":   public_pension,
        "공적연금_소득세": pub_tax,
        "공적연금_건보료": pub_health,
        "공적연금_세후":   pub_net,
        "IRP_세전":        irp_income,
        "IRP_세금":        irp_tax,
        "IRP_세후":        irp_net,
        "IRP_한도월":      _irp_limit_monthly,
        "IRP_한도연":      _irp_limit_annual,
        "IRP_한도초과":    _irp_excess,
        "IRP_개인납입연":  _irp_personal_annual,
        "IRP_개인세율":    _personal_tax_rate,
        "IRP_퇴직세":      _irp_ret_tax,
        "IRP_개인세":      _irp_personal_tax,
        "연금저축_세전":   ps_income,
        "연금저축_세금":   ps_tax,
        "연금저축_세후":   ps_net,
        "ISA_세전":        isa_income,
        "ISA_세금":        isa_tax,
        "ISA_세후":        isa_net,
        "총_세전":         total_gross,
        "총_공제액":       total_tax,
        "총_세후":         total_net,
        "실효세율":        (total_tax / total_gross * 100) if total_gross > 0 else 0,
    }


# ════════════════════════════════════════════════════════
def calc_withdrawal_plan(
    target_monthly: float,
    public_pension_net: float,
    irp_total: float,
    isa_total: float,
    general_total: float,
    irp_weight: float,
    isa_weight: float,
    general_weight: float,
    use_after_tax: bool,
    use_health_ins: bool,
    ps_total: float = 0.0,
    ps_weight: float = 0.0,
) -> dict:
    """
    목표 생활비를 충당하기 위한 계좌별 필요 인출액 역산.

    흐름
    ────
    1. 공무원연금(세후)으로 우선 충당
    2. 부족분을 IRP·ISA·일반 가중치 비율로 배분
    3. 각 계좌별 필요 인출 원금(세전) 역산
    4. 원금 대비 분배율(%) 계산 → 슬라이더 권장값 제시
    5. 해당 분배율로 실제 달성 가능 여부 검증

    반환값 (dict)
    ─────────────
    shortage          : 공무원연금 충당 후 월 부족액
    irp_need_gross    : IRP 필요 인출액 (세전)
    isa_need_gross    : ISA 필요 인출액 (세전)
    gen_need_gross    : 일반 필요 인출액 (세전)
    irp_rate_suggest  : IRP 권장 분배율 (%)
    isa_rate_suggest  : ISA 권장 분배율 (%)
    gen_rate_suggest  : 일반 권장 분배율 (%)
    total_net_est     : 달성 예상 세후 합계
    gap               : 목표 대비 잉여/부족
    feasible          : 목표 달성 가능 여부
    """
    # 공무원연금 세후 계산 (건보료 옵션 반영)
    _tr_pub = calc_after_tax(public_pension_net, 0, 0)
    pub_net = _tr_pub["공적연금_세후"]
    if not use_health_ins:
        pub_net += _tr_pub["공적연금_건보료"]

    # 공무원연금으로 충당 후 부족분
    shortage = max(0.0, target_monthly - pub_net)

    if shortage <= 0:
        # 연금만으로 목표 달성
        return {
            "shortage":         0.0,
            "irp_need_gross":   0.0,
            "isa_need_gross":   0.0,
            "gen_need_gross":   0.0,
            "ps_need_gross":    0.0,
            "irp_rate_suggest": 0.0,
            "isa_rate_suggest": 0.0,
            "gen_rate_suggest": 0.0,
            "ps_rate_suggest":  0.0,
            "total_net_est":    pub_net,
            "gap":              pub_net - target_monthly,
            "feasible":         True,
        }

    # 가중치 합 정규화
    total_w = irp_weight + isa_weight + general_weight + ps_weight
    if total_w <= 0:
        total_w = 1.0
        irp_weight = isa_weight = general_weight = 1/3
        ps_weight = 0.0

    # 부족분을 가중치 비율로 각 계좌에 배분 (세후 목표)
    irp_need_net = shortage * (irp_weight / total_w)
    isa_need_net = shortage * (isa_weight / total_w)
    gen_need_net = shortage * (general_weight / total_w)
    ps_need_net  = shortage * (ps_weight / total_w)

    # 세전 역산 (세금률 반영)
    irp_need_gross = irp_need_net / (1 - IRP_TAX_RATE)
    # ISA: 비과세 한도 고려
    if isa_need_net <= ISA_TAX_FREE_MONTHLY * (1 - 0.099):
        isa_need_gross = isa_need_net   # 비과세 범위 내
    else:
        isa_need_gross = ISA_TAX_FREE_MONTHLY + (
            (isa_need_net - ISA_TAX_FREE_MONTHLY) / (1 - 0.099)
        )
    gen_need_gross = gen_need_net / (1 - 0.154)   # 배당소득세 15.4%
    ps_need_gross  = ps_need_net  / (1 - PS_TAX_RATE)  # 연금소득세 5.5%

    # 분배율 역산 (원금 대비 %)
    irp_rate_s = (irp_need_gross / irp_total * 100) if irp_total > 0 else 0.0
    isa_rate_s = (isa_need_gross / isa_total * 100) if isa_total > 0 else 0.0
    gen_rate_s = (gen_need_gross / general_total * 100) if general_total > 0 else 0.0
    ps_rate_s  = (ps_need_gross  / ps_total      * 100) if ps_total      > 0 else 0.0

    # 검증: 역산된 분배율로 실제 세후 합계
    irp_income_v = irp_total * (irp_rate_s / 100)
    isa_income_v = isa_total * (isa_rate_s / 100)
    gen_income_v = general_total * (gen_rate_s / 100)
    gen_tax_v    = gen_income_v * 0.154
    tr_v = calc_after_tax(public_pension_net, irp_income_v, isa_income_v)
    if not use_health_ins:
        tr_v["총_세후"] += tr_v["공적연금_건보료"]
    total_net_v = tr_v["총_세후"] + (gen_income_v - gen_tax_v)

    return {
        "shortage":         shortage,
        "irp_need_gross":   irp_need_gross,
        "isa_need_gross":   isa_need_gross,
        "gen_need_gross":   gen_need_gross,
        "ps_need_gross":    ps_need_gross if ps_total > 0 else 0.0,
        "irp_rate_suggest": min(irp_rate_s, 5.0),
        "isa_rate_suggest": min(isa_rate_s, 5.0),
        "gen_rate_suggest": min(gen_rate_s, 2.0),
        "ps_rate_suggest":  min(ps_rate_s,  5.0) if ps_total > 0 else 0.0,
        "total_net_est":    total_net_v,
        "gap":              total_net_v - target_monthly,
        "feasible":         total_net_v >= target_monthly * 0.99,
    }


# 1. 페이지 설정
# ════════════════════════════════════════════════════════
st.set_page_config(page_title="연금 현금흐름 관제탑", layout="wide")

st.markdown("""
<style>
/* ── 제목 크기 조정 ── */
[data-testid="stAppViewContainer"] h1,
.stTitle  { font-size:1.4rem !important; font-weight:600 !important; }
[data-testid="stAppViewContainer"] h2 { font-size:1.15rem !important; font-weight:600 !important; }
[data-testid="stAppViewContainer"] h3 { font-size:1.0rem  !important; font-weight:600 !important; }
[data-testid="stAppViewContainer"] h4 { font-size:0.9rem  !important; font-weight:600 !important; }
[data-testid="stHeader"]    { font-size:1.05rem !important; }
[data-testid="stSubheader"] { font-size:0.95rem !important; }

/* ── 메트릭 ── */
[data-testid="stMetricValue"] { font-size:1.4rem !important; font-weight:700 !important; }
[data-testid="stMetricLabel"] { font-size:0.78rem !important; }

/* ── 세금 내역 행 ── */
.tax-row { display:flex; justify-content:space-between; padding:6px 0;
           border-bottom:1px solid rgba(255,255,255,0.06); font-size:0.88rem; }
.tax-label { color:rgba(255,255,255,0.6); }
.tax-val   { font-weight:600; }
.tax-neg   { color:#FF4B4B; }
.tax-pos   { color:#7dffb0; }
</style>
""", unsafe_allow_html=True)


# ════════════════════════════════════════════════════════
# 2. 구글 시트 로드 + 데이터 처리
# ════════════════════════════════════════════════════════

@st.cache_data(ttl=DATA_TTL, show_spinner=False)
def load_sheet(url: str, gid: str = "919720494") -> pd.DataFrame:
    """공개 구글 시트 CSV export URL로 직접 읽기 (캐시 5분)"""
    match = re.search(r"/d/([a-zA-Z0-9_-]+)", url)
    if not match:
        raise ValueError("올바른 구글 시트 URL이 아닙니다.")
    sheet_id = match.group(1)
    csv_url  = (
        f"https://docs.google.com/spreadsheets/d/{sheet_id}"
        f"/export?format=csv&gid={gid}"
    )
    return pd.read_csv(csv_url)




@st.cache_data(ttl=DATA_TTL, show_spinner=False)
def load_contribution(url: str, gid: str) -> pd.DataFrame:
    """
    납입현황 탭 로드.
    헤더: 계좌 | 연도 | 납입액 | 세액공제신청액 | 메모
    """
    if not gid or not str(gid).strip().isdigit():
        return pd.DataFrame()
    try:
        import re as _re
        m = _re.search(r"/d/([a-zA-Z0-9_-]+)", url)
        if not m:
            return pd.DataFrame()
        df = pd.read_csv(
            f"https://docs.google.com/spreadsheets/d/{m.group(1)}"
            f"/export?format=csv&gid={gid}"
        )
        if df.empty:
            return pd.DataFrame()
        for col in ["납입액", "세액공제신청액"]:
            if col in df.columns:
                df[col] = pd.to_numeric(
                    df[col].astype(str).str.replace(",", ""), errors="coerce"
                ).fillna(0)
        if "연도" in df.columns:
            df["연도"] = pd.to_numeric(df["연도"], errors="coerce").fillna(0).astype(int)
        return df
    except Exception:
        return pd.DataFrame()


def calc_contribution_status(contrib_df: pd.DataFrame, current_year: int) -> dict:  # noqa
    """
    납입현황 탭 → 당해연도 납입액·세액공제 집계.
    반환: {irp_annual, ps_annual, combined_annual,
           irp_deduct, ps_deduct, combined_deduct,
           irp_remain_limit, ps_remain_limit, combined_remain_limit,
           irp_deduct_remain, ps_deduct_remain, combined_deduct_remain}
    """
    empty = {
        "irp_annual": 0, "ps_annual": 0, "combined_annual": 0,
        "irp_deduct": 0, "ps_deduct": 0, "combined_deduct": 0,
        "irp_remain_limit": ANNUAL_CONTRIBUTION_LIMIT,
        "combined_remain_limit": ANNUAL_CONTRIBUTION_LIMIT,
        "irp_deduct_remain": IRP_TAX_DEDUCT_LIMIT,
        "ps_deduct_remain": PS_TAX_DEDUCT_LIMIT,
        "combined_deduct_remain": COMBINED_TAX_DEDUCT_LIMIT,
        # ISA
        "isa_annual": 0,
        "isa_cumulative": 0,
        "isa_remain_annual": ISA_ANNUAL_LIMIT,
    }
    if contrib_df.empty or "연도" not in contrib_df.columns:
        return empty

    yr = contrib_df[contrib_df["연도"] == current_year]
    if yr.empty:
        return empty

    irp_rows = yr[yr["계좌"].astype(str).str.strip() == "IRP"]
    ps_rows  = yr[yr["계좌"].astype(str).str.strip() == "연금저축"]
    isa_rows = yr[yr["계좌"].astype(str).str.strip() == "ISA"]

    irp_annual  = float(irp_rows["납입액"].sum()) if not irp_rows.empty else 0
    ps_annual   = float(ps_rows["납입액"].sum())  if not ps_rows.empty  else 0
    combined    = irp_annual + ps_annual

    irp_deduct  = float(irp_rows["세액공제신청액"].sum()) if "세액공제신청액" in irp_rows.columns else 0
    ps_deduct   = float(ps_rows["세액공제신청액"].sum())  if "세액공제신청액" in ps_rows.columns  else 0
    comb_deduct = irp_deduct + ps_deduct

    # ISA — 당해연도 납입액 + 전체 기간 누적
    isa_annual   = float(isa_rows["납입액"].sum()) if not isa_rows.empty else 0
    all_isa_rows = contrib_df[contrib_df["계좌"].astype(str).str.strip() == "ISA"]
    isa_cumul    = float(all_isa_rows["납입액"].sum()) if not all_isa_rows.empty else 0

    return {
        "irp_annual":             irp_annual,
        "ps_annual":              ps_annual,
        "combined_annual":        combined,
        "irp_deduct":             irp_deduct,
        "ps_deduct":              ps_deduct,
        "combined_deduct":        comb_deduct,
        "irp_remain_limit":       max(0, ANNUAL_CONTRIBUTION_LIMIT - combined),
        "combined_remain_limit":  max(0, ANNUAL_CONTRIBUTION_LIMIT - combined),
        "irp_deduct_remain":      max(0, IRP_TAX_DEDUCT_LIMIT - irp_deduct),
        "ps_deduct_remain":       max(0, PS_TAX_DEDUCT_LIMIT  - ps_deduct),
        "combined_deduct_remain": max(0, COMBINED_TAX_DEDUCT_LIMIT - comb_deduct),
        # ISA
        "isa_annual":        isa_annual,
        "isa_cumulative":    isa_cumul,
        "isa_remain_annual": max(0, ISA_ANNUAL_LIMIT - isa_annual),
    }

@st.cache_data(ttl=DATA_TTL, show_spinner=False)
def load_scenarios(url: str, gid: str) -> pd.DataFrame:
    """
    구글 시트 '시나리오' 탭 로드.
    헤더: 시나리오명 | 계좌 | 종목명 | 원금 | 분배율(%) | 메모
    gid 미설정 또는 숫자가 아닌 값 입력 시 빈 DataFrame 반환.
    """
    # gid는 숫자 문자열이어야 함 — 탭 이름 등 잘못된 값 방어
    if not gid or not str(gid).strip().isdigit():
        return pd.DataFrame()
    try:
        import re as _re
        match = _re.search(r"/d/([a-zA-Z0-9_-]+)", url)
        if not match:
            return pd.DataFrame()
        sid = match.group(1)
        df  = pd.read_csv(
            f"https://docs.google.com/spreadsheets/d/{sid}"
            f"/export?format=csv&gid={gid}"
        )
        if df.empty:
            return pd.DataFrame()

        # ── 컬럼명 자동 매핑 ────────────────────────────────
        # 현재 시트 형식(관심종목 분석 형식)도 지원
        rename_map = {}

        # 시나리오명 없으면 기본값 "현재안" 삽입
        if "시나리오명" not in df.columns:
            df.insert(0, "시나리오명", "현재안")

        # 분배율 컬럼 정규화 — 일분배율/월분배율 → 분배율(%)
        for _rc in df.columns:
            if "분배율" in _rc and _rc != "분배율(%)":
                rename_map[_rc] = "분배율(%)"
                break
        # 평가액 → 원금 (시나리오 탭에 원금 없고 평가액만 있는 경우)
        if "원금" not in df.columns and "평가액" in df.columns:
            rename_map["평가액"] = "원금"

        # 평가액 → 원금 매핑 (원금 컬럼 없을 때)
        if "원금" not in df.columns:
            if "평가액" in df.columns:
                rename_map["평가액"] = "원금"
            elif "수량" in df.columns and "현재가" in df.columns:
                # 수량 × 현재가로 원금 계산
                df["원금"] = (
                    pd.to_numeric(df["수량"].astype(str).str.replace(",",""), errors="coerce").fillna(0)
                    * pd.to_numeric(df["현재가"].astype(str).str.replace(",",""), errors="coerce").fillna(0)
                )

        if rename_map:
            df = df.rename(columns=rename_map)

        # 필수 컬럼 최종 확인
        if "시나리오명" not in df.columns or "계좌" not in df.columns:
            return pd.DataFrame()

        # 숫자 변환
        for col in ["원금", "분배율(%)"]:
            if col in df.columns:
                df[col] = pd.to_numeric(
                    df[col].astype(str).str.replace(",", ""),
                    errors="coerce"
                ).fillna(0)

        # 기본적용 컬럼 정규화 (Y/y/1/True → True)
        if "기본적용" in df.columns:
            df["기본적용"] = df["기본적용"].astype(str).str.strip().str.upper()                               .isin(["Y", "YES", "1", "TRUE", "예", "O"])
        else:
            df["기본적용"] = False

        return df
    except Exception:
        return pd.DataFrame()


def build_scenario_params(sc_df: pd.DataFrame, sc_name: str) -> dict:
    """
    시나리오명으로 필터링 → 계좌별 원금 합산 및 가중평균 분배율 계산.

    변경사항:
    - irp/isa/gen/ps_exists 플래그 추가 → 미입력 계좌 명시적 0 초기화
    - 과세표준(원) 컬럼 포함 추출 → gen 세금 정확 계산
    - rows.empty이면 total=0, rate=0 확정 (이전값 잔류 방지)
    """
    sub = sc_df[sc_df["시나리오명"] == sc_name].copy()
    result = {
        "irp_total": 0.0, "isa_total": 0.0, "gen_total": 0.0, "ps_total": 0.0,
        "irp_rate":  0.0, "isa_rate":  0.0, "gen_rate":  0.0, "ps_rate":  0.0,
        "irp_종목": [],   "isa_종목": [],   "gen_종목": [],   "ps_종목": [],
        "irp_personal_ratio": None,
        # 계좌별 존재 여부 (True면 시나리오에 해당 계좌 행이 있음)
        "irp_exists": False, "isa_exists": False,
        "gen_exists": False, "ps_exists":  False,
    }
    acc_map = {"IRP": "irp", "ISA": "isa", "일반": "gen", "연금저축": "ps"}

    for acc_kr, acc_en in acc_map.items():
        rows = sub[sub["계좌"] == acc_kr]
        result[f"{acc_en}_exists"] = not rows.empty   # ← 존재 여부 기록

        if rows.empty:
            # 시나리오에 해당 계좌 없음 → 0 확정 (다른 시나리오 값 잔류 방지)
            result[f"{acc_en}_total"] = 0.0
            result[f"{acc_en}_rate"]  = 0.0
            result[f"{acc_en}_종목"]  = []
            continue

        rows = rows.copy()
        # 과세표준(원) 컬럼 포함
        for _nc in ["원금","수량","주당분배금","현재가","분배율(%)","과세표준(원)"]:
            if _nc in rows.columns:
                rows[_nc] = pd.to_numeric(
                    rows[_nc].astype(str).str.replace(",",""), errors="coerce"
                ).fillna(0)

        total = rows["원금"].sum()
        if total < 0:
            continue

        def _monthly(r):
            qty  = float(r.get("수량",      0) or 0)
            dps  = float(r.get("주당분배금", 0) or 0)
            amt  = float(r.get("원금",      0) or 0)
            rate = float(r.get("분배율(%)", 0) or 0)
            if qty > 0 and dps > 0:
                return qty * dps
            elif amt > 0 and rate > 0:
                return amt * rate / 100
            return 0.0

        rows = rows.copy()
        rows["_월분배금"] = rows.apply(_monthly, axis=1)
        total_monthly     = rows["_월분배금"].sum()
        w_rate            = (total_monthly / total * 100) if total > 0 else 0.0
        result[f"{acc_en}_total"] = total
        result[f"{acc_en}_rate"]  = w_rate / 100

        # 과세표준(원) 포함해서 종목 추출
        _cols = ["종목명","원금","분배율(%)"]
        for _extra in ["수량","주당분배금","현재가","메모","원천구분","과세표준(원)"]:
            if _extra in rows.columns:
                _cols.append(_extra)
        result[f"{acc_en}_종목"] = rows[_cols].to_dict("records")

    # ★ 시나리오 IRP 원천별 비율 자동 계산
    _sc_irp = sub[sub["계좌"] == "IRP"].copy()
    if not _sc_irp.empty:
        if "원금" not in _sc_irp.columns and "평가액" in _sc_irp.columns:
            _sc_irp["원금"] = _sc_irp["평가액"]
        _sc_irp["원금"] = pd.to_numeric(_sc_irp["원금"], errors="coerce").fillna(0)
        _sc_total = _sc_irp["원금"].sum()

        def _sc_cls(m):
            m = str(m).strip()
            return "개인납입" if any(k in m for k in ["개인","납입"]) else "퇴직금"

        if "원천구분" in _sc_irp.columns:
            _sc_irp["_s"] = _sc_irp["원천구분"].astype(str).apply(_sc_cls)
        elif "메모" in _sc_irp.columns:
            _sc_irp["_s"] = _sc_irp["메모"].apply(_sc_cls)
        else:
            _sc_irp["_s"] = "퇴직금"

        _sc_pers = _sc_irp[_sc_irp["_s"] == "개인납입"]["원금"].sum()
        if _sc_total > 0:
            result["irp_personal_ratio"] = float(_sc_pers / _sc_total)

    return result



# ── 지출 카테고리 정의 ────────────────────────────────
EXPENSE_CATEGORIES = ["식비", "주거/관리비", "의료/건강", "여행/여가", "교통/통신", "기타"]
INCOME_CATEGORIES  = ["공무원연금", "IRP분배금", "ISA분배금", "일반분배금", "기타수입"]


@st.cache_data(ttl=DATA_TTL, show_spinner=False)
def load_household(url: str, gid: str) -> pd.DataFrame:
    """
    구글 시트 '가계부' 탭 로드.
    헤더: 연월 | 구분 | 카테고리 | 항목 | 금액 | 비고
    구분: 수입 / 지출
    """
    if not gid or not str(gid).strip().isdigit():
        return pd.DataFrame()
    try:
        import re as _re
        match = _re.search(r"/d/([a-zA-Z0-9_-]+)", url)
        if not match:
            return pd.DataFrame()
        sid = match.group(1)
        df  = pd.read_csv(
            f"https://docs.google.com/spreadsheets/d/{sid}"
            f"/export?format=csv&gid={gid}"
        )
        if df.empty or "연월" not in df.columns:
            return pd.DataFrame()
        df["금액"] = pd.to_numeric(
            df["금액"].astype(str).str.replace(",", ""),
            errors="coerce"
        ).fillna(0)
        df["연월"] = df["연월"].astype(str).str.strip()
        df["구분"] = df["구분"].astype(str).str.strip()
        df["카테고리"] = df["카테고리"].astype(str).str.strip()
        df["항목"]     = df["항목"].astype(str).str.strip()
        return df
    except Exception:
        return pd.DataFrame()



@st.cache_data(ttl=DATA_TTL, show_spinner=False)
def load_watchlist(url: str, gid: str) -> pd.DataFrame:
    """
    구글 시트 '관심종목' 탭 로드.
    헤더: 종목명|계좌|목표가|월분배율(%)|수량|주당분배금|분배주기|메모
    """
    if not gid or not str(gid).strip().isdigit():
        return pd.DataFrame()
    try:
        import re as _re
        sid = _re.search(r"/d/([a-zA-Z0-9_-]+)", url).group(1)
        df  = pd.read_csv(
            f"https://docs.google.com/spreadsheets/d/{sid}"
            f"/export?format=csv&gid={gid}"
        )
        if df.empty or "종목명" not in df.columns:
            return pd.DataFrame()
        for col in ["목표가", "월분배율(%)", "수량", "주당분배금", "현재가", "평가액"]:
            if col in df.columns:
                df[col] = pd.to_numeric(
                    df[col].astype(str).str.replace(",", ""),
                    errors="coerce"
                ).fillna(0)
        # 종목코드 문자열 정리
        if "종목코드" in df.columns:
            df["종목코드"] = df["종목코드"].astype(str).str.strip()                                           .str.replace(".KS","").str.replace(".KQ","")
        return df
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=DATA_TTL, show_spinner=False)
def load_and_validate(url: str, gid: str) -> tuple[pd.DataFrame, list[str]]:
    """시트 로드 + 유효성 검사 결과를 캐시해 반환"""
    df     = load_sheet(url, gid)
    errors = validate_df(df)
    return df, errors



# _is_new_format → validate_df 앞으로 이동


def parse_pension_sheet_new(df: pd.DataFrame) -> dict:
    """
    신규 연금현황 탭 파싱.
    헤더: 계좌 | 종목명 | 종목코드 | 수량 | 주당분배금 | 원금 | 분배율(%) | 기본적용 | 메모
    특수행: 계좌='공적연금' → 월 수령액(원금 컬럼)
            계좌='목표생활비' → 월 목표(원금 컬럼)
    """
    # 컬럼명 정규화 (시트 헤더 변형 대응)
    col_rename = {}
    for col in df.columns:
        if col.strip() in ("매수가",):
            pass  # 무시
        elif "분배율" in col and col != "분배율(%)":
            col_rename[col] = "분배율(%)"
    if col_rename:
        df = df.rename(columns=col_rename)

    # 숫자 컬럼 정규화
    for col in ["수량", "주당분배금", "원금", "분배율(%)", "매수가", "평가액", "현재가"]:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col].astype(str).str.replace(",", ""),
                errors="coerce"
            ).fillna(0)
    if "계좌" in df.columns:
        df["계좌"] = df["계좌"].astype(str).str.strip()
    if "종목명" in df.columns:
        df["종목명"] = df["종목명"].astype(str).str.strip()
    if "종목코드" in df.columns:
        df["종목코드"] = df["종목코드"].astype(str).str.strip()                          .str.replace(".KS", "").str.replace(".KQ", "")

    def _sum_col(acc_kr, col, default=0.0):
        rows = df[df["계좌"] == acc_kr]
        if rows.empty or col not in rows.columns:
            return default
        return float(rows[col].sum())

    def _first(acc_kr, col, default=0.0):
        rows = df[df["계좌"] == acc_kr]
        if rows.empty or col not in rows.columns:
            return default
        v = rows[col].iloc[0]
        return float(v) if v and str(v) not in ("nan","0","") else default

    def _get_items(acc_kr):
        """계좌별 종목 리스트 반환 — 종목명 없는 행도 포함, 메모에서 원천 구분"""
        rows = df[df["계좌"] == acc_kr].copy()
        if rows.empty:
            return []
        items = []
        for _, row in rows.iterrows():
            nm    = str(row.get("종목명", "") or "").strip()
            qty   = float(row.get("수량", 0) or 0)
            dps   = float(row.get("주당분배금", 0) or 0)
            amt   = float(row.get("원금", 0) or 0)
            rate  = float(row.get("분배율(%)", 0) or 0)
            code  = str(row.get("종목코드", "") or "").strip()
            memo  = str(row.get("메모", "") or "").strip()
            # 메모에서 원천 구분 (퇴직금/개인납입 키워드)
            if "퇴직" in memo:
                source = "퇴직금"
            elif "개인" in memo or "납입" in memo:
                source = "개인납입"
            else:
                source = "퇴직금"  # IRP 기본값 퇴직금
            # 종목명 없는 행: 계좌명을 종목명으로 대체 (원금 행)
            if not nm or nm in ("nan",""):
                if amt > 0:
                    items.append({
                        "종목명":     f"({acc_kr} 원금)",
                        "종목코드":   "",
                        "수량":       0,
                        "주당분배금": 0,
                        "원금":       amt,
                        "분배율(%)":  rate,
                    })
            else:
                items.append({
                    "종목명":     nm,
                    "종목코드":   code,
                    "수량":       qty,
                    "주당분배금": dps,
                    "원금":       amt,
                    "분배율(%)":  rate,
                    "원천":       source if acc_kr == "IRP" else "개인납입",
                })
        return items

    # 계좌별 원금 합산
    irp_total     = _sum_col("IRP",    "원금")
    isa_total     = _sum_col("ISA",    "원금")
    gen_total     = _sum_col("일반",   "원금")
    ps_total      = _sum_col("연금저축","원금")

    # 계좌별 가중평균 분배율 계산
    def _wavg_rate(acc_kr):
        rows = df[df["계좌"] == acc_kr]
        if rows.empty: return 0.0
        total_amt = rows["원금"].sum()
        if total_amt <= 0: return 0.0
        # 수량×주당분배금 우선, 없으면 원금×분배율
        total_dist = 0.0
        for _, r in rows.iterrows():
            qty = float(r.get("수량", 0) or 0)
            dps = float(r.get("주당분배금", 0) or 0)
            amt = float(r.get("원금", 0) or 0)
            rt  = float(r.get("분배율(%)", 0) or 0)
            if qty > 0 and dps > 0:
                total_dist += qty * dps
            elif amt > 0 and rt > 0:
                total_dist += amt * rt / 100
        return (total_dist / total_amt * 100) if total_amt > 0 else 0.0

    # 단일 종목 수량·DPS (기존 호환용)
    def _first_shares(acc_kr):
        rows = df[df["계좌"] == acc_kr]
        if rows.empty: return 0.0
        return float(rows["수량"].sum()) if "수량" in rows.columns else 0.0

    def _first_dps(acc_kr):
        rows = df[df["계좌"] == acc_kr]
        if rows.empty: return 0.0
        r = rows.iloc[0]
        return float(r.get("주당분배금", 0) or 0)

    # ── IRP 원천별 비율 자동 계산 ─────────────────────────────
    _irp_rows = df[df["계좌"] == "IRP"].copy()
    _irp_rows["원금"] = pd.to_numeric(_irp_rows["원금"], errors="coerce").fillna(0)
    _irp_total_amt = _irp_rows["원금"].sum()

    def _classify_source(memo: str) -> str:
        m = str(memo).strip()
        if "퇴직" in m: return "퇴직금"
        if "개인" in m or "납입" in m: return "개인납입"
        return "퇴직금"

    if "원천구분" in _irp_rows.columns:
        _irp_rows["_src"] = _irp_rows["원천구분"].astype(str).apply(
            lambda x: "개인납입" if any(k in x for k in ["개인","납입"]) else "퇴직금"
        )
    elif "메모" in _irp_rows.columns:
        _irp_rows["_src"] = _irp_rows["메모"].apply(_classify_source)
    else:
        _irp_rows["_src"] = "퇴직금"

    _irp_pers_amt = _irp_rows[_irp_rows["_src"] == "개인납입"]["원금"].sum()
    _irp_personal_ratio_auto = (
        float(_irp_pers_amt / _irp_total_amt) if _irp_total_amt > 0 else 0.0
    )

    return {
        "public_pension":           _first("공적연금", "원금"),
        "irp_total":                irp_total,
        "isa_total":                isa_total,
        "general_total":            gen_total,
        "ps_total":                 ps_total,
        "target_monthly":           _first("목표생활비", "원금", default=1.0),
        "isa_limit":                float(ISA_LIMIT),
        "default_palantir":         _wavg_rate("IRP"),
        "default_kodex":            _wavg_rate("ISA"),
        "default_general":          _wavg_rate("일반") if _wavg_rate("일반") > 0 else 2.88,
        "default_ps":               _wavg_rate("연금저축"),
        "irp_shares":               _first_shares("IRP"),
        "isa_shares":               _first_shares("ISA"),
        "ps_shares":                _first_shares("연금저축"),
        "irp_dps_default":          _first_dps("IRP"),
        "isa_dps_default":          _first_dps("ISA"),
        "ps_dps_default":           _first_dps("연금저축"),
        "irp_종목":                 _get_items("IRP"),
        "isa_종목":                 _get_items("ISA"),
        "gen_종목":                 _get_items("일반"),
        "ps_종목":                  _get_items("연금저축"),
        # ★ 원천별 비율 자동 계산
        "irp_personal_ratio_auto":  _irp_personal_ratio_auto,
        "irp_ret_amt":              float(_irp_total_amt - _irp_pers_amt),
        "irp_pers_amt":             float(_irp_pers_amt),
    }

def extract_values(df: pd.DataFrame) -> dict:
    """
    DataFrame에서 모든 설정값을 추출해 dict로 반환.
    신규 포맷(계좌|종목명|수량… 행 구조)과 기존 포맷(항목|금액) 모두 지원.
    """
    if _is_new_format(df):
        return parse_pension_sheet_new(df)

    # ── 기존 포맷 (하위 호환) ────────────────────────────
    return {
        "public_pension":   safe_get(df, "공적연금"),
        "irp_total":        safe_get(df, "IRP"),
        "isa_total":        safe_get(df, "ISA"),
        "general_total":    safe_get(df, "일반",          default=0.0),
        "target_monthly":   safe_get(df, "목표생활비",    default=1.0),
        "default_palantir": safe_get(df, "IRP기본분배율",  default=1.2),
        "default_kodex":    safe_get(df, "ISA기본분배율",  default=0.8),
        "default_general":  safe_get(df, "일반기본분배율", default=2.88),
        "irp_shares":       safe_get(df, "IRP수량",       default=0.0),
        "isa_shares":       safe_get(df, "ISA수량",       default=0.0),
        "isa_limit":        safe_get(df, "ISA납입한도",   default=float(ISA_LIMIT)),
        "irp_dps_default":  safe_get(df, "IRP주당분배금", default=0.0),
        "isa_dps_default":  safe_get(df, "ISA주당분배금", default=0.0),
        "ps_total":         safe_get(df, "연금저축",      default=0.0),
        "default_ps":       safe_get(df, "연금저축기본분배율", default=1.0),
        "ps_shares":        safe_get(df, "연금저축수량",  default=0.0),
        "ps_dps_default":   safe_get(df, "연금저축주당분배금", default=0.0),
        # 기존 포맷은 종목 상세 없음
        "irp_종목": [], "isa_종목": [], "gen_종목": [], "ps_종목": [],
    }


# ── 5단계 로딩 진행률 ────────────────────────────────────
with st.status("📡 연금 데이터를 불러오는 중...", expanded=True) as _status:

    # STEP 0 — 납입현황 탭 로딩
    contrib_df     = load_contribution(SHEET_URL, CONTRIBUTION_SHEET_GID)
    contrib_year   = datetime.now().year
    contrib_status = calc_contribution_status(contrib_df, contrib_year)

    # STEP 1 — 구글 시트 연결
    st.write("📋 구글 시트 연결 중...")
    try:
        df, _errors = load_and_validate(SHEET_URL, "919720494")
    except Exception as _e:
        _status.update(label="❌ 연결 실패", state="error")
        st.error(f"구글 시트 읽기 오류: {_e}")
        st.info(
            "체크리스트\n"
            "1. 시트 공유 설정이 **링크가 있는 모든 사용자 → 뷰어** 인지 확인\n"
            f"2. 워크시트 탭 이름이 정확히 **{WORKSHEET_NAME}** 인지 확인\n"
            "3. 시트 URL이 올바른지 확인"
        )
        st.stop()

    # STEP 2 — 데이터 유효성 검사
    st.write("🔍 데이터 유효성 검사 중...")
    if _errors:
        _status.update(label="❌ 시트 데이터 오류", state="error")
        st.error("📋 시트 데이터 오류")
        for _err in _errors:
            st.warning(f"• {_err}")
        with st.expander("현재 시트 미리보기"):
            st.dataframe(df)
        st.info(
            "구글 시트 연금현황 탭 구조를 확인하세요.\n"
            "신규 포맷: 계좌|종목명|종목코드|수량|주당분배금|원금|분배율(%)|기본적용|메모\n"
            "필수 행: 공적연금, 목표생활비, IRP/ISA/일반 중 하나 이상"
        )
        st.stop()

    # STEP 3 — 값 추출
    st.write("🔢 수치 데이터 파싱 중...")
    _vals = extract_values(df)

    # STEP 4 — 현금흐름 계산
    st.write("💰 현금흐름 계산 중...")
    public_pension   = _vals["public_pension"]
    irp_total        = _vals["irp_total"]
    isa_total        = _vals["isa_total"]
    general_total    = _vals["general_total"]
    target_monthly   = _vals["target_monthly"]
    default_palantir = _vals["default_palantir"]
    default_kodex    = _vals["default_kodex"]
    default_general  = _vals["default_general"]
    irp_shares       = _vals["irp_shares"]
    isa_shares       = _vals["isa_shares"]
    isa_limit        = _vals.get("isa_limit", float(ISA_LIMIT))
    irp_dps_default  = _vals["irp_dps_default"]
    isa_dps_default  = _vals["isa_dps_default"]
    ps_total         = _vals["ps_total"]
    default_ps       = _vals["default_ps"]
    ps_shares        = _vals["ps_shares"]
    ps_dps_default   = _vals["ps_dps_default"]
    # 신규 포맷: 종목 상세 (기존 포맷은 빈 리스트)
    _pension_irp_items = _vals.get("irp_종목", [])
    _pension_isa_items = _vals.get("isa_종목", [])
    _pension_gen_items = _vals.get("gen_종목", [])
    _pension_ps_items  = _vals.get("ps_종목",  [])

    # STEP 5 — 시나리오 탭 로드 (실패해도 앱 중단 없음)
    st.write("🎯 시나리오 데이터 로드 중...")
    try:
        sc_df    = load_scenarios(SHEET_URL, SCENARIO_SHEET_GID)
        sc_names = sc_df["시나리오명"].unique().tolist() if not sc_df.empty else []
        # 기본적용=Y 시나리오 자동 감지
        if not sc_df.empty and "기본적용" in sc_df.columns:
            _default_sc_rows = sc_df[sc_df["기본적용"] == True]["시나리오명"].unique()
            sc_default_name  = _default_sc_rows[0] if len(_default_sc_rows) > 0 else ""
        else:
            sc_default_name = ""
    except Exception:
        sc_df           = pd.DataFrame()
        sc_names        = []
        sc_default_name = ""

    # STEP 5 — 가계부 로드
    st.write("📒 가계부 데이터 로드 중...")
    try:
        hh_df = load_household(SHEET_URL, HOUSEHOLD_SHEET_GID)
    except Exception:
        hh_df = pd.DataFrame()
    try:
        _wl_gid = WATCHLIST_SHEET_GID or SCENARIO_SHEET_GID
        wl_df = load_watchlist(SHEET_URL, _wl_gid)
    except Exception:
        wl_df = pd.DataFrame()

    st.write("✨ 준비 완료...")
    _cache_info = (
        "🔄 새로 로드됨"
        if not st.session_state.get("_data_loaded")
        else f"⚡ 캐시 사용 중 (갱신 주기: {DATA_TTL})"
    )
    st.session_state["_data_loaded"] = True

    _status.update(
        label=f"✅ 데이터 로드 완료  ·  {_cache_info}",
        state="complete",
        expanded=False,
    )


# ════════════════════════════════════════════════════════
# 4. 사이드바
# ════════════════════════════════════════════════════════
with st.sidebar:
    # ── 분배금 — 시트 자동값 + 조정 모드 ──────────────────
    st.header("💰 월 분배금")

    # ── 시트 기반 자동 계산값 ────────────────────────────
    # 수량×주당분배금 → 월 분배금 (신규 연금현황 탭 자동 연산)
    _irp_default_amt = int(irp_total * (default_palantir / 100))
    _isa_default_amt = int(isa_total  * (default_kodex    / 100))
    _ps_default_amt  = int(ps_total   * (default_ps       / 100)) if ps_total > 0 else 0

    # 시트 자동값 표시 카드
    _ps_auto_line = (f"<br>🏦 연금저축 <b>{_ps_default_amt:,.0f}원</b>") if ps_total > 0 else ""
    st.markdown(
        f"<div style='background:rgba(255,215,0,0.08); padding:8px 10px; "
        f"border-radius:8px; border-left:3px solid #FFD700; font-size:0.82rem;'>"
        f"<span style='color:rgba(255,255,255,0.5); font-size:0.75rem;'>시트 자동 계산 (수량×주당분배금)</span><br>"
        f"💼 IRP <b>{_irp_default_amt:,.0f}원</b>  "
        f"({default_palantir:.2f}%)<br>"
        f"📦 ISA <b>{_isa_default_amt:,.0f}원</b>  "
        f"({default_kodex:.2f}%)"
        + _ps_auto_line +
        f"</div>",
        unsafe_allow_html=True,
    )

    # 기본값: 시트 자동값 사용
    irp_income_input = _irp_default_amt
    isa_income_input = _isa_default_amt
    ps_income_input  = _ps_default_amt
    palantir_rate    = default_palantir / 100
    kodex_rate       = default_kodex    / 100
    ps_rate          = default_ps       / 100

    # ── 조정 모드 (필요 시만 사용) ───────────────────────
    _input_mode = st.radio(
        "분배금 조정",
        ["🔒 시트 자동값 사용", "💵 실입금액 입력", "📊 분배율(%) 조정"],
        index=0,
        key="input_mode_radio",
        horizontal=True,
    )

    if _input_mode == "💵 실입금액 입력":
        # 실제 입금된 분배금이 시트와 다를 때 조정
        st.caption("실제 입금된 분배금으로 수정하세요.")
        _irp_max_b = max(int(irp_total * 0.10), 100_000)
        _isa_max_b = max(int(isa_total * 0.10),  100_000)
        irp_income_input = st.number_input(
            "💼 IRP 실입금액 (원)",
            min_value=0, max_value=_irp_max_b,
            value=min(_irp_default_amt, _irp_max_b), step=10_000, key="irp_amt",
        )
        isa_income_input = st.number_input(
            "📦 ISA 실입금액 (원)",
            min_value=0, max_value=_isa_max_b,
            value=min(_isa_default_amt, _isa_max_b), step=10_000, key="isa_amt",
        )
        if ps_total > 0:
            _ps_max_b = max(int(ps_total * 0.10), 100_000)
            ps_income_input = st.number_input(
                "🏦 연금저축 실입금액 (원)",
                min_value=0, max_value=_ps_max_b,
                value=min(_ps_default_amt, _ps_max_b), step=10_000, key="ps_amt",
            )
        palantir_rate = irp_income_input / irp_total if irp_total > 0 else default_palantir / 100
        kodex_rate    = isa_income_input / isa_total  if isa_total  > 0 else default_kodex    / 100
        ps_rate       = ps_income_input  / ps_total   if ps_total   > 0 else default_ps       / 100
        _irp_diff = irp_income_input - _irp_default_amt
        _isa_diff = isa_income_input - _isa_default_amt
        if abs(_irp_diff) + abs(_isa_diff) > 0:
            st.caption(f"기준 대비  IRP {_irp_diff:+,.0f}원 / ISA {_isa_diff:+,.0f}원")

    elif _input_mode == "📊 분배율(%) 조정":
        # 미래 시나리오 시뮬레이션용
        st.caption("분배율 변경 시 예상 분배금을 시뮬레이션합니다.")
        palantir_rate = st.slider(
            "💼 IRP 월 분배율 (%)",
            min_value=0.5, max_value=3.0,
            value=float(default_palantir), step=0.1, key="irp_rate",
        ) / 100
        kodex_rate = st.slider(
            "📦 ISA 월 분배율 (%)",
            min_value=0.3, max_value=2.0,
            value=float(default_kodex), step=0.1, key="isa_rate",
        ) / 100
        if ps_total > 0:
            ps_rate = st.slider(
                "🏦 연금저축 월 분배율 (%)",
                min_value=0.0, max_value=3.0,
                value=float(default_ps), step=0.1, key="ps_rate_slider",
            ) / 100
        irp_income_input = int(irp_total * palantir_rate)
        isa_income_input = int(isa_total  * kodex_rate)
        ps_income_input  = int(ps_total   * ps_rate) if ps_total > 0 else 0
        st.caption(
            f"↳ IRP {irp_income_input:,.0f}원 / ISA {isa_income_input:,.0f}원"
            + (f" / 연금저축 {ps_income_input:,.0f}원" if ps_total > 0 else "")
        )
    st.divider()
    st.subheader("🎯 목표 생활비 조정")
    _tgt_base = float(target_monthly)
    target_monthly = st.number_input(
        "월 목표 생활비 (원)",
        min_value=500_000,
        max_value=15_000_000,
        value=int(_tgt_base),
        step=100_000,
        key="target_input",
        help=f"시트 기준값: {_tgt_base:,.0f}원",
    )
    _tgt_delta = target_monthly - _tgt_base
    if abs(_tgt_delta) > 0:
        st.caption(
            f"시트 기준 {_tgt_base/10000:.0f}만원 대비 "
            f"**{_tgt_delta/10000:+.0f}만원**"
        )

    # ── 계좌별 인출액 직접 지정 ───────────────────────────
    st.divider()
    st.subheader("🔧 계좌별 인출액 지정")
    st.caption("목표 부족분을 각 계좌에서 얼마씩 충당할지 직접 입력합니다.")

    # 공무원연금 세후 추정 (건보료 제외 기준 기본값)
    _pub_net_est = public_pension - (
        public_pension * 0.055 * 1.1
    )
    _shortfall = max(0.0, target_monthly - _pub_net_est)

    # 기본값: 부족분을 IRP 60% / ISA 30% / 일반 10% 배분
    _irp_w_def = int(_shortfall * 0.6 / 10000) * 10000
    _isa_w_def = int(_shortfall * 0.3 / 10000) * 10000
    _gen_w_def = int(_shortfall * 0.1 / 10000) * 10000

    _irp_max = max(int(irp_total), 1_000_000)   # 잔액 전체
    _isa_max = max(int(isa_total), 1_000_000)   # 잔액 전체
    _ps_max2 = max(int(ps_total),  1_000_000)   # 잔액 전체
    _gen_max = max(int(general_total) if general_total > 0 else 1_000_000, 1_000_000)

    irp_withdraw = st.number_input(
        "💼 IRP 월 인출액 (원)",
        min_value=0, max_value=_irp_max,
        value=min(_irp_w_def, _irp_max), step=50_000, key="irp_withdraw",
    )
    isa_withdraw = st.number_input(
        "📦 ISA 월 인출액 (원)",
        min_value=0, max_value=_isa_max,
        value=min(_isa_w_def, _isa_max), step=50_000, key="isa_withdraw",
    )
    _ps_w_def = int(_shortfall * 0.05 / 10000) * 10000
    _ps_max   = max(int(ps_total) if ps_total > 0 else 1_000_000, 1_000_000)
    ps_withdraw = st.number_input(
        "🏦 연금저축 월 인출액 (원)",
        min_value=0, max_value=_ps_max,
        value=min(_ps_w_def, _ps_max), step=50_000, key="ps_withdraw",
    )
    gen_withdraw = st.number_input(
        "💵 일반 월 인출액 (원)",
        min_value=0, max_value=_gen_max,
        value=min(_gen_w_def, _gen_max), step=50_000, key="gen_withdraw",
    )
    _total_withdraw = irp_withdraw + isa_withdraw + ps_withdraw + gen_withdraw
    _total_plan     = _pub_net_est + _total_withdraw
    _plan_color     = "#7dffb0" if _total_plan >= target_monthly else "#FF4B4B"
    st.markdown(
        f"<div style='font-size:0.82rem; margin-top:4px;'>"
        f"인출 합계: <b>{_total_withdraw:,.0f}원</b><br>"
        f"연금+인출 예상: "
        f"<b style='color:{_plan_color};'>{_total_plan:,.0f}원</b> "
        f"({'여유' if _total_plan >= target_monthly else '부족'} "
        f"{abs(_total_plan - target_monthly):,.0f}원)</div>",
        unsafe_allow_html=True,
    )
    # calc_withdrawal_plan용 가중치 역산
    irp_weight = irp_withdraw / max(_total_withdraw, 1)
    isa_weight = isa_withdraw / max(_total_withdraw, 1)
    gen_weight = gen_withdraw / max(_total_withdraw, 1)
    ps_weight  = ps_withdraw  / max(_total_withdraw, 1)

    # ── 세금 옵션 ─────────────────────────────────────────
    st.divider()
    st.subheader("⚙️ 세금 옵션")
    show_tax       = st.toggle("세후 실수령액 표시",  value=True)
    use_health_ins = False  # 건강보험료는 생활비에 포함 → 세후 계산에서 제외

    # 공무원연금 과세비율 설정
    # 소득세법 §12①: 2002년 이전 납부분은 비과세, 이후 납부분만 과세
    # 공무원연금공단이 적용하는 비율 = 2002년 이후 납부월수 / 전체 납부월수
    with st.expander("📋 공무원연금 과세비율 설정", expanded=False):
        st.caption(
            "공무원연금공단은 2002년 이전·이후 납부기간 비율에 따라 "
            "연금액 일부만 과세소득으로 처리합니다. "
            "공단 지급내역서의 실제 세금을 기준으로 조정하세요."
        )
        _pub_taxable_pct = st.slider(
            "과세 대상 비율 (%)",
            min_value=0, max_value=100,
            value=int(st.session_state.get("pub_taxable_pct", 93)),
            step=1,
            key="pub_taxable_pct",
            help=(
                "연금월액 중 과세소득으로 인정되는 비율.\n"
                "• 2002년 이전 입직: 낮을수록 세금 감소\n"
                "• 2002년 이후 입직: 100% 적용\n"
                "공단 지급내역서의 소득세 역산으로 확인 가능"
            ),
        )
        _pub_taxable_ratio = _pub_taxable_pct / 100.0
        # 현재 연금월액 기준 예상 세금 실시간 표시
        _pub_monthly_preview = float(_vals.get("public_pension", 0) or 0) if "_vals" in dir() else 0.0
        if _pub_monthly_preview > 0:
            _prev_annual     = _pub_monthly_preview * 12 * _pub_taxable_ratio
            _prev_deduct     = (
                13_420_000 if _prev_annual > 35_000_000
                else 12_420_000 + (_prev_annual - 25_000_000) * 0.10 if _prev_annual > 25_000_000
                else 10_220_000 + (_prev_annual - 14_000_000) * 0.20 if _prev_annual > 14_000_000
                else 7_700_000 + (_prev_annual - 7_700_000) * 0.40   if _prev_annual > 7_700_000
                else _prev_annual
            )
            _prev_taxable    = max(0.0, _prev_annual - _prev_deduct)
            _prev_tax_b4     = (
                840_000 + (_prev_taxable - 14_000_000) * 0.15 if _prev_taxable > 14_000_000
                else _prev_taxable * 0.06
            )
            _prev_tax        = max(0.0, _prev_tax_b4 - 900_000)
            _prev_monthly    = (_prev_tax * 1.1) / 12
            st.caption(
                f"예상 월 소득세(지방세 포함): **{_prev_monthly:,.0f}원** "
                f"(연금월액 {_pub_monthly_preview:,.0f}원 기준)"
            )

    # ── 시나리오 선택 ─────────────────────────────────
    st.divider()
    if st.button("🔄 실시간 데이터 전체 갱신",
                 use_container_width=True, key="global_refresh"):
        load_sheet.clear()
        load_and_validate.clear()
        fetch_watchlist_prices.clear()
        try:
            fetch_price_history.clear()
        except Exception:
            pass
        st.rerun()
    st.divider()

    st.subheader("🎯 포트폴리오 시나리오")

    # 시트 시나리오 선택 전용
    if sc_names:
            # 기본적용 시나리오 자동 선택 (최초 1회)
            _sc_opts = ["기본 시트 현황"] + sc_names

            sc_choice = st.selectbox(
                "시나리오 선택",
                _sc_opts,
                index=0,   # 항상 기본 시트 현황으로 시작
                key="sc_choice",
                help="시나리오 선택 시 해당 포트폴리오 기준으로 계산됩니다.",
            )
            # 기본적용=Y 시나리오 안내 (자동 선택 안 함)
            if sc_default_name:
                st.caption(f"📌 권장 시나리오: {sc_default_name}")

            # 구성 종목 표출 — 기본(연금현황) 포함 항상 표시
            if sc_choice == "기본 시트 현황":
                _disp_items = {
                    "IRP":    _pension_irp_items,
                    "ISA":    _pension_isa_items,
                    "일반":   _pension_gen_items,
                    "연금저축": _pension_ps_items,
                }
                _disp_source = "연금현황 시트"
            else:
                _sc_params = build_scenario_params(sc_df, sc_choice)
                _disp_items = {
                    acc_kr: _sc_params.get(f"{acc_en}_종목", [])
                    for acc_kr, acc_en in [("IRP","irp"),("ISA","isa"),("일반","gen"),("연금저축","ps")]
                }
                _disp_source = sc_choice

            # 계좌별 요약 캡션
            _sc_sum = []
            for acc_kr, acc_en in [("IRP","irp"),("ISA","isa"),("일반","gen"),("연금저축","ps")]:
                if sc_choice == "기본 시트 현황":
                    _t = {"IRP": irp_total, "ISA": isa_total, "일반": general_total,
                          "연금저축": ps_total}.get(acc_kr, 0)
                    _r = {"IRP": default_palantir, "ISA": default_kodex,
                          "일반": default_general/12, "연금저축": default_ps}.get(acc_kr, 0)
                else:
                    _t = _sc_params.get(f"{acc_en}_total", 0)
                    _r = _sc_params.get(f"{acc_en}_rate", 0) * 100
                if _t > 0:
                    _sc_sum.append(f"{acc_kr} {_t/100_000_000:.2f}억({_r:.2f}%)")
            st.caption(" / ".join(_sc_sum) if _sc_sum else "")

            with st.expander("구성 종목 보기"):
                for acc_kr, items in _disp_items.items():
                    if items:
                        st.markdown(f"**{acc_kr}**")
                        for it in items:
                            _원금   = float(it.get("원금", 0))
                            _분배율 = float(it.get("분배율(%)", 0))
                            _수량   = float(it.get("수량", 0))
                            _dps    = float(it.get("주당분배금", 0))
                            _월분배 = _수량 * _dps if _수량 > 0 and _dps > 0 else _원금 * _분배율 / 100
                            st.caption(
                                f"  {it.get('종목명','')} — "
                                + (f"{int(_수량):,}주 × {int(_dps):,}원 = {_월분배:,.0f}원/월"
                                   if _수량 > 0 and _dps > 0 else
                                   f"{_원금/10_000_000:.1f}천만원 / {_분배율:.2f}%")
                            )

# ════════════════════════════════════════════════════════
# 5. 계산
# ════════════════════════════════════════════════════════

# ── 시나리오 적용 ─────────────────────────────────────
_sc_applied  = False   # 시나리오 적용 여부 플래그
# 연금현황 신규 포맷: 기본 종목명은 시트에서 직접 가져옴
_irp_names   = [r["종목명"] for r in _pension_irp_items if r.get("종목명")]
_isa_names   = [r["종목명"] for r in _pension_isa_items if r.get("종목명")]
_gen_names   = [r["종목명"] for r in _pension_gen_items if r.get("종목명")]

if sc_choice != "기본 시트 현황" and not sc_df.empty:
    _sc = build_scenario_params(sc_df, sc_choice)

    # ★ 미입력 계좌: 명시적 0 초기화 (다른 시나리오/기본값 잔류 방지)
    # exists=True → 시나리오에 해당 계좌 행 있음 → 값 적용
    # exists=False → 시나리오에 해당 계좌 행 없음 → 0으로 초기화
    if _sc["irp_exists"]:
        if _sc["irp_total"] > 0: irp_total = _sc["irp_total"]
        if _sc["irp_rate"]  > 0:
            palantir_rate    = _sc["irp_rate"]
            irp_income_input = int(irp_total * palantir_rate)
    else:
        irp_total        = 0.0
        irp_income_input = 0

    if _sc["isa_exists"]:
        if _sc["isa_total"] > 0: isa_total = _sc["isa_total"]
        if _sc["isa_rate"]  > 0:
            kodex_rate       = _sc["isa_rate"]
            isa_income_input = int(isa_total * kodex_rate)
    else:
        isa_total        = 0.0
        isa_income_input = 0

    if _sc["ps_exists"]:
        if _sc.get("ps_total", 0) > 0: ps_total = _sc["ps_total"]
        if _sc.get("ps_rate",  0) > 0:
            ps_rate         = _sc["ps_rate"]
            ps_income_input = int(ps_total * ps_rate)
    else:
        ps_total        = 0.0
        ps_income_input = 0

    _gen_sc_exists = _sc["gen_exists"]
    if _gen_sc_exists and _sc["gen_total"] > 0:
        general_total = _sc["gen_total"]
    elif not _gen_sc_exists:
        general_total = 0.0

    _sc_applied = True

    # ★ 시나리오 원천비율을 슬라이더에 반영 (최초 1회)
    _sc_ratio = _sc.get("irp_personal_ratio", None)
    if _sc_ratio is not None and "irp_personal_ratio" not in st.session_state:
        st.session_state["irp_personal_ratio"] = int(_sc_ratio * 100)

    # 종목명 추출 — _sc 딕셔너리 우선, 없으면 sc_df 직접 파싱
    def _extract_names(acc_kr):
        key = {"IRP":"irp","ISA":"isa","일반":"gen"}.get(acc_kr,"")
        names = [r.get("종목명","") for r in _sc.get(f"{key}_종목",[]) if r.get("종목명","")]
        if names:
            return names
        if not sc_df.empty and "종목명" in sc_df.columns:
            sub = sc_df[(sc_df["시나리오명"]==sc_choice) & (sc_df["계좌"]==acc_kr)]
            return [str(n) for n in sub["종목명"].dropna().tolist() if str(n).strip()]
        return []
    _irp_names = _extract_names("IRP")
    _isa_names = _extract_names("ISA")
    _gen_names = _extract_names("일반")

# 이번 달 분배금 확정
# 시나리오 적용 시: 시나리오 분배율 기반 계산값 사용
# 기본 모드:       사이드바 직접 입력값 사용
irp_income   = float(irp_income_input)
isa_income   = float(isa_income_input)
ps_income    = float(ps_income_input) if ps_total > 0 else 0.0
# 일반 계좌 월 분배금 — 종목별 수량×DPS 합산 우선
def _calc_gen_monthly(items, total, annual_rate_pct):
    """일반 계좌 월 분배금 계산: 수량×DPS 합산 → 없으면 원금×분배율 → 없으면 연분배율÷12"""
    total_m = 0.0
    has_dps = False
    for it in items:
        qty  = float(it.get("수량", 0) or 0)
        dps  = float(it.get("주당분배금", 0) or 0)
        amt  = float(it.get("원금", 0) or 0)
        rate = float(it.get("분배율(%)", 0) or 0)
        nm   = str(it.get("종목명", ""))
        if "(원금)" in nm:   # 빈 행 더미 항목 제외
            continue
        if qty > 0 and dps > 0:
            total_m += qty * dps
            has_dps = True
        elif amt > 0 and rate > 0:
            total_m += amt * rate / 100
            has_dps = True
    if not has_dps and total > 0:
        total_m = total * (annual_rate_pct / 100 / 12)
    return total_m

# 시나리오 또는 연금현황 일반 종목 데이터 선택
_gen_items_for_calc = (
    _sc.get("gen_종목", []) if _sc_applied and "_sc" in dir() and _sc.get("gen_종목")
    else _pension_gen_items
)
_gen_annual_rate_pct = _vals.get("default_general", 2.88)
_gen_monthly_income  = _calc_gen_monthly(
    _gen_items_for_calc, general_total, _gen_annual_rate_pct
)
total_income = public_pension + irp_income + ps_income + isa_income + _gen_monthly_income

# ════════════════════════════════════════════════════════
# 세후 계산
# ════════════════════════════════════════════════════════
_irp_pension_yr = int(st.session_state.get("irp_pension_year", 1))
_current_age    = datetime.now().year - 1971  # birth_year 고정값
_pub_taxable_r  = float(st.session_state.get("pub_taxable_pct", 93)) / 100.0

# ── ① 원천비율 결정 (우선순위: 시나리오 > 시트자동 > 슬라이더) ──
if _sc_applied and "_sc" in dir():
    # 시나리오 모드: 시나리오 탭 메모 기반 자동계산
    _sc_ratio = _sc.get("irp_personal_ratio", None)
    _irp_personal_r = float(_sc_ratio) if _sc_ratio is not None else (
        float(st.session_state.get("irp_personal_ratio", 0)) / 100
    )
else:
    # 기본 모드: 연금현황 시트 자동계산 우선
    _irp_ratio_auto = float(_vals.get("irp_personal_ratio_auto", -1))
    if _irp_ratio_auto >= 0:
        _irp_personal_r = _irp_ratio_auto
        if "irp_personal_ratio" not in st.session_state:
            st.session_state["irp_personal_ratio"] = int(_irp_ratio_auto * 100)
    else:
        _irp_personal_r = float(st.session_state.get("irp_personal_ratio", 0)) / 100

# ── ② 분배금과세 시트 → 실제 과세표준 계산 (기본모드 전용) ──
_now_ym = datetime.now().strftime("%Y-%m")
_taxbase_result = {"has_data": False}

if not _sc_applied:
    # 분배금과세 시트 로드
    @st.cache_data(ttl=DATA_TTL, show_spinner=False)
    def _load_dist_tax_inner(url: str, gid: str) -> pd.DataFrame:
        try:
            from pension_tax_monitor import load_dist_tax_sheet
            return load_dist_tax_sheet(url, gid)
        except Exception:
            return pd.DataFrame()

    _dist_tax_gid = st.secrets.get("DIST_TAX_SHEET_GID", "")
    try:
        dist_tax_df = _load_dist_tax_inner(SHEET_URL, _dist_tax_gid)
    except Exception:
        dist_tax_df = pd.DataFrame()

    # 과세표준 계산
    try:
        _taxbase_result = calc_irp_taxbase_from_sheet(
            dist_tax_df=dist_tax_df,
            year_month=_now_ym,
            pension_items=_pension_irp_items,
        )
    except Exception:
        _taxbase_result = {"has_data": False}
else:
    # 시나리오 모드: 과세표준 시트 연동 안 함
    dist_tax_df = pd.DataFrame()

# ── ③ 세후 계산: irp_income은 항상 실제 분배금으로 전달 ──────
# calc_after_tax의 irp_income 인자 = 실제 분배금 (변경 없음)
# 과세표준은 세금만 따로 보정하는 방식으로 처리
tax_result = calc_after_tax(
    public_pension, irp_income, isa_income, ps_income,
    irp_total=irp_total,
    irp_pension_year=_irp_pension_yr,
    irp_personal_ratio=_irp_personal_r,
    age=_current_age,
    pub_taxable_ratio=_pub_taxable_r,
)

# ── ④ 과세표준 기반 IRP 세금 보정 (시트 데이터 있을 때만) ──────
# 원리: 실제 세금은 과세표준 기준, 실수령은 실제 분배금 기준
if _taxbase_result.get("has_data"):
    _irp_taxbase     = _taxbase_result.get("irp_taxbase_monthly", 0.0)
    _irp_dist_in_sh  = _taxbase_result.get("irp_dist_monthly", 0.0)
    # 미입력 종목: 분배금 전액을 과세표준으로 보수적 처리
    _irp_not_in_sh   = max(0.0, irp_income - _irp_dist_in_sh)
    _irp_taxbase_tot = _irp_taxbase + _irp_not_in_sh

    # 과세표준 기준 세금 재계산 (퇴직금 원천 기준)
    _irp_yr_v   = max(1, min(_irp_pension_yr, 10))
    _limit_ann  = irp_total / (11 - _irp_yr_v) * 1.2 if irp_total > 0 else 20_900_000
    _limit_mon  = _limit_ann / 12
    _pers_part  = _irp_taxbase_tot * _irp_personal_r
    _ret_part   = _irp_taxbase_tot * (1 - _irp_personal_r)
    _within     = min(_ret_part, _limit_mon)
    _excess     = max(0.0, _ret_part - _limit_mon)
    _age_key    = "80s" if _current_age >= 80 else ("70s" if _current_age >= 70 else "60s")
    _pers_rate  = IRP_PENSION_PERSONAL_TAX[_age_key]
    _irp_tax_corr = (_within * IRP_PENSION_TAX_WITHIN
                     + _excess * IRP_PENSION_TAX_EXCESS
                     + _pers_part * _pers_rate)

    # 세금만 보정, 실수령은 실제 분배금 기준
    _irp_net_corr = irp_income - _irp_tax_corr
    _tax_diff     = _irp_tax_corr - tax_result["IRP_세금"]

    tax_result["IRP_세금"]   = _irp_tax_corr
    tax_result["IRP_세후"]   = _irp_net_corr
    tax_result["총_공제액"]  += _tax_diff
    tax_result["총_세후"]    -= _tax_diff
    tax_result["실효세율"]   = (
        tax_result["총_공제액"] / tax_result["총_세전"] * 100
        if tax_result["총_세전"] > 0 else 0
    )
if not use_health_ins:
    tax_result["공적연금_세후"]  += tax_result["공적연금_건보료"]
    tax_result["총_세후"]        += tax_result["공적연금_건보료"]
    tax_result["총_공제액"]      -= tax_result["공적연금_건보료"]
    tax_result["실효세율"]        = (
        tax_result["총_공제액"] / tax_result["총_세전"] * 100
        if tax_result["총_세전"] > 0 else 0
    )

display_income = tax_result["총_세후"] if show_tax else total_income

# ── ⑤ 일반계좌 과세표준 기반 세금 계산 후 display_income 합산 ──────
# calc_after_tax는 공무원연금·IRP·ISA·연금저축만 포함
# 일반계좌(gen)는 별도 계산 → display_income에 합산

# 과세표준 비율: 시나리오 종목별 과세표준(원) / 주당분배금 가중평균
_gen_taxbase_ratio = 1.0   # 기본: 전액 과세표준 (보수적)
if _sc_applied and "_sc" in dir():
    _g_dist_sum    = 0.0
    _g_taxbase_sum = 0.0
    for _git in _sc.get("gen_종목", []):
        _g_qty    = float(_git.get("수량",         0) or 0)
        _g_dps    = float(_git.get("주당분배금",    0) or 0)
        _g_taxdps = float(_git.get("과세표준(원)",  0) or 0)
        if _g_qty > 0 and _g_dps > 0:
            _g_dist_sum    += _g_qty * _g_dps
            _g_taxbase_sum += _g_qty * _g_taxdps
    if _g_dist_sum > 0:
        _gen_taxbase_ratio = _g_taxbase_sum / _g_dist_sum   # 예: 0.0503 (5.03%)

_gen_taxbase_monthly = _gen_monthly_income * _gen_taxbase_ratio
_gen_tax_monthly     = _gen_taxbase_monthly * 0.154
_gen_net_monthly     = _gen_monthly_income - _gen_tax_monthly

if show_tax:
    display_income += _gen_net_monthly
else:
    display_income += _gen_monthly_income
achievement    = (display_income / target_monthly) * 100 if target_monthly > 0 else 0

# ── 목표 달성 역산 계획 ──────────────────────────────────
withdrawal_plan = calc_withdrawal_plan(
    target_monthly   = target_monthly,
    public_pension_net = public_pension,
    irp_total        = irp_total,
    isa_total        = isa_total,
    general_total    = general_total,
    irp_weight       = float(irp_weight),
    isa_weight       = float(isa_weight),
    general_weight   = float(gen_weight),
    use_after_tax    = show_tax,
    use_health_ins   = use_health_ins,
    ps_total         = ps_total,
    ps_weight        = float(ps_weight) if ps_total > 0 else 0.0,
)


# ════════════════════════════════════════════════════════
# 6. 메인 화면
# ════════════════════════════════════════════════════════
st.markdown(
    "<h1 style='font-size:1.4rem; font-weight:700; margin-bottom:0.3rem;'>"
    "🚀 연금자산 현금흐름 관제탑</h1>",
    unsafe_allow_html=True,
)
# 시나리오 배지
if sc_choice != "기본 시트 현황" and sc_names:
    st.markdown(
        f"<span style='background:rgba(255,215,0,0.15); color:#FFD700; "
        f"padding:3px 12px; border-radius:12px; font-size:0.82rem; "
        f"border:1px solid rgba(255,215,0,0.3);'>🎯 시나리오: {sc_choice}</span>",
        unsafe_allow_html=True,
    )

# ── 메인 탭 ──────────────────────────────────────────
_main_tab1, _main_tab2, _main_tab3, _main_tab4, _main_tab5, _main_tab6, _main_tab7, _main_tab8, _main_tab9 = st.tabs([
    "📊 현금흐름 대시보드", "📒 월별 가계부",
    "📈 보유종목", "🔍 관심종목",
    "📐 수익률 벤치마크", "🎲 Monte Carlo",
    "🤖 AI 자문", "🏦 과세관리",
    "♻️ 재투자 시뮬레이터",
])

with _main_tab2:
    _render_household_tab(hh_df, display_income, target_monthly, public_pension,
                          irp_income, isa_income, now_kst=datetime.now())

with _main_tab3:
    _render_holdings_tab(
        pension_items={
            "IRP":    _pension_irp_items,
            "ISA":    _pension_isa_items,
            "일반":   _pension_gen_items,
            "연금저축": _pension_ps_items,
        },
        sc_df=sc_df, sc_choice=sc_choice, wl_df=wl_df,
        irp_total=irp_total, isa_total=isa_total, gen_total=general_total,
        ps_total=ps_total,
    )

with _main_tab4:
    _render_watchlist_tab(
        wl_df=wl_df,
        irp_total=irp_total, isa_total=isa_total,
        general_total=general_total, ps_total=ps_total,
        public_pension=public_pension,
        target_monthly=target_monthly,
        show_tax=show_tax,
        sc_df=sc_df, sc_names=sc_names,
        dist_tax_df=dist_tax_df if "dist_tax_df" in dir() else None,
    )

with _main_tab1:
    tax_label = "세후 " if show_tax else "세전 "
    st.markdown(
        f"### 현재 예상 월 수입 ({tax_label}): "
        f"**{display_income:,.0f}원**"
        + (f"  <span style='font-size:0.9rem; color:rgba(255,255,255,0.4);'>"
           f"(세전 {total_income:,.0f}원)</span>" if show_tax else ""),
        unsafe_allow_html=True,
    )

    # ── 게이지 + 메트릭 레이아웃 ──────────────────────────
    gauge_col, metric_col = st.columns([1, 1])

    with gauge_col:
        # 달성률 단계별 색상 정의
        # 0~50%: 빨강 / 50~80%: 주황 / 80~100%: 노랑 / 100~150%: 초록 / 150%+: 파랑
        def gauge_color(val: float) -> str:
            if val < 50:   return "#FF4B4B"
            if val < 80:   return "#FF8C00"
            if val < 100:  return "#FFD700"
            if val < 150:  return "#7dffb0"
            return "#87CEEB"

        def gauge_label(val: float) -> str:
            if val < 50:   return "⚠️ 위험 — 대폭 부족"
            if val < 80:   return "🔶 주의 — 부족"
            if val < 100:  return "🟡 근접 — 목표 미달"
            if val < 150:  return "✅ 달성 — 목표 초과"
            return "💎 우수 — 여유 충분"

        g_color = gauge_color(achievement)
        g_label = gauge_label(achievement)
        # 게이지 최대값: 200% 고정 (초과 달성도 표시)
        g_max   = 200

        fig_gauge = go.Figure(go.Indicator(
            mode="gauge+number+delta",
            value=achievement,
            number=dict(suffix="%", font=dict(size=40, color=g_color)),
            delta=dict(
                reference=100,
                suffix="%p",
                increasing=dict(color="#7dffb0"),
                decreasing=dict(color="#FF4B4B"),
            ),
            gauge=dict(
                axis=dict(
                    range=[0, g_max],
                    tickwidth=1,
                    tickcolor="rgba(255,255,255,0.3)",
                    tickfont=dict(color="rgba(255,255,255,0.5)", size=10),
                    dtick=50,
                ),
                bar=dict(color=g_color, thickness=0.25),
                bgcolor="rgba(255,255,255,0.03)",
                borderwidth=0,
                steps=[
                    dict(range=[0,   50],  color="rgba(255,75,75,0.12)"),
                    dict(range=[50,  80],  color="rgba(255,140,0,0.12)"),
                    dict(range=[80,  100], color="rgba(255,215,0,0.12)"),
                    dict(range=[100, 150], color="rgba(125,255,176,0.12)"),
                    dict(range=[150, 200], color="rgba(135,206,235,0.12)"),
                ],
                threshold=dict(
                    line=dict(color="white", width=2),
                    thickness=0.8,
                    value=100,
                ),
            ),
            title=dict(
                text=f"목표 달성률<br><span style='font-size:0.85rem; color:{g_color};'>{g_label}</span>",
                font=dict(size=15, color="rgba(255,255,255,0.85)"),
            ),
        ))
        fig_gauge.update_layout(
            height=280,
            paper_bgcolor="rgba(0,0,0,0)",
            font_color="white",
            margin=dict(t=60, b=10, l=30, r=30),
        )
        st.plotly_chart(fig_gauge, use_container_width=True)

        # 단계 범례
        st.markdown("""
            <div style='display:flex; flex-wrap:wrap; gap:6px; font-size:0.78rem; margin-top:-8px;'>
                <span style='background:rgba(255,75,75,0.15); padding:3px 8px; border-radius:10px;
                             border:1px solid rgba(255,75,75,0.4); color:#FF4B4B;'>0~50% 위험</span>
                <span style='background:rgba(255,140,0,0.15); padding:3px 8px; border-radius:10px;
                             border:1px solid rgba(255,140,0,0.4); color:#FF8C00;'>50~80% 주의</span>
                <span style='background:rgba(255,215,0,0.15); padding:3px 8px; border-radius:10px;
                             border:1px solid rgba(255,215,0,0.4); color:#FFD700;'>80~100% 근접</span>
                <span style='background:rgba(125,255,176,0.15); padding:3px 8px; border-radius:10px;
                             border:1px solid rgba(125,255,176,0.4); color:#7dffb0;'>100~150% 달성</span>
                <span style='background:rgba(135,206,235,0.15); padding:3px 8px; border-radius:10px;
                             border:1px solid rgba(135,206,235,0.4); color:#87CEEB;'>150%+ 우수</span>
            </div>
        """, unsafe_allow_html=True)

    with metric_col:
        st.markdown("#### 📊 현황 요약")
        with st.container(border=True):
            # 월 수입 vs 목표
            surplus = display_income - target_monthly
            surplus_color = "#7dffb0" if surplus >= 0 else "#FF4B4B"
            surplus_label = "여유" if surplus >= 0 else "부족"
            st.markdown(
                f"<div style='display:flex; justify-content:space-between; "
                f"padding:8px 0; border-bottom:1px solid rgba(255,255,255,0.06);'>"
                f"<span style='color:rgba(255,255,255,0.6);'>월 {'세후' if show_tax else '세전'} 수입</span>"
                f"<span style='font-weight:700;'>{display_income:,.0f}원</span></div>"

                f"<div style='display:flex; justify-content:space-between; "
                f"padding:8px 0; border-bottom:1px solid rgba(255,255,255,0.06);'>"
                f"<span style='color:rgba(255,255,255,0.6);'>목표 생활비</span>"
                f"<span style='font-weight:700;'>{target_monthly:,.0f}원"
                + (f" <span style='font-size:0.78rem; color:#FFD700;'>({_tgt_delta/10000:+.0f}만)</span>"
                   if abs(_tgt_delta) > 0 else "")
                + f"</span></div>"

                f"<div style='display:flex; justify-content:space-between; "
                f"padding:8px 0; border-bottom:1px solid rgba(255,255,255,0.06);'>"
                f"<span style='color:rgba(255,255,255,0.6);'>월 {surplus_label}액</span>"
                f"<span style='font-weight:700; color:{surplus_color};'>{surplus:+,.0f}원</span></div>"

                f"<div style='display:flex; justify-content:space-between; "
                f"padding:8px 0; border-bottom:1px solid rgba(255,255,255,0.06);'>"
                f"<span style='color:rgba(255,255,255,0.6);'>총 세전 수입</span>"
                f"<span style='font-weight:700;'>{total_income:,.0f}원</span></div>"

                f"<div style='display:flex; justify-content:space-between; "
                f"padding:8px 0;'>"
                f"<span style='color:rgba(255,255,255,0.6);'>총 공제액 (실효 {tax_result['실효세율']:.1f}%)</span>"
                f"<span style='font-weight:700; color:#FF4B4B;'>-{tax_result['총_공제액']:,.0f}원</span></div>",
                unsafe_allow_html=True,
            )

        st.info("💡 8월 알프스 여정 대비 현금 흐름을 점검 중입니다.")

    st.divider()

    # ── 계좌별 인출 계획 섹션 ────────────────────────────────
    st.markdown("#### 🏦 목표 생활비 달성을 위한 계좌별 인출 조정 플랜")
    st.caption(
        "목표 생활비 변동 시 각 계좌에서 얼마를 인출해야 하는지 자동 계산합니다. "
        "사이드바 **계좌별 인출 비중** 슬라이더로 IRP·ISA·일반 배분 비율을 조정하세요."
    )

    wp = withdrawal_plan
    _surplus_col = "#7dffb0" if wp["gap"] >= 0 else "#FF4B4B"
    _surplus_lbl = "여유" if wp["gap"] >= 0 else "부족"

    if wp["shortage"] <= 0:
        st.success(
            f"✅ 공무원연금({tax_result['공적연금_세후']:,.0f}원)만으로 "
            f"목표 생활비를 충당할 수 있습니다. "
            f"월 **{abs(wp['gap']):,.0f}원** 여유"
        )
    else:
        # 부족분 요약
        sh1, sh2, sh3 = st.columns(3)
        sh1.metric("공무원연금 세후", f"{tax_result['공적연금_세후']:,.0f}원")
        sh2.metric("월 부족분",
                   f"{wp['shortage']:,.0f}원",
                   delta=f"목표 {target_monthly/10000:.0f}만원 기준",
                   delta_color="inverse")
        sh3.metric("달성 예상 세후",
                   f"{wp['total_net_est']:,.0f}원",
                   delta=f"{wp['gap']:+,.0f}원",
                   delta_color="normal" if wp["gap"] >= 0 else "inverse")

        st.markdown("**각 계좌별 필요 인출액 및 권장 분배율**")
        _has_ps_card = ps_total > 0
        if _has_ps_card:
            w1, w2, w3, w4 = st.columns(4)
        else:
            w1, w2, w3 = st.columns(3)
            w4 = None

        # ── 수량·DPS 데이터 준비 — 시나리오 종목 우선 ────
        def _sc_shares_dps(acc_kr):
            """시나리오 종목에서 수량·DPS 추출. 없으면 원금÷현재가 역산."""
            key = {"IRP":"irp","ISA":"isa","일반":"gen"}.get(acc_kr,"irp")
            items = _sc.get(f"{key}_종목", []) if _sc_applied else []
            total_qty, total_dps = 0.0, 0.0
            for it in items:
                qty  = float(it.get("수량", 0) or 0)
                dps  = float(it.get("주당분배금", 0) or 0)
                amt  = float(it.get("원금", 0) or 0)
                price= float(it.get("현재가", 0) or 0)
                # 수량 없으면 원금÷현재가 역산
                if qty == 0 and amt > 0 and price > 0:
                    qty = amt / price
                total_qty += qty
                if dps > 0:
                    total_dps = dps  # 마지막 종목 DPS (단일 종목 기준)
            return total_qty, total_dps

        # 시나리오 적용 여부에 따라 참조 데이터 결정
        if _sc_applied and not sc_df.empty:
            _irp_sc = _sc if "_sc" in dir() else {}
            _irp_shares_cur, _irp_dps_cur = _sc_shares_dps("IRP")
            _isa_shares_cur, _isa_dps_cur = _sc_shares_dps("ISA")
            # 시나리오에 수량 없으면 연금현황 폴백
            if _irp_shares_cur == 0:
                _irp_shares_cur = float(st.session_state.get("irp_shares_input",
                                         _vals.get("irp_shares", 0)))
            if _isa_shares_cur == 0:
                _isa_shares_cur = float(st.session_state.get("isa_shares_input",
                                         _vals.get("isa_shares", 0)))
            if _irp_dps_cur == 0:
                _irp_dps_cur = float(st.session_state.get("irp_dps",
                                      _vals.get("irp_dps_default", 0)))
            if _isa_dps_cur == 0:
                _isa_dps_cur = float(st.session_state.get("isa_dps",
                                      _vals.get("isa_dps_default", 0)))
        else:
            _irp_shares_cur = float(st.session_state.get("irp_shares_input",
                                     _vals.get("irp_shares", 0)))
            _isa_shares_cur = float(st.session_state.get("isa_shares_input",
                                     _vals.get("isa_shares", 0)))
            _irp_dps_cur    = float(st.session_state.get("irp_dps",
                                     _vals.get("irp_dps_default", 0)))
            _isa_dps_cur    = float(st.session_state.get("isa_dps",
                                     _vals.get("isa_dps_default", 0)))

        # ── 계좌간 원금 조정 분석 ─────────────────────────
        # 세후 효율 비교: ISA > IRP (ISA 비과세 한도 내)
        _total_invest    = irp_total + isa_total + general_total
        _irp_eff         = 1 - IRP_TAX_RATE           # 0.945
        _isa_eff         = 1 - 0.0                    # 비과세 한도 내
        _gen_eff         = 1 - 0.154                  # 0.846

        # ── 관심종목 탭 연계 — 목표 분배율 달성 가능 종목 ─
        _wl_suggestions  = []
        if not wl_df.empty and "월분배율(%)" in wl_df.columns:
            for _, _wr in wl_df.iterrows():
                _wn   = str(_wr.get("종목명",""))
                _wacc = str(_wr.get("계좌",""))
                _wrat = float(_wr.get("월분배율(%)", 0))
                if _wrat > 0:
                    _wl_suggestions.append({
                        "종목명": _wn, "계좌": _wacc, "분배율": _wrat,
                    })

        def _withdrawal_card_v2(col, label, color,
                                need_gross, rate_suggest, total_asset,
                                current_rate, actual_income,
                                shares_cur=0, dps_cur=0,
                                acc_key="irp", isa_limit=ISA_LIMIT):
            with col:
                with st.container(border=True):
                    # 헤더
                    st.markdown(
                        f"<div style='color:{color}; font-weight:700; "
                        f"font-size:0.95rem; margin-bottom:6px;'>{label}</div>",
                        unsafe_allow_html=True,
                    )

                    # ISA 전용: 납입 한도 제약 표시
                    if acc_key == "isa":
                        _isa_used_pct = (total_asset / isa_limit * 100) if isa_limit > 0 else 0
                        _isa_remain   = max(0, isa_limit - total_asset)
                        _limit_c      = "#FF4B4B" if _isa_used_pct >= 100 else "#FFD700"
                        st.markdown(
                            f"<div style='font-size:0.75rem; padding:4px 8px; "
                            f"border-radius:4px; background:rgba(255,215,0,0.08); "
                            f"margin-bottom:6px;'>"
                            f"<span style='color:{_limit_c};'>🔒 ISA 납입 한도</span>  "
                            f"현재 {total_asset/10_000_000:.0f}천만 / "
                            f"한도 {isa_limit/10_000_000:.0f}천만원  "
                            f"{'<b>한도 소진</b>' if _isa_remain==0 else f'잔여 {_isa_remain/10_000_000:.0f}천만'}"
                            f"</div>"
                            f"<div style='font-size:0.72rem; color:rgba(255,165,0,0.8); "
                            f"margin-bottom:4px;'>"
                            f"⚠️ 원금만 자유 인출 · 이익은 만기 후 비과세 수령</div>",
                            unsafe_allow_html=True,
                        )

                    # ① 과부족 표시
                    _surplus     = actual_income - need_gross
                    _surplus_c   = "#7dffb0" if _surplus >= 0 else "#FF4B4B"
                    _surplus_lbl = "여유" if _surplus >= 0 else "부족"

                    r1, r2 = st.columns(2)
                    r1.metric("지정 인출액", f"{need_gross:,.0f}원",
                              help="목표 생활비 달성을 위해 이 계좌에서 필요한 세전 금액")
                    r2.metric("실제 수령액", f"{actual_income:,.0f}원",
                              delta=f"{_surplus:+,.0f}원 ({_surplus_lbl})",
                              delta_color="normal" if _surplus >= 0 else "inverse")

                    st.markdown(
                        f"<div style='text-align:center; padding:4px 0; "
                        f"border-radius:6px; font-size:0.82rem; font-weight:700; "
                        f"background:rgba({"125,255,176" if _surplus>=0 else "255,75,75"},0.12);'>"
                        f"{'✅' if _surplus>=0 else '⚠️'} "
                        f"{'초과 ' if _surplus>=0 else '부족 '}"
                        f"{abs(_surplus):,.0f}원</div>",
                        unsafe_allow_html=True,
                    )

                    st.divider()

                    # ② 권장 분배율
                    _rate_delta = rate_suggest - current_rate * 100
                    st.metric("권장 분배율",
                              f"{rate_suggest:.2f}%",
                              delta=f"현재 {current_rate*100:.2f}% → {_rate_delta:+.2f}%p",
                              delta_color="inverse" if _rate_delta > 0 else "normal")

                    # ③ 수량 조정 방안
                    if shares_cur > 0 and dps_cur > 0 and need_gross > 0:
                        _shares_needed = int(need_gross / dps_cur)
                        _shares_diff   = _shares_needed - int(shares_cur)
                        _diff_lbl      = f"{'▲' if _shares_diff>0 else '▼'} {abs(_shares_diff):,}주"
                        _diff_c        = "#FF4B4B" if _shares_diff > 0 else "#7dffb0"
                        st.markdown(
                            f"<div style='font-size:0.78rem; margin-top:2px;'>"
                            f"<span style='color:rgba(255,255,255,0.5);'>수량 조정: </span>"
                            f"{int(shares_cur):,}주 → {_shares_needed:,}주 "
                            f"<span style='color:{_diff_c}; font-weight:700;'>"
                            f"({_diff_lbl})</span></div>",
                            unsafe_allow_html=True,
                        )

                    # ④ 잔액 유지 연수
                    if total_asset > 0 and need_gross > 0:
                        _months = total_asset / need_gross
                        _yrs    = int(_months // 12) if _months < 1_200 else 999
                        st.caption(
                            f"잔액 {total_asset/100_000_000:.1f}억 · "
                            + (f"약 {_yrs}년 유지" if _yrs < 100 else "기대수명 초과")
                        )

        # 실제 수령액 (세전 기준)
        # general_rate_hm = 연분배율/100/12 (월 환산율)
        _gen_actual      = _gen_monthly_income   # 시나리오/연금현황 수량×DPS 기반

        # 순서: IRP → ISA → 연금저축 → 일반
        _withdrawal_card_v2(
            w1, "💼 IRP", "#FFD700",
            wp["irp_need_gross"], wp["irp_rate_suggest"],
            irp_total, palantir_rate, irp_income,
            shares_cur=_irp_shares_cur, dps_cur=_irp_dps_cur, acc_key="irp",
        )
        _withdrawal_card_v2(
            w2, "📦 ISA", "#FF4B4B",
            wp["isa_need_gross"], wp["isa_rate_suggest"],
            isa_total, kodex_rate, isa_income,
            shares_cur=_isa_shares_cur, dps_cur=_isa_dps_cur,
            acc_key="isa", isa_limit=float(isa_limit),
        )
        if _has_ps_card and w3 is not None:
            _withdrawal_card_v2(
                w3, "🏦 연금저축", "#5DCAA5",
                wp.get("ps_need_gross", 0.0), wp.get("ps_rate_suggest", 0.0),
                ps_total, ps_rate, ps_income,
                acc_key="ps",
            )
        _gen_col = w4 if _has_ps_card and w4 is not None else w3
        _withdrawal_card_v2(
            _gen_col, "💵 일반", "#87CEEB",
            wp["gen_need_gross"], wp["gen_rate_suggest"],
            general_total, (_gen_monthly_income / general_total if general_total > 0 else 0), _gen_actual,
            acc_key="gen",
        )

        # ── 계좌간 원금 조정 제안 ────────────────────────
        st.divider()
        with st.expander("💡 최적화 제안", expanded=False):
            # ② 계좌간 원금 조정
            st.markdown("**계좌간 투자원금 조정**")
            st.caption(
                f"세후 효율: ISA({_isa_eff*100:.0f}%) > IRP({_irp_eff*100:.1f}%) > 일반({_gen_eff*100:.1f}%)  "
                f"· 총 투자원금 {_total_invest/100_000_000:.2f}억원"
            )
            # ISA 비과세 한도(연 200만원) 기준 최적 배분
            _isa_opt_for_tax_free = ISA_TAX_FREE_MONTHLY * 12 / (
                float(_vals.get("default_kodex", 1.42)) / 100
            ) if float(_vals.get("default_kodex", 1.42)) > 0 else 0
            oc1, oc2, oc3 = st.columns(3)
            oc1.metric("ISA 비과세 최적 원금",
                       f"{_isa_opt_for_tax_free/100_000_000:.2f}억",
                       delta=f"현재 {isa_total/100_000_000:.2f}억 대비 "
                             f"{(_isa_opt_for_tax_free-isa_total)/10_000_000:+.0f}천만",
                       help="ISA 연 200만원 비과세 한도를 정확히 소진하는 최적 원금")
            oc2.metric("IRP 현재 원금",
                       f"{irp_total/100_000_000:.2f}억",
                       help="IRP는 연금소득세 5.5% 일괄 적용")
            oc3.metric("총 세금 절감 가능",
                       f"{(ISA_TAX_FREE_MONTHLY * 0.099):,.0f}원/월",
                       help="ISA 비과세 한도 내 절감액")

            # ③ 관심종목 탭 연계 신규 편입 제안
            if _wl_suggestions:
                st.divider()
                st.markdown("**관심종목 편입 시 달성률 시뮬레이션**")
                st.caption("관심종목 탭의 종목을 현재 계좌에 편입할 경우 예상 달성률입니다.")
                _sug_cols = st.columns(min(len(_wl_suggestions), 3))
                for _si, _sg in enumerate(_wl_suggestions[:3]):
                    _sg_acc   = _sg["계좌"]
                    _sg_bal   = {"IRP": irp_total,"ISA": isa_total,"일반": general_total}.get(_sg_acc, 0)
                    _sg_gross = _sg_bal * _sg["분배율"] / 100
                    _sg_tax   = IRP_TAX_RATE if _sg_acc in ["IRP","연금저축"] else 0.099
                    _sg_net   = _sg_gross * (1 - _sg_tax) + public_pension
                    _sg_ach   = _sg_net / target_monthly * 100 if target_monthly > 0 else 0
                    _sg_c     = "#7dffb0" if _sg_ach >= 100 else "#FFD700" if _sg_ach >= 80 else "#FF4B4B"
                    with _sug_cols[_si]:
                        with st.container(border=True):
                            st.markdown(
                                f"<div style='font-size:0.78rem; font-weight:700;'>"
                                f"{_sg['종목명'][:16]}</div>"
                                f"<div style='font-size:0.72rem; color:rgba(255,255,255,0.5);'>"
                                f"{_sg_acc} · {_sg['분배율']:.2f}%</div>",
                                unsafe_allow_html=True,
                            )
                            st.metric("달성률", f"{_sg_ach:.0f}%",
                                      delta=f"{_sg_net/10000:.0f}만원",
                                      delta_color="normal" if _sg_ach >= 100 else "inverse")

        # 권장 분배율 적용 시 고갈 시점 간단 추정
        with st.expander("📊 권장 분배율 적용 시 고갈 예상", expanded=False):
            ec1, ec2 = st.columns(2)
            _start_yr = datetime.now().year   # ✅ 하드코딩 제거
            for col, asset_name, asset_val, rate_s in [
                (ec1, "IRP", irp_total, wp["irp_rate_suggest"] / 100),
                (ec2, "ISA", isa_total, wp["isa_rate_suggest"] / 100),
            ]:
                if asset_val > 0 and rate_s > 0:
                    bal  = asset_val
                    year = _start_yr
                    while bal > 0 and year < 2100:
                        bal = max(0.0, bal - bal * rate_s * 12)
                        year += 1
                    # birth_year는 타임라인 사이드바(이 섹션보다 뒤)에서 정의됨
                    # → session_state 또는 기본값으로 안전하게 읽기
                    _birth_yr = int(st.session_state.get("birth_year_input", 1971))
                    exhaust_age = year - _birth_yr
                    col.metric(
                        f"{asset_name} 고갈 시점",
                        f"{year}년 ({exhaust_age}세)" if year < 2100 else "고갈 없음",
                        delta=f"{year - _start_yr}년 후" if year < 2100 else "✅ 충분",
                        delta_color="inverse" if year < 2100 else "normal",
                    )
                else:
                    col.metric(f"{asset_name} 고갈 시점", "해당 없음")

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
            st.markdown("**💼 IRP**")
            da, db = st.columns(2)
            da.metric("세전", f"{tax_result['IRP_세전']:,.0f}원")
            _irp_eff_rate = (tax_result["IRP_세금"] / tax_result["IRP_세전"] * 100
                               if tax_result["IRP_세전"] > 0 else 0)
            db.metric(f"퇴직연금세 {_irp_eff_rate:.2f}%", f"-{tax_result['IRP_세금']:,.0f}원",
                      delta_color="inverse")
            st.markdown(
                f"<div style='text-align:right; font-size:1.1rem; font-weight:700; color:#7dffb0;'>"
                f"실수령 {tax_result['IRP_세후']:,.0f}원</div>",
                unsafe_allow_html=True
            )
            # 한도 정보 표시
            _irp_lm = tax_result.get("IRP_한도월", 0)
            _irp_ex = tax_result.get("IRP_한도초과", 0)
            if _irp_lm > 0:
                _lm_color = "#FF4B4B" if _irp_ex > 0 else "rgba(255,255,255,0.4)"
                st.markdown(
                    f"<div style='font-size:0.72rem; color:{_lm_color}; margin-top:2px;'>"
                    f"{'⚠️ 한도초과 ' + f'{_irp_ex:,.0f}원/월 → 1.1% 적용' if _irp_ex > 0 else '✅ 한도 내 수령 (0.76%)'}"
                    f" | 월한도 {_irp_lm:,.0f}원</div>",
                    unsafe_allow_html=True,
                )
            # 원천별 세금 상세 캡션
            _pers_annual = tax_result.get("IRP_개인납입연", 0)
            _pers_rate   = tax_result.get("IRP_개인세율", 0.055)
            _ret_tax     = tax_result.get("IRP_퇴직세", 0)
            _pers_tax    = tax_result.get("IRP_개인세", 0)
            # 연금현황 시트 원천별 원금 집계
            _irp_ret_amt  = sum(float(r.get("원금",0)) for r in _pension_irp_items
                               if r.get("원천","퇴직금") == "퇴직금")
            _irp_pers_amt = sum(float(r.get("원금",0)) for r in _pension_irp_items
                               if r.get("원천","") == "개인납입")
            _irp_ratio_str = ""
            if _irp_ret_amt + _irp_pers_amt > 0:
                _irp_ratio_str = (
                    f" | 퇴직금 {_irp_ret_amt/10000:.0f}만 / "
                    f"개인납입 {_irp_pers_amt/10000:.0f}만"
                )
            if _pers_annual > 0 or _ret_tax > 0:
                st.markdown(
                    f"<div style='font-size:0.72rem; color:rgba(255,255,255,0.45); margin-top:2px;'>"
                    f"퇴직금분 {_ret_tax:,.0f}원 | "
                    f"개인납입분 {_pers_tax:,.0f}원 ({_pers_rate*100:.1f}%)"
                    f"{_irp_ratio_str}</div>",
                    unsafe_allow_html=True,
                )
            # 연간 1,500만원 종합과세 경고 — 개인납입+운용수익 원천 기준
            if _pers_annual > IRP_PENSION_COMPREHENSIVE_LIMIT:
                st.warning(
                    f"⚠️ **종합과세 주의** — 개인납입금·운용수익 원천 연간 수령액 "
                    f"{_pers_annual/10000:.0f}만원이 1,500만원 초과. "
                    f"종합과세 또는 분리과세(16.5%) 중 선택 필요.",
                    icon="⚠️"
                )

        # 연금저축 카드 (잔액이 있을 때만 표시)
        if ps_total > 0:
            with st.container(border=True):
                st.markdown("**🏦 연금저축**")
                pa, pb = st.columns(2)
                pa.metric("세전", f"{tax_result['연금저축_세전']:,.0f}원")
                pb.metric("연금소득세 5.5%", f"-{tax_result['연금저축_세금']:,.0f}원",
                          delta_color="inverse")
                st.markdown(
                    f"<div style='text-align:right; font-size:1.1rem; font-weight:700; color:#7dffb0;'>"
                    f"실수령 {tax_result['연금저축_세후']:,.0f}원</div>",
                    unsafe_allow_html=True
                )


        # ISA 카드
        with st.container(border=True):
            st.markdown("**📦 ISA**")
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

        # 일반 계좌 카드 (잔액 있을 때만)
        if general_total > 0:
            _gen_inc  = _gen_monthly_income
            _gen_tax  = _gen_tax_monthly      # 과세표준 기반 세금
            _gen_net  = _gen_net_monthly      # 과세표준 기반 세후
            _taxbase_pct_disp = round(_gen_taxbase_ratio * 100, 1)
            with st.container(border=True):
                st.markdown("**💵 일반**")
                ga, gb = st.columns(2)
                ga.metric("세전", f"{_gen_inc:,.0f}원",
                          help="연간 분배금 ÷ 12 (월 평균)")
                _tax_label = (
                    f"배당소득세 15.4% (과표 {_taxbase_pct_disp}%)"
                    if _taxbase_pct_disp < 99.9
                    else "배당소득세 15.4%"
                )
                gb.metric(_tax_label, f"-{_gen_tax:,.0f}원", delta_color="inverse")
                st.markdown(
                    f"<div style='text-align:right; font-size:1.1rem; "
                    f"font-weight:700; color:#7dffb0;'>"
                    f"실수령 {_gen_net:,.0f}원</div>",
                    unsafe_allow_html=True
                )
                _gen_annual_val = _gen_inc * 12
                st.caption(f"연 분배금 {_gen_annual_val:,.0f}원 ÷ 12 = 월 {_gen_inc:,.0f}원 "
                           f"(실제 입금: 연 1회)")

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
        _gen_inc_bar  = _gen_monthly_income   # 시나리오/연금현황 수량×DPS 기반
        _gen_net_bar  = _gen_inc_bar * (1 - 0.154)
        _bar_구분 = ["공적연금","IRP"] + (["ISA"]) + (["일반"] if general_total>0 else [])
        _bar_세전 = [tax_result["공적연금_세전"], tax_result["IRP_세전"],
                     tax_result["ISA_세전"]] + ([_gen_inc_bar] if general_total>0 else [])
        _bar_세후 = [tax_result["공적연금_세후"], tax_result["IRP_세후"],
                     tax_result["ISA_세후"]] + ([_gen_net_bar] if general_total>0 else [])
        bar_df = pd.DataFrame({"구분":_bar_구분, "세전":_bar_세전, "세후":_bar_세후})
        fig_bar = go.Figure()
        fig_bar.add_trace(go.Bar(
            name="세전", x=bar_df["구분"], y=bar_df["세전"],
            marker_color="rgba(135,206,235,0.4)",
            text=[f"{v/10000:.0f}만" for v in bar_df["세전"]],
            textposition="outside",
        ))
        fig_bar.add_trace(go.Bar(
            name="세후", x=bar_df["구분"], y=bar_df["세후"],
            marker_color=(["#87CEEB","#FFD700","#FF4B4B","#AFA9EC"] if general_total>0 else ["#87CEEB","#FFD700","#FF4B4B"]),
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
            "구분": ["공적연금","IRP 수익","ISA 수익"] + (["일반"] if general_total>0 else []),
            "금액": [tax_result["공적연금_세후"], tax_result["IRP_세후"],
                     tax_result["ISA_세후"]] + ([_gen_net_bar] if general_total>0 else []),
        })
        fig_pie = px.pie(
            pie_df, values="금액", names="구분",
            hole=0.4,
            title="세후 월 수입 구성",
            color_discrete_sequence=(["#87CEEB","#FFD700","#FF4B4B","#AFA9EC"] if general_total>0 else ["#87CEEB","#FFD700","#FF4B4B"]),
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
                ("IRP 퇴직연금세 (한도내 0.76%·초과 1.1%)", tax_result["IRP_세금"]),
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


    # ── 납입한도·세액공제 현황 ────────────────────────────
    if not contrib_df.empty:
        st.divider()
        with st.container(border=True):
            st.markdown("**📋 납입한도 및 세액공제 현황**")
            cs  = contrib_status
            _comb     = cs["combined_annual"]
            _comb_pct = min(_comb / ANNUAL_CONTRIBUTION_LIMIT * 100, 100)

            h1, h2, h3 = st.columns(3)
            h1.metric(f"{contrib_year}년 IRP 납입",
                      f"{cs['irp_annual']/10000:.0f}만원")
            h2.metric(f"{contrib_year}년 연금저축 납입",
                      f"{cs['ps_annual']/10000:.0f}만원")
            h3.metric("합산 납입 (한도 1,800만원)",
                      f"{_comb/10000:.0f}만원",
                      delta=f"잔여 {max(0,ANNUAL_CONTRIBUTION_LIMIT-_comb)/10000:.0f}만원",
                      delta_color="inverse" if _comb > ANNUAL_CONTRIBUTION_LIMIT else "normal")
            st.progress(int(_comb_pct),
                        text=f"IRP+연금저축 합산 납입 {_comb_pct:.1f}% / 1,800만원")
            if _comb > ANNUAL_CONTRIBUTION_LIMIT:
                st.error("⚠️ 합산 납입액이 연 1,800만원 한도를 초과합니다!")

            st.markdown("**세액공제 한도 활용률**")
            d1, d2, d3 = st.columns(3)
            _irp_dp  = min(cs["irp_deduct"] / IRP_TAX_DEDUCT_LIMIT * 100, 100) if IRP_TAX_DEDUCT_LIMIT > 0 else 0
            _ps_dp   = min(cs["ps_deduct"]  / PS_TAX_DEDUCT_LIMIT  * 100, 100) if PS_TAX_DEDUCT_LIMIT  > 0 else 0
            _co_dp   = min(cs["combined_deduct"] / COMBINED_TAX_DEDUCT_LIMIT * 100, 100)
            d1.metric("IRP 세액공제 (한도 300만)",
                      f"{cs['irp_deduct']/10000:.0f}만원",
                      delta=f"잔여 {cs['irp_deduct_remain']/10000:.0f}만원")
            d2.metric("연금저축 세액공제 (한도 600만)",
                      f"{cs['ps_deduct']/10000:.0f}만원",
                      delta=f"잔여 {cs['ps_deduct_remain']/10000:.0f}만원")
            _benefit = min(cs["combined_deduct"], COMBINED_TAX_DEDUCT_LIMIT) * TAX_DEDUCT_RATE_GENERAL
            d3.metric("합산 세액공제 (한도 900만)",
                      f"{cs['combined_deduct']/10000:.0f}만원",
                      delta=f"절세 혜택 약 {_benefit/10000:.0f}만원")
            st.progress(int(_co_dp),
                        text=f"합산 세액공제 한도 활용률 {_co_dp:.1f}% / 900만원")
            if cs["combined_deduct"] > COMBINED_TAX_DEDUCT_LIMIT:
                st.warning("⚠️ 합산 세액공제 신청액이 900만원 한도를 초과합니다.", icon="⚠️")
            if cs["irp_deduct"] > IRP_TAX_DEDUCT_LIMIT:
                st.warning("⚠️ IRP 세액공제 신청액이 300만원 한도를 초과합니다.", icon="⚠️")

            # ── ISA 납입한도 현황 (별도 — 세액공제와 무관) ──
            if cs.get("isa_annual", 0) > 0 or cs.get("isa_cumulative", 0) > 0:
                st.divider()
                st.markdown("**ISA 납입현황 (연간 2,000만원 / 최대 2억원)**")
                _isa_ann     = cs.get("isa_annual", 0)
                _isa_cumul   = cs.get("isa_cumulative", 0)
                _isa_ann_pct = min(_isa_ann / ISA_ANNUAL_LIMIT * 100, 100)
                _isa_cum_pct = min(_isa_cumul / ISA_LIMIT * 100, 100)   # 최대 2억 기준

                ia1, ia2, ia3 = st.columns(3)
                ia1.metric(
                    f"{contrib_year}년 ISA 납입액",
                    f"{_isa_ann/10000:.0f}만원",
                    delta=f"잔여 {cs.get('isa_remain_annual',0)/10000:.0f}만원",
                    delta_color="normal",
                )
                ia2.metric(
                    "ISA 누적 납입액",
                    f"{_isa_cumul/10000:.0f}만원",
                    delta=f"최대 2억 기준 {_isa_cum_pct:.1f}%",
                )
                ia3.metric(
                    "ISA 비과세 한도 잔여",
                    f"{max(0, ISA_TAX_FREE_MONTHLY*12 - (_isa_ann * 0.1))/10000:.0f}만원",
                    help="일반형 연 200만원 비과세 기준 추정",
                )
                st.progress(int(_isa_ann_pct),
                            text=f"ISA 연간 납입 {_isa_ann_pct:.1f}% / 2,000만원")
                if _isa_ann > ISA_ANNUAL_LIMIT:
                    st.error("⚠️ ISA 연간 납입액이 2,000만원 한도를 초과합니다!")
                # 만기 전환 안내 (누적 납입 후 연금계좌 전환 시 추가 공제)
                if _isa_cumul >= 10_000_000:
                    _extra_deduct = min(_isa_cumul * 0.10, 3_000_000)
                    st.info(
                        f"💡 ISA 만기 후 연금계좌 전환 시 추가 세액공제 가능 — "
                        f"전환금액의 10%, 최대 300만원 "
                        f"(현재 누적 기준 약 {_extra_deduct/10000:.0f}만원)",
                        icon="💡"
                    )

    # ════════════════════════════════════════════════════════
    # 수령 타임라인
    # ════════════════════════════════════════════════════════
    st.divider()
    st.markdown("## 📅 연도별 수령 타임라인")
    st.caption("은퇴부터 기대수명까지 수입원이 어떻게 바뀌는지 한눈에 확인합니다.")

    # ── 타임라인 파라미터 ────────────────────────────────
    # 고정값 (확정): 출생연도 1971 / 은퇴 55세 / 공무원연금 개시 55세 / 기대수명 90세
    birth_year  = 1971
    retire_age  = 55
    pension_age = 55
    life_exp    = 90

    with st.sidebar:
        st.divider()
        st.subheader("📅 타임라인")
        inflation_rate = st.slider(
            "물가상승률 (%)",
            min_value=0.0, max_value=5.0, value=2.0, step=0.1,
            help="공적연금 물가 연동 및 목표생활비 실질 계산에 적용",
        ) / 100
        irp_pension_year_input = st.number_input(
            "IRP 연금수령 연차",
            min_value=1, max_value=10, value=1, step=1,
            key="irp_pension_year",
            help="수령 개시연도=1차, 매년+1. 한도=잔액÷(11-연차)×120%",
        )
        irp_personal_ratio = st.slider(
            "IRP 개인납입금 비율 (%)",
            min_value=0, max_value=100, value=20, step=5,
            key="irp_personal_ratio",
            help="IRP 잔액 중 개인납입금(세액공제분)+운용수익 비율. "
                 "나머지는 퇴직금 원천. 연금소득세율 5.5% 적용 부분.",
        ) / 100
        # 연금수령한도 실시간 표시
        _limit_preview = irp_total / (11 - int(irp_pension_year_input)) * 1.2
        st.caption(
            f"출생 {birth_year}년 · 은퇴 {retire_age}세 · "
            f"공무원연금 {pension_age}세 개시 · 기대수명 {life_exp}세\n"
            f"IRP {irp_pension_year_input}차 연금수령한도: {_limit_preview/10000:.0f}만원/년 "
            f"({_limit_preview/12/10000:.0f}만원/월) | 개인납입 {irp_personal_ratio*100:.0f}%"
        )

        st.divider()
        st.subheader("📈 생활비 패턴 설정")
        st.caption("고령화에 따른 소비 변화를 반영합니다.")
        expense_mode = st.radio(
            "목표생활비 적용 방식",
            ["📈 피크-수렴형 (권장)", "📊 물가 상승만 (단순)"],
            key="expense_mode",
            help="피크-수렴형: 액티브 시기 증가→고령기 감소 / 물가 상승: 단순 연동",
        )
        _use_peak = (expense_mode == "📈 피크-수렴형 (권장)")

        if _use_peak:
            peak_age_input = st.slider(
                "생활비 최대 연령",
                min_value=int(retire_age) + 5,
                max_value=int(life_exp) - 5,
                value=70, step=1,
                help="이 나이에 생활비가 최대가 됩니다 (여행·활동 등 액티브 피크)",
            )
            peak_amount_input = st.slider(
                "최대 생활비 (만원)",
                min_value=int(target_monthly / 10000),
                max_value=700,
                value=700, step=10,
                help="최대 700만원 상한 적용",
            ) * 10_000
            end_ratio_input = st.slider(
                "기대수명 시 생활비 비율 (%)",
                min_value=50, max_value=100,
                value=80, step=5,
                help="기대수명 시 생활비를 현재 목표의 몇 %로 설정할지 (의료비↑, 활동비↓)",
            ) / 100
            end_amount_input = target_monthly * end_ratio_input
            st.caption(
                f"패턴: {int(retire_age)}세 {target_monthly/10000:.0f}만 → "
                f"{peak_age_input}세 {peak_amount_input/10000:.0f}만 → "
                f"{int(life_exp)}세 {end_amount_input/10000:.0f}만원"
            )
        else:
            peak_age_input    = 70
            peak_amount_input = 7_000_000
            end_amount_input  = None

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
        use_peak: bool = False,
        peak_age: int = 70,
        peak_amount: float = 7_000_000,
        end_amount: float | None = None,
    ) -> pd.DataFrame:
        rows = []
        irp_balance = irp_total
        isa_balance = isa_total

        for yr in years:
            age = yr - birth_year
            elapsed = yr - retire_year   # 은퇴 후 경과 연수

            # 목표 생활비: 피크-수렴형 or 단순 물가
            if use_peak:
                target_real = calc_target_expense(
                    age          = age,
                    base         = target_monthly,
                    retire_age   = retire_age,
                    life_exp     = life_exp,
                    peak_age     = peak_age,
                    peak_amount  = peak_amount,
                    end_amount   = end_amount,
                    inflation_rate = inflation_rate,
                )
            else:
                target_real = target_monthly * ((1 + inflation_rate) ** elapsed)

            # 공적연금: 개시 연도부터 수령 + 매년 물가 반영
            # 공무원연금은 전년도 소비자물가 상승률 연동 (공무원연금법 §43)
            if yr >= pension_year:
                pub_elapsed = yr - pension_year   # 연금 개시 후 경과 연수
                pub = public_pension_monthly * ((1 + inflation_rate) ** pub_elapsed)
            else:
                pub = 0.0

            # IRP: 은퇴 즉시 인출 (잔액 있을 때만)
            irp_m = irp_balance * irp_rate if irp_balance > 0 else 0.0
            irp_balance = max(0.0, irp_balance - irp_m)  # 월 수익 차감 (월 단위)

            # ISA: 은퇴 즉시 인출 (잔액 있을 때만)
            isa_m = isa_balance * isa_rate if isa_balance > 0 else 0.0
            isa_balance = max(0.0, isa_balance - isa_m)  # 월 수익 차감

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
        use_peak       = _use_peak,
        peak_age       = peak_age_input,
        peak_amount    = peak_amount_input,
        end_amount     = end_amount_input,
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
        name=(
            f"목표생활비 (피크-수렴형 | 물가{inflation_rate*100:.1f}%)"
            if _use_peak
            else f"목표생활비 (물가{inflation_rate*100:.1f}% 반영)"
        ),
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
    lc2, lc3 = st.columns(2)  # ✅ lc1 미사용 컬럼 제거
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


    # ════════════════════════════════════════════════════════
    # IRP·ISA 잔액 고갈 시뮬레이션
    # ════════════════════════════════════════════════════════
    st.divider()
    st.markdown("## 💰 IRP·ISA 잔액 고갈 시뮬레이션")
    st.caption("분배율 변화에 따라 자산이 언제 고갈되는지, 3가지 시나리오로 비교합니다.")

    def simulate_balance(
        irp_total: float, isa_total: float,
        irp_rate: float, isa_rate: float,
        retire_year: int, end_year: int,
        birth_year: int = 1971,   # ✅ 전역변수 직접 참조 제거 — 인자로 수신
    ) -> pd.DataFrame:
        """연도별 IRP·ISA 잔액 시뮬레이션 — 단일 시나리오"""
        rows = []
        irp_bal = irp_total
        isa_bal = isa_total
        for yr in range(retire_year, end_year + 1):
            irp_m = irp_bal * irp_rate if irp_bal > 0 else 0.0
            isa_m = isa_bal * isa_rate if isa_bal > 0 else 0.0
            irp_bal = max(0.0, irp_bal - irp_m * 12)  # 연간 인출 (고갈 시뮬레이션용)
            isa_bal = max(0.0, isa_bal - isa_m * 12)
            rows.append({
                "연도": yr,
                "나이": yr - birth_year,
                "IRP잔액": irp_bal,
                "ISA잔액": isa_bal,
                "IRP월수익": irp_m,
                "ISA월수익": isa_m,
            })
        return pd.DataFrame(rows)

    # ── 시나리오 설정 ─────────────────────────────────────
    with st.expander("⚙️ 시나리오 분배율 설정", expanded=False):
        sc1, sc2, sc3 = st.columns(3)
        with sc1:
            st.markdown("**🔴 비관 시나리오**")
            irp_bear = st.slider("IRP 비관 (%)", 0.5, 2.0,
                                 max(0.5, float(default_palantir) - 0.5), 0.1,
                                 key="irp_bear") / 100
            isa_bear = st.slider("ISA 비관 (%)", 0.3, 1.5,
                                 max(0.3, float(default_kodex) - 0.3), 0.1,
                                 key="isa_bear") / 100
        with sc2:
            st.markdown("**🟡 기본 시나리오**")
            irp_base = st.slider("IRP 기본 (%)", 0.5, 2.0,
                                 float(default_palantir), 0.1,
                                 key="irp_base") / 100
            isa_base = st.slider("ISA 기본 (%)", 0.3, 1.5,
                                 float(default_kodex), 0.1,
                                 key="isa_base") / 100
        with sc3:
            st.markdown("**🟢 낙관 시나리오**")
            irp_bull = st.slider("IRP 낙관 (%)", 0.5, 2.0,
                                 min(2.0, float(default_palantir) + 0.5), 0.1,
                                 key="irp_bull") / 100
            isa_bull = st.slider("ISA 낙관 (%)", 0.3, 1.5,
                                 min(1.5, float(default_kodex) + 0.3), 0.1,
                                 key="isa_bull") / 100

    # ── 3 시나리오 시뮬레이션 ─────────────────────────────
    scenarios = {
        "🔴 비관": (irp_bear, isa_bear),
        "🟡 기본": (irp_base, isa_base),
        "🟢 낙관": (irp_bull, isa_bull),
    }
    sc_colors = {
        "🔴 비관": ("#FF4B4B", "rgba(255,75,75,0.15)"),
        "🟡 기본": ("#FFD700", "rgba(255,215,0,0.15)"),
        "🟢 낙관": ("#7dffb0", "rgba(125,255,176,0.15)"),
    }
    sc_dfs = {
        name: simulate_balance(irp_total, isa_total, ir, isar, retire_year, end_year,
                               birth_year=birth_year)  # ✅ birth_year 명시 전달
        for name, (ir, isar) in scenarios.items()
    }

    def find_exhaust(df: pd.DataFrame, col: str):
        mask = df[col] <= 0
        if mask.any():
            row = df[mask].iloc[0]
            return int(row["연도"]), int(row["나이"])
        return None, None

    # ── 고갈 시점 요약 카드 ───────────────────────────────
    st.markdown("#### 📌 시나리오별 고갈 시점")
    hd_cols = st.columns(3)
    for i, (sc_name, sc_df) in enumerate(sc_dfs.items()):
        irp_yr, irp_age = find_exhaust(sc_df, "IRP잔액")
        isa_yr, isa_age = find_exhaust(sc_df, "ISA잔액")
        line_color = sc_colors[sc_name][0]
        with hd_cols[i]:
            with st.container(border=True):
                st.markdown(
                    f"<div style='font-size:1rem; font-weight:700; "
                    f"color:{line_color}; margin-bottom:8px;'>{sc_name}</div>",
                    unsafe_allow_html=True,
                )
                st.markdown(
                    f"**IRP:** "
                    + (f"{irp_yr}년 ({irp_age}세, {irp_yr - retire_year}년 후)"
                       if irp_yr else "✅ 기대수명까지 유지")
                )
                st.markdown(
                    f"**ISA:** "
                    + (f"{isa_yr}년 ({isa_age}세, {isa_yr - retire_year}년 후)"
                       if isa_yr else "✅ 기대수명까지 유지")
                )
                irp_rate_pct = scenarios[sc_name][0] * 100
                isa_rate_pct = scenarios[sc_name][1] * 100
                st.caption(f"IRP {irp_rate_pct:.1f}% / ISA {isa_rate_pct:.1f}%")

    st.divider()

    # ── IRP 잔액 추이 차트 ────────────────────────────────
    bal_tab1, bal_tab2 = st.tabs(["💼 IRP 잔액 추이", "📦 ISA 잔액 추이"])

    for tab, asset, bal_col, inc_col, asset_color in [
        (bal_tab1, "IRP", "IRP잔액", "IRP월수익", "#FFD700"),
        (bal_tab2, "ISA", "ISA잔액", "ISA월수익", "#FF4B4B"),
    ]:
        with tab:
            fig_bal = go.Figure()

            # 시나리오별 잔액 라인
            for sc_name, sc_df in sc_dfs.items():
                line_c, fill_c = sc_colors[sc_name]
                exhaust_yr, exhaust_age = find_exhaust(sc_df, bal_col)

                fig_bal.add_trace(go.Scatter(
                    x=sc_df["연도"],
                    y=sc_df[bal_col] / 100_000_000,
                    name=sc_name,
                    mode="lines",
                    line=dict(color=line_c, width=2.5),
                    fill="tozeroy",
                    fillcolor=fill_c,
                    hovertemplate=(
                        f"{sc_name}<br>"
                        "%{x}년: %{y:.2f}억원<extra></extra>"
                    ),
                ))

                # 고갈 시점 마커
                if exhaust_yr:
                    fig_bal.add_vline(
                        x=exhaust_yr,
                        line_dash="dot",
                        line_color=line_c,
                        line_width=1.5,
                        annotation_text=f"{sc_name} 고갈<br>{exhaust_yr}년 ({exhaust_age}세)",
                        annotation_position="top",
                        annotation_font_color=line_c,
                        annotation_font_size=11,
                    )

            fig_bal.update_layout(
                height=380,
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(255,255,255,0.02)",
                font_color="white",
                legend=dict(orientation="h", yanchor="bottom",
                            y=-0.25, xanchor="center", x=0.5),
                margin=dict(t=20, b=80, l=10, r=10),
                yaxis=dict(title=f"{asset} 잔액 (억원)", tickformat=".2f"),
                xaxis=dict(title="연도", dtick=2),
                hovermode="x unified",
            )
            st.plotly_chart(fig_bal, use_container_width=True)

            # 월 수익 추이 (보조 차트)
            st.markdown(f"**📈 {asset} 월 수익 추이 (시나리오별)**")
            fig_inc = go.Figure()
            for sc_name, sc_df in sc_dfs.items():
                line_c, _ = sc_colors[sc_name]
                fig_inc.add_trace(go.Scatter(
                    x=sc_df["연도"],
                    y=sc_df[inc_col] / 10000,
                    name=sc_name,
                    mode="lines",
                    line=dict(color=line_c, width=2),
                    hovertemplate=f"{sc_name}: %{{y:,.1f}}만원<extra></extra>",
                ))
            fig_inc.update_layout(
                height=220,
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(255,255,255,0.02)",
                font_color="white",
                showlegend=False,
                margin=dict(t=10, b=40, l=10, r=10),
                yaxis=dict(title="월 수익 (만원)", tickformat=","),
                xaxis=dict(dtick=2),
                hovermode="x unified",
            )
            st.plotly_chart(fig_inc, use_container_width=True)

    # ── 통합 잔액 (IRP+ISA) 비교 ─────────────────────────
    st.divider()
    st.markdown("#### 🔗 IRP + ISA 통합 잔액 시나리오 비교")

    fig_total_bal = go.Figure()
    for sc_name, sc_df in sc_dfs.items():
        line_c, fill_c = sc_colors[sc_name]
        total_bal = sc_df["IRP잔액"] + sc_df["ISA잔액"]
        exhaust_mask = total_bal <= 0
        exhaust_yr_total = sc_df[exhaust_mask]["연도"].min() if exhaust_mask.any() else None

        fig_total_bal.add_trace(go.Scatter(
            x=sc_df["연도"],
            y=total_bal / 100_000_000,
            name=sc_name,
            mode="lines",
            line=dict(color=line_c, width=3),
            hovertemplate=f"{sc_name}: %{{y:.2f}}억원<extra></extra>",
        ))
        if exhaust_yr_total:
            exhaust_age_total = exhaust_yr_total - birth_year
            fig_total_bal.add_annotation(
                x=exhaust_yr_total,
                y=0,
                text=f"{sc_name}<br>완전 고갈<br>{exhaust_yr_total}년({exhaust_age_total}세)",
                showarrow=True,
                arrowhead=2,
                arrowcolor=line_c,
                font=dict(color=line_c, size=11),
                bgcolor="rgba(0,0,0,0.6)",
                bordercolor=line_c,
                borderwidth=1,
            )

    fig_total_bal.add_hline(y=0, line_color="rgba(255,255,255,0.2)", line_width=1)
    fig_total_bal.update_layout(
        height=350,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(255,255,255,0.02)",
        font_color="white",
        legend=dict(orientation="h", yanchor="bottom",
                    y=-0.25, xanchor="center", x=0.5),
        margin=dict(t=10, b=80, l=10, r=10),
        yaxis=dict(title="통합 잔액 (억원)", tickformat=".2f"),
        xaxis=dict(title="연도", dtick=2),
        hovermode="x unified",
    )
    st.plotly_chart(fig_total_bal, use_container_width=True)

    # 시나리오별 요약 인사이트
    ins1, ins2, ins3 = st.columns(3)
    for ins_col, (sc_name, sc_df) in zip([ins1, ins2, ins3], sc_dfs.items()):
        line_c = sc_colors[sc_name][0]
        total_bal_last = sc_df["IRP잔액"].iloc[-1] + sc_df["ISA잔액"].iloc[-1]
        total_income_sum = (sc_df["IRP월수익"] + sc_df["ISA월수익"]).sum()
        with ins_col:
            st.markdown(
                f"<div style='background:rgba(255,255,255,0.03); padding:12px; "
                f"border-radius:8px; border-left:3px solid {line_c};'>"
                f"<div style='color:{line_c}; font-weight:700; margin-bottom:6px;'>{sc_name}</div>"
                f"<div style='font-size:0.85rem; color:rgba(255,255,255,0.6);'>기대수명 시 잔액</div>"
                f"<div style='font-size:1.1rem; font-weight:700;'>{total_bal_last/100_000_000:.2f}억원</div>"
                f"<div style='font-size:0.85rem; color:rgba(255,255,255,0.6); margin-top:6px;'>총 수령액 (월 합계)</div>"
                f"<div style='font-size:1.1rem; font-weight:700;'>{total_income_sum/10000:,.0f}만원</div>"
                f"</div>",
                unsafe_allow_html=True,
            )


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


    # ════════════════════════════════════════════════════════
    # 연간 현금흐름 캘린더 히트맵
    # ════════════════════════════════════════════════════════
    st.divider()
    st.markdown("## 📅 연간 현금흐름 캘린더 히트맵")
    st.caption("월별 세후 수령액을 색상 강도로 표현합니다. ETF 분배금은 잔액 감소에 따라 월마다 달라집니다.")

    # ── 히트맵용 월별 수령액 계산 ────────────────────────────
    import calendar as cal_mod

    def build_monthly_cashflow(
        start_year: int,
        n_years: int,
        public_pension: float,
        irp_total: float,
        isa_total: float,
        general_total: float,
        irp_rate: float,
        isa_rate: float,
        general_rate: float,
        use_after_tax: bool,
        use_health_ins: bool,
        inflation_rate: float = 0.02,   # ✅ 공적연금 물가 반영
    ) -> pd.DataFrame:
        """
        연도×월 단위로 세후 수령액을 계산해 DataFrame 반환.
        IRP·ISA·일반 잔액은 매월 인출 후 감소.
        공무원연금은 매년 물가상승률 반영 (공무원연금법 §43).
        """
        rows = []
        irp_bal  = irp_total
        isa_bal  = isa_total
        gen_bal  = general_total

        for yr in range(start_year, start_year + n_years):
            yr_elapsed = yr - start_year   # 시작 연도 기준 경과 연수
            for mo in range(1, 13):
                # 수입원별 월 수령액
                # 공무원연금: 매년 물가 반영 (연초 기준 갱신)
                pub_m = public_pension * ((1 + inflation_rate) ** yr_elapsed)
                irp_m = irp_bal * irp_rate  if irp_bal > 0 else 0.0
                isa_m = isa_bal * isa_rate  if isa_bal > 0 else 0.0
                gen_m = gen_bal * general_rate if gen_bal > 0 else 0.0

                # 잔액 차감 (월말 기준)
                irp_bal = max(0.0, irp_bal - irp_m)
                isa_bal = max(0.0, isa_bal - isa_m)
                gen_bal = max(0.0, gen_bal - gen_m)

                gross = pub_m + irp_m + isa_m + gen_m

                # 세후 적용
                if use_after_tax and gross > 0:
                    tr = calc_after_tax(pub_m, irp_m, isa_m)
                    # 일반 계좌 배당소득세 15.4%
                    gen_tax = gen_m * 0.154
                    net = tr["총_세후"] + (gen_m - gen_tax)
                    if not use_health_ins:
                        net += tr["공적연금_건보료"]
                else:
                    net = gross

                rows.append({
                    "연도": yr,
                    "월":   mo,
                    "공무원연금": pub_m,
                    "IRP":       irp_m,
                    "ISA":       isa_m,
                    "일반":      gen_m,
                    "세전합계":  gross,
                    "세후합계":  net,
                    "IRP잔액":   irp_bal,
                    "ISA잔액":   isa_bal,
                })

        return pd.DataFrame(rows)

    # ── 히트맵 설정 ──────────────────────────────────────────
    current_year = datetime.now().year  # ✅ 하드코딩 제거 — 실행 시점 연도 자동 반영
    hm_years = st.slider(
        "히트맵 표시 연수",
        min_value=3, max_value=30, value=10, step=1,
        key="hm_years",
    )

    # 일반 계좌 분배율 (사이드바 또는 기본값)
    general_total   = _vals["general_total"]    # extract_values에서 이미 추출
    default_general = _vals["default_general"]  # extract_values에서 이미 추출

    with st.sidebar:
        st.divider()
        st.subheader("📅 히트맵 설정")
        _gen_annual_rate = st.slider(
            "일반(머니마켓) 연 분배율 (%)",
            min_value=0.0, max_value=10.0,
            value=float(default_general), step=0.1,
            key="general_rate_hm_annual",
            help="연간 분배율 입력 → 월 수령액은 연간 분배금 ÷ 12로 계산",
        )
        general_rate_hm = _gen_annual_rate / 100 / 12   # 월 환산율 (내부 계산용)
        st.session_state["general_rate_hm"] = general_rate_hm
        st.caption(f"월 환산: {_gen_annual_rate/12:.3f}% · 예상 연 분배금 "
                   f"{general_total * _gen_annual_rate / 100:,.0f}원")

    hm_df = build_monthly_cashflow(
        start_year    = min(current_year, retire_year),
        n_years       = hm_years,
        public_pension = public_pension,
        irp_total     = irp_total,
        isa_total     = isa_total,
        general_total = general_total,
        irp_rate      = palantir_rate,
        isa_rate      = kodex_rate,
        general_rate  = general_rate_hm,
        use_after_tax = show_tax,
        use_health_ins = use_health_ins,
        inflation_rate = inflation_rate,  # ✅ 공적연금 물가 반영
    )

    # income_col은 타임라인 섹션(735줄)에서 이미 정의됨 — 중복 제거
    # ── 히트맵 차트 ─────────────────────────────────────────
    years_list = sorted(hm_df["연도"].unique())
    months_kr  = ["1월","2월","3월","4월","5월","6월",
                   "7월","8월","9월","10월","11월","12월"]

    # z값: 연도(행) × 월(열) 매트릭스
    z_matrix    = []
    text_matrix = []
    for yr in years_list:
        row_data = hm_df[hm_df["연도"] == yr].sort_values("월")
        z_row, t_row = [], []
        for _, r in row_data.iterrows():
            val = r[income_col]
            z_row.append(val / 10000)
            t_row.append(f"{yr}년 {int(r['월'])}월<br>"
                         f"{'세후' if show_tax else '세전'}: {val/10000:.1f}만원<br>"
                         f"공무원연금: {r['공무원연금']/10000:.1f}만원<br>"
                         f"IRP: {r['IRP']/10000:.1f}만원<br>"
                         f"ISA: {r['ISA']/10000:.1f}만원"
                         + (f"<br>일반: {r['일반']/10000:.1f}만원" if r['일반'] > 0 else ""))
        z_matrix.append(z_row)
        text_matrix.append(t_row)

    fig_hm = go.Figure(go.Heatmap(
        z            = z_matrix,
        x            = months_kr,
        y            = [str(y) for y in years_list],
        text         = text_matrix,
        hovertemplate = "%{text}<extra></extra>",
        colorscale   = [
            [0.0,  "#1a1a2e"],
            [0.2,  "#16213e"],
            [0.4,  "#0f4c81"],
            [0.6,  "#1a7abf"],
            [0.8,  "#87CEEB"],
            [1.0,  "#7dffb0"],
        ],
        showscale    = True,
        colorbar     = dict(
            title    = dict(text="만원", side="right"),
            tickfont = dict(color="rgba(255,255,255,0.6)", size=10),
            thickness = 12,
            len       = 0.8,
        ),
        xgap = 2,
        ygap = 2,
    ))

    # 목표 생활비 기준선 — 색상 경계값 강조 annotaion
    target_in_man = target_monthly / 10000
    fig_hm.add_annotation(
        x=months_kr[-1], y=str(years_list[-1]),
        text=f"목표 {target_in_man:.0f}만원",
        showarrow=False,
        font=dict(color="#FFD700", size=10),
        xanchor="right", yanchor="bottom",
    )

    fig_hm.update_layout(
        height        = max(300, hm_years * 32 + 80),
        paper_bgcolor = "rgba(0,0,0,0)",
        plot_bgcolor  = "rgba(0,0,0,0)",
        font_color    = "white",
        margin        = dict(t=20, b=40, l=60, r=80),
        xaxis         = dict(
            tickfont = dict(size=11, color="rgba(255,255,255,0.7)"),
            side     = "top",
        ),
        yaxis         = dict(
            tickfont  = dict(size=11, color="rgba(255,255,255,0.7)"),
            autorange = "reversed",
        ),
    )
    st.plotly_chart(fig_hm, use_container_width=True)

    # ── 월별 수령액 추이 라인차트 (수입원 분해) ──────────────
    st.markdown("#### 📈 월별 수입원 분해 추이")

    fig_line = go.Figure()

    source_map = [
        ("공무원연금", "#87CEEB", "solid"),
        ("IRP",       "#FFD700", "solid"),
        ("ISA",       "#FF4B4B", "solid"),
    ]
    if general_total > 0:
        source_map.append(("일반", "#7dffb0", "dot"))

    # x축: 연월 문자열
    hm_df["연월"] = hm_df["연도"].astype(str) + "-" + hm_df["월"].apply(lambda m: f"{m:02d}")

    for src, color, dash in source_map:
        fig_line.add_trace(go.Scatter(
            x    = hm_df["연월"],
            y    = hm_df[src] / 10000,
            name = src,
            mode = "lines",
            line = dict(color=color, width=2, dash=dash),
            hovertemplate = f"{src}: %{{y:.1f}}만원<extra></extra>",
        ))

    # 세후 합계
    fig_line.add_trace(go.Scatter(
        x    = hm_df["연월"],
        y    = hm_df[income_col] / 10000,
        name = f"{'세후' if show_tax else '세전'} 합계",
        mode = "lines",
        line = dict(color="white", width=2.5, dash="solid"),
        hovertemplate = "합계: %{y:.1f}만원<extra></extra>",
    ))

    # 목표 생활비 기준선
    fig_line.add_hline(
        y             = target_in_man,
        line_dash     = "dot",
        line_color    = "#FFD700",
        line_width    = 1.5,
        annotation_text = f"목표 {target_in_man:.0f}만원",
        annotation_position = "top right",
        annotation_font_color = "#FFD700",
    )

    # x축 눈금: 매년 1월만 표시
    tick_vals = hm_df[hm_df["월"] == 1]["연월"].tolist()
    fig_line.update_layout(
        height        = 320,
        paper_bgcolor = "rgba(0,0,0,0)",
        plot_bgcolor  = "rgba(255,255,255,0.02)",
        font_color    = "white",
        legend        = dict(orientation="h", yanchor="bottom",
                             y=-0.3, xanchor="center", x=0.5),
        margin        = dict(t=10, b=80, l=10, r=10),
        yaxis         = dict(title="월 수령액 (만원)", tickformat=","),
        xaxis         = dict(
            tickvals  = tick_vals,
            ticktext  = [v[:4] + "년" for v in tick_vals],
            tickangle = -30,
        ),
        hovermode     = "x unified",
    )
    st.plotly_chart(fig_line, use_container_width=True)

    # ── 연간 총 수령액 요약 테이블 ──────────────────────────
    st.markdown("#### 📋 연간 수령액 요약")

    annual = (
        hm_df.groupby("연도")[[
            "공무원연금", "IRP", "ISA", "일반", "세전합계", "세후합계"
        ]].sum() / 10000
    ).round(1).reset_index()

    annual["나이"] = annual["연도"] - birth_year
    annual["달성률(%)"] = (
        (annual[income_col] * 10000 / 12) / target_monthly * 100
        if target_monthly > 0 else 0
    ).round(1)

    display_annual = annual.rename(columns={
        "공무원연금": "공무원연금(만원)",
        "IRP":       "IRP(만원)",
        "ISA":       "ISA(만원)",
        "일반":      "일반(만원)",
        "세전합계":  "세전합계(만원)",
        "세후합계":  "세후합계(만원)",
        "달성률(%)": "월평균 달성률(%)",
    })

    st.dataframe(
        display_annual,
        hide_index=True,
        use_container_width=True,
        column_config={
            "연도":             st.column_config.NumberColumn("연도",   format="%d년"),
            "나이":             st.column_config.NumberColumn("나이",   format="%d세"),
            "공무원연금(만원)": st.column_config.NumberColumn("공무원연금", format="%,.1f"),
            "IRP(만원)":        st.column_config.NumberColumn("IRP",    format="%,.1f"),
            "ISA(만원)":        st.column_config.NumberColumn("ISA",    format="%,.1f"),
            "일반(만원)":       st.column_config.NumberColumn("일반",   format="%,.1f"),
            "세전합계(만원)":   st.column_config.NumberColumn("세전합계", format="%,.1f"),
            "세후합계(만원)":   st.column_config.NumberColumn("세후합계", format="%,.1f"),
            "월평균 달성률(%)": st.column_config.ProgressColumn(
                "월평균 달성률",
                format="%.1f%%",
                min_value=0,
                max_value=300,
            ),
        },
    )

# ════════════════════════════════════════════════════════
# 📐 수익률 벤치마크 탭 (pension_benchmark.py)
# ════════════════════════════════════════════════════════
with _main_tab5:
    from pension_benchmark import render_benchmark_tab
    render_benchmark_tab(
        irp_items = _pension_irp_items,
        isa_items = _pension_isa_items,
        ps_items  = _pension_ps_items,
        gen_items = _pension_gen_items,
        irp_total = irp_total,
        isa_total = isa_total,
        ps_total  = ps_total,
        gen_total = general_total,
    )

# ════════════════════════════════════════════════════════
# 🎲 Monte Carlo 탭 (pension_montecarlo.py)
# ════════════════════════════════════════════════════════
with _main_tab6:
    from pension_montecarlo import render_montecarlo_tab
    render_montecarlo_tab(
        irp_total      = irp_total,
        isa_total      = isa_total,
        ps_total       = ps_total,
        gen_total      = general_total,
        public_pension = public_pension,
        target_monthly = target_monthly,
        irp_rate       = default_palantir,
        isa_rate       = default_kodex,
        ps_rate        = default_ps,
        gen_rate       = default_general / 12,
        birth_year     = 1971,
    )

# ════════════════════════════════════════════════════════
# 🤖 AI 자문 탭
# ════════════════════════════════════════════════════════
with _main_tab7:
    from pension_advisor import render_advisor_tab

    _shortage_summary = ""
    try:
        _s_df = tl_df[tl_df["잉여/부족"] < 0]
        if not _s_df.empty:
            _shortage_summary = (
                f"{int(_s_df['연도'].min())}년({int(_s_df.iloc[0]['나이'])}세)~"
                f"{int(_s_df['연도'].max())}년({int(_s_df.iloc[-1]['나이'])}세) "
                f"총 {len(_s_df)}개년 부족"
            )
    except Exception:
        pass

    _advisor_ctx = {
        "birth_year":      1971,
        "retire_age":      retire_age,
        "pension_age":     pension_age,
        "public_pension":  public_pension,
        "irp_income":      irp_income,
        "isa_income":      isa_income,
        "ps_income":       ps_income,
        "display_income":  display_income,
        "total_income":    total_income,
        "target_monthly":  target_monthly,
        "achievement":     achievement,
        "irp_total":       irp_total,
        "isa_total":       isa_total,
        "ps_total":        ps_total,
        "general_total":   general_total,
        "show_tax":        show_tax,
        "use_health_ins":  use_health_ins,
        "sc_choice":       sc_choice,
        "irp_names":       _irp_names,
        "isa_names":       _isa_names,
        "ps_names":        [r["종목명"] for r in _pension_ps_items if r.get("종목명")],
        "gen_names":       _gen_names,
        "shortage_summary": _shortage_summary,
    }

    render_advisor_tab(_advisor_ctx)


# ════════════════════════════════════════════════════════
# 🏦 과세 관리 탭
# ════════════════════════════════════════════════════════
with _main_tab8:
    from pension_tax_monitor import render_tax_monitor_tab
    _tax_ctx = {
        "dist_df":       dist_tax_df,
        "year":          datetime.now().year,
        "current_month": datetime.now().month,
        "irp_monthly":   irp_income,
        "isa_monthly":   isa_income,
        "gen_monthly":   _gen_monthly_income,
        "ps_monthly":    ps_income,
        "target_monthly": target_monthly,
    }
    render_tax_monitor_tab(_tax_ctx)


# ════════════════════════════════════════════════════════
# ♻️ 재투자 시뮬레이터 탭
# ════════════════════════════════════════════════════════
def _render_reinvest_tab(
    pension_items: dict,
    irp_income: float,
    isa_income: float,
    ps_income: float,
    gen_income: float,
    irp_total: float,
    isa_total: float,
    ps_total: float,
    target_monthly: float,
    sc_df,
    sc_names: list,
):
    """♻️ 재투자 시뮬레이터"""
    import plotly.graph_objects as _go

    st.markdown(
        "<h3 style='margin-bottom:0.2rem;'>♻️ 재투자 시뮬레이터</h3>"
        "<p style='color:rgba(255,255,255,0.5);font-size:0.83rem;margin-top:0;'>"
        "종목별 배분%의 합계가 재투자 비율입니다. "
        "예: SOL 10% + TIGER 5% → 월 분배금의 15% 재투자, 85% 실수령</p>",
        unsafe_allow_html=True,
    )

    # ── 보유 종목 수집 ────────────────────────────────────────
    all_items = []
    for acc, items in pension_items.items():
        for it in items:
            nm    = str(it.get("종목명","")).strip()
            qty   = float(it.get("수량",  0) or 0)
            dps   = float(it.get("주당분배금", 0) or 0)
            price = float(it.get("현재가", 0) or 0)
            amt   = float(it.get("원금",  0) or 0)
            rate  = float(it.get("분배율(%)", 0) or 0)
            memo  = str(it.get("메모","")).strip()
            if not nm or nm in ("nan","") or qty == 0:
                continue
            if price == 0 and amt > 0 and qty > 0:
                price = amt / qty
            src     = "개인납입" if any(k in memo for k in ["개인","납입"]) else "퇴직금"
            monthly = qty * dps if dps > 0 else amt * rate / 100
            all_items.append({
                "nm": nm, "acc": acc, "qty": qty, "dps": dps,
                "price": price, "amt": amt, "src": src, "monthly": monthly,
            })

    if not all_items:
        st.info("연금현황 시트에 보유 종목(수량·주당분배금)을 입력하면 시뮬레이션이 활성화됩니다.")
        return

    base_monthly = irp_income + isa_income + ps_income + gen_income
    base_asset   = sum(it["price"] * it["qty"] for it in all_items if it["price"] > 0)

    # ── 파라미터 ─────────────────────────────────────────────
    st.markdown("#### ⚙️ 시뮬레이션 파라미터")
    p1, p2, p3 = st.columns(3)
    sim_yr  = p1.slider("시뮬레이션 기간 (년)", 1, 15, 5, 1, key="ri_yr")
    inf_pct = p2.slider("물가상승률 (%/년)", 0.0, 5.0, 2.0, 0.5, key="ri_inf")
    ri_tgt  = p3.number_input("목표 생활비 (원/월)",
                               value=int(target_monthly), step=100_000, key="ri_tgt")

    # ── 종목별 배분% · 주가 상승률 설정 ──────────────────────
    st.markdown("#### 📋 종목별 재투자 배분 및 주가 상승률 설정")
    st.caption(
        "배분% = 월 분배금 중 해당 종목 매수에 쓸 비율 (합계가 재투자 비율) "
        "· 상승률 = 연간 예측 주가 상승률"
    )

    ACC_COLORS = {
        "IRP":    ("#87CEEB", "rgba(135,206,235,0.12)"),
        "ISA":    ("#7dffb0", "rgba(125,255,176,0.12)"),
        "연금저축": ("#FFD700", "rgba(255,215,0,0.10)"),
        "일반":   ("#AFA9EC", "rgba(175,169,236,0.10)"),
    }

    hdr = st.columns([0.3, 2.8, 1.0, 0.8, 0.8, 0.8, 0.8])
    for lbl, col in zip(["","종목명","계좌","현재가","배분%/월","상승률%/년","월분배금"], hdr):
        col.markdown(
            f"<div style='font-size:0.72rem;color:rgba(255,255,255,0.4);"
            f"padding-bottom:4px;'>{lbl}</div>",
            unsafe_allow_html=True,
        )

    item_configs = []
    for i, it in enumerate(all_items):
        bc, bg = ACC_COLORS.get(it["acc"], ("#AFA9EC","rgba(175,169,236,0.10)"))
        cols = st.columns([0.3, 2.8, 1.0, 0.8, 0.8, 0.8, 0.8])
        sel   = cols[0].checkbox("", value=(i < 4), key=f"ri_sel_{i}",
                                 label_visibility="collapsed")
        cols[1].markdown(
            f"<div style='padding:4px 0;font-size:0.85rem;'>{it['nm'][:28]}</div>",
            unsafe_allow_html=True)
        cols[2].markdown(
            f"<div style='padding:4px 0;font-size:0.75rem;'>"
            f"<span style='background:{bg};color:{bc};padding:1px 6px;"
            f"border-radius:3px;font-weight:600;'>{it['acc']}</span></div>",
            unsafe_allow_html=True)
        cols[3].markdown(
            f"<div style='padding:4px 0;font-size:0.82rem;text-align:right;'>"
            f"{int(it['price']):,}원</div>",
            unsafe_allow_html=True)
        alloc = cols[4].number_input("배분%", 0, 100,
                                     value=(5 if i < 4 else 0), step=1,
                                     key=f"ri_alloc_{i}",
                                     label_visibility="collapsed")
        rise  = cols[5].number_input("상승률", -5.0, 20.0, value=0.0, step=0.5,
                                     key=f"ri_rise_{i}",
                                     label_visibility="collapsed")
        cols[6].markdown(
            f"<div style='padding:4px 0;font-size:0.82rem;text-align:right;'>"
            f"{it['monthly']:,.0f}원</div>",
            unsafe_allow_html=True)
        item_configs.append({**it, "sel": sel, "alloc": alloc, "rise": rise})

    # 배분% 합계 = 재투자 비율 자동 계산
    alloc_sum   = sum(c["alloc"] for c in item_configs if c["sel"])
    ri_pct      = alloc_sum
    consume_pct = 100 - ri_pct
    _ri_c = "#FF4B4B" if ri_pct > 80 else "#FFD700" if ri_pct > 50 else "#7dffb0"
    st.markdown(
        f"<div style='display:flex;gap:24px;padding:8px 0;font-size:0.82rem;'>"
        f"<span>재투자 비율: <b style='color:{_ri_c};font-size:1rem;'>{ri_pct}%</b></span>"
        f"<span style='color:rgba(255,255,255,0.5);'>실수령 비율: "
        f"<b style='color:#7dffb0;'>{consume_pct}%</b></span>"
        f"<span style='color:rgba(255,255,255,0.35);'>재투자액: "
        f"<b>{base_monthly*ri_pct/100:,.0f}원/월</b></span>"
        f"<span style='color:rgba(255,255,255,0.35);'>실수령: "
        f"<b>{base_monthly*consume_pct/100:,.0f}원/월</b></span>"
        f"</div>",
        unsafe_allow_html=True,
    )
    if ri_pct > 80:
        st.warning("⚠️ 재투자 비율이 80%를 초과합니다. 실수령 생활비가 매우 줄어듭니다.")

    st.divider()

    # ── 시뮬레이션 엔진 ──────────────────────────────────────
    def _simulate(years, with_reinvest, with_rise):
        months    = years * 12
        qtys      = [c["qty"]   for c in item_configs]
        prices    = [c["price"] for c in item_configs]
        cum_extra = [0] * len(item_configs)
        cum_dist  = 0.0
        rows = []
        for m in range(1, months + 1):
            if with_rise:
                for i, c in enumerate(item_configs):
                    if c["rise"] != 0:
                        prices[i] = c["price"] * ((1 + c["rise"] / 100 / 12) ** m)
            monthly = (
                sum(c["dps"] * qtys[i] for i, c in enumerate(item_configs))
                + isa_income + ps_income + gen_income
                - sum(c["dps"] * c["qty"] for c in item_configs)
            )
            total_ri = 0.0
            added_qty = 0
            if with_reinvest:
                for i, c in enumerate(item_configs):
                    if not c["sel"] or c["alloc"] <= 0: continue
                    buy_amt = monthly * c["alloc"] / 100
                    total_ri += buy_amt
                    if prices[i] <= 0: continue
                    nq = int(buy_amt / prices[i])
                    qtys[i] += nq; cum_extra[i] += nq; added_qty += nq
            consume  = monthly - total_ri
            asset    = sum(prices[i] * qtys[i] for i in range(len(item_configs)))
            cum_dist += monthly
            rows.append({
                "m": m, "monthly": monthly, "ri": total_ri,
                "consume": consume, "asset": asset,
                "added_qty": added_qty,
                "cum_extra": sum(cum_extra), "cum_dist": cum_dist,
            })
        return rows

    sim_A = _simulate(sim_yr, True,  True)
    sim_B = _simulate(sim_yr, True,  False)
    sim_C = _simulate(sim_yr, False, True)

    # ── KPI 탭 3종 ───────────────────────────────────────────
    st.markdown("#### 📊 시뮬레이션 결과 요약")
    tab_A, tab_B, tab_C = st.tabs([
        "A: 재투자+상승 (최적)", "B: 재투자만 (보수적)", "C: 기준 (상승만)"
    ])

    def _kpi(sim):
        f = sim[-1]
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("현재 월 분배금", f"{base_monthly:,.0f}원")
        k2.metric(f"{sim_yr}년 후 월 분배금",
                  f"{f['monthly']:,.0f}원",
                  delta=f"{(f['monthly']-base_monthly)/base_monthly*100:+.1f}%")
        k3.metric(f"{sim_yr}년 후 자산 평가액",
                  f"{f['asset']/100_000_000:.2f}억원",
                  delta=(f"{(f['asset']-base_asset)/base_asset*100:+.1f}%"
                         if base_asset > 0 else None))
        k4.metric(f"{sim_yr}년 누적 분배금", f"{f['cum_dist']/100_000_000:.2f}억원")

    with tab_A: _kpi(sim_A)
    with tab_B: _kpi(sim_B)
    with tab_C: _kpi(sim_C)

    # ── 차트 3종 ─────────────────────────────────────────────
    st.divider()
    cc1, cc2, cc3 = st.columns(3)
    _ly = dict(
        height=260, paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(255,255,255,0.02)", font_color="white",
        legend=dict(orientation="h", y=-0.3, xanchor="center", x=0.5, font_size=10),
        margin=dict(t=30, b=60, l=10, r=10), hovermode="x unified",
    )
    qi   = [i for i in range(len(sim_A)) if (i+1) % 3 == 0]
    qlbl = [f"{(i+1)//12}y" if (i+1) % 12 == 0 else f"{(i+1)//3}q" for i in qi]

    with cc1:
        st.markdown("**월 분배금 성장**")
        fig1 = _go.Figure()
        for sim, nm, clr, dash in [
            (sim_A, "A 재투자+상승", "#7dffb0", "solid"),
            (sim_B, "B 재투자만",    "#87CEEB", "dot"),
            (sim_C, "C 기준",        "#888780", "dash"),
        ]:
            fig1.add_trace(_go.Scatter(
                x=qlbl, y=[sim[i]["monthly"]/10000 for i in qi],
                name=nm, line=dict(color=clr, width=2, dash=dash), mode="lines",
            ))
        fig1.add_hline(
            y=ri_tgt*(1-ri_pct/100)/10000,
            line_dash="dot", line_color="#FFD700", line_width=1,
            annotation_text="실수령 목표", annotation_font_color="#FFD700",
            annotation_position="top left",
        )
        fig1.update_layout(**_ly, yaxis=dict(title="만원", tickformat=","),
                           xaxis=dict(tickangle=-30))
        st.plotly_chart(fig1, use_container_width=True)

    with cc2:
        st.markdown("**자산 평가액 추이**")
        fig2 = _go.Figure()
        for sim, nm, clr, dash in [
            (sim_A, "A", "#7dffb0", "solid"),
            (sim_B, "B", "#87CEEB", "dot"),
            (sim_C, "C", "#888780", "dash"),
        ]:
            fig2.add_trace(_go.Scatter(
                x=qlbl, y=[sim[i]["asset"]/100_000_000 for i in qi],
                name=nm, line=dict(color=clr, width=2, dash=dash), mode="lines",
            ))
        fig2.update_layout(**_ly, yaxis=dict(title="억원", tickformat=".2f"),
                           xaxis=dict(tickangle=-30))
        st.plotly_chart(fig2, use_container_width=True)

    with cc3:
        st.markdown("**현금흐름 vs 물가조정 목표**")
        fig3 = _go.Figure()
        fig3.add_trace(_go.Scatter(
            x=qlbl, y=[sim_A[i]["consume"]/10000 for i in qi],
            name="A 실수령", line=dict(color="#FFD700", width=2), mode="lines",
            fill="tozeroy", fillcolor="rgba(255,215,0,0.06)",
        ))
        fig3.add_trace(_go.Scatter(
            x=qlbl,
            y=[ri_tgt * (1 + inf_pct/100) ** ((i+1)/12) / 10000 for i in qi],
            name="물가조정 목표",
            line=dict(color="#FF4B4B", width=1.5, dash="dot"), mode="lines",
        ))
        fig3.update_layout(**_ly, yaxis=dict(title="만원", tickformat=","),
                           xaxis=dict(tickangle=-30))
        st.plotly_chart(fig3, use_container_width=True)

    # ── 연도별 비교 테이블 ───────────────────────────────────
    st.divider()
    st.markdown("#### 📋 연도별 시나리오 비교")
    tbl_rows = []
    for yr in range(1, sim_yr + 1):
        idx     = yr * 12 - 1
        adj_tgt = ri_tgt * ((1 + inf_pct/100) ** yr)
        a, b, c = sim_A[idx], sim_B[idx], sim_C[idx]
        gp      = a["consume"] / adj_tgt * 100 if adj_tgt > 0 else 0
        tbl_rows.append({
            "연도":       f"{yr}년",
            "A 월분배금": f"{a['monthly']:,.0f}원",
            "A 자산":     f"{a['asset']/100_000_000:.2f}억",
            "A 실수령":   f"{a['consume']:,.0f}원",
            "B 자산":     f"{b['asset']/100_000_000:.2f}억",
            "B 실수령":   f"{b['consume']:,.0f}원",
            "C 자산":     f"{c['asset']/100_000_000:.2f}억",
            "C 실수령":   f"{c['consume']:,.0f}원",
            "목표달성률": f"{gp:.0f}%",
            "물가목표":   f"{adj_tgt:,.0f}원",
        })
    st.dataframe(pd.DataFrame(tbl_rows), hide_index=True, use_container_width=True)

    # ── 월별 상세 ────────────────────────────────────────────
    with st.expander("📅 월별 상세 현금흐름 (A 시나리오)", expanded=False):
        d_rows = []
        for r in sim_A:
            adj_tgt = ri_tgt * ((1 + inf_pct/100) ** (r["m"]/12))
            d_rows.append({
                "월":          f"{r['m']}m",
                "월분배금":    f"{r['monthly']:,.0f}원",
                "재투자액":    f"{r['ri']:,.0f}원",
                "매수수량":    f"{r['added_qty']}주",
                "누적추가수량": f"{r['cum_extra']}주",
                "자산평가액":  f"{r['asset']/100_000_000:.3f}억",
                "실수령":      f"{r['consume']:,.0f}원",
                "목표달성률":  f"{r['consume']/adj_tgt*100:.0f}%",
            })
        st.dataframe(pd.DataFrame(d_rows), hide_index=True, use_container_width=True)

    # ── 유의사항 ─────────────────────────────────────────────
    st.divider()
    st.markdown("#### ⚠️ 재투자 시 유의사항")
    n1, n2, n3 = st.columns(3)
    for col, title, color, body in [
        (n1, "과세 발생", "#FF4B4B",
         "분배금을 재투자해도 수령 시점에 과세됩니다. "
         "IRP 퇴직금 원천: 0.76~1.1% 퇴직소득세, 개인납입·연금저축: 5.5% 연금소득세."),
        (n2, "납입 한도", "#FFD700",
         "IRP·연금저축 추가 납입 시 연간 한도(각 1,800만원) 확인이 필요합니다. "
         "퇴직금으로 채워진 IRP는 추가 납입 여력이 제한될 수 있습니다."),
        (n3, "과세표준 증가", "#87CEEB",
         "재투자로 수량이 늘면 분배금과 과세표준도 증가합니다. "
         "연금소득 1,500만원 한도를 과세관리 탭에서 함께 모니터링하세요."),
    ]:
        col.markdown(
            f"<div style='background:rgba(255,255,255,0.03);"
            f"border-left:4px solid {color};"
            f"padding:10px 14px;border-radius:0 8px 8px 0;font-size:0.83rem;line-height:1.7;'>"
            f"<b style='color:{color};'>{title}</b><br>{body}</div>",
            unsafe_allow_html=True,
        )


with _main_tab9:
    _render_reinvest_tab(
        pension_items={
            "IRP":     _pension_irp_items,
            "ISA":     _pension_isa_items,
            "일반":    _pension_gen_items,
            "연금저축": _pension_ps_items,
        },
        irp_income=irp_income,
        isa_income=isa_income,
        ps_income=ps_income,
        gen_income=_gen_monthly_income,
        irp_total=irp_total,
        isa_total=isa_total,
        ps_total=ps_total,
        target_monthly=target_monthly,
        sc_df=sc_df,
        sc_names=sc_names,
    )
