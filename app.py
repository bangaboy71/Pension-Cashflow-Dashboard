import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
from bs4 import BeautifulSoup

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
    st.error(f"데이터 로드 실패: {e}")
    st.stop()

# 사적 자산 필터링 (340만 목표)
private_assets = df[df['계좌 유형'].isin(['IRP', 'ISA', '일반'])].copy()
TARGET_PRIVATE = 3400000
current_total = private_assets['목표인출액'].sum()
achievement = (current_total / TARGET_PRIVATE) * 100

# ---------------------------------------------------------
# 3. 시장 지표 엔진 (가족 관제탑 로직 반영 및 정밀화)
# ---------------------------------------------------------
@st.cache_data(ttl=600)
def get_market_status():
    data = {
        "KOSPI": {"val": "-", "delta": "0.00"},
        "KOSDAQ": {"val": "-", "delta": "0.00"},
        "USD/KRW": {"val": "-", "delta": "0원"},
        "VOLUME": {"val": "-", "delta": "천주"}
    }
    header = {'User-Agent': 'Mozilla/5.0'}
    try:
        # 코스피 & 코스닥 & 거래량
        for code in ["KOSPI", "KOSDAQ"]:
            url = f"https://finance.naver.com/sise/sise_index.naver?code={code}"
            res = requests.get(url, headers=header, timeout=5)
            res.encoding = 'euc-kr'
            soup = BeautifulSoup(res.text, 'html.parser')
            
            val = soup.select_one("#now_value").get_text(strip=True)
            diff = soup.select_one("#change_value_and_rate").get_text(" ", strip=True)
            for word in ["상승", "하락", "보합"]: diff = diff.replace(word, "")
            
            data[code]["val"] = val
            data[code]["delta"] = diff
            
            if code == "KOSPI":
                data["VOLUME"]["val"] = soup.select_one("#quant").get_text(strip=True)

        # 환율
        ex_res = requests.get("https://finance.naver.com/marketindex/", headers=header, timeout=5)
        ex_soup = BeautifulSoup(ex_res.text, 'html.parser')
        ex_val = ex_soup.select_one("span.value").get_text(strip=True)
        ex_change = ex_soup.select_one("span.change").get_text(strip=True)
        ex_blind = ex_soup.select_one("div.head_info > span.blind").get_text()
        sign = "-" if "하락" in ex_blind else ("+" if "상승" in ex_blind else "")
        
        data["USD/KRW"]["val"] = ex_val
        data["USD/KRW"]["delta"] = f"{sign}{ex_change}원"
    except: pass
    return data

# ---------------------------------------------------------
# 4. 화면 구성 (디자인 및 Metric 연동)
# ---------------------------------------------------------
# 제목 중앙 정렬
st.markdown("<h2 style='text-align: center; color: #1E3A8A;'>🛡️ 현금흐름 통합 관제탑</h2>", unsafe_allow_html=True)
st.markdown(f"<p style='text-align: center; color: #666;'>사적 자산 목표: <b>월 {TARGET_PRIVATE/10000:,.0f}만 원</b> (공적연금 제외)</p>", unsafe_allow_html=True)

# 시장 지표 데이터 가져오기
m_data = get_market_status()

# 시장 지표 섹션 (4열 배치)
st.markdown("<br>", unsafe_allow_html=True)
idx1, idx2, idx3, idx4 = st.columns(4)
idx1.metric("KOSPI", m_data["KOSPI"]["val"], m_data["KOSPI"]["delta"])
idx2.metric("KOSDAQ", m_data["KOSDAQ"]["val"], m_data["KOSDAQ"]["delta"])
# 환율: delta_color="inverse" 적용 (내려갈 때 파란색이 아닌 긍정적 의미 부여 가능)
idx3.metric("원/달러 환율", m_data["USD/KRW"]["val"], m_data["USD/KRW"]["delta"], delta_color="inverse")
idx4.metric("코스피 거래량", m_data["VOLUME"]["val"], m_data["VOLUME"]["delta"])

st.markdown("<hr style='border: 0.5px solid #eee;'>", unsafe_allow_html=True)

# 자산 핵심 KPI
k1, k2, k3, k4 = st.columns(4)
k1.metric("월 사적 수입", f"{current_total:,.0f}원")
k2.metric("목표 달성률", f"{achievement:.1f}%", delta=f"{achievement-100:.1f}%")
k3.metric("사적 자산 평가액", f"{private_assets['현재 가치'].sum():,.0f}원")
k4.metric("세금 성격", "절세 중심", delta="비과세/이연")

# 5. 고도화 시각화 탭
st.markdown("<br>", unsafe_allow_html=True)
t1, t2, t3, t4 = st.tabs(["📊 자산 구조", "🌊 수입 폭포", "📅 입금 일정", "🛡️ 세금 보안"])

with t1:
    st.subheader("계층형 포트폴리오 분석")
    fig_sun = px.sunburst(private_assets, path=['계좌 유형', '투자성격', '종목명'], values='투자원금',
                          color='투자성격', color_discrete_map={'안전':'#0D47A1', '위험':'#B71C1C'},
                          template="plotly_white")
    st.plotly_chart(fig_sun, use_container_width=True)

with t2:
    st.subheader("사적 자산 수입 엔진 (목표 340만)")
    fig_water = go.Figure(go.Waterfall(
        measure = ["relative"] * len(private_assets) + ["total"],
        x = list(private_assets['종목명']) + ["총 수입"],
        y = list(private_assets['목표인출액']) + [0],
        text = [f"{v/10000:,.1f}만" for v in private_assets['목표인출액']] + [f"{current_total/10000:,.1f}만"],
        connector = {"line":{"color":"#ddd"}},
    ))
    fig_water.add_hline(y=TARGET_PRIVATE, line_dash="dash", line_color="red", annotation_text="목표 340만")
    st.plotly_chart(fig_water, use_container_width=True)

with t3:
    st.subheader("월간 현금 유입 스케줄")
    sched_df = private_assets.sort_values('입금예정일')
    fig_bar = px.bar(sched_df, x='입금예정일', y='목표인출액', color='계좌 유형', text='종목명')
    fig_bar.update_layout(xaxis_type='category')
    st.plotly_chart(fig_bar, use_container_width=True)

with t4:
    st.subheader("세금 방어막 현황")
    tax_df = private_assets.groupby('세금성격')['투자원금'].sum().reset_index()
    fig_tax = px.pie(tax_df, values='투자원금', names='세금성격', hole=0.5)
    st.plotly_chart(fig_tax, use_container_width=True)
