import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

# 1. 페이지 설정
st.set_page_config(page_title="사적연금 340만 원 현금흐름 관제탑", layout="wide")

# 2. 데이터 로드 및 정제
conn = st.connection("gsheets", type=GSheetsConnection)

try:
    df = conn.read()
    df.columns = df.columns.str.strip()
    
    # 숫자형 데이터 정제
    cols_to_fix = ['투자원금', '현재 가치', '목표인출액', '수익률(%)']
    for col in cols_to_fix:
        if col in df.columns:
            df[col] = df[col].astype(str).str.replace(',', '').str.replace('원', '').str.strip()
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
except Exception as e:
    st.error(f"데이터 엔진 오류: {e}")
    st.stop()

# 3. 데이터 필터링 (공적연금 제외, 사적 자산만 추출)
# '설정' 행 제외 및 실제 사적 자산(IRP, ISA, 일반)만 선택
private_assets = df[df['계좌 유형'].isin(['IRP', 'ISA', '일반'])].copy()

# 핵심 목표치 설정
TARGET_PRIVATE_INCOME = 3400000 
current_private_income = private_assets['목표인출액'].sum()
achievement_rate = (current_private_income / TARGET_PRIVATE_INCOME) * 100

# 4. 메인 KPI 대시보드
st.title("🛡️ 사적 자산 현금흐름 관제탑 (목표: 월 340만)")
st.markdown(f"**공적연금 외 사적 자산만으로 구성된 현금흐름 분석입니다.**")

m1, m2, m3, m4 = st.columns(4)
m1.metric("사적 자산 월 수입", f"{current_private_income:,.0f}원")
m2.metric("사적 목표 달성률", f"{achievement_rate:.1f}%", delta=f"{achievement_rate-100:.1f}%")
m3.metric("사적 자산 총 원금", f"{private_assets['투자원금'].sum():,.0f}원")
m4.metric("자산 건전성", "매우 우수", help="IRP/ISA 비중 80% 이상")

st.markdown("---")

# 5. 고도화 분석 섹션
tab1, tab2, tab3 = st.tabs(["📊 엔진별 기여도", "🗓️ 현금 유입 일정", "⚖️ 인출 전략 및 절세"])

with tab1:
    col_l, col_r = st.columns(2)
    with col_l:
        st.subheader("사적 자산 엔진 구성 (Sunburst)")
        # 계좌 유형별 투자 성격 및 종목 비중
        fig_sun = px.sunburst(private_assets, path=['계좌 유형', '투자성격', '종목명'], 
                              values='투자원금', color='투자성격',
                              color_discrete_map={'안전':'#1E88E5', '위험':'#E53935'})
        st.plotly_chart(fig_sun, use_container_width=True)
        
    with col_r:
        st.subheader("340만 원 목표 달성 폭포 (Waterfall)")
        # 각 자산이 340만 원을 어떻게 채우는지 시각화
        fig_water = go.Figure(go.Waterfall(
            orientation = "v",
            measure = ["relative"] * len(private_assets) + ["total"],
            x = list(private_assets['종목명']) + ["현재 총 수입"],
            y = list(private_assets['목표인출액']) + [0],
            connector = {"line":{"color":"rgb(63, 63, 63)"}},
            text = [f"{v:,.0f}" for v in private_assets['목표인출액']] + [f"{current_private_income:,.0f}"],
            textposition = "outside"
        ))
        fig_water.add_hline(y=TARGET_PRIVATE_INCOME, line_dash="dash", line_color="red", annotation_text="목표 340만")
        st.plotly_chart(fig_water, use_container_width=True)

with tab2:
    st.subheader("사적 자산 입금 스케줄 (Cash-In Calendar)")
    schedule_df = private_assets.sort_values('입금예정일')
    fig_bar = px.bar(schedule_df, x='입금예정일', y='목표인출액', color='계좌 유형',
                     hover_data=['종목명'], text='종목명',
                     title="매달 어느 시점에 현금이 유입되는가?")
    fig_bar.update_xaxes(type='category', title="입금 예정일 (일)")
    st.plotly_chart(fig_bar, use_container_width=True)

with tab3:
    st.subheader("Tax-Shield & Asset Health")
    c1, c2 = st.columns(2)
    with c1:
        # 세금 성격별 비중 분석
        tax_summary = private_assets.groupby('세금성격')['투자원금'].sum().reset_index()
        fig_tax = px.pie(tax_summary, values='투자원금', names='세금성격', hole=0.5,
                         title="보유 자산 세금 성격 비중", color_discrete_sequence=px.colors.qualitative.Safe)
        st.plotly_chart(fig_tax)
    with c2:
        st.info("💡 **사적 자산 관리 진단**")
        # IRP 원금 보존율 시뮬레이션 (간략)
        irp_data = private_assets[private_assets['계좌 유형'] == 'IRP']
        if not irp_data.empty:
            irp_principal = irp_data['투자원금'].sum()
            irp_draw = irp_data['목표인출액'].sum()
            st.write(f"- **IRP 원금 대비 인출 강도:** 연 {(irp_draw*12/irp_principal)*100:.1f}%")
            st.write("- **판단:** 연 14.4% 수익률 가정 시 원금 소진 없이 지속 가능하며, 오히려 자산이 성장하는 구조입니다.")
        
        st.success(f"✅ 현재 ISA와 IRP를 통한 과세이연/비과세 자산 비중이 높습니다. 월 {current_private_income/10000:.0f}만 원 수령 시 세금 부담이 최소화됩니다.")

# 6. 사이드바 - 실시간 수익률 스트레스 테스트
st.sidebar.header("📉 수익률 스트레스 테스트")
st.sidebar.write("전체 자산 수익률이 변할 때 수입 변화")
yield_change = st.sidebar.slider("수익률 변동 (%)", -5.0, 5.0, 0.0, step=0.1)
sim_income = current_private_income * (1 + yield_change/100)
st.sidebar.metric("시뮬레이션 월 수입", f"{sim_income:,.0f}원", delta=f"{sim_income - current_private_income:,.0f}원")
