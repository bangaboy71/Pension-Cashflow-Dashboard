import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import yfinance as yf  # 시장 데이터용

# 1. 페이지 설정
st.set_page_config(page_title="현금흐름 340만 관제탑", layout="wide")

# 2. 데이터 로드 및 정제 레이어
conn = st.connection("gsheets", type=GSheetsConnection)

try:
    df = conn.read(ttl=0)
    df.columns = df.columns.str.strip().str.replace('\ufeff', '', regex=False)
    
    cols_to_fix = ['투자원금', '현재 가치', '목표인출액', '목표가']
    for col in cols_to_fix:
        if col in df.columns:
            df[col] = df[col].astype(str).str.replace(',', '').str.replace('원', '').str.strip()
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            
except Exception as e:
    st.error(f"데이터 엔진 로드 실패: {e}")
    st.stop()

# 3. 사적 자산 필터링 및 목표 설정
target_col = '계좌 유형'
if target_col not in df.columns:
    df.rename(columns={df.columns[0]: '계좌 유형'}, inplace=True)
private_assets = df[df['계좌 유형'].isin(['IRP', 'ISA', '일반'])].copy()

TARGET_PRIVATE = 3400000
current_total = private_assets['목표인출액'].sum()
achievement = (current_total / TARGET_PRIVATE) * 100

# ---------------------------------------------------------
# 4. 중앙 제목 구성
# ---------------------------------------------------------
st.markdown("<h2 style='text-align: center;'>🛡️ 현금흐름 고도화 관제탑</h2>", unsafe_allow_html=True)
st.markdown(f"<p style='text-align: center; color: gray;'>사적 자산 목표: 월 {TARGET_PRIVATE/10000:,.0f}만 원</p>", unsafe_allow_html=True)

# ---------------------------------------------------------
# 5. 실시간 시장 지표 (가족 자산 관제탑 형식)
# ---------------------------------------------------------
def get_market_data():
    try:
        # 코스피, 코스닥, 환율 데이터 가져오기
        tickers = {"KOSPI": "^KS11", "KOSDAQ": "^KQ11", "USD/KRW": "USDKRW=X"}
        data = yf.download(list(tickers.values()), period="2d", interval="1d")
        
        market_metrics = {}
        for name, ticker in tickers.items():
            current = data['Close'][ticker].iloc[-1]
            prev = data['Close'][ticker].iloc[-2]
            delta = current - prev
            market_metrics[name] = (current, delta)
            
        # 거래량 (코스피 기준)
        volume = data['Volume']["^KS11"].iloc[-1] / 10**8 # 억 단위
        return market_metrics, volume
    except:
        return None, 0

market_info, k_volume = get_market_data()

# 시장 지표 표시용 4컬럼
idx1, idx2, idx3, idx4 = st.columns(4)
if market_info:
    idx1.metric("KOSPI", f"{market_info['KOSPI'][0]:,.2f}", f"{market_info['KOSPI'][1]:,.2f}")
    idx2.metric("KOSDAQ", f"{market_info['KOSDAQ'][0]:,.2f}", f"{market_info['KOSDAQ'][1]:,.2f}")
    idx3.metric("원/달러 환율", f"{market_info['USD/KRW'][0]:,.1f}", f"{market_info['USD/KRW'][1]:,.1f}", delta_color="inverse")
    idx4.metric("코스피 거래량", f"{k_volume:.1f}억", help="단위: 억 주")

st.markdown("---")

# 6. 상단 KPI 리포트 (연금 자산 전용)
m1, m2, m3, m4 = st.columns(4)
m1.metric("사적 월 수입", f"{current_total:,.0f}원")
m2.metric("사적 목표 달성률", f"{achievement:.1f}%", delta=f"{achievement-100:.1f}%")
m3.metric("사적 자산 평가액", f"{private_assets['현재 가치'].sum():,.0f}원")
m4.metric("세금 방어막", "우수", delta="비과세/이연 중심")

# 7. 4대 핵심 시각화 탭
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
