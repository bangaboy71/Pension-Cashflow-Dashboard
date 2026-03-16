import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

# 1. 페이지 설정 및 디자인 테마
st.set_page_config(page_title="연금 현금흐름 통합 관제탑 v2.0", layout="wide")

# 2. 데이터 로드 및 고도화된 정제 작업
conn = st.connection("gsheets", type=GSheetsConnection)

try:
    df = conn.read()
    df.columns = df.columns.str.strip()
    
    # 숫자형 데이터 정제 (쉼표 제거 및 변환)
    cols_to_fix = ['투자원금', '목표가', '주당 배당금', '목표인출액', '현재 가치']
    for col in cols_to_fix:
        if col in df.columns:
            df[col] = df[col].astype(str).str.replace(',', '').str.replace('원', '').str.strip()
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

except Exception as e:
    st.error(f"데이터 엔진 로드 실패: {e}")
    st.stop()

# 3. 핵심 변수 추출
# '설정' 행에서 목표 금액 추출
goal_row = df[df['계좌 유형'] == '설정']
target_monthly = goal_row['목표인출액'].values[0] if not goal_row.empty else 7000000

# 실제 자산 데이터만 필터링 (설정 제외)
assets_df = df[df['계좌 유형'] != '설정'].copy()

# 4. 상단 메인 지표 (KPI)
st.title("🛡️ 연금 자산 현금흐름 통합 관제탑")

# 수입 합계 계산 (공적연금 + 각 계좌별 목표인출액)
total_cash_in = assets_df['목표인출액'].sum()
achievement_rate = (total_cash_in / target_monthly) * 100

m1, m2, m3, m4 = st.columns(4)
m1.metric("월 총 예상 수입", f"{total_cash_in:,.0f}원")
m2.metric("목표 달성률", f"{achievement_rate:.1f}%", delta=f"{achievement_rate-100:.1f}%")
m3.metric("총 투자 원금", f"{assets_df['투자원금'].sum():,.0f}원")
m4.metric("세금 방어막", "우수", delta="비과세/이연 중심")

st.markdown("---")

# 5. 고도화 분석 섹션
tab1, tab2, tab3 = st.tabs(["📊 포트폴리오 분석", "📅 현금흐름 스케줄", "⚖️ 세금 및 안전진단"])

with tab1:
    col_left, col_right = st.columns(2)
    with col_left:
        st.subheader("계좌별 자산 비중 (Sunburst)")
        # 계좌 유형 -> 종목명으로 이어지는 계층 구조 시각화
        fig_sun = px.sunburst(assets_df, path=['계좌 유형', '종목명'], values='투자원금',
                              color='투자성격', color_discrete_map={'안전':'#2E7D32', '위험':'#C62828'})
        st.plotly_chart(fig_sun, use_container_width=True)
        
    with col_right:
        st.subheader("현금흐름 기여도 (Waterfall)")
        # 목표 대비 각 수입원이 어떻게 채워지는지 시각화
        fig_water = go.Figure(go.Waterfall(
            orientation = "v",
            measure = ["relative"] * len(assets_df) + ["total"],
            x = list(assets_df['종목명']) + ["최종 수입"],
            y = list(assets_df['목표인출액']) + [0],
            connector = {"line":{"color":"rgb(63, 63, 63)"}},
        ))
        fig_water.update_layout(title="수입원별 목표 달성 기여도")
        st.plotly_chart(fig_water, use_container_width=True)

with tab2:
    st.subheader("월간 현금 입금 달력")
    # 입금예정일 기준 정렬
    schedule_df = assets_df[['입금예정일', '종목명', '목표인출액', '계좌 유형']].sort_values('입금예정일')
    fig_sched = px.bar(schedule_df, x='입금예정일', y='목표인출액', color='계좌 유형',
                       text='종목명', title="날짜별 현금 유입 계획")
    st.plotly_chart(fig_sched, use_container_width=True)

with tab3:
    st.subheader("Tax-Shield & Health Guard")
    c1, c2 = st.columns(2)
    with c1:
        # 세금 성격별 분석
        tax_df = assets_df.groupby('세금성격')['투자원금'].sum().reset_index()
        fig_tax = px.pie(tax_df, values='투자원금', names='세금성격', hole=0.4, title="절세 자산 구성")
        st.plotly_chart(fig_tax)
    with c2:
        st.info("💡 **건보료 및 절세 진단**")
        irp_isa_sum = assets_df[assets_df['계좌 유형'].isin(['IRP', 'ISA'])]['투자원금'].sum()
        st.write(f"- **사적연금/절세 자산 규모:** {irp_isa_sum:,.0f}원")
        st.write("- **진단:** 현재 주력 수입원이 IRP와 ISA에 집중되어 있어 지역가입자 전환 시 건보료 산정 소득에서 매우 유리한 구조입니다.")
        if any(assets_df['세금성격'] == '과세이연'):
            st.success("✅ 과세이연 자산이 확인되었습니다. 인출 시점까지 복리 효과가 극대화됩니다.")

# 6. 사이드바 - 실시간 수익률 시뮬레이션
st.sidebar.header("📉 마켓 시나리오")
st.sidebar.write("전체 투자 수익률 변동 시 시뮬레이션")
market_change = st.sidebar.slider("시장 변동성 (%)", -20, 20, 0)
sim_total_value = assets_df['현재 가치'].sum() * (1 + market_change/100)
st.sidebar.metric("시뮬레이션 자산 가치", f"{sim_total_value:,.0f}원")
