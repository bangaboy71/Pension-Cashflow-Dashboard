"""
pension_benchmark.py — 연금 포트폴리오 수익률 · 벤치마크 비교 분석
======================================================================

pension_app.py 에 "📊 수익률 벤치마크" 탭을 추가합니다.
pension_app.py 의 기존 코드를 전혀 수정하지 않고
아래 두 줄만 삽입하면 동작합니다.

  ── pension_app.py 2966번 줄 (탭 선언부) 수정 ──
  # 기존
  _main_tab1, _main_tab2, _main_tab3, _main_tab4 = st.tabs([...])

  # 변경
  _main_tab1, _main_tab2, _main_tab3, _main_tab4, _main_tab5 = st.tabs([
      "📊 현금흐름 대시보드", "📒 월별 가계부",
      "📈 보유종목", "🔍 관심종목", "📐 수익률 벤치마크",
  ])

  ── pension_app.py 탭 블록 끝에 추가 ──
  with _main_tab5:
      from pension_benchmark import render_benchmark_tab
      render_benchmark_tab(
          irp_items    = _pension_irp_items,
          isa_items    = _pension_isa_items,
          ps_items     = _pension_ps_items,
          gen_items    = _pension_gen_items,
          irp_total    = irp_total,
          isa_total    = isa_total,
          ps_total     = ps_total,
          gen_total    = general_total,
      )

벤치마크 지표
─────────────────────────────────────────────────────────────
KOSPI          ^KS11      국내 주식 시장 대표 지수
KOSPI200       069500.KS  KODEX200 ETF (직접 매수 대안)
KOSDAQ         ^KQ11      코스닥 지수
S&P500         ^GSPC      미국 주식 대표 지수
나스닥100      ^IXIC      미국 기술주
미국채 10년    ^TNX       안전자산 금리 기준
배당지수 참고  402970.KS  TIGER 미국배당다우존스 (IRP 내 대표 ETF)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd
import streamlit as st

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

# ════════════════════════════════════════════════════════
# 벤치마크 정의
# ════════════════════════════════════════════════════════

BENCHMARKS: dict[str, dict] = {
    "KOSPI":      {"ticker": "^KS11",    "label": "KOSPI",        "color": "#87CEEB"},
    "KOSPI200":   {"ticker": "069500.KS","label": "KODEX 200",    "color": "#5DCAA5"},
    "S&P500":     {"ticker": "^GSPC",    "label": "S&P 500",      "color": "#FFD700"},
    "나스닥100":   {"ticker": "^IXIC",   "label": "NASDAQ",       "color": "#AFA9EC"},
    "미국채10년":  {"ticker": "^TNX",    "label": "US10Y (%)",    "color": "#FF9999"},
}

# 기간 옵션 (label → days)
PERIOD_OPTIONS: dict[str, int] = {
    "1개월":  30,
    "3개월":  90,
    "6개월": 180,
    "1년":   365,
    "2년":   730,
}

# 계좌별 색상
ACCOUNT_COLORS: dict[str, str] = {
    "IRP":    "#FFD700",
    "ISA":    "#87CEEB",
    "연금저축": "#7dffb0",
    "일반":   "#FF9999",
    "포트폴리오 합계": "#ffffff",
}


# ════════════════════════════════════════════════════════
# 1. 가격 이력 수집
# ════════════════════════════════════════════════════════

@st.cache_data(ttl="30m", show_spinner=False)
def _fetch_yf_history(ticker: str, days: int) -> pd.DataFrame:
    """
    Yahoo Finance 에서 일봉 OHLCV 수집.
    pension_app.py 의 fetch_price_history() 와 동일 방식,
    기간을 days 단위로 자유롭게 지정 가능.
    반환: Date(index) | Close 컬럼 DataFrame
    """
    range_map = {30: "1mo", 60: "2mo", 90: "3mo", 180: "6mo",
                 365: "1y", 730: "2y", 1095: "3y"}
    # 가장 가까운 range 선택
    yrange = "1y"
    for d_limit in sorted(range_map):
        if days <= d_limit:
            yrange = range_map[d_limit]
            break
    else:
        yrange = "2y"

    try:
        import requests
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        res = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            params={"interval": "1d", "range": yrange},
            timeout=8,
        )
        data   = res.json()
        result = data["chart"]["result"][0]
        times  = result["timestamp"]
        closes = result["indicators"]["quote"][0].get("close", [])

        rows = []
        for ts, cl in zip(times, closes):
            if cl is None or cl <= 0:
                continue
            rows.append({
                "Date":  pd.Timestamp(ts, unit="s", tz="Asia/Seoul").tz_localize(None),
                "Close": float(cl),
            })

        df = pd.DataFrame(rows).dropna().sort_values("Date")
        df = df.set_index("Date")

        # 요청 기간으로 자름
        cutoff = datetime.now() - timedelta(days=days)
        df = df[df.index >= pd.Timestamp(cutoff)]
        return df

    except Exception as e:
        logger.warning(f"_fetch_yf_history({ticker}): {e}")
        return pd.DataFrame()


# ════════════════════════════════════════════════════════
# 2. 포트폴리오 수익률 계산
# ════════════════════════════════════════════════════════

def _calc_return_series(
    items: list[dict],
    account_name: str,
    days: int,
) -> pd.Series:
    """
    계좌의 종목 리스트 → 가중평균 수익률 시계열(%) 반환.

    계산 방식:
      - 수량 > 0 이고 종목코드 있음: 실시간 가격 이력 × 수량 → 평가액 가중
      - 그 외 (원금·분배율만 있음): 일정 수익률 시뮬레이션
    """
    if not items:
        return pd.Series(dtype=float, name=account_name)

    weighted_series: list[tuple[pd.Series, float]] = []  # (수익률 시계열, 가중치)

    for item in items:
        nm   = str(item.get("종목명", "")).strip()
        code = str(item.get("종목코드", "")).strip()
        qty  = float(item.get("수량",       0) or 0)
        dps  = float(item.get("주당분배금", 0) or 0)
        amt  = float(item.get("원금",       0) or 0)
        rate = float(item.get("분배율(%)",  0) or 0)

        if not nm or nm.startswith("(") or nm in ("nan", ""):
            continue

        weight = qty * (dps * 12 / rate * 100 if rate > 0 else 0) if qty > 0 else amt
        if weight <= 0:
            weight = amt if amt > 0 else 1_000_000

        # 종목코드 있으면 실제 가격 이력 수집
        if code and code not in ("nan", "0", ""):
            ycode = code if "." in code else f"{code}.KS"
            price_df = _fetch_yf_history(ycode, days)
            if not price_df.empty and len(price_df) > 1:
                base  = price_df["Close"].iloc[0]
                ret_s = (price_df["Close"] / base - 1) * 100
                ret_s.name = nm
                weighted_series.append((ret_s, weight))
                continue

        # 종목코드 없음 또는 수집 실패 → 분배율 기반 선형 시뮬
        if rate > 0:
            n_days = days
            dates  = pd.date_range(
                end=datetime.now(), periods=n_days, freq="D"
            )
            daily_rate = rate / 100 / 30   # 월 분배율 → 일 수익률
            ret_arr    = pd.Series(
                [daily_rate * i * 100 for i in range(n_days)],
                index=dates, name=nm,
            )
            weighted_series.append((ret_arr, weight))

    if not weighted_series:
        return pd.Series(dtype=float, name=account_name)

    # ── 공통 날짜 인덱스로 정렬 후 가중평균 ──
    total_weight = sum(w for _, w in weighted_series)
    if total_weight <= 0:
        return pd.Series(dtype=float, name=account_name)

    # 가장 짧은 시계열 기준 날짜 결합
    all_dates = weighted_series[0][0].index
    for s, _ in weighted_series[1:]:
        all_dates = all_dates.union(s.index)

    combined = pd.DataFrame(index=all_dates)
    for i, (s, w) in enumerate(weighted_series):
        combined[f"_s{i}"] = s.reindex(all_dates).interpolate(method="time")
        combined[f"_w{i}"] = w / total_weight

    # 가중평균 계산
    result = sum(
        combined[f"_s{i}"] * combined[f"_w{i}"]
        for i in range(len(weighted_series))
    )
    result.name = account_name
    return result.dropna()


def _calc_portfolio_return(
    irp_items:  list[dict],
    isa_items:  list[dict],
    ps_items:   list[dict],
    gen_items:  list[dict],
    irp_total:  float,
    isa_total:  float,
    ps_total:   float,
    gen_total:  float,
    days:       int,
) -> dict[str, pd.Series]:
    """
    계좌별 수익률 시계열 + 통합 포트폴리오 수익률 반환.
    반환: {계좌명: pd.Series(수익률%, DatetimeIndex)}
    """
    results: dict[str, pd.Series] = {}

    account_map = [
        ("IRP",    irp_items,  irp_total),
        ("ISA",    isa_items,  isa_total),
        ("연금저축", ps_items,  ps_total),
        ("일반",   gen_items,  gen_total),
    ]

    for acc_name, items, total in account_map:
        if total > 0 and items:
            s = _calc_return_series(items, acc_name, days)
            if not s.empty:
                results[acc_name] = s

    # ── 전체 포트폴리오 가중평균 ──
    grand_total = irp_total + isa_total + ps_total + gen_total
    if grand_total > 0 and results:
        weights = {
            "IRP":    irp_total / grand_total,
            "ISA":    isa_total / grand_total,
            "연금저축": ps_total / grand_total,
            "일반":   gen_total / grand_total,
        }
        all_dates = results[list(results.keys())[0]].index
        for s in results.values():
            all_dates = all_dates.union(s.index)

        portfolio = pd.Series(0.0, index=all_dates)
        total_w   = 0.0
        for acc, s in results.items():
            w = weights.get(acc, 0)
            if w > 0:
                portfolio += s.reindex(all_dates).interpolate(method="time").fillna(0) * w
                total_w += w
        if total_w > 0:
            portfolio /= total_w
            results["포트폴리오 합계"] = portfolio.dropna()

    return results


# ════════════════════════════════════════════════════════
# 3. 벤치마크 수익률 수집
# ════════════════════════════════════════════════════════

def _fetch_benchmark_returns(days: int) -> dict[str, pd.Series]:
    """선택된 벤치마크들의 수익률(%) 시계열 반환"""
    result: dict[str, pd.Series] = {}
    for key, info in BENCHMARKS.items():
        df = _fetch_yf_history(info["ticker"], days)
        if df.empty or len(df) < 2:
            continue
        base = df["Close"].iloc[0]
        ret  = (df["Close"] / base - 1) * 100
        ret.name = info["label"]
        result[key] = ret
    return result


# ════════════════════════════════════════════════════════
# 4. 성과 지표 계산
# ════════════════════════════════════════════════════════

def _calc_performance_metrics(
    ret_series: pd.Series,
    label: str,
) -> dict:
    """
    단일 수익률 시계열 → 성과 지표 dict 반환.
    지표: 기간수익률, 연환산수익률, MDD, 샤프지수, 변동성
    """
    s = ret_series.dropna()
    if len(s) < 5:
        return {"label": label, "기간수익률": None}

    total_ret = float(s.iloc[-1])               # 기간 수익률(%)
    n_days    = (s.index[-1] - s.index[0]).days
    n_years   = max(n_days / 365, 0.01)
    ann_ret   = ((1 + total_ret / 100) ** (1 / n_years) - 1) * 100

    # 일별 변화량 (수익률 시계열 → 일 수익률)
    daily_ret = s.diff().dropna()
    vol       = float(daily_ret.std() * (252 ** 0.5)) if len(daily_ret) > 1 else 0

    # MDD (최대낙폭)
    cum  = (1 + s / 100)
    peak = cum.cummax()
    dd   = (cum - peak) / peak * 100
    mdd  = float(dd.min())

    # 샤프 (무위험이자율 3.5% 가정)
    rf_daily = 3.5 / 252
    sharpe   = (
        (daily_ret.mean() - rf_daily) / daily_ret.std() * (252 ** 0.5)
        if daily_ret.std() > 0 else 0
    )

    return {
        "label":    label,
        "기간수익률":  round(total_ret, 2),
        "연환산수익률": round(ann_ret, 2),
        "MDD(%)":    round(mdd, 2),
        "변동성(연환산%)": round(vol, 2),
        "샤프지수":   round(sharpe, 2),
    }


# ════════════════════════════════════════════════════════
# 5. 메인 렌더링 함수
# ════════════════════════════════════════════════════════

def render_benchmark_tab(
    irp_items:  list[dict],
    isa_items:  list[dict],
    ps_items:   list[dict],
    gen_items:  list[dict],
    irp_total:  float,
    isa_total:  float,
    ps_total:   float,
    gen_total:  float,
) -> None:
    """
    📐 수익률 벤치마크 탭 전체 렌더링.
    pension_app.py 에서 with _main_tab5: 블록 안에서 호출합니다.
    """
    import plotly.graph_objects as go

    st.markdown("#### 📐 연금 포트폴리오 수익률 벤치마크 비교")

    # ── 기간 선택 ──────────────────────────────────────
    col_period, col_bench, col_refresh = st.columns([3, 5, 1])

    with col_period:
        period_label = st.selectbox(
            "분석 기간",
            list(PERIOD_OPTIONS.keys()),
            index=2,   # 기본값: 6개월
            key="bm_period",
            label_visibility="collapsed",
        )
    days = PERIOD_OPTIONS[period_label]

    with col_bench:
        selected_benchmarks = st.multiselect(
            "벤치마크 선택",
            list(BENCHMARKS.keys()),
            default=["KOSPI", "S&P500"],
            key="bm_selected",
            label_visibility="collapsed",
        )

    with col_refresh:
        if st.button("🔄", key="bm_refresh", help="데이터 새로고침",
                     use_container_width=True):
            _fetch_yf_history.clear()
            st.rerun()

    st.caption(f"분석 기간: {period_label}  ·  기준일 대비 누적 수익률(%)")

    # ── 데이터 수집 ──────────────────────────────────────
    with st.spinner("수익률 데이터 수집 중..."):
        portfolio_rets = _calc_portfolio_return(
            irp_items, isa_items, ps_items, gen_items,
            irp_total, isa_total, ps_total, gen_total, days,
        )
        benchmark_rets = _fetch_benchmark_returns(days)

    if not portfolio_rets and not benchmark_rets:
        st.info(
            "수익률 데이터를 수집할 수 없습니다.\n\n"
            "연금현황 시트에 **종목코드** 컬럼을 추가하면 실제 가격 이력이 반영됩니다.\n"
            "종목코드 없는 경우 분배율 기반 선형 시뮬레이션으로 표시됩니다."
        )
        return

    # ════════════════════════════════════════════════════
    # 섹션 1: 수익률 비교 라인 차트
    # ════════════════════════════════════════════════════
    fig = go.Figure()

    # 포트폴리오 시계열 추가
    for acc_name, ret_s in portfolio_rets.items():
        if ret_s.empty:
            continue
        color = ACCOUNT_COLORS.get(acc_name, "#aaaaaa")
        width = 2.5 if acc_name == "포트폴리오 합계" else 1.5
        dash  = "solid" if acc_name == "포트폴리오 합계" else "dot"
        fig.add_trace(go.Scatter(
            x=ret_s.index, y=ret_s.values,
            name=acc_name, mode="lines",
            line=dict(color=color, width=width, dash=dash),
            hovertemplate=f"{acc_name}: %{{y:+.2f}}%<extra></extra>",
        ))

    # 벤치마크 시계열 추가
    for bm_key in selected_benchmarks:
        if bm_key not in benchmark_rets:
            continue
        info  = BENCHMARKS[bm_key]
        ret_s = benchmark_rets[bm_key]
        fig.add_trace(go.Scatter(
            x=ret_s.index, y=ret_s.values,
            name=info["label"], mode="lines",
            line=dict(color=info["color"], width=1.5, dash="dashdot"),
            hovertemplate=f"{info['label']}: %{{y:+.2f}}%<extra></extra>",
        ))

    # 기준선 (0%)
    fig.add_hline(
        y=0, line_dash="solid",
        line_color="rgba(255,255,255,0.15)", line_width=1,
    )

    fig.update_layout(
        height=400,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(255,255,255,0.02)",
        font_color="white",
        legend=dict(
            orientation="h", yanchor="bottom", y=-0.25,
            xanchor="center", x=0.5, font=dict(size=11),
        ),
        margin=dict(t=20, b=90, l=50, r=20),
        yaxis=dict(
            title="누적 수익률 (%)",
            tickformat="+.1f",
            gridcolor="rgba(255,255,255,0.05)",
            zeroline=False,
        ),
        xaxis=dict(
            gridcolor="rgba(255,255,255,0.05)",
            showspikes=True,
            spikecolor="rgba(255,255,255,0.3)",
        ),
        hovermode="x unified",
    )
    st.plotly_chart(fig, use_container_width=True)

    # ════════════════════════════════════════════════════
    # 섹션 2: 성과 지표 테이블
    # ════════════════════════════════════════════════════
    st.markdown("##### 📊 성과 지표 비교")

    metrics_rows = []

    # 포트폴리오 지표
    for acc_name, ret_s in portfolio_rets.items():
        m = _calc_performance_metrics(ret_s, acc_name)
        if m.get("기간수익률") is not None:
            m["구분"] = "포트폴리오"
            metrics_rows.append(m)

    # 벤치마크 지표
    for bm_key in selected_benchmarks:
        if bm_key not in benchmark_rets:
            continue
        info = BENCHMARKS[bm_key]
        m    = _calc_performance_metrics(benchmark_rets[bm_key], info["label"])
        if m.get("기간수익률") is not None:
            m["구분"] = "벤치마크"
            metrics_rows.append(m)

    if metrics_rows:
        metrics_df = pd.DataFrame(metrics_rows).set_index("label")

        # 컬럼 순서
        display_cols = [c for c in ["구분","기간수익률","연환산수익률",
                                     "MDD(%)","변동성(연환산%)","샤프지수"]
                        if c in metrics_df.columns]
        metrics_df = metrics_df[display_cols]

        # 색상 스타일
        def _style_metrics(df):
            styles = pd.DataFrame("", index=df.index, columns=df.columns)
            for col in ["기간수익률", "연환산수익률"]:
                if col in df.columns:
                    styles[col] = df[col].apply(
                        lambda v: "color:#FF4B4B; font-weight:600" if isinstance(v,(int,float)) and v > 0
                        else ("color:#87CEEB; font-weight:600"  if isinstance(v,(int,float)) and v < 0
                              else "")
                    )
            if "MDD(%)" in df.columns:
                styles["MDD(%)"] = df["MDD(%)"].apply(
                    lambda v: "color:#FF4B4B" if isinstance(v,(int,float)) and v < -10
                    else ("color:#FFD700" if isinstance(v,(int,float)) and v < -5 else "")
                )
            if "샤프지수" in df.columns:
                styles["샤프지수"] = df["샤프지수"].apply(
                    lambda v: "color:#7dffb0; font-weight:600" if isinstance(v,(int,float)) and v > 1.0
                    else ("color:#FFD700" if isinstance(v,(int,float)) and v > 0.5 else "")
                )
            return styles

        st.dataframe(
            metrics_df.style.apply(_style_metrics, axis=None).format({
                "기간수익률":    "{:+.2f}%",
                "연환산수익률":  "{:+.2f}%",
                "MDD(%)":        "{:.2f}%",
                "변동성(연환산%)": "{:.2f}%",
                "샤프지수":      "{:.2f}",
            }, na_rep="-"),
            use_container_width=True,
        )

    # ════════════════════════════════════════════════════
    # 섹션 3: 초과 수익률 (포트폴리오 - 벤치마크)
    # ════════════════════════════════════════════════════
    portfolio_total = portfolio_rets.get("포트폴리오 합계")
    if portfolio_total is not None and not portfolio_total.empty and selected_benchmarks:
        st.markdown("##### 📈 포트폴리오 초과 수익률")
        st.caption("포트폴리오 합계 수익률 − 각 벤치마크 수익률")

        fig_excess = go.Figure()
        for bm_key in selected_benchmarks:
            if bm_key not in benchmark_rets:
                continue
            info   = BENCHMARKS[bm_key]
            bm_ret = benchmark_rets[bm_key]

            # 공통 날짜 교집합
            common = portfolio_total.index.intersection(bm_ret.index)
            if len(common) < 5:
                continue
            excess = portfolio_total.reindex(common) - bm_ret.reindex(common)

            # 색상: 초과 수익 양수=초록, 음수=빨강
            pos_mask = excess >= 0
            fig_excess.add_trace(go.Bar(
                x=excess[pos_mask].index,
                y=excess[pos_mask].values,
                name=f"vs {info['label']} (초과)",
                marker_color="rgba(125,255,176,0.6)",
                showlegend=True,
            ))
            fig_excess.add_trace(go.Bar(
                x=excess[~pos_mask].index,
                y=excess[~pos_mask].values,
                name=f"vs {info['label']} (미달)",
                marker_color="rgba(255,75,75,0.5)",
                showlegend=True,
            ))
            # 누적 초과 수익 라인
            fig_excess.add_trace(go.Scatter(
                x=excess.index, y=excess.values,
                name=f"vs {info['label']} 누적",
                mode="lines",
                line=dict(color=info["color"], width=1.5),
                showlegend=True,
            ))

        fig_excess.add_hline(
            y=0, line_dash="solid",
            line_color="rgba(255,255,255,0.2)", line_width=1,
        )
        fig_excess.update_layout(
            height=300, barmode="overlay",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(255,255,255,0.02)",
            font_color="white",
            legend=dict(orientation="h", y=-0.3, xanchor="center", x=0.5),
            margin=dict(t=10, b=80, l=50, r=20),
            yaxis=dict(title="초과 수익률 (%p)", tickformat="+.1f",
                       gridcolor="rgba(255,255,255,0.05)"),
            xaxis=dict(gridcolor="rgba(255,255,255,0.05)"),
            hovermode="x unified",
        )
        st.plotly_chart(fig_excess, use_container_width=True)

    # ════════════════════════════════════════════════════
    # 섹션 4: 계좌별 수익률 최신 요약 카드
    # ════════════════════════════════════════════════════
    st.divider()
    st.markdown("##### 💳 계좌별 수익률 현황")

    account_order = ["IRP", "ISA", "연금저축", "일반"]
    card_data = {
        acc: portfolio_rets[acc]
        for acc in account_order
        if acc in portfolio_rets and not portfolio_rets[acc].empty
    }

    if card_data:
        cols = st.columns(len(card_data))
        for i, (acc, ret_s) in enumerate(card_data.items()):
            latest_ret = float(ret_s.iloc[-1])
            color = "#7dffb0" if latest_ret >= 0 else "#FF4B4B"
            # 벤치마크 대비 초과 수익 계산 (KOSPI 기준)
            excess_vs_kospi = None
            if "KOSPI" in benchmark_rets and not benchmark_rets["KOSPI"].empty:
                common = ret_s.index.intersection(benchmark_rets["KOSPI"].index)
                if len(common) > 0:
                    port_last = float(ret_s.reindex(common).iloc[-1])
                    kospi_last = float(benchmark_rets["KOSPI"].reindex(common).iloc[-1])
                    excess_vs_kospi = port_last - kospi_last

            with cols[i]:
                with st.container(border=True):
                    st.markdown(
                        f"<div style='font-size:0.8rem; color:rgba(255,255,255,0.5); "
                        f"margin-bottom:4px;'>{acc}</div>"
                        f"<div style='font-size:1.6rem; font-weight:700; "
                        f"color:{color};'>{latest_ret:+.2f}%</div>",
                        unsafe_allow_html=True,
                    )
                    if excess_vs_kospi is not None:
                        exc_color = "#7dffb0" if excess_vs_kospi >= 0 else "#FF4B4B"
                        st.markdown(
                            f"<div style='font-size:0.78rem; color:{exc_color};'>"
                            f"vs KOSPI {excess_vs_kospi:+.2f}%p</div>",
                            unsafe_allow_html=True,
                        )
                    st.caption(f"{period_label} 기준")

    # ════════════════════════════════════════════════════
    # 섹션 5: 데이터 안내
    # ════════════════════════════════════════════════════
    with st.expander("ℹ️ 수익률 계산 방식 안내", expanded=False):
        st.markdown("""
**포트폴리오 수익률 계산 방식**

| 조건 | 계산 방식 |
|------|-----------|
| 종목코드 + 수량 있음 | Yahoo Finance 실제 가격 이력 기반 |
| 종목코드 없음 / 수집 실패 | 월 분배율 기반 선형 시뮬레이션 |
| 계좌별 통합 | 원금 기준 가중평균 수익률 |

**성과 지표 정의**

- **기간수익률**: 분석 기간 시작일 대비 현재 누적 수익률
- **연환산수익률**: 기간 수익률을 연율화 (복리 환산)
- **MDD**: 최대 낙폭 (Peak 대비 최대 하락폭)
- **변동성**: 일 수익률 표준편차 × √252 (연환산)
- **샤프지수**: (수익률 − 무위험이자율 3.5%) ÷ 변동성

**벤치마크 소스**: Yahoo Finance API (15분 딜레이)

> 종목코드를 연금현황 시트에 입력하면 정확도가 높아집니다.
> 예: SOL팔란티어커버드콜OTM → 종목코드 `476560`
""")
