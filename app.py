import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import yfinance as yf

# 1. 페이지 설정
st.set_page_config(page_title="현금흐름 340만 관제탑", layout="wide")

# 2. 데이터 로드 및 정제
conn = st.connection("gsheets", type=GSheetsConnection)
try:
    df = conn.read(ttl=0)
    df.columns = df.columns.str.strip().str.replace('\ufeff', '', regex=False)
    numeric_cols = ['투자원금', '현재 가치', '목표인출액']
    for col in numeric_cols:
        if col in df.columns:
            df[col] = df[col].astype(str).str.replace(',', '').str.replace('원', '').str.strip()
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
except Exception as e:
    st.error(f"데이터 엔진 로드 실패: {e}")
    st.stop()

# 사적 자산 필터링 (340만 목표)
private_assets = df[df['계좌 유형'].isin(['IRP', 'ISA', '일반'])].copy()
TARGET_PRIVATE = 3400000
current_total = private_assets['목표인출액'].sum()
achievement = (current_total / TARGET_PRIVATE) * 100

# ---------------------------------------------------------
# 3. [안정화] yfinance 기반 시장 지표 엔진
# ---------------------------------------------------------
@st.cache_data(ttl=600)
def get_market_data_yf():
    # KOSPI: ^KS11, KOSDAQ: ^KQ11, 환율: USDKRW=X
    tickers = {"KOSPI": "^KS11", "KOSDAQ": "^KQ11", "환율": "USDKRW=X"}
    try:
        data = yf.download(list(tickers.values()), period="2d", interval="1d", progress=False)
        
        results = {}
        for name, symbol in tickers.items():
            current = data['Close'][symbol].iloc[-1]
            prev = data['Close'][symbol].iloc[-2]
            diff = current - prev
            pct = (diff / prev) * 100
            
            # 부호(+) 강제 부여하여 색상 연동
            prefix = "+" if diff > 0 else ""
            results[name] = {
                "val": f"{current:,.2f}" if name != "환율" else f"{current:,.1f}",
                "delta": f"{prefix}{diff:,.2f} ({prefix}{pct:.2f}%)" if name != "환율" else f"{prefix}{diff:,.1f}원"
            }
        
        # 거래량 (코스피 기준)
        vol_raw = data['Volume']["^KS11"].iloc[-1]
        results["거래량"] = {"val": f"{vol_raw/1000:,.0f}", "delta": "천주"}
        
        return results
    except:
        return None

# ---------------------------------------------------------
# 4. 화면 구성 (중앙 제목 및 Metric 레이아웃)
# ---------------------------------------------------------
# 제목 중앙 정렬 (사이즈 축소)
st.markdown("<h3 style='text-align: center; color: #1E3A8A;'>🛡️ 사적 자산 현금흐름 통합 관제탑</h3>", unsafe_allow_html=True)
st.markdown(f"<p style='text-align: center; color: #666;'>사적 자산 목표: <b>월 {TARGET_PRIVATE/10000:,.0f}만 원</b></p>", unsafe_allow_html=True)

m_data = get_market_data_yf()

st.markdown("<br>", unsafe_allow_html=True)
idx1, idx2, idx3, idx4 = st.columns(4)

if m_data:
    # KOSPI/KOSDAQ: 상승 시 빨강, 하락 시 파랑 자동 연동
    idx1.metric("KOSPI", m_data["KOSPI"]["val"], m_data["KOSPI"]["delta"])
    idx2.metric("KOSDAQ", m_data["KOSDAQ"]["val"], m_data["KOSDAQ"]["delta"])
    # 환율: delta_color="inverse" (하락 시 파랑/초록으로 긍정 표시)
    idx3.metric("원/달러 환율", m_data["환율"]["val"], m_data["환율"]["delta"], delta_color="inverse")
    # 거래량: 색상 변화 없이 표시
    idx4.metric("코스피 거래량", m_data["거래량"]["val"], m_data["거래량"]["delta"], delta_color="off")
else:
    st.warning("시장 지표를 불러오는 중입니다... (잠시 후 새로고침)")

st.markdown("<hr style='border: 0.5px solid #eee;'>", unsafe_allow_html=True)

# 자산 핵심 KPI
k1, k2, k3, k4 = st.columns(4)
k1.metric("월 사적 수입", f"{current_total:,.0f}원")
k2.metric("목표 달성률", f"{achievement:.1f}%", delta=f"{achievement-100:.1f}%")
k3.metric("자산 평가액", f"{private_assets['현재 가치'].sum():,.0f}원")
k4.metric("세금 성격", "절세 중심", delta="비과세/이연", delta_color="normal")

# 5. 시각화 탭 (가족 관제탑 심미성 적용)
st.markdown("<br>", unsafe_allow_html=True)
t1, t2, t3, t4 = st.tabs(["📊 자산 구조", "🌊 수입 폭포", "📅 입금 일정", "🛡️ 세금 보안"])

with t1:
    fig_sun = px.sunburst(private_assets, path=['계좌 유형', '투자성격', '종목명'], values='투자원금',
                          color='투자성격', color_discrete_map={'안전':'#0D47A1', '위험':'#B71C1C'},
                          template="plotly_white")
    st.plotly_chart(fig_sun, use_container_width=True)

with t2:
    fig_water = go.Figure(go.Waterfall(
        measure = ["relative"] * len(private_assets) + ["total"],
        x = list(private_assets['종목명']) + ["총 수입"],
        y = list(private_assets['목표인출액']) + [0],
        text = [f"{v/10000:,.1f}만" for v in private_assets['목표인출액']] + [f"{current_total/10000:,.1f}만"],
        connector = {"line":{"color":"#ddd"}},
    ))
    fig_water.add_hline(y=TARGET_PRIVATE, line_dash="dash", line_color="red", annotation_text="목표 340만")
    fig_water.update_layout(title="340만 원 목표 달성 엔진", template="plotly_white")
    st.plotly_chart(fig_water, use_container_width=True)

with t3:
    sched_df = private_assets.sort_values('입금예정일')
    fig_bar = px.bar(sched_df, x='입금예정일', y='목표인출액', color='계좌 유형', text='종목명',
                     color_discrete_sequence=px.colors.qualitative.Safe)
    fig_bar.update_layout(xaxis_type='category', title="월간 현금 유입 일정")
    st.plotly_chart(fig_bar, use_container_width=True)

with t4:
    tax_df = private_assets.groupby('세금성격')['투자원금'].sum().reset_index()
    fig_tax = px.pie(tax_df, values='투자원금', names='세금성격', hole=0.5,
                     color_discrete_sequence=px.colors.sequential.RdBu)
    st.plotly_chart(fig_tax, use_container_width=True)
