import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import plotly.express as px

st.set_page_config(page_title="연금 현금흐름 관제탑", layout="wide")

conn = st.connection("gsheets", type=GSheetsConnection)

try:
    # 1. 데이터 로드 및 컬럼명 청소
    df = conn.read()
    
    # 컬럼명의 앞뒤 공백을 제거하고, 모든 공백을 없애서 비교하기 쉽게 만듭니다.
    # 예: '자산 명 ' -> '자산명', '금액(원)' -> '금액(원)'
    df.columns = df.columns.str.strip().str.replace(' ', '')

    # 2. 유연한 컬럼 매핑 (항목 or 자산명 / 금액 or 금액(원))
    name_col = '자산명' if '자산명' in df.columns else '항목'
    val_col = '금액(원)' if '금액(원)' in df.columns else '금액'
    rate_col = '예상수익률(연%)' if '예상수익률(연%)' in df.columns else '예상수익률'

    # 금액 데이터 숫자 변환 (쉼표 제거)
    df[val_col] = df[val_col].astype(str).str.replace(',', '').str.replace('원', '').str.strip()
    df[val_col] = pd.to_numeric(df[val_col], errors='coerce').fillna(0)

    # 3. 데이터 추출 (시트의 내용과 코드의 매칭)
    # 시트에 '공적연금' 혹은 '국민연금' 등 명칭이 정확해야 합니다.
    public_pension = df.loc[df[name_col].str.contains('연금', na=False), val_col].values[0]
    irp_total = df.loc[df[name_col].str.contains('IRP', na=False), val_col].values[0]
    isa_total = df.loc[df[name_col].str.contains('ISA', na=False), val_col].values[0]
    target_monthly = df.loc[df[name_col].str.contains('목표', na=False), val_col].values[0]
    
    # 수익률 계산 (시트에 수익률 열이 있으면 사용, 없으면 기본값 적용)
    irp_rate = 0.012 # 기본 월 1.2%
    if rate_col in df.columns:
        try:
            raw_rate = df.loc[df[name_col].str.contains('IRP', na=False), rate_col].values[0]
            irp_rate = float(str(raw_rate).replace('%','')) / 100 / 12
        except: pass

except Exception as e:
    st.error(f"데이터 로드 실패: {e}")
    st.write("현재 인식된 컬럼:", list(df.columns))
    st.stop()

# 3. 사이드바 - 실시간 시뮬레이션
st.sidebar.header("📊 시장 상황 시뮬레이션")
irp_rate = st.sidebar.slider("IRP 월 분배율 (%)", 0.5, 2.5, float(irp_rate_base*100), step=0.1) / 100
isa_rate = st.sidebar.slider("ISA 월 분배율 (%)", 0.1, 2.0, float(isa_rate_base*100), step=0.1) / 100

# 4. 현금흐름 계산
irp_income = irp_total * irp_rate
isa_income = isa_total * isa_rate
total_income = public_pension + irp_income + isa_income
shortfall = target_monthly - total_income
achievement = (total_income / target_monthly) * 100

# 5. 메인 화면 출력
st.title("💰 연금자산 현금흐름 관제탑")
st.markdown(f"### 현재 예상 월 수입: **{total_income:,.0f}원**")

col1, col2, col3 = st.columns(3)
with col1:
    st.metric("목표 달성률", f"{achievement:.1f}%", delta=f"{achievement-100:.1f}%")
with col2:
    label = "목표 대비 부족액" if shortfall > 0 else "목표 초과 달성"
    st.metric(label, f"{abs(shortfall):,.0f}원", delta_color="inverse" if shortfall > 0 else "normal")
with col3:
    st.metric("건보료 상태", "안전 (비과세)", help="IRP/ISA 내 운용수익은 건보료 산정 제외")

st.markdown("---")

# 6. 시각화
left, right = st.columns(2)
with left:
    fig_pie = px.pie(
        values=[public_pension, irp_income, isa_income],
        names=["공적연금", "IRP수익", "ISA수익"],
        hole=0.5, title="수입 비중"
    )
    st.plotly_chart(fig_pie, use_container_width=True)
with right:
    fig_bar = px.bar(
        x=["현재 수입", "목표 수입"],
        y=[total_income, target_monthly],
        color=["현재", "목표"],
        text_auto=',.0f', title="수입 vs 목표"
    )
    st.plotly_chart(fig_bar, use_container_width=True)
