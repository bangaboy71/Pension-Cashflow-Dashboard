import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import plotly.express as px

# 1. 페이지 설정
st.set_page_config(page_title="연금 현금흐름 관제탑", layout="wide")

# 2. 구글 시트 연결
conn = st.connection("gsheets", type=GSheetsConnection)

try:
    # 데이터 로드
    df = conn.read()
    
    # [핵심] 컬럼명 정리: 앞뒤 공백 제거
    df.columns = df.columns.str.strip()
    
    # [핵심] 금액 데이터 정제: 쉼표(,) 제거 및 숫자로 변환
    if '금액(원)' in df.columns:
        df['금액(원)'] = df['금액(원)'].astype(str).str.replace(',', '').str.replace(' ', '')
        df['금액(원)'] = pd.to_numeric(df['금액(원)'], errors='coerce').fillna(0)

    # 데이터 추출 (항목명이 정확해야 합니다)
    public_pension = df.loc[df['자산명'] == '공적연금', '금액(원)'].values[0]
    irp_total = df.loc[df['자산명'] == 'IRP (SOL팔란티어)', '금액(원)'].values[0]
    isa_total = df.loc[df['자산명'] == 'ISA (KODEX200)', '금액(원)'].values[0]
    target_monthly = df.loc[df['자산명'] == '목표생활비', '금액(원)'].values[0]
    
    # 수익률 추출 및 월 수익률 변환
    irp_rate_base = df.loc[df['자산명'] == 'IRP (SOL팔란티어)', '예상수익률(연 %)'].values[0] / 12 / 100
    isa_rate_base = df.loc[df['자산명'] == 'ISA (KODEX200)', '예상수익률(연 %)'].values[0] / 12 / 100

except Exception as e:
    st.error(f"데이터 로드 실패: {e}")
    # 디버깅용: 현재 시트가 어떻게 읽히는지 보여줍니다.
    st.write("현재 시트 컬럼 목록:", list(df.columns) if 'df' in locals() else "로드 안 됨")
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
