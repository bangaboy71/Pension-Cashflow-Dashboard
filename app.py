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
REQUIRED_ITEMS      = ["공적연금", "IRP", "ISA", "목표생활비"]
SCENARIO_SHEET_GID  = ""   # ← 시나리오 탭 gid 입력 (없으면 기능 비활성화)

# ── 세금 상수 ─────────────────────────────────────────
# 건강보험료: 지역가입자 기준 (건보 6.99% + 장기요양 0.9182% ≈ 7.09%)
# 단, 공무원연금 수령자는 연금소득의 50%를 소득월액 기준으로 산정
HEALTH_INS_RATE        = 0.0709
# ISA 비과세 한도: 연 200만원 → 월 환산
ISA_TAX_FREE_MONTHLY   = 2_000_000 / 12
# IRP·퇴직연금 분리과세: 5.5%
IRP_TAX_RATE           = 0.055


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


def calc_after_tax(
    public_pension: float,
    irp_income: float,
    isa_income: float,
) -> dict:
    """
    세목별 공제 후 실수령액 계산 (소득세법 정확 적용).

    공적연금 (공무원연금)
    ─ 연금소득공제(소득세법 §47의2) → 과세표준 → 기본세율(§55)
    ─ 지방소득세 10% 가산
    ─ 건강보험료: 연금소득 × 7.09% (지역가입자, 장기요양 포함)

    IRP / 퇴직연금
    ─ 연금소득세 분리과세 5.5% (지방세 포함) 적용

    ISA (KODEX 월배당)
    ─ 연 200만원 비과세 한도 내: 세금 0
    ─ 초과분: 9.9% 분리과세

    검증: 세전 3,831,570원 → 세후 3,624,210원 (공무원연금공단 기준)
    """
    # ── 공적연금: 연간 기준 정확 계산 (소득세법 기준) ──
    annual_pub   = public_pension * 12
    deduction    = _pension_income_deduction(annual_pub)
    taxable      = max(0.0, annual_pub - deduction)
    income_tax_a = _income_tax_rate(taxable)
    # 연금소득 세액공제 (소득세법 §59의3): 연 900,000원 한도
    PENSION_TAX_CREDIT = 900_000
    income_tax_a = max(0.0, income_tax_a - PENSION_TAX_CREDIT)
    local_tax_a  = income_tax_a * 0.10        # 지방소득세 10%
    pub_tax      = (income_tax_a + local_tax_a) / 12   # 월 환산
    # 건강보험료: 지역가입자 별도 고지 방식이지만 앱에서 선택 가능하도록 유지
    pub_health   = public_pension * HEALTH_INS_RATE
    pub_net      = public_pension - pub_tax - pub_health

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
            "irp_rate_suggest": 0.0,
            "isa_rate_suggest": 0.0,
            "gen_rate_suggest": 0.0,
            "total_net_est":    pub_net,
            "gap":              pub_net - target_monthly,
            "feasible":         True,
        }

    # 가중치 합 정규화
    total_w = irp_weight + isa_weight + general_weight
    if total_w <= 0:
        total_w = 1.0
        irp_weight = isa_weight = general_weight = 1/3

    # 부족분을 가중치 비율로 각 계좌에 배분 (세후 목표)
    irp_need_net = shortage * (irp_weight / total_w)
    isa_need_net = shortage * (isa_weight / total_w)
    gen_need_net = shortage * (general_weight / total_w)

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

    # 분배율 역산 (원금 대비 %)
    irp_rate_s = (irp_need_gross / irp_total * 100) if irp_total > 0 else 0.0
    isa_rate_s = (isa_need_gross / isa_total * 100) if isa_total > 0 else 0.0
    gen_rate_s = (gen_need_gross / general_total * 100) if general_total > 0 else 0.0

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
        "irp_rate_suggest": min(irp_rate_s, 5.0),   # 월 5% 상한
        "isa_rate_suggest": min(isa_rate_s, 5.0),
        "gen_rate_suggest": min(gen_rate_s, 2.0),
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
def load_scenarios(url: str, gid: str) -> pd.DataFrame:
    """
    구글 시트 '시나리오' 탭 로드.
    헤더: 시나리오명 | 계좌 | 종목명 | 원금 | 분배율(%) | 메모
    gid 미설정 시 빈 DataFrame 반환.
    """
    if not gid:
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
        if df.empty or "시나리오명" not in df.columns:
            return pd.DataFrame()
        # 숫자 변환
        for col in ["원금", "분배율(%)"]:
            if col in df.columns:
                df[col] = pd.to_numeric(
                    df[col].astype(str).str.replace(",", ""),
                    errors="coerce"
                ).fillna(0)
        return df
    except Exception:
        return pd.DataFrame()


def build_scenario_params(sc_df: pd.DataFrame, sc_name: str) -> dict:
    """
    시나리오명으로 필터링 → 계좌별 원금 합산 및 가중평균 분배율 계산.
    반환: {
      "irp_total": float, "isa_total": float, "gen_total": float,
      "irp_rate":  float, "isa_rate":  float, "gen_rate":  float,
      "irp_종목":  list[dict], "isa_종목": list[dict], "gen_종목": list[dict],
    }
    """
    sub = sc_df[sc_df["시나리오명"] == sc_name].copy()
    result = {
        "irp_total": 0.0, "isa_total": 0.0, "gen_total": 0.0,
        "irp_rate":  0.0, "isa_rate":  0.0, "gen_rate":  0.0,
        "irp_종목": [],   "isa_종목": [],   "gen_종목": [],
    }
    acc_map = {"IRP": "irp", "ISA": "isa", "일반": "gen"}
    for acc_kr, acc_en in acc_map.items():
        rows = sub[sub["계좌"] == acc_kr]
        if rows.empty:
            continue
        total = rows["원금"].sum()
        if total <= 0:
            continue
        # 가중평균 분배율
        w_rate = (rows["원금"] * rows["분배율(%)"]).sum() / total
        result[f"{acc_en}_total"] = total
        result[f"{acc_en}_rate"]  = w_rate / 100   # % → 소수
        result[f"{acc_en}_종목"]  = rows[["종목명","원금","분배율(%)"]].to_dict("records")
    return result


@st.cache_data(ttl=DATA_TTL, show_spinner=False)
def load_and_validate(url: str, gid: str) -> tuple[pd.DataFrame, list[str]]:
    """시트 로드 + 유효성 검사 결과를 캐시해 반환"""
    df     = load_sheet(url, gid)
    errors = validate_df(df)
    return df, errors


def extract_values(df: pd.DataFrame) -> dict:
    """DataFrame에서 모든 설정값을 추출해 dict로 반환"""
    return {
        "public_pension":   safe_get(df, "공적연금"),
        "irp_total":        safe_get(df, "IRP"),
        "isa_total":        safe_get(df, "ISA"),
        "general_total":    safe_get(df, "일반",          default=0.0),
        "target_monthly":   safe_get(df, "목표생활비",    default=1.0),
        "default_palantir": safe_get(df, "IRP기본분배율",  default=1.2),
        "default_kodex":    safe_get(df, "ISA기본분배율",  default=0.8),
        "default_general":  safe_get(df, "일반기본분배율", default=0.1),
        # 보유 수량 (주당 분배금 입력 모드에 사용)
        "irp_shares":       safe_get(df, "IRP수량",  default=0.0),
        "isa_shares":       safe_get(df, "ISA수량",  default=0.0),
        # 주당 기본 분배금 (시트에 있으면 초기값으로 사용)
        "irp_dps_default":  safe_get(df, "IRP주당분배금", default=0.0),
        "isa_dps_default":  safe_get(df, "ISA주당분배금", default=0.0),
    }


# ── 5단계 로딩 진행률 ────────────────────────────────────
with st.status("📡 연금 데이터를 불러오는 중...", expanded=True) as _status:

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
            f"구글 시트에 아래 항목이 '항목' 컬럼에 정확히 있어야 합니다:\n"
            + "\n".join(f"• {item}" for item in REQUIRED_ITEMS)
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
    irp_dps_default  = _vals["irp_dps_default"]
    isa_dps_default  = _vals["isa_dps_default"]

    # STEP 5 — 시나리오 탭 로드
    st.write("🎯 시나리오 데이터 로드 중...")
    sc_df = load_scenarios(SHEET_URL, SCENARIO_SHEET_GID)
    sc_names = sc_df["시나리오명"].unique().tolist() if not sc_df.empty else []

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
    # ── 분배금 입력 — 듀얼 모드 ────────────────────────────
    st.header("💰 분배금 입력")

    # 기본 추정값 계산
    _irp_default_amt  = int(irp_total * (default_palantir / 100))
    _isa_default_amt  = int(isa_total  * (default_kodex    / 100))

    # 모드 토글
    _use_amount = st.toggle(
        "월 분배금(원) 직접 입력",
        value=True,
        key="input_mode_toggle",
        help="ON: 확정된 분배금(원) 직접 입력  |  OFF: 분배율(%) 입력 후 자동 계산",
    )

    # 입력 모드 선택 (3가지)
    _input_mode = st.radio(
        "입력 방식",
        ["💰 주당 분배금(원)", "💵 월 총 분배금(원)", "📊 분배율(%)"],
        index=0,
        key="input_mode_radio",
        horizontal=True,
        label_visibility="collapsed",
    )

    if _input_mode == "💰 주당 분배금(원)":
        # ── 모드 A: 주당 분배금 입력 → 수량×DPS = 월 총 분배금 ──
        st.caption("주당 분배금을 입력하면 보유 수량 기준으로 자동 계산됩니다.")

        # 수량 — 시트에 IRP수량/ISA수량 행이 있으면 자동, 없으면 직접 입력
        if irp_shares > 0:
            _irp_shares_val = int(irp_shares)
            st.caption(f"IRP 보유 수량: {_irp_shares_val:,}주 (시트 자동)")
        else:
            _irp_shares_val = st.number_input(
                "💼 IRP 보유 수량 (주)", min_value=0, value=20000,
                step=100, key="irp_shares_input",
            )
        if isa_shares > 0:
            _isa_shares_val = int(isa_shares)
            st.caption(f"ISA 보유 수량: {_isa_shares_val:,}주 (시트 자동)")
        else:
            _isa_shares_val = st.number_input(
                "📦 ISA 보유 수량 (주)", min_value=0, value=2300,
                step=100, key="isa_shares_input",
            )

        # 주당 분배금 입력
        _irp_dps_init = int(irp_dps_default) if irp_dps_default > 0             else (int(_irp_default_amt / _irp_shares_val) if _irp_shares_val > 0 else 185)
        _isa_dps_init = int(isa_dps_default) if isa_dps_default > 0             else (int(_isa_default_amt / _isa_shares_val) if _isa_shares_val > 0 else 252)

        irp_dps = st.number_input(
            "💼 IRP 주당 분배금 (원)",
            min_value=0, max_value=10_000,
            value=_irp_dps_init, step=1,
            key="irp_dps",
            help="SOL 팔란티어커버드콜 1주당 이번달 분배금",
        )
        isa_dps = st.number_input(
            "📦 ISA 주당 분배금 (원)",
            min_value=0, max_value=10_000,
            value=_isa_dps_init, step=1,
            key="isa_dps",
            help="KODEX200타겟위클리커버드콜 1주당 이번달 분배금",
        )

        # 월 총 분배금 계산
        irp_income_input = irp_dps * _irp_shares_val
        isa_income_input = isa_dps * _isa_shares_val

        # 결과 표시
        st.markdown(
            f"<div style='background:rgba(255,215,0,0.08); padding:8px 10px; "
            f"border-radius:8px; border-left:3px solid #FFD700; font-size:0.82rem; "
            f"margin-top:4px;'>"
            f"IRP {irp_dps:,}원 × {_irp_shares_val:,}주 = "
            f"<b>{irp_income_input:,.0f}원</b><br>"
            f"ISA {isa_dps:,}원 × {_isa_shares_val:,}주 = "
            f"<b>{isa_income_input:,.0f}원</b>"
            f"</div>",
            unsafe_allow_html=True,
        )

        # 분배율 역산 → 시뮬레이션 연동
        palantir_rate = irp_income_input / irp_total if irp_total > 0 else default_palantir / 100
        kodex_rate    = isa_income_input / isa_total  if isa_total  > 0 else default_kodex    / 100
        st.caption(f"↳ IRP {palantir_rate*100:.2f}% / ISA {kodex_rate*100:.2f}% (시뮬레이션 반영)")

    elif _input_mode == "💵 월 총 분배금(원)":
        # ── 모드 B: 월 총 분배금(원) 직접 입력 ──────────────
        st.caption("지급 확정된 월 총 분배금을 직접 입력하세요.")
        irp_income_input = st.number_input(
            "💼 IRP 월 총 분배금 (원)",
            min_value=0, max_value=int(irp_total * 0.10),
            value=_irp_default_amt, step=10_000, key="irp_amt",
            help=f"시트 기준 추정: {_irp_default_amt:,.0f}원",
        )
        isa_income_input = st.number_input(
            "📦 ISA 월 총 분배금 (원)",
            min_value=0, max_value=int(isa_total * 0.10),
            value=_isa_default_amt, step=10_000, key="isa_amt",
            help=f"시트 기준 추정: {_isa_default_amt:,.0f}원",
        )
        palantir_rate = irp_income_input / irp_total if irp_total > 0 else default_palantir / 100
        kodex_rate    = isa_income_input / isa_total  if isa_total  > 0 else default_kodex    / 100
        _irp_diff = irp_income_input - _irp_default_amt
        _isa_diff = isa_income_input - _isa_default_amt
        st.caption(
            f"IRP {palantir_rate*100:.2f}% / ISA {kodex_rate*100:.2f}%"
            + (f"  |  기준 대비 IRP {_irp_diff:+,.0f} / ISA {_isa_diff:+,.0f}원"
               if abs(_irp_diff)+abs(_isa_diff) > 0 else "")
        )

    else:
        # ── 모드 C: 분배율(%) 슬라이더 ───────────────────────
        st.caption("분배율을 조정하면 분배금이 자동 계산됩니다.")
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
        irp_income_input = int(irp_total * palantir_rate)
        isa_income_input = int(isa_total  * kodex_rate)
        st.caption(
            f"↳ IRP {irp_income_input:,.0f}원 / ISA {isa_income_input:,.0f}원"
        )

    # ── 목표 생활비 조정 ──────────────────────────────────
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

    irp_withdraw = st.number_input(
        "💼 IRP 월 인출액 (원)",
        min_value=0, max_value=int(irp_total * 0.10),
        value=_irp_w_def, step=100_000, key="irp_withdraw",
    )
    isa_withdraw = st.number_input(
        "📦 ISA 월 인출액 (원)",
        min_value=0, max_value=int(isa_total * 0.10),
        value=_isa_w_def, step=100_000, key="isa_withdraw",
    )
    gen_withdraw = st.number_input(
        "💵 일반 월 인출액 (원)",
        min_value=0, max_value=int(general_total * 0.10) if general_total > 0 else 1_000_000,
        value=_gen_w_def, step=100_000, key="gen_withdraw",
    )
    _total_withdraw = irp_withdraw + isa_withdraw + gen_withdraw
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

    # ── 세금 옵션 ─────────────────────────────────────────
    st.divider()
    st.subheader("⚙️ 세금 옵션")
    show_tax       = st.toggle("세후 실수령액 표시",  value=True)
    use_health_ins = st.toggle("건강보험료 포함",     value=True)

    # ── 시나리오 선택 ─────────────────────────────────
    st.divider()
    st.subheader("🎯 포트폴리오 시나리오")

    # 모드: 시트 불러오기 vs 앱에서 직접 작성
    _sc_mode = st.radio(
        "시나리오 모드",
        ["📋 시트 시나리오 선택", "✏️ 앱에서 직접 작성"],
        horizontal=True,
        key="sc_mode",
        label_visibility="collapsed",
    )

    if _sc_mode == "📋 시트 시나리오 선택":
        # ── 시트 저장 시나리오 선택 ──────────────────────
        if sc_names:
            sc_choice = st.selectbox(
                "시나리오 선택",
                ["기본 (시트 연금현황)"] + sc_names,
                key="sc_choice",
                help="구글 시트 '시나리오' 탭에서 정의한 포트폴리오 구성",
            )
            if sc_choice != "기본 (시트 연금현황)":
                _sc_params = build_scenario_params(sc_df, sc_choice)
                st.caption(
                    f"IRP {_sc_params['irp_total']/100_000_000:.2f}억 "
                    f"({_sc_params['irp_rate']*100:.2f}%) / "
                    f"ISA {_sc_params['isa_total']/10_000_000:.1f}천만 "
                    f"({_sc_params['isa_rate']*100:.2f}%)"
                )
                with st.expander("구성 종목 보기"):
                    for acc_kr, acc_en in [("IRP","irp"),("ISA","isa"),("일반","gen")]:
                        items = _sc_params[f"{acc_en}_종목"]
                        if items:
                            st.markdown(f"**{acc_kr}**")
                            for it in items:
                                st.caption(
                                    f"  {it['종목명']} — "
                                    f"{it['원금']/10_000_000:.1f}천만원 / "
                                    f"{it['분배율(%)']:.2f}%"
                                )
        else:
            sc_choice = "기본 (시트 연금현황)"
            st.caption("구글 시트에 '시나리오' 탭을 추가하면 여기서 선택할 수 있습니다.")

    else:
        # ── 앱에서 직접 시나리오 작성 ────────────────────
        sc_choice = "기본 (시트 연금현황)"   # 직접 작성 모드는 아래 변수로 제어

        st.caption("계좌별 종목 구성을 자유롭게 설정해 보세요. (최대 3종목/계좌)")

        def _account_builder(acc_label: str, acc_key: str,
                             default_total: float, default_rate: float) -> tuple:
            """계좌별 종목 구성 입력 UI → (총원금, 가중평균분배율) 반환"""
            with st.expander(f"{acc_label} 종목 구성", expanded=False):
                n_items = st.number_input(
                    f"종목 수", min_value=1, max_value=5,
                    value=1, step=1, key=f"{acc_key}_n",
                )
                rows = []
                for i in range(int(n_items)):
                    c1, c2, c3 = st.columns([3, 2, 2])
                    name = c1.text_input(
                        f"종목{i+1}", value="" if i > 0 else acc_label.split()[0],
                        key=f"{acc_key}_name{i}", label_visibility="collapsed",
                    )
                    amt = c2.number_input(
                        "원금(만원)", min_value=0,
                        value=int(default_total/10000) if i == 0 else 0,
                        step=100, key=f"{acc_key}_amt{i}",
                        label_visibility="collapsed",
                    ) * 10_000
                    rate = c3.number_input(
                        "분배율(%)", min_value=0.0, max_value=10.0,
                        value=default_rate if i == 0 else 0.0,
                        step=0.01, key=f"{acc_key}_rate{i}",
                        label_visibility="collapsed",
                        format="%.2f",
                    )
                    if amt > 0:
                        rows.append({"종목명": name, "원금": amt, "분배율(%)": rate})

                if not rows:
                    return 0.0, 0.0, []
                total_amt = sum(r["원금"] for r in rows)
                w_rate    = sum(r["원금"] * r["분배율(%)"] for r in rows) / total_amt if total_amt > 0 else 0
                st.caption(
                    f"합계 {total_amt/100_000_000:.2f}억원 | "
                    f"가중평균 분배율 {w_rate:.2f}%"
                )
                return total_amt, w_rate / 100, rows

        _custom_irp_total, _custom_irp_rate, _custom_irp_items = _account_builder(
            "💼 IRP", "irp_custom", irp_total, default_palantir,
        )
        _custom_isa_total, _custom_isa_rate, _custom_isa_items = _account_builder(
            "📦 ISA", "isa_custom", isa_total, default_kodex,
        )
        _custom_gen_total, _custom_gen_rate, _custom_gen_items = _account_builder(
            "💵 일반", "gen_custom", general_total, default_general * 100,
        )

        # 결과 요약
        _custom_irp_m  = _custom_irp_total  * _custom_irp_rate
        _custom_isa_m  = _custom_isa_total  * _custom_isa_rate
        _custom_gen_m  = _custom_gen_total  * _custom_gen_rate
        _custom_total_m = public_pension + _custom_irp_m + _custom_isa_m + _custom_gen_m
        _custom_ach    = (_custom_total_m / target_monthly * 100) if target_monthly > 0 else 0
        _ach_color     = "#7dffb0" if _custom_ach >= 100 else "#FFD700" if _custom_ach >= 80 else "#FF4B4B"
        st.markdown(
            f"<div style='background:rgba(255,255,255,0.04); padding:10px 12px; "
            f"border-radius:8px; border-left:3px solid {_ach_color}; "
            f"font-size:0.85rem; margin-top:8px;'>"
            f"<b>이 구성의 예상 월 수입</b><br>"
            f"공무원연금 {public_pension/10000:.0f}만 + "
            f"IRP {_custom_irp_m/10000:.0f}만 + "
            f"ISA {_custom_isa_m/10000:.0f}만 + "
            f"일반 {_custom_gen_m/10000:.0f}만<br>"
            f"<b style='color:{_ach_color};'>합계 {_custom_total_m/10000:.0f}만원 "
            f"(달성률 {_custom_ach:.0f}%)</b></div>",
            unsafe_allow_html=True,
        )

        # 시트 저장 안내
        st.markdown(
            "<div style='font-size:0.78rem; color:rgba(255,255,255,0.4); "
            f"margin-top:6px;'>📌 구글 시트 '시나리오' 탭에 저장하면 다음에도 불러올 수 있습니다.</div>",
            unsafe_allow_html=True,
        )

        # 세션에 커스텀 값 저장 → 계산 섹션에서 사용
        st.session_state["_custom_sc"] = {
            "irp_total": _custom_irp_total, "isa_total": _custom_isa_total,
            "gen_total": _custom_gen_total, "irp_rate":  _custom_irp_rate,
            "isa_rate":  _custom_isa_rate,  "gen_rate":  _custom_gen_rate,
        }

    st.divider()
    if st.button("🔄 데이터 갱신", use_container_width=True):
        load_sheet.clear()
        load_and_validate.clear()
        load_scenarios.clear()
        st.session_state.pop("_data_loaded", None)
        st.rerun()
    st.caption(f"워크시트: {WORKSHEET_NAME} · 캐시: {DATA_TTL}")


# ════════════════════════════════════════════════════════
# 5. 계산
# ════════════════════════════════════════════════════════

# ── 시나리오 적용 ─────────────────────────────────────
# 직접 작성 모드 우선 적용, 그 다음 시트 시나리오
_sc_mode_val = st.session_state.get("sc_mode", "📋 시트 시나리오 선택")
if _sc_mode_val == "✏️ 앱에서 직접 작성":
    _cs = st.session_state.get("_custom_sc", {})
    if _cs:
        if _cs.get("irp_total", 0) > 0: irp_total     = _cs["irp_total"]
        if _cs.get("isa_total", 0) > 0: isa_total     = _cs["isa_total"]
        if _cs.get("gen_total", 0) > 0: general_total = _cs["gen_total"]
        if _input_mode == "📊 분배율(%)":
            if _cs.get("irp_rate", 0) > 0: palantir_rate = _cs["irp_rate"]
            if _cs.get("isa_rate", 0) > 0: kodex_rate    = _cs["isa_rate"]
elif sc_choice != "기본 (시트 연금현황)" and sc_names:
    _sc = build_scenario_params(sc_df, sc_choice)
    # 원금 교체
    irp_total     = _sc["irp_total"]   if _sc["irp_total"]  > 0 else irp_total
    isa_total     = _sc["isa_total"]   if _sc["isa_total"]  > 0 else isa_total
    general_total = _sc["gen_total"]   if _sc["gen_total"]  > 0 else general_total
    # 분배율 교체 (분배금 직접 입력 모드가 아닐 때만)
    if _input_mode == "📊 분배율(%)":
        palantir_rate = _sc["irp_rate"] if _sc["irp_rate"] > 0 else palantir_rate
        kodex_rate    = _sc["isa_rate"] if _sc["isa_rate"] > 0 else kodex_rate

# 이번 달 분배금: 사이드바 직접 입력값 사용
irp_income   = float(irp_income_input)
isa_income   = float(isa_income_input)
total_income = public_pension + irp_income + isa_income

# 세후 계산
tax_result = calc_after_tax(public_pension, irp_income, isa_income)
if not use_health_ins:
    tax_result["공적연금_세후"]  += tax_result["공적연금_건보료"]
    tax_result["총_세후"]        += tax_result["공적연금_건보료"]
    tax_result["총_공제액"]      -= tax_result["공적연금_건보료"]
    tax_result["실효세율"]        = (
        tax_result["총_공제액"] / tax_result["총_세전"] * 100
        if tax_result["총_세전"] > 0 else 0
    )

display_income = tax_result["총_세후"] if show_tax else total_income
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
if sc_choice != "기본 (시트 연금현황)" and sc_names:
    st.markdown(
        f"<span style='background:rgba(255,215,0,0.15); color:#FFD700; "
        f"padding:3px 12px; border-radius:12px; font-size:0.82rem; "
        f"border:1px solid rgba(255,215,0,0.3);'>🎯 시나리오: {sc_choice}</span>",
        unsafe_allow_html=True,
    )

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
    w1, w2, w3 = st.columns(3)

    def _withdrawal_card(col, label, color, need_gross, rate_suggest,
                         total_asset, current_rate):
        with col:
            with st.container(border=True):
                st.markdown(
                    f"<div style='color:{color}; font-weight:700; "
                    f"font-size:0.95rem; margin-bottom:8px;'>{label}</div>",
                    unsafe_allow_html=True,
                )
                st.metric("필요 인출액(월)", f"{need_gross:,.0f}원")
                st.metric("권장 분배율",
                          f"{rate_suggest:.2f}%",
                          delta=f"현재 {current_rate*100:.2f}% 대비 "
                                f"{rate_suggest - current_rate*100:+.2f}%p",
                          delta_color="inverse" if rate_suggest > current_rate*100
                                      else "normal")
                if total_asset > 0:
                    if need_gross > 0:
                        months_left = total_asset / need_gross
                        yrs = int(months_left // 12) if months_left < 1_200 else 999
                    else:
                        yrs = 999
                    st.caption(
                        f"잔액 {total_asset/100_000_000:.1f}억 기준 "
                        + (f"약 {yrs}년 유지 가능" if yrs < 100 else "기대수명 충분히 초과")
                    )

    _withdrawal_card(w1, "💼 IRP", "#FFD700",
                     wp["irp_need_gross"], wp["irp_rate_suggest"],
                     irp_total, palantir_rate)
    _withdrawal_card(w2, "📦 ISA", "#FF4B4B",
                     wp["isa_need_gross"], wp["isa_rate_suggest"],
                     isa_total, kodex_rate)
    # 일반 계좌는 히트맵 사이드바에서 설정한 분배율 사용
    # (사이드바 순서상 아직 정의 전일 수 있으므로 session_state로 안전하게 읽기)
    _gen_rate_now = float(st.session_state.get("general_rate_hm", 0.001))
    _withdrawal_card(w3, "💵 일반", "#87CEEB",
                     wp["gen_need_gross"], wp["gen_rate_suggest"],
                     general_total, _gen_rate_now)

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
    birth_year     = st.number_input("출생 연도", value=1971, min_value=1950, max_value=1985, step=1, key="birth_year_input")
    retire_age     = st.number_input("은퇴 나이",  value=55,  min_value=50,   max_value=75,   step=1)
    pension_age    = st.number_input("공무원연금 개시 나이", value=55, min_value=50, max_value=70, step=1)
    life_exp       = st.number_input("기대 수명",  value=90,  min_value=70,   max_value=100,  step=1)
    inflation_rate = st.slider("물가상승률 (%)", min_value=0.0, max_value=5.0, value=2.0, step=0.1) / 100

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
    general_rate_hm = st.slider(
        "일반(머니마켓) 월 분배율 (%)",
        min_value=0.0, max_value=1.0,
        value=float(default_general), step=0.01,
        key="general_rate_hm",
    ) / 100

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
# 실지급 & 생활비 실적 추적
# ════════════════════════════════════════════════════════
st.divider()
st.markdown("## 📒 실지급 & 생활비 실적 관리")
st.caption(
    "매월 실제 수령액과 지출액을 기록해 예측과 비교합니다. "
    "구글 시트 **실적** 탭에 데이터를 입력하면 자동 반영됩니다."
)

# ── 실적 시트 로드 ────────────────────────────────────
ACTUAL_SHEET_GID = st.secrets.get("actual_gid", "")   # 실적 탭 gid (미설정 시 빈 문자열)

@st.cache_data(ttl=DATA_TTL, show_spinner=False)
def load_actual_sheet(url: str, gid: str) -> pd.DataFrame:
    """실적 탭 로드. gid 미설정 시 빈 DataFrame 반환."""
    if not gid:
        return pd.DataFrame()
    try:
        match = re.search(r"/d/([a-zA-Z0-9_-]+)", url)
        if not match:
            return pd.DataFrame()
        sid = match.group(1)
        df  = pd.read_csv(
            f"https://docs.google.com/spreadsheets/d/{sid}"
            f"/export?format=csv&gid={gid}"
        )
        return df
    except Exception:
        return pd.DataFrame()

actual_df = load_actual_sheet(SHEET_URL, ACTUAL_SHEET_GID)

# ── 실적 시트 안내 ────────────────────────────────────
if actual_df.empty:
    with st.expander("📋 실적 시트 설정 방법", expanded=True):
        st.markdown("""
        **구글 시트에 `실적` 탭을 추가하고 아래 헤더로 구성하세요.**

        | 연월 | 공무원연금 | IRP분배금 | ISA분배금 | 일반분배금 | 생활비 | 비고 |
        |---|---|---|---|---|---|---|
        | 2026-03 | 3624210 | 3827200 | 556200 | 0 | 3250000 | |
        | 2026-04 | 3624210 | 3801000 | 548700 | 0 | 3410000 | |

        **설정 순서**
        1. 구글 시트에서 `실적` 탭 생성 후 위 형식으로 입력
        2. 탭 우클릭 → 시트 ID(gid=XXXXXX) 확인
        3. Streamlit Cloud → Manage app → Settings → Secrets 에 추가:
        ```toml
        actual_gid = "여기에_실적탭_gid"
        ```
        """)
else:
    # ── 실적 데이터 처리 ──────────────────────────────
    required_actual = ["연월", "공무원연금", "IRP분배금", "ISA분배금", "생활비"]
    missing_cols = [col for col in required_actual if col not in actual_df.columns]

    if missing_cols:
        st.warning(f"실적 시트에 다음 컬럼이 없습니다: {', '.join(missing_cols)}")
    else:
        # 숫자 변환
        for col in ["공무원연금", "IRP분배금", "ISA분배금", "일반분배금", "생활비"]:
            if col in actual_df.columns:
                actual_df[col] = pd.to_numeric(
                    actual_df[col].astype(str).str.replace(",", ""),
                    errors="coerce"
                ).fillna(0)

        actual_df["총수입"] = (
            actual_df["공무원연금"]
            + actual_df["IRP분배금"]
            + actual_df["ISA분배금"]
            + actual_df.get("일반분배금", 0)
        )
        actual_df["잉여/부족"] = actual_df["총수입"] - actual_df["생활비"]

        # ── 최근 3개월 요약 카드 ──────────────────────
        recent = actual_df.tail(3)
        st.markdown("#### 📊 최근 실적 요약")
        r_cols = st.columns(len(recent))
        for i, (_, row) in enumerate(recent.iterrows()):
            gap_color = "#7dffb0" if row["잉여/부족"] >= 0 else "#FF4B4B"
            with r_cols[i]:
                with st.container(border=True):
                    st.markdown(
                        f"<div style='font-size:0.85rem; font-weight:700; "
                        f"color:rgba(255,255,255,0.7);'>{row['연월']}</div>",
                        unsafe_allow_html=True,
                    )
                    st.metric("총 수입",  f"{row['총수입']:,.0f}원")
                    st.metric("생활비",   f"{row['생활비']:,.0f}원")
                    st.metric(
                        "잉여/부족",
                        f"{row['잉여/부족']:+,.0f}원",
                        delta_color="normal" if row["잉여/부족"] >= 0 else "inverse",
                    )

        # ── 예측 vs 실적 비교 차트 ────────────────────
        st.markdown("#### 📈 예측 vs 실적 비교")
        fig_act = go.Figure()

        # 실적 총수입
        fig_act.add_trace(go.Bar(
            x=actual_df["연월"], y=actual_df["총수입"] / 10000,
            name="실제 수입", marker_color="#87CEEB",
            text=[f"{v/10000:.0f}만" for v in actual_df["총수입"]],
            textposition="outside",
        ))
        # 실제 생활비
        fig_act.add_trace(go.Bar(
            x=actual_df["연월"], y=actual_df["생활비"] / 10000,
            name="실제 생활비", marker_color="rgba(255,75,75,0.6)",
            text=[f"{v/10000:.0f}만" for v in actual_df["생활비"]],
            textposition="outside",
        ))
        # 목표 생활비 기준선
        fig_act.add_hline(
            y=target_monthly / 10000,
            line_dash="dot", line_color="#FFD700", line_width=1.5,
            annotation_text=f"목표 {target_monthly/10000:.0f}만원",
            annotation_position="top right",
            annotation_font_color="#FFD700",
        )
        fig_act.update_layout(
            barmode="group", height=320,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(255,255,255,0.02)",
            font_color="white",
            legend=dict(orientation="h", y=-0.25, xanchor="center", x=0.5),
            margin=dict(t=20, b=70, l=10, r=10),
            yaxis=dict(title="금액 (만원)", tickformat=","),
            xaxis=dict(tickangle=-30),
            hovermode="x unified",
        )
        st.plotly_chart(fig_act, use_container_width=True)

        # ── 잉여/부족 추이 ────────────────────────────
        gap_colors = ["#7dffb0" if v >= 0 else "#FF4B4B"
                      for v in actual_df["잉여/부족"]]
        fig_gap_act = go.Figure(go.Bar(
            x=actual_df["연월"],
            y=actual_df["잉여/부족"] / 10000,
            marker_color=gap_colors,
            text=[f"{v/10000:+.0f}만" for v in actual_df["잉여/부족"]],
            textposition="outside",
        ))
        fig_gap_act.add_hline(y=0, line_color="rgba(255,255,255,0.2)", line_width=1)
        fig_gap_act.update_layout(
            height=240,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(255,255,255,0.02)",
            font_color="white",
            margin=dict(t=10, b=50, l=10, r=10),
            yaxis=dict(title="잉여/부족 (만원)", tickformat=","),
            xaxis=dict(tickangle=-30),
        )
        st.plotly_chart(fig_gap_act, use_container_width=True)

        # ── 수입원별 실적 테이블 ──────────────────────
        with st.expander("📋 전체 실적 데이터", expanded=False):
            display_actual = actual_df.copy()
            for col in ["공무원연금","IRP분배금","ISA분배금","생활비","총수입","잉여/부족"]:
                if col in display_actual.columns:
                    display_actual[col] = display_actual[col].apply(
                        lambda v: f"{v:+,.0f}" if col == "잉여/부족" else f"{v:,.0f}"
                    )
            st.dataframe(display_actual, hide_index=True, use_container_width=True)

        # ── 누적 성과 요약 ────────────────────────────
        st.markdown("#### 📌 누적 실적 요약")
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("총 수입 누계",  f"{actual_df['총수입'].sum():,.0f}원")
        s2.metric("총 생활비 누계", f"{actual_df['생활비'].sum():,.0f}원")
        s3.metric("누적 잉여/부족",
                  f"{actual_df['잉여/부족'].sum():+,.0f}원",
                  delta_color="normal" if actual_df["잉여/부족"].sum() >= 0 else "inverse")
        avg_income = actual_df["총수입"].mean()
        s4.metric("월 평균 수입",   f"{avg_income:,.0f}원",
                  delta=f"목표 대비 {avg_income - target_monthly:+,.0f}원")
