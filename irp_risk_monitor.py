"""
irp_risk_monitor.py
───────────────────
IRP 위험자산 비중 실시간 모니터링 모듈

근거: 근로자퇴직급여보장법 퇴직연금감독규정
  - 위험자산 투자한도: 적립금의 70% 이하  ← '평가금액' 기준 (매입금액 X)
  - 한도 초과 시: 위험자산 추가 매수 불가 (보유 유지는 가능)

pension_app.py 연동:
  _pension_irp_items  : parse_pension_sheet_new() 반환 리스트
  irp_total           : IRP 계좌 원금 합산 (float)

호출 예시 (pension_app.py _main_tab1 상단):
  from irp_risk_monitor import render_irp_risk_monitor
  render_irp_risk_monitor(_pension_irp_items, irp_total)
"""

import streamlit as st
import pandas as pd

# ══════════════════════════════════════════════════════════
# 1. 자산 분류 규칙
# ══════════════════════════════════════════════════════════

# ① 최우선 안전자산 키워드 (커버드콜이 포함돼도 이게 있으면 안전)
SAFE_PRIORITY = [
    "채권혼합",     # SOL 팔란티어커버드콜OTM채권혼합 → 주식 50% 미만 → 안전
    "TDF", "TIF",  # 적격 TDF → 규정상 100% 안전자산
]

# ② 일반 안전자산 키워드
SAFE_KW = [
    "CD금리", "KOFR",
    "단기채", "국고채", "국채", "통안채",
    "머니마켓", "MMF",
    "예수금", "현금", "예금", "ELB",
    "삼성신종종류형", "CMA",
]

# ③ 위험자산 키워드
RISK_KW = [
    "나스닥", "S&P", "SP500",
    "커버드콜", "COVERED", "CALL",
    "배당",
    "리츠", "REIT",
    "테크", "반도체", "AI", "인공지능",
    "MSCI", "코스피", "코스닥", "200타겟",
    "팔란티어",
]

# ④ 수동 override (자동분류가 애매한 종목만 여기에 명시)
MANUAL: dict[str, str] = {}

RISK_LIMIT = 0.70   # 법정 위험자산 한도


def _classify(name: str) -> str:
    """종목명 → '위험' / '안전' 자동 분류"""
    if not name or str(name).strip() in ("", "nan"):
        return "안전"    # 현금·예수금 계열

    n = str(name)

    if n in MANUAL:
        return MANUAL[n]

    for kw in SAFE_PRIORITY:       # 채권혼합·TDF 최우선
        if kw in n:
            return "안전"

    for kw in SAFE_KW:
        if kw in n:
            return "안전"

    for kw in RISK_KW:
        if kw in n:
            return "위험"

    return "위험"                  # 불명확 → 보수적으로 위험


# ══════════════════════════════════════════════════════════
# 2. 집계
# ══════════════════════════════════════════════════════════

def _calc_summary(irp_items: list, irp_total: float) -> dict | None:
    """
    _pension_irp_items 리스트 → 위험/안전 비중 집계

    irp_items 각 원소 키:
        종목명, 수량, 주당분배금, 원금, 분배율(%), 종목코드, 원천

    평가금액 = 원금 컬럼 사용
      (시트에 '현재가' 컬럼이 있으면 수량×현재가로 교체 가능 — 하단 주석 참조)
    """
    if not irp_items:
        return None

    rows = []
    for it in irp_items:
        nm  = str(it.get("종목명", "") or "").strip()
        amt = float(it.get("원금", 0) or 0)
        if not nm or nm in ("nan", ""):
            continue
        rows.append({"종목명": nm, "평가금액": amt, "자산구분": _classify(nm)})

    if not rows:
        return None

    df      = pd.DataFrame(rows)
    total   = df["평가금액"].sum()

    # irp_total 과 비교해 더 큰 값 사용 (예수금 등 미분류 잔액 흡수)
    total   = max(total, irp_total)

    if total == 0:
        return None

    risk_amt = df[df["자산구분"] == "위험"]["평가금액"].sum()
    safe_amt = df[df["자산구분"] == "안전"]["평가금액"].sum()

    # 미분류 잔액(예수금 등 items에 없는 금액)은 안전자산으로 처리
    unaccounted = total - df["평가금액"].sum()
    if unaccounted > 0:
        safe_amt += unaccounted

    risk_pct        = risk_amt / total
    safe_pct        = safe_amt / total
    headroom_amount = total * RISK_LIMIT - risk_amt   # 양수=여력, 음수=초과
    headroom_pct    = RISK_LIMIT - risk_pct

    return {
        "total":            total,
        "risk_amt":         risk_amt,
        "safe_amt":         safe_amt,
        "risk_pct":         risk_pct,
        "safe_pct":         safe_pct,
        "headroom_amount":  headroom_amount,
        "headroom_pct":     headroom_pct,
        "can_buy":          risk_pct < RISK_LIMIT,
        "df":               df.sort_values("평가금액", ascending=False),
    }


# ══════════════════════════════════════════════════════════
# 3. Streamlit UI
# ══════════════════════════════════════════════════════════

def render_irp_risk_monitor(irp_items: list, irp_total: float = 0.0) -> None:
    """
    IRP 위험자산 비중 모니터 위젯 렌더링.

    pension_app.py _main_tab1 상단 호출:
        render_irp_risk_monitor(_pension_irp_items, irp_total)
    """
    st.markdown(
        "<h4 style='margin-bottom:0.3rem;'>🔔 IRP 위험자산 비중 모니터</h4>"
        "<p style='font-size:0.8rem;color:rgba(255,255,255,0.45);margin-top:0;'>"
        "판단 기준: <b>현재 평가금액</b> (매입금액 아님) — 근로자퇴직급여보장법 퇴직연금감독규정</p>",
        unsafe_allow_html=True,
    )

    info = _calc_summary(irp_items, irp_total)

    if info is None:
        st.info("IRP 종목 데이터가 없습니다. 연금현황 시트를 확인하세요.")
        return

    risk_pct        = info["risk_pct"]
    safe_pct        = info["safe_pct"]
    total           = info["total"]
    risk_amt        = info["risk_amt"]
    safe_amt        = info["safe_amt"]
    headroom_amount = info["headroom_amount"]
    headroom_pct    = info["headroom_pct"]
    can_buy         = info["can_buy"]
    df              = info["df"]

    # ── 신호등 상태 ────────────────────────────────────────
    if risk_pct < 0.68:
        emoji, label, msg = "🟢", "정상", "위험자산 추가 매수 가능합니다."
        alert_fn = st.success
    elif risk_pct < RISK_LIMIT:
        emoji, label, msg = "🟡", "주의", f"한도까지 {headroom_pct*100:.1f}%p 남았습니다."
        alert_fn = st.warning
    else:
        emoji, label, msg = "🔴", "한도 초과", "추가 매수 불가 — 리밸런싱을 검토하세요."
        alert_fn = st.error

    # ── 요약 카드 3열 ──────────────────────────────────────
    c1, c2, c3 = st.columns(3)
    c1.metric("IRP 총 원금", f"{total:,.0f}원")
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
    c3.metric(
        "안전자산 비중",
        f"{safe_pct*100:.1f}%",
        help="법정 최소 30% 이상 유지 필요",
    )

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
        view["비중"]    = (df["평가금액"] / total * 100).map("{:.1f}%".format)
        view["평가금액"] = df["평가금액"].map("{:,.0f}원".format)
        st.dataframe(
            view[["종목명", "자산구분", "평가금액", "비중"]].reset_index(drop=True),
            use_container_width=True,
            hide_index=True,
        )
        st.caption(
            "※ 분류 근거 — **안전**: 채권혼합형(주식 50% 미만)·TDF·CD금리형·예수금·MMF  \n"
            "　　　　　 **위험**: 주식형 커버드콜·나스닥·MSCI·배당주형 ETF  \n"
            "※ 'SOL 팔란티어커버드콜OTM채권혼합'은 '채권혼합' 키워드로 안전자산 분류  \n"
            "※ 평가금액 = 원금(매수가×수량) 기준. 연금현황 시트에 현재가 컬럼 추가 시 실시간 연동 가능"
        )

    # ── 리밸런싱 가이드 (초과 시만 표시) ──────────────────
    if not can_buy:
        st.markdown("---")
        st.markdown(
            "<h5 style='margin-bottom:0.3rem;'>🔄 리밸런싱 가이드</h5>",
            unsafe_allow_html=True,
        )
        excess = abs(headroom_amount)
        risk_df = df[df["자산구분"] == "위험"].copy()

        if not risk_df.empty:
            risk_sum = risk_df["평가금액"].sum()
            risk_df["권장 매도금액"] = (
                risk_df["평가금액"] / risk_sum * excess
            ).map("{:,.0f}원".format)
            risk_df["평가금액"] = risk_df["평가금액"].map("{:,.0f}원".format)

            st.info(
                f"70% 한도 달성을 위해 위험자산 **{excess:,.0f}원**을 안전자산으로 전환하세요.  \n"
                "아래 권장 매도금액은 현재 위험자산 보유 비율로 안분한 참고값입니다."
            )
            st.dataframe(
                risk_df[["종목명", "평가금액", "권장 매도금액"]].reset_index(drop=True),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info(f"위험자산 {excess:,.0f}원 상당을 안전자산으로 전환하세요.")


# ══════════════════════════════════════════════════════════
# 현재가 컬럼 연동 업그레이드 방법 (선택사항)
# ══════════════════════════════════════════════════════════
# 연금현황 시트에 '현재가' 컬럼을 추가하면 평가금액을 실시간화 할 수 있습니다.
#
# parse_pension_sheet_new()의 _get_items() 함수에서
# items.append() 시 "현재가" 키를 추가:
#
#   price = float(row.get("현재가", 0) or 0)
#   items.append({
#       ...
#       "현재가": price,
#   })
#
# 그리고 이 파일의 _calc_summary()에서 평가금액 계산을 교체:
#
#   # 기존
#   amt = float(it.get("원금", 0) or 0)
#
#   # 현재가 있으면 수량×현재가, 없으면 원금
#   price = float(it.get("현재가", 0) or 0)
#   qty   = float(it.get("수량",   0) or 0)
#   amt   = price * qty if price > 0 and qty > 0 else float(it.get("원금", 0) or 0)
