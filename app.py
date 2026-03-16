import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

# 1. 페이지 설정
st.set_page_config(page_title="현금흐름 340만 관제탑", layout="wide")

# 2. 데이터 로드 및 정제 레이어 (BOM 및 공백 제거)
conn = st.connection("gsheets", type=GSheetsConnection)

try:
    df = conn.read(ttl=0)
    df.columns = df.columns.str.strip().str.replace('\ufeff', '', regex=False)
    
    # 숫자형 데이터 정제
    cols_to_fix = ['투자원금', '현재 가치', '목표인출액', '목표가']
    for col in cols_to_fix:
        if col in df.columns:
            df[col] = df[col].astype(str).str.replace(',', '').str.replace('원', '').str.strip()
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            
except Exception as e:
    st.error(f"데이터 엔진 로드 실패: {e}")
    st.stop()

# 3. 사적 자산 필터링 (공적연금 제외)
target_col = '계좌 유형'
if target_col in df.columns:
    private_assets = df[df[target_col].isin(['IRP', 'ISA', '일반'])].copy()
else:
    # 컬럼명을 못 찾을 경우 첫 번째 컬럼을 강제로 지정
    df.rename(columns={df.columns[0]: '계좌 유형'}, inplace=True)
    private_assets = df[df['계좌 유형'].isin(['IRP', 'ISA', '일반'])].copy()

# 4. 목표 설정 (사적 자산 월 340만 원)
TARGET_PRIVATE = 3400000
current_total = private_assets['목표인출액'].sum()
achievement = (current_total / TARGET_PRIVATE) * 100

# 5. 메인 화면 구성 (제목 크기 축소)
st.markdown("### 🛡️ 현금흐름 고도화 관제탑")
st.markdown(f"**자산 목표: 월 {TARGET_PRIVATE/10000:,.0f}만 원**")

# 상단 KPI 리포트
m1, m2, m3, m4 = st.columns(4)
m1.metric("월 수입", f"{current_total:,.0f}원")
m2.metric("목표 달성률", f"{achievement:.1f}%", delta=f"{achievement-100:.1f}%")
m3.metric("자산 평가액", f"{private_assets['현재 가치'].sum():,.0f}원")
m4.metric("세금 방어막", "우수", delta="비과세/이연 중심")

st.markdown("---")

# 6. 4대 핵심 시각화 탭
tab1, tab2, tab3, tab4 = st.tabs([
    "📊 계층형 포트폴리오 (Sunburst)", 
    "🌊 현금흐름 폭포 (Waterfall)", 
    "📅 입금 스케줄 (Schedule)", 
    "🛡️ 세금 방어막 (Tax-Shield)"
])

with tab1:
    st.subheader("계층형 포트폴리오 분석")
    fig_sun = px.sunburst(
        private_assets, 
        path=['계좌 유형', '투자성격', '종목명'], 
        values='투자원금',
        color='투자성격',
        color_discrete_map={'안전': '#1E88E5', '위험': '#E53935'}
    )
    st.plotly_chart(fig_sun, use_container_width=True)

with tab2:
    st.subheader("340만 원 목표 달성 폭포")
    fig_water = go.Figure(go.Waterfall(
        orientation = "v",
        measure = ["relative"] * len(private_assets) + ["total"],
        x = list(private_assets['종목명']) + ["현재 수입 합계"],
        y = list(private_assets['목표인출액']) + [0],
        connector = {"line":{"color":"rgb(63, 63, 63)"}},
        text = [f"{v:,.0f}" for v in private_assets['목표인출액']] + [f"{current_total:,.0f}"],
        textposition = "outside"
    ))
    fig_water.add_hline(y=TARGET_PRIVATE, line_dash="dash", line_color="red", annotation_text="목표 340만")
    st.plotly_chart(fig_water, use_container_width=True)

with tab3:
    st.subheader("날짜별 입금 일정")
    schedule_df = private_assets.sort_values('입금예정일')
    fig_sched = px.bar(
        schedule_df, x='입금예정일', y='목표인출액', color='계좌 유형',
        text='종목명', title="월간 현금 유입 흐름"
    )
    fig_sched.update_xaxes(type='category', title="입금일")
    st.plotly_chart(fig_sched, use_container_width=True)

with tab4:
    st.subheader("Tax-Shield Meter")
    col_l, col_r = st.columns(2)
    with col_l:
        tax_df = private_assets.groupby('세금성격')['투자원금'].sum().reset_index()
        fig_tax = px.pie(tax_df, values='투자원금', names='세금성격', hole=0.5)
        st.plotly_chart(fig_tax)
    with col_r:
        st.info("💡 **안전 진단 리포트**")
        st.write(f"- **비과세/과세이연 자산 합계:** {private_assets[private_assets['세금성격'].isin(['비과세', '과세이연'])]['투자원금'].sum():,.0f}원")
        st.write("- **진단:** 금융소득종합과세 및 건보료 산정 소득으로부터 매우 안전한 상태입니다.")
