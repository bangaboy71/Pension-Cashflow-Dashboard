import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import plotly.express as px

# 1. 페이지 설정
st.set_page_config(page_title="연금 현금흐름 관제탑", layout="wide")

# 2. 구글 시트 연결 (기존 가족 자산과 분리된 연금 전용 시트 주소 사용)
conn = st.connection("gsheets", type=GSheetsConnection)

try:
    # 데이터 로드
    df = conn.read()
    
    # 구글 시트의 '항목' 컬럼에서 값을 찾아 변수에 할당합니다.
    public_pension = df.loc[df['항목'] == '공적연금', '금액'].values[0]
    irp_total = df.loc[df['항목'] == 'IRP', '금액'].values[0]
    isa_total = df.loc[df['항목'] == 'ISA', '금액'].values[0]
    target_monthly = df.loc[df['항목'] == '목표생활비', '금액'].values[0]

except Exception as e:
    st.error(f"데이터 로드 실패: {e}")
    st.info("구글 시트에 '항목'과 '금액' 열이 있는지, 공유 설정이 '링크가 있는 모든 사용자'인지 확인해 주세요.")
    st.stop()

# 3. 사이드바 - 실시간 수익률 시뮬레이션
st.sidebar.header("📈 수익률 시뮬레이션")
palantir_rate = st.sidebar.slider("IRP(팔란티어) 월 분배율 (%)", 0.5, 2.0, 1.2, step=0.1) / 100
kodex_rate = st.sidebar.slider("ISA(KODEX) 월 분배율 (%)", 0.3, 1.5, 0.8, step=0.1) / 100

# 4. 현금흐름 계산
irp_income = irp_total * palantir_rate
isa_income = isa_total * kodex_rate
total_income = public_pension + irp_income + isa_income
achievement = (total_income / target_monthly) * 100

# 5. 메인 화면 출력
st.title("🚀 연금자산 현금흐름 관제탑")
st.markdown(f"### 현재 예상 월 수입: **{total_income:,.0f}원**")

col1, col2 = st.columns([1, 1])
with col1:
    st.metric("목표 달성률", f"{achievement:.1f}%", delta=f"{achievement-100:.1f}%")
    st.info("💡 8월 알프스 여정 대비 현금 흐름을 점검 중입니다.")
    
with col2:
    # 수입 비중 시각화
    fig_df = pd.DataFrame({
        "구분": ["공적연금", "IRP수익", "ISA수익"],
        "금액": [public_pension, irp_income, isa_income]
    })
    fig = px.pie(fig_df, values='금액', names='구분', hole=0.4, title="월 수입 구성")
    st.plotly_chart(fig, use_container_width=True)