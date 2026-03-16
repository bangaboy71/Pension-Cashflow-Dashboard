import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import plotly.express as px

# 1. 페이지 설정
st.set_page_config(page_title="연금 현금흐름 관제탑", layout="wide")

# 2. 구글 시트 연결
conn = st.connection("gsheets", type=GSheetsConnection)

try:
    df = conn.read()
    
    # 데이터 추출 및 전처리
    public_pension = df.loc[df['자산명'] == '공적연금', '금액(원)'].values[0]
    irp_value = df.loc[df['자산명'] == 'IRP (SOL 팔란티어)', '금액(원)'].values[0]
    isa_value = df.loc[df['자산명'] == 'ISA (KODEX 200)', '금액(원)'].values[0]
    target_monthly = df.loc[df['자산명'] == '목표생활비', '금액(원)'].values[0]
    
    # 시트에서 기본 수익률 가져오기 (연 수익률을 월로 변환)
    irp_rate_base = df.loc[df['자산명'] == 'IRP (SOL 팔란티어)', '예상 수익률(연 %)'].values[0] / 12 / 100
    isa_rate_base = df.loc[df['자산명'] == 'ISA (KODEX 200)', '예상 수익률(연 %)'].values[0] / 12 / 100

except Exception as e:
    st.error(f"데이터 로드 실패: {e}")
    st.info("시트의 컬럼명을 [구분, 자산명, 금액(원), 예상 수익률(연 %)]로 맞춰주세요.")
    st.stop()

# 3. 사이드바 - 동적 시뮬레이션
st.sidebar.header("📊 시장 상황 시뮬레이션")
st.sidebar.write("분배금 지급률 변동 시 수입 변화")
irp_rate = st.sidebar.slider("IRP 월 분배율 (%)", 0.5, 2.5, float(irp_rate_base*100), step=0.1) / 100
isa_rate = st.sidebar.slider("ISA 월 분배율 (%)", 0.1, 2.0, float(isa_rate_base*100), step=0.1) / 100

# 4. 현금흐름 계산
irp_income = irp_value * irp_rate
isa_income = isa_value * isa_rate
total_income = public_pension + irp_income + isa_income
shortfall = target_monthly - total_income
achievement = (total_income / target_monthly) * 100

# 5. 메인 대시보드 출력
st.title("💰 연금자산 현금흐름 관제탑")
st.markdown(f"### 현재 예상 월 수입: **{total_income:,.0f}원**")

# 상단 KPI
col1, col2, col3 = st.columns(3)
with col1:
    st.metric("목표 달성률", f"{achievement:.1f}%", delta=f"{achievement-100:.1f}%")
with col2:
    if shortfall > 0:
        st.metric("목표까지 부족액", f"{shortfall:,.0f}원", delta_color="inverse")
    else:
        st.metric("목표 초과 달성", f"{abs(shortfall):,.0f}원", delta_color="normal")
with col3:
    st.metric("건보료 상태", "안전 (비과세)", help="IRP/ISA 내 운용수익은 건보료 산정 제외")

st.markdown("---")

# 6. 시각화 섹션
left_col, right_col = st.columns(2)

with left_col:
    st.subheader("💳 수입 구성 비중")
    fig_pie = px.pie(
        values=[public_pension, irp_income, isa_income],
        names=["공적연금", "IRP 분배수익", "ISA 분배수익"],
        hole=0.5,
        color_discrete_sequence=px.colors.sequential.RdBu
    )
    st.plotly_chart(fig_pie, use_container_width=True)

with right_col:
    st.subheader("📈 수입 vs 목표")
    # 목표 대비 현재 수입 바 차트
    fig_bar = px.bar(
        x=["현재 수입", "목표 수입"],
        y=[total_income, target_monthly],
        color=["수입", "목표"],
        text_auto=',.0f'
    )
    st.plotly_chart(fig_bar, use_container_width=True)

# 7. 하단 안내문
st.caption(f"본 대시보드는 구글 시트의 실시간 데이터를 기반으로 산출되었습니다. (기준금액: IRP {irp_value/100000000:.1f}억, ISA {isa_value/10000:.0f}만)")
