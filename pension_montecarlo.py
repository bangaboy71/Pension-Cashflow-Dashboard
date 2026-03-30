"""
pension_montecarlo.py — 연금 자산 Monte Carlo 시뮬레이션
==========================================================

pension_app.py 에 "🎲 Monte Carlo" 탭을 추가합니다.
기존 코드 수정 없이 탭 선언부에 2줄만 추가하면 동작합니다.

── pension_app.py 2966번 줄 탭 선언 수정 ──────────────────
# 기존 (4개)
_main_tab1, _main_tab2, _main_tab3, _main_tab4 = st.tabs([
    "📊 현금흐름 대시보드", "📒 월별 가계부", "📈 보유종목", "🔍 관심종목"
])

# 변경 (5개 또는 6개 — 벤치마크 탭도 있으면 6개)
_main_tab1, _main_tab2, _main_tab3, _main_tab4, _main_tab5 = st.tabs([
    "📊 현금흐름 대시보드", "📒 월별 가계부",
    "📈 보유종목", "🔍 관심종목", "🎲 Monte Carlo",
])

── 탭 블록 끝에 추가 ──────────────────────────────────────
with _main_tab5:
    from pension_montecarlo import render_montecarlo_tab
    render_montecarlo_tab(
        irp_total      = irp_total,
        isa_total      = isa_total,
        ps_total       = ps_total,
        gen_total      = general_total,
        public_pension = public_pension,
        target_monthly = target_monthly,
        irp_rate       = default_palantir,   # IRP 월 분배율 (%)
        isa_rate       = default_kodex,      # ISA 월 분배율 (%)
        ps_rate        = default_ps,
        gen_rate       = default_general / 12,
        birth_year     = 1971,
    )

시뮬레이션 모델
──────────────────────────────────────────────────────────
• 각 계좌 자산은 매월 분배금을 지급하면서도 원금이 시장 수익률에 따라 변동
• 수익률은 GBM(기하 브라운 운동) 모델 사용 — 실제 금융 자산의 표준 모델
  dS = μ·S·dt + σ·S·dW   (μ: 기대수익률, σ: 변동성, W: 위너 프로세스)
• 인플레이션: 공적연금에만 반영 (소비자물가 연동 가정)
• 생존 기간: 95세까지 (한국 남성 기대수명 + 안전마진)
• 시뮬레이션 횟수: 1,000회 (정밀도와 속도의 균형)
"""
from __future__ import annotations

import logging
from datetime import datetime

import numpy as np
import pandas as pd
import streamlit as st

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════
# 기본 파라미터
# ════════════════════════════════════════════════════════

N_SIM          = 1_000    # 시뮬레이션 횟수
MAX_AGE        = 95       # 시뮬레이션 종료 나이
BIRTH_YEAR_DEF = 1971

# 계좌별 기대 수익률 / 변동성 기본값 (연율, %)
# 실제 ETF 과거 데이터 기반 추정치
_ASSET_PARAMS: dict[str, dict] = {
    "IRP":    {"mu": 8.0,  "sigma": 12.0},  # 커버드콜 ETF 위주 — 중수익·중변동
    "ISA":    {"mu": 7.0,  "sigma": 10.0},  # KODEX200 위주 — 지수 추종
    "연금저축": {"mu": 7.5,  "sigma": 11.0},
    "일반":   {"mu": 6.0,  "sigma": 8.0},   # 채권혼합·배당 위주
}

# 색상
C_MEDIAN  = "#7dffb0"
C_P90     = "#87CEEB"
C_P10     = "#FF4B4B"
C_TARGET  = "#FFD700"
C_ZERO    = "rgba(255,255,255,0.2)"


# ════════════════════════════════════════════════════════
# 1. GBM 시뮬레이션 엔진
# ════════════════════════════════════════════════════════

def _run_gbm(
    initial: float,
    monthly_withdrawal: float,
    mu_annual: float,       # 기대 수익률 (연율 %)
    sigma_annual: float,    # 변동성 (연율 %)
    n_months: int,
    n_sim: int,
    seed: int = 42,
) -> np.ndarray:
    """
    GBM 기반 Monte Carlo 시뮬레이션.
    반환: shape (n_sim, n_months+1) — 각 경로의 월별 자산 잔액
    """
    if initial <= 0:
        return np.zeros((n_sim, n_months + 1))

    rng   = np.random.default_rng(seed)
    mu    = mu_annual / 100 / 12          # 월 기대 수익률
    sigma = sigma_annual / 100 / (12 ** 0.5)  # 월 변동성

    paths = np.zeros((n_sim, n_months + 1))
    paths[:, 0] = initial

    for t in range(1, n_months + 1):
        z        = rng.standard_normal(n_sim)
        ret      = np.exp((mu - 0.5 * sigma ** 2) + sigma * z)
        balance  = paths[:, t - 1] * ret - monthly_withdrawal
        paths[:, t] = np.maximum(balance, 0)   # 잔액 음수 방지

    return paths


# ════════════════════════════════════════════════════════
# 2. 통합 포트폴리오 시뮬레이션
# ════════════════════════════════════════════════════════

def run_portfolio_simulation(
    irp_total:      float,
    isa_total:      float,
    ps_total:       float,
    gen_total:      float,
    public_pension: float,   # 월 공적연금 (세전)
    target_monthly: float,   # 목표 생활비
    irp_rate:       float,   # IRP 월 분배율 (%)
    isa_rate:       float,   # ISA 월 분배율 (%)
    ps_rate:        float,
    gen_rate:       float,
    birth_year:     int,
    irp_mu:    float = _ASSET_PARAMS["IRP"]["mu"],
    irp_sigma: float = _ASSET_PARAMS["IRP"]["sigma"],
    isa_mu:    float = _ASSET_PARAMS["ISA"]["mu"],
    isa_sigma: float = _ASSET_PARAMS["ISA"]["sigma"],
    ps_mu:     float = _ASSET_PARAMS["연금저축"]["mu"],
    ps_sigma:  float = _ASSET_PARAMS["연금저축"]["sigma"],
    gen_mu:    float = _ASSET_PARAMS["일반"]["mu"],
    gen_sigma: float = _ASSET_PARAMS["일반"]["sigma"],
    inflation: float = 2.0,  # 공적연금 물가 연동 (연율 %)
    n_sim:     int   = N_SIM,
) -> dict:
    """
    4개 계좌 + 공적연금 통합 Monte Carlo 시뮬레이션.

    반환 dict
    ──────────────────────────────────────────────────────
    ages        : list[int]          — 나이 배열
    total_p50   : np.ndarray         — 총 자산 중앙값 경로
    total_p10   : np.ndarray         — 하위 10% (비관)
    total_p90   : np.ndarray         — 상위 90% (낙관)
    income_p50  : np.ndarray         — 월 수령액 중앙값
    income_p10  : np.ndarray         — 월 수령액 하위 10%
    income_p90  : np.ndarray         — 월 수령액 상위 90%
    depletion_prob : float           — 자산 고갈 확률 (%)
    median_depletion_age : int|None  — 중앙값 경로 고갈 나이
    paths_total : np.ndarray         — 전체 경로 (n_sim × months)
    account_paths : dict             — 계좌별 중앙값 경로
    """
    current_year = datetime.now().year
    current_age  = current_year - birth_year
    n_months     = (MAX_AGE - current_age) * 12
    if n_months <= 0:
        n_months = 12

    # ── 계좌별 월 인출액 계산 ──────────────────────────
    irp_withdrawal = irp_total * irp_rate / 100 if irp_total > 0 else 0
    isa_withdrawal = isa_total * isa_rate / 100 if isa_total > 0 else 0
    ps_withdrawal  = ps_total  * ps_rate  / 100 if ps_total  > 0 else 0
    gen_withdrawal = gen_total * gen_rate  / 100 if gen_total > 0 else 0

    # ── 계좌별 GBM 시뮬레이션 ──────────────────────────
    accounts = {
        "IRP":    (irp_total, irp_withdrawal, irp_mu, irp_sigma),
        "ISA":    (isa_total, isa_withdrawal, isa_mu, isa_sigma),
        "연금저축": (ps_total,  ps_withdrawal,  ps_mu,  ps_sigma),
        "일반":   (gen_total,  gen_withdrawal,  gen_mu,  gen_sigma),
    }

    paths: dict[str, np.ndarray] = {}
    for i, (acc, (init, wd, mu, sigma)) in enumerate(accounts.items()):
        paths[acc] = _run_gbm(init, wd, mu, sigma, n_months, n_sim, seed=42 + i)

    # ── 공적연금 (인플레이션 반영 고정 수입) ──────────
    # 매년 inflation% 씩 증가하는 공적연금 시계열
    pub_series = np.array([
        public_pension * ((1 + inflation / 100) ** (m / 12))
        for m in range(n_months + 1)
    ])

    # ── 총 자산 경로 (공적연금은 자산 아님 — 수입으로만 계산) ──
    paths_total = sum(paths[acc] for acc in accounts)   # shape: (n_sim, n_months+1)

    # ── 월 수령액 경로: 분배금 + 공적연금 ──────────────
    monthly_dist = np.zeros((n_sim, n_months + 1))
    for acc, (init, wd, _, _) in accounts.items():
        if init > 0:
            # 잔액이 있을 때만 분배금 수령 (잔액 고갈 시 0)
            mask = paths[acc] > 0
            monthly_dist += mask * wd

    # 공적연금 추가 (브로드캐스트)
    monthly_income = monthly_dist + pub_series[np.newaxis, :]

    # ── 백분위 계산 ──────────────────────────────────
    total_p50 = np.percentile(paths_total, 50, axis=0)
    total_p10 = np.percentile(paths_total, 10, axis=0)
    total_p90 = np.percentile(paths_total, 90, axis=0)

    income_p50 = np.percentile(monthly_income, 50, axis=0)
    income_p10 = np.percentile(monthly_income, 10, axis=0)
    income_p90 = np.percentile(monthly_income, 90, axis=0)

    # ── 자산 고갈 확률 ────────────────────────────────
    # MAX_AGE 시점에 총 자산이 0인 시뮬레이션 비율
    depletion_prob = float((paths_total[:, -1] == 0).mean() * 100)

    # ── 중앙값 경로 고갈 나이 ─────────────────────────
    median_depletion_age = None
    for m, val in enumerate(total_p50):
        if val <= 0:
            median_depletion_age = current_age + m // 12
            break

    # ── 나이 배열 (연 단위, 연말 기준) ───────────────
    ages = list(range(current_age, MAX_AGE + 1))

    # 월 → 연 집계 (연말 잔액 / 연평균 수령액)
    n_years = MAX_AGE - current_age + 1

    def _to_annual_balance(monthly_arr):
        """월별 경로 → 연말 잔액 배열 (연 단위)"""
        out = np.zeros(n_years)
        for y in range(n_years):
            m_idx = min(y * 12, len(monthly_arr) - 1)
            out[y] = monthly_arr[m_idx]
        return out

    def _to_annual_income(monthly_arr):
        """월별 수령액 → 연간 수령액 배열"""
        out = np.zeros(n_years)
        for y in range(n_years):
            s = y * 12
            e = min(s + 12, len(monthly_arr))
            out[y] = monthly_arr[s:e].sum()
        return out

    return {
        "ages":                ages,
        "total_p50":           _to_annual_balance(total_p50),
        "total_p10":           _to_annual_balance(total_p10),
        "total_p90":           _to_annual_balance(total_p90),
        "income_p50":          _to_annual_income(income_p50) / 12,   # 월평균
        "income_p10":          _to_annual_income(income_p10) / 12,
        "income_p90":          _to_annual_income(income_p90) / 12,
        "depletion_prob":      depletion_prob,
        "median_depletion_age": median_depletion_age,
        "paths_total":         paths_total,
        "account_paths": {
            acc: _to_annual_balance(np.percentile(paths[acc], 50, axis=0))
            for acc in accounts
            if accounts[acc][0] > 0
        },
        "pub_series_annual":   np.array([
            public_pension * ((1 + inflation / 100) ** y) * 12 / 12
            for y in range(n_years)
        ]),
        "n_sim": n_sim,
        "current_age": current_age,
        "target_monthly": target_monthly,
    }


# ════════════════════════════════════════════════════════
# 3. 메인 렌더링 함수
# ════════════════════════════════════════════════════════

def render_montecarlo_tab(
    irp_total:      float,
    isa_total:      float,
    ps_total:       float,
    gen_total:      float,
    public_pension: float,
    target_monthly: float,
    irp_rate:       float,
    isa_rate:       float,
    ps_rate:        float,
    gen_rate:       float,
    birth_year:     int = BIRTH_YEAR_DEF,
) -> None:
    """
    🎲 Monte Carlo 탭 전체 렌더링.
    pension_app.py with _main_tab5: 블록에서 호출합니다.
    """
    import plotly.graph_objects as go

    st.markdown("#### 🎲 Monte Carlo 연금 자산 수명 시뮬레이션")
    st.caption(
        f"GBM 모델 · {N_SIM:,}회 시뮬레이션 · "
        f"95세까지 · 분배율 기반 월 인출 가정"
    )

    # ════════════════════════════════════════════════════
    # 파라미터 패널
    # ════════════════════════════════════════════════════
    with st.expander("⚙️ 시뮬레이션 파라미터 조정", expanded=False):
        st.caption("기대 수익률과 변동성을 조정하면 시뮬레이션이 즉시 업데이트됩니다.")
        pc1, pc2, pc3, pc4 = st.columns(4)

        irp_mu    = pc1.number_input("IRP 기대수익률(%)", 0.0, 20.0,
                    float(_ASSET_PARAMS["IRP"]["mu"]), 0.5, key="mc_irp_mu")
        irp_sigma = pc1.number_input("IRP 변동성(%)",     0.0, 40.0,
                    float(_ASSET_PARAMS["IRP"]["sigma"]), 0.5, key="mc_irp_sigma")

        isa_mu    = pc2.number_input("ISA 기대수익률(%)", 0.0, 20.0,
                    float(_ASSET_PARAMS["ISA"]["mu"]), 0.5, key="mc_isa_mu")
        isa_sigma = pc2.number_input("ISA 변동성(%)",     0.0, 40.0,
                    float(_ASSET_PARAMS["ISA"]["sigma"]), 0.5, key="mc_isa_sigma")

        ps_mu    = pc3.number_input("연금저축 기대수익률(%)", 0.0, 20.0,
                   float(_ASSET_PARAMS["연금저축"]["mu"]), 0.5, key="mc_ps_mu")
        ps_sigma = pc3.number_input("연금저축 변동성(%)",     0.0, 40.0,
                   float(_ASSET_PARAMS["연금저축"]["sigma"]), 0.5, key="mc_ps_sigma")

        gen_mu    = pc4.number_input("일반 기대수익률(%)", 0.0, 20.0,
                    float(_ASSET_PARAMS["일반"]["mu"]), 0.5, key="mc_gen_mu")
        gen_sigma = pc4.number_input("일반 변동성(%)",     0.0, 40.0,
                    float(_ASSET_PARAMS["일반"]["sigma"]), 0.5, key="mc_gen_sigma")

        inflation = st.slider("공적연금 물가 연동 (%)", 0.0, 5.0, 2.0, 0.1,
                              key="mc_inflation")

        n_sim_choice = st.select_slider(
            "시뮬레이션 횟수",
            options=[200, 500, 1_000, 2_000, 5_000],
            value=1_000,
            key="mc_n_sim",
        )

    # ── 시뮬레이션 실행 ──────────────────────────────────
    with st.spinner(f"Monte Carlo 시뮬레이션 실행 중... ({n_sim_choice:,}회)"):
        result = run_portfolio_simulation(
            irp_total=irp_total, isa_total=isa_total,
            ps_total=ps_total,   gen_total=gen_total,
            public_pension=public_pension, target_monthly=target_monthly,
            irp_rate=irp_rate, isa_rate=isa_rate,
            ps_rate=ps_rate,   gen_rate=gen_rate,
            birth_year=birth_year,
            irp_mu=irp_mu,   irp_sigma=irp_sigma,
            isa_mu=isa_mu,   isa_sigma=isa_sigma,
            ps_mu=ps_mu,     ps_sigma=ps_sigma,
            gen_mu=gen_mu,   gen_sigma=gen_sigma,
            inflation=inflation,
            n_sim=n_sim_choice,
        )

    ages   = result["ages"]
    dep_p  = result["depletion_prob"]
    dep_a  = result["median_depletion_age"]
    total  = irp_total + isa_total + ps_total + gen_total

    # ════════════════════════════════════════════════════
    # 요약 지표 카드 (상단 4개)
    # ════════════════════════════════════════════════════
    surv_p    = 100 - dep_p
    surv_color = "#7dffb0" if surv_p >= 80 else ("#FFD700" if surv_p >= 50 else "#FF4B4B")
    dep_color  = "#FF4B4B" if dep_p >= 30 else ("#FFD700" if dep_p >= 10 else "#7dffb0")

    # 중앙값 경로 기준 95세 잔액
    last_p50  = float(result["total_p50"][-1])
    last_p10  = float(result["total_p10"][-1])

    # 목표 달성률 (중앙값 경로 기준 월 수령액 vs 목표 생활비)
    avg_income_p50 = float(result["income_p50"].mean())
    ach_rate  = avg_income_p50 / target_monthly * 100 if target_monthly > 0 else 0
    ach_color = "#7dffb0" if ach_rate >= 100 else ("#FFD700" if ach_rate >= 80 else "#FF4B4B")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("95세 생존 확률",
              f"{surv_p:.1f}%",
              delta="자산 유지" if surv_p >= 80 else "고갈 위험",
              delta_color="normal" if surv_p >= 80 else "inverse",
              help=f"{n_sim_choice:,}회 시뮬레이션 중 95세까지 자산이 남는 비율")
    m2.metric("자산 고갈 확률",
              f"{dep_p:.1f}%",
              delta=f"고갈 예상 나이: {dep_a}세" if dep_a else "95세까지 유지",
              delta_color="inverse" if dep_p >= 20 else "normal")
    m3.metric("중앙값 95세 잔액",
              f"{last_p50/100_000_000:.2f}억원" if last_p50 >= 0 else "고갈",
              delta=f"비관 시나리오: {last_p10/100_000_000:.2f}억원" if last_p10 >= 0 else "고갈",
              delta_color="normal" if last_p10 >= 0 else "inverse")
    m4.metric("평균 월 수령액 (중앙값)",
              f"{avg_income_p50:,.0f}원",
              delta=f"목표 달성률 {ach_rate:.0f}%",
              delta_color="normal" if ach_rate >= 100 else "inverse")

    st.divider()

    # ════════════════════════════════════════════════════
    # 차트 1: 총 자산 수명 Fan Chart
    # ════════════════════════════════════════════════════
    st.markdown("##### 📈 연금 자산 수명 예측 (총 자산)")

    fig_asset = go.Figure()

    # 신뢰 구간 밴드
    fig_asset.add_trace(go.Scatter(
        x=ages + ages[::-1],
        y=list(result["total_p90"] / 1e8) + list(result["total_p10"] / 1e8)[::-1],
        fill="toself",
        fillcolor="rgba(135,206,235,0.1)",
        line=dict(color="rgba(0,0,0,0)"),
        name="10%~90% 구간",
        hoverinfo="skip",
    ))

    # 낙관 (P90)
    fig_asset.add_trace(go.Scatter(
        x=ages, y=result["total_p90"] / 1e8,
        name="낙관 (상위 10%)",
        line=dict(color=C_P90, width=1.5, dash="dot"),
        hovertemplate="%{x}세: %{y:.2f}억원<extra>낙관</extra>",
    ))
    # 비관 (P10)
    fig_asset.add_trace(go.Scatter(
        x=ages, y=result["total_p10"] / 1e8,
        name="비관 (하위 10%)",
        line=dict(color=C_P10, width=1.5, dash="dot"),
        hovertemplate="%{x}세: %{y:.2f}억원<extra>비관</extra>",
    ))
    # 중앙값 (P50)
    fig_asset.add_trace(go.Scatter(
        x=ages, y=result["total_p50"] / 1e8,
        name="중앙값 (50%)",
        line=dict(color=C_MEDIAN, width=2.5),
        hovertemplate="%{x}세: %{y:.2f}억원<extra>중앙값</extra>",
    ))

    # 현재 총자산 참조선
    fig_asset.add_hline(
        y=total / 1e8,
        line_dash="dot", line_color=C_ZERO, line_width=1,
        annotation_text=f"현재 {total/1e8:.2f}억원",
        annotation_font_color="rgba(255,255,255,0.5)",
        annotation_position="top left",
    )
    # 0선
    fig_asset.add_hline(y=0, line_color=C_ZERO, line_width=0.5)

    fig_asset.update_layout(
        height=380,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(255,255,255,0.02)",
        font_color="white",
        legend=dict(orientation="h", yanchor="bottom", y=-0.3,
                    xanchor="center", x=0.5),
        margin=dict(t=20, b=80, l=60, r=20),
        xaxis=dict(title="나이 (세)", gridcolor="rgba(255,255,255,0.05)",
                   dtick=5),
        yaxis=dict(title="총 자산 (억원)", gridcolor="rgba(255,255,255,0.05)"),
        hovermode="x unified",
    )
    st.plotly_chart(fig_asset, use_container_width=True)

    # ════════════════════════════════════════════════════
    # 차트 2: 계좌별 자산 잔액 (누적 영역)
    # ════════════════════════════════════════════════════
    st.markdown("##### 🏦 계좌별 자산 잔액 추이 (중앙값 기준)")

    fig_acc = go.Figure()
    acc_colors = {"IRP": "#FFD700", "ISA": "#87CEEB",
                  "연금저축": "#7dffb0", "일반": "#FF9999"}
    cumulative = np.zeros(len(ages))

    def _hex_to_rgba(hex_color: str, alpha: float = 0.2) -> str:
        h = hex_color.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f"rgba({r},{g},{b},{alpha})"

    for acc, path in result["account_paths"].items():
        color  = acc_colors.get(acc, "#aaaaaa")
        fill_c = _hex_to_rgba(color, 0.2)
        fig_acc.add_trace(go.Scatter(
            x=ages, y=(cumulative + path) / 1e8,
            name=acc, mode="lines",
            fill="tonexty" if any(cumulative) else "tozeroy",
            fillcolor=fill_c,
            line=dict(color=color, width=1),
            hovertemplate=f"{acc}: %{{y:.2f}}억원<extra></extra>",
        ))
        cumulative = cumulative + path

    fig_acc.add_hline(y=0, line_color=C_ZERO, line_width=0.5)
    fig_acc.update_layout(
        height=320,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(255,255,255,0.02)",
        font_color="white",
        legend=dict(orientation="h", yanchor="bottom", y=-0.3,
                    xanchor="center", x=0.5),
        margin=dict(t=10, b=80, l=60, r=20),
        xaxis=dict(title="나이 (세)", gridcolor="rgba(255,255,255,0.05)", dtick=5),
        yaxis=dict(title="자산 잔액 (억원)", gridcolor="rgba(255,255,255,0.05)"),
        hovermode="x unified",
    )
    st.plotly_chart(fig_acc, use_container_width=True)

    # ════════════════════════════════════════════════════
    # 차트 3: 월 수령액 Fan Chart
    # ════════════════════════════════════════════════════
    st.markdown("##### 💰 월 수령액 예측 (공적연금 + 분배금)")

    fig_income = go.Figure()

    # 신뢰 구간
    fig_income.add_trace(go.Scatter(
        x=ages + ages[::-1],
        y=list(result["income_p90"] / 1e4) + list(result["income_p10"] / 1e4)[::-1],
        fill="toself",
        fillcolor="rgba(255,215,0,0.08)",
        line=dict(color="rgba(0,0,0,0)"),
        name="10%~90% 구간",
        hoverinfo="skip",
    ))
    fig_income.add_trace(go.Scatter(
        x=ages, y=result["income_p90"] / 1e4,
        name="낙관 (상위 10%)",
        line=dict(color=C_P90, width=1.5, dash="dot"),
        hovertemplate="%{x}세: %{y:,.0f}만원/월<extra>낙관</extra>",
    ))
    fig_income.add_trace(go.Scatter(
        x=ages, y=result["income_p10"] / 1e4,
        name="비관 (하위 10%)",
        line=dict(color=C_P10, width=1.5, dash="dot"),
        hovertemplate="%{x}세: %{y:,.0f}만원/월<extra>비관</extra>",
    ))
    fig_income.add_trace(go.Scatter(
        x=ages, y=result["income_p50"] / 1e4,
        name="중앙값",
        line=dict(color=C_MEDIAN, width=2.5),
        hovertemplate="%{x}세: %{y:,.0f}만원/월<extra>중앙값</extra>",
    ))
    # 공적연금 (고정)
    fig_income.add_trace(go.Scatter(
        x=ages, y=result["pub_series_annual"] / 1e4,
        name="공적연금 (물가 연동)",
        line=dict(color="#AFA9EC", width=1.5, dash="dashdot"),
        hovertemplate="%{x}세: %{y:,.0f}만원/월<extra>공적연금</extra>",
    ))
    # 목표 생활비 기준선
    if target_monthly > 0:
        fig_income.add_hline(
            y=target_monthly / 1e4,
            line_dash="dot", line_color=C_TARGET, line_width=1.5,
            annotation_text=f"목표 {target_monthly/1e4:.0f}만원",
            annotation_font_color=C_TARGET,
            annotation_position="top right",
        )

    fig_income.update_layout(
        height=320,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(255,255,255,0.02)",
        font_color="white",
        legend=dict(orientation="h", yanchor="bottom", y=-0.3,
                    xanchor="center", x=0.5),
        margin=dict(t=10, b=80, l=60, r=20),
        xaxis=dict(title="나이 (세)", gridcolor="rgba(255,255,255,0.05)", dtick=5),
        yaxis=dict(title="월 수령액 (만원)", gridcolor="rgba(255,255,255,0.05)"),
        hovermode="x unified",
    )
    st.plotly_chart(fig_income, use_container_width=True)

    # ════════════════════════════════════════════════════
    # 차트 4: 고갈 확률 히스토그램
    # ════════════════════════════════════════════════════
    st.markdown("##### 📊 자산 고갈 나이 분포")

    paths_total = result["paths_total"]
    current_age = result["current_age"]
    n_months    = paths_total.shape[1] - 1

    # 각 시뮬레이션의 고갈 나이 계산
    depletion_ages = []
    for sim_idx in range(paths_total.shape[0]):
        path = paths_total[sim_idx]
        depleted = False
        for m in range(1, n_months + 1):
            if path[m] <= 0 < path[m - 1]:
                depletion_ages.append(current_age + m // 12)
                depleted = True
                break
        if not depleted:
            depletion_ages.append(MAX_AGE + 1)   # 95세 초과 = 생존

    dep_arr   = np.array(depletion_ages)
    survived  = (dep_arr > MAX_AGE).sum()
    survived_pct = survived / len(dep_arr) * 100

    # 고갈된 케이스만 히스토그램
    depleted_only = dep_arr[dep_arr <= MAX_AGE]

    fig_hist = go.Figure()
    if len(depleted_only) > 0:
        fig_hist.add_trace(go.Histogram(
            x=depleted_only,
            nbinsx=MAX_AGE - current_age,
            name="고갈 케이스",
            marker_color="rgba(255,75,75,0.7)",
            hovertemplate="고갈 나이 %{x}세: %{y}건<extra></extra>",
        ))

    # 생존 비율 주석
    fig_hist.add_annotation(
        x=MAX_AGE - 2, y=0,
        text=f"95세 생존: {survived_pct:.1f}%<br>({survived:,}건)",
        showarrow=False,
        font=dict(color=C_MEDIAN, size=12),
        xanchor="right", yanchor="bottom",
        bgcolor="rgba(0,0,0,0.4)",
    )
    fig_hist.update_layout(
        height=260,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(255,255,255,0.02)",
        font_color="white",
        showlegend=False,
        margin=dict(t=10, b=50, l=60, r=20),
        xaxis=dict(title="고갈 나이 (세)", gridcolor="rgba(255,255,255,0.05)",
                   range=[current_age, MAX_AGE + 1]),
        yaxis=dict(title="시뮬레이션 건수", gridcolor="rgba(255,255,255,0.05)"),
    )
    st.plotly_chart(fig_hist, use_container_width=True)

    # ════════════════════════════════════════════════════
    # 연도별 요약 테이블
    # ════════════════════════════════════════════════════
    st.divider()
    st.markdown("##### 📋 나이별 자산·수령액 요약 (5년 단위)")

    milestone_ages = [a for a in ages if (a - current_age) % 5 == 0 or a == current_age]
    rows = []
    for a in milestone_ages:
        idx = ages.index(a) if a in ages else -1
        if idx < 0:
            continue
        p50  = result["total_p50"][idx]
        p10  = result["total_p10"][idx]
        p90  = result["total_p90"][idx]
        inc  = result["income_p50"][idx]
        inc10 = result["income_p10"][idx]
        rows.append({
            "나이":           a,
            "총자산 중앙값":   round(p50 / 1e8, 2),
            "총자산 비관":     round(p10 / 1e8, 2),
            "총자산 낙관":     round(p90 / 1e8, 2),
            "월수령 중앙값":   round(inc / 1e4, 1),
            "월수령 비관":     round(inc10 / 1e4, 1),
            "목표달성(%)":    round(inc / target_monthly * 100, 1) if target_monthly > 0 else 0,
        })

    if rows:
        tbl_df = pd.DataFrame(rows)
        st.dataframe(
            tbl_df, hide_index=True, use_container_width=True,
            column_config={
                "나이":           st.column_config.NumberColumn("나이", format="%d세"),
                "총자산 중앙값":   st.column_config.NumberColumn("총자산 중앙값(억)", format="%.2f"),
                "총자산 비관":     st.column_config.NumberColumn("비관(억)",           format="%.2f"),
                "총자산 낙관":     st.column_config.NumberColumn("낙관(억)",           format="%.2f"),
                "월수령 중앙값":   st.column_config.NumberColumn("월수령 중앙값(만원)", format="%,.1f"),
                "월수령 비관":     st.column_config.NumberColumn("월수령 비관(만원)",   format="%,.1f"),
                "목표달성(%)":    st.column_config.ProgressColumn(
                    "목표달성률",
                    format="%.1f%%",
                    min_value=0, max_value=200,
                ),
            },
        )

    # ════════════════════════════════════════════════════
    # 모델 설명
    # ════════════════════════════════════════════════════
    with st.expander("📖 시뮬레이션 모델 설명", expanded=False):
        st.markdown(f"""
**GBM(기하 브라운 운동) 모델**

실제 금융 자산 가격 변동의 표준 수학 모델입니다.

```
dS = μ·S·dt + σ·S·dW
```
- `μ` : 기대 수익률 (위에서 입력한 값)
- `σ` : 변동성 (표준편차, 연율화)
- `dW` : 무작위 충격 (위너 프로세스)

**매월 인출 구조**

각 계좌에서 `원금 × 월 분배율` 만큼 매월 인출되며,
잔액이 0이 되면 해당 계좌 수령은 중단됩니다.
공적연금은 인플레이션 {inflation:.1f}%로 매년 증가합니다.

**백분위 해석**

| 구간 | 의미 |
|------|------|
| 상위 10% (낙관) | 시뮬레이션 중 상위 10%의 좋은 결과 |
| 중앙값 (50%)   | 가장 가능성 높은 결과 |
| 하위 10% (비관) | 시뮬레이션 중 하위 10%의 나쁜 결과 |

**주의사항**

- 세금 효과는 현재 단순화하여 적용됨
- 미래 수익률·변동성은 과거 데이터 기반 추정치
- 실제 자산 가격은 더 복잡한 패턴을 보일 수 있음
- 투자 의사결정 전 전문가 상담을 권장합니다
""")
