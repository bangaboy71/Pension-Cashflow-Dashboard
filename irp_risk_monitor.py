"""
irp_risk_monitor.py
───────────────────
IRP 위험자산 비중 실시간 모니터링 모듈

핵심 원칙:
  위험자산 70% 한도 판단 기준 = '현재 평가금액' (매입금액 아님)
  근거: 근로자퇴직급여보장법 퇴직연금감독규정

평가금액 계산 우선순위:
  1. price_map 에 현재가 있으면 → 현재가 × 수량  (실시간)
  2. 없으면 → 원금 컬럼 (매입금액 폴백)

pension_app.py 호출:
  from irp_risk_monitor import render_irp_risk_monitor
  render_irp_risk_monitor(_pension_irp_items, irp_total, _irp_price_map)
"""

import streamlit as st
import pandas as pd

# ══════════════════════════════════════════════════════════
# 1. 자산 분류 규칙
# ══════════════════════════════════════════════════════════

SAFE_PRIORITY = ["채권혼합", "TDF", "TIF"]

SAFE_KW = [
    "CD금리", "KOFR", "단기채", "국고채", "국채", "통안채",
    "머니마켓", "MMF", "예수금", "현금", "예금", "ELB",
    "삼성신종종류형", "CMA",
]

RISK_KW = [
    "나스닥", "S&P", "SP500", "커버드콜", "COVERED", "CALL",
    "배당", "리츠", "REIT", "테크", "반도체", "AI", "인공지능",
    "MSCI", "코스피", "코스닥", "200타겟", "팔란티어",
]

MANUAL: dict = {}
RISK_LIMIT = 0.70


def _classify(name: str) -> str:
    if not name or str(name).strip() in ("", "nan"):
        return "안전"
    n = str(name)
    if n in MANUAL:
        return MANUAL[n]
    for kw in SAFE_PRIORITY:
        if kw in n:
            return "안전"
    for kw in SAFE_KW:
        if kw in n:
            return "안전"
    for kw in RISK_KW:
        if kw in n:
            return "위험"
    return "위험"


# ══════════════════════════════════════════════════════════
# 2. 집계 — 평가금액 기준 (현재가 × 수량 우선)
# ══════════════════════════════════════════════════════════

def _calc_summary(irp_items: list, irp_total: float, price_map: dict) -> dict | None:
    """
    평가금액 = 현재가 × 수량  (price_map에 현재가 있으면)
             = 원금            (없으면 매입금액 폴백)
    """
    if not irp_items:
        return None

    rows = []
    rt_count = fallback_count = 0

    for it in irp_items:
        nm  = str(it.get("종목명", "") or "").strip()
        qty = float(it.get("수량", 0) or 0)
        amt = float(it.get("원금", 0) or 0)
        if not nm or nm in ("nan", ""):
            continue

        curr_price = price_map.get(nm, 0)
        if curr_price > 0 and qty > 0:
            eval_amt = curr_price * qty
            rt_count += 1
        else:
            eval_amt = amt
            fallback_count += 1

        rows.append({
            "종목명":   nm,
            "수량":     qty,
            "현재가":   curr_price,
            "매입금액": amt,
            "평가금액": eval_amt,
            "자산구분": _classify(nm),
        })

    if not rows:
        return None

    df = pd.DataFrame(rows)
    total = df["평가금액"].sum()

    # 예수금 등 items에 없는 잔액(안전자산)을 irp_total로 흡수
    buy_total = df["매입금액"].sum()
    unaccounted = max(0.0, irp_total - buy_total)
    if unaccounted > 0 and total < irp_total:
        total = df["평가금액"].sum() + unaccounted

    if total == 0:
        return None

    risk_amt = df[df["자산구분"] == "위험"]["평가금액"].sum()
    safe_amt = total - risk_amt
    risk_pct = risk_amt / total
    safe_pct = safe_amt / total
    headroom_amount = total * RISK_LIMIT - risk_amt
    headroom_pct    = RISK_LIMIT - risk_pct

    return {
        "total": total, "risk_amt": risk_amt, "safe_amt": safe_amt,
        "risk_pct": risk_pct, "safe_pct": safe_pct,
        "headroom_amount": headroom_amount, "headroom_pct": headroom_pct,
        "can_buy": risk_pct < RISK_LIMIT,
        "df": df.sort_values("평가금액", ascending=False),
        "rt_count": rt_count, "fallback_count": fallback_count,
    }


# ══════════════════════════════════════════════════════════
# 3. Streamlit UI
# ══════════════════════════════════════════════════════════

def render_irp_risk_monitor(
    irp_items: list,
    irp_total: float = 0.0,
    price_map: dict | None = None,
) -> None:
    """
    pension_app.py _main_tab1 상단 호출:
        render_irp_risk_monitor(_pension_irp_items, irp_total, _irp_price_map)
    """
    if price_map is None:
        price_map = {}

    st.markdown(
        "<h4 style='margin-bottom:0.3rem;'>🔔 IRP 위험자산 비중 모니터</h4>"
        "<p style='font-size:0.8rem;color:rgba(255,255,255,0.45);margin-top:0;'>"
        "판단 기준: <b>현재 평가금액</b> (현재가 × 수량) — "
        "근로자퇴직급여보장법 퇴직연금감독규정</p>",
        unsafe_allow_html=True,
    )

    info = _calc_summary(irp_items, irp_total, price_map)

    if info is None:
        st.info("IRP 종목 데이터가 없습니다. 연금현황 시트를 확인하세요.")
        return

    risk_pct = info["risk_pct"]
    safe_pct = info["safe_pct"]
    total    = info["total"]
    risk_amt = info["risk_amt"]
    safe_amt = info["safe_amt"]
    headroom_amount = info["headroom_amount"]
    headroom_pct    = info["headroom_pct"]
    can_buy  = info["can_buy"]
    df       = info["df"]
    rt_count = info["rt_count"]
    fallback_count = info["fallback_count"]

    # ── 실시간 연동 상태 ──────────────────────────────────
    if rt_count > 0 and fallback_count == 0:
        st.caption(f"📡 전 종목 실시간 현재가 반영 ({rt_count}종목) — 평가금액 기준")
    elif rt_count > 0:
        st.caption(
            f"📡 실시간 반영: {rt_count}종목 / "
            f"{fallback_count}종목은 매입금액 사용 (현재가 조회 실패)"
        )
    else:
        st.caption("⚠️ 현재가 조회 실패 — 매입금액 기준으로 표시 (실제보다 부정확할 수 있음)")

    # ── 신호등 ─────────────────────────────────────────────
    if risk_pct < 0.68:
        emoji, label, msg, alert_fn = "🟢", "정상", "위험자산 추가 매수 가능합니다.", st.success
    elif risk_pct < RISK_LIMIT:
        emoji, label, msg, alert_fn = "🟡", "주의", f"한도까지 {headroom_pct*100:.1f}%p 남았습니다.", st.warning
    else:
        emoji, label, msg, alert_fn = "🔴", "한도 초과", "추가 매수 불가 — 리밸런싱을 검토하세요.", st.error

    # ── 요약 카드 3열 ──────────────────────────────────────
    c1, c2, c3 = st.columns(3)
    c1.metric("IRP 총 평가금액", f"{total:,.0f}원")
    c2.metric(
        f"위험자산 비중  {emoji}",
        f"{risk_pct*100:.1f}%",
        delta=(
            f"+{headroom_amount:,.0f}원 여력"
            if headroom_amount >= 0
            else f"{headroom_amount:,.0f}원 초과"
        ),
        delta_color="normal" if headroom_amount >= 0 else "inverse",
    )
    c3.metric("안전자산 비중", f"{safe_pct*100:.1f}%",
              help="법정 최소 30% 이상 유지 필요")

    # ── 게이지 바 ──────────────────────────────────────────
    st.markdown(
        f"**위험자산** &nbsp;`{risk_pct*100:.1f}%` &nbsp;/&nbsp; "
        f"한도 `{RISK_LIMIT*100:.0f}%` &nbsp;&nbsp;"
        f"<span style='font-size:0.8rem;color:rgba(255,255,255,0.5);'>"
        f"(위험 {risk_amt:,.0f}원 &nbsp;|&nbsp; 안전 {safe_amt:,.0f}원)</span>",
        unsafe_allow_html=True,
    )
    st.progress(min(risk_pct / RISK_LIMIT, 1.0))

    # ── 상태 메시지 ────────────────────────────────────────
    if headroom_amount >= 0:
        alert_fn(
            f"{emoji} **{label}** — {msg}  \n"
            f"위험자산 추가 매수 가능 여력: **{headroom_amount:,.0f}원** "
            f"({headroom_pct*100:.1f}%p)"
        )
    else:
        alert_fn(
            f"{emoji} **{label}** — {msg}  \n"
            f"초과금액: **{abs(headroom_amount):,.0f}원** "
            f"({abs(headroom_pct)*100:.1f}%p)"
        )

    # ── 종목별 상세 ────────────────────────────────────────
    with st.expander("📋 종목별 자산구분 상세", expanded=False):
        view = df.copy()
        view["자산구분"] = view["자산구분"].map({"위험": "⚠️ 위험", "안전": "✅ 안전"})
        view["비중"]     = (df["평가금액"] / total * 100).map("{:.1f}%".format)
        view["현재가"]   = df["현재가"].apply(
            lambda x: f"{int(x):,}원" if x > 0 else "—(매입가 사용)"
        )
        view["평가금액"] = df["평가금액"].map("{:,.0f}원".format)
        view["매입금액"] = df["매입금액"].map("{:,.0f}원".format)

        st.dataframe(
            view[["종목명", "자산구분", "현재가", "평가금액", "매입금액", "비중"]]
            .reset_index(drop=True),
            use_container_width=True,
            hide_index=True,
        )
        st.caption(
            "※ **분류 근거** — 안전: 채권혼합형·TDF·CD금리·예수금·MMF  \n"
            "　　　　　　　　 위험: 주식형 커버드콜·나스닥·MSCI·배당주형 ETF  \n"
            "※ 'SOL 팔란티어커버드콜OTM채권혼합'은 채권혼합 키워드로 안전자산 분류  \n"
            "※ 현재가 조회 실패 종목은 매입금액으로 대체"
        )

    # ── 리밸런싱 가이드 (초과 시만) ────────────────────────
    if not can_buy:
        st.markdown("---")
        st.markdown("<h5 style='margin-bottom:0.3rem;'>🔄 리밸런싱 가이드</h5>",
                    unsafe_allow_html=True)
        excess  = abs(headroom_amount)
        risk_df = df[df["자산구분"] == "위험"].copy()

        if not risk_df.empty:
            risk_sum = risk_df["평가금액"].sum()
            risk_df["권장 매도금액"] = (
                risk_df["평가금액"] / risk_sum * excess
            ).map("{:,.0f}원".format)
            risk_df["평가금액"] = risk_df["평가금액"].map("{:,.0f}원".format)

            st.info(
                f"70% 한도 달성을 위해 위험자산 **{excess:,.0f}원**을 "
                "안전자산으로 전환하세요.  \n"
                "권장 매도금액은 위험자산 보유 비율로 안분한 참고값입니다."
            )
            st.dataframe(
                risk_df[["종목명", "평가금액", "권장 매도금액"]].reset_index(drop=True),
                use_container_width=True,
                hide_index=True,
            )
