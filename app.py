import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import requests
from bs4 import BeautifulSoup
import plotly.express as px
import plotly.graph_objects as go
import yfinance as yf
from datetime import datetime

# 1. 페이지 설정 및 UI 스타일
st.set_page_config(page_title="현금흐름 방어 관제탑", layout="wide")

st.markdown("""
    <style>
    [data-testid="stMetricValue"] { font-size: 1.6rem !important; font-weight: bold !important; }
    .market-box { text-align: center; padding: 12px; border-radius: 12px; background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.1); }
    .market-title { color: #aaa; font-size: 0.85rem; font-weight: bold; margin-bottom: 5px; }
    .status-card { padding: 20px; border-radius: 12px; background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.1); height: 100%; }
    .section-title { font-size: 1.1rem; font-weight: bold; color: #87CEEB; margin-bottom: 15px; border-left: 4px solid #87CEEB; padding-left: 10px; }
    </style>
    """, unsafe_allow_html=True)

# 2. 데이터 로드 및 기초 설정
conn = st.connection("gsheets", type=GSheetsConnection)
try:
    df = conn.read(ttl=0)
    df.columns = df.columns.str.strip().str.replace('\ufeff', '', regex=False)
    
    numeric_cols = ['투자원금', '수량', '매입단가', '목표가', '수익률(%)']
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', '').str.replace('%', ''), errors='coerce').fillna(0)
except Exception as e:
    st.error(f"데이터 로드 실패: {e}")
    st.stop()

# 고정 인출 목표 (IRP 290만, ISA 40만, 일반 10만)
WITHDRAWAL_TARGETS = {"IRP": 2900000, "ISA": 400000, "일반": 100000}
TOTAL_WITHDRAWAL = sum(WITHDRAWAL_TARGETS.values())

# 사적 자산 필터링
private_assets = df[df['계좌 유형'].isin(['IRP', 'ISA', '일반'])].copy()
private_assets['종목명'] = private_assets['종목명'].str.strip()

# ---------------------------------------------------------
# 3. [복구] 시장 지표 엔진 (네이버 크롤링)
# ---------------------------------------------------------
@st.cache_data(ttl=600)
def get_market_status():
    data = {"KOSPI": {"val": "-", "delta": "0.00", "color": "#ffffff"}, "KOSDAQ": {"val": "-", "delta": "0.00", "color": "#ffffff"}, "USD/KRW": {"val": "-", "delta": "0원", "color": "#ffffff"}, "VOLUME": {"val": "-", "delta": "천주", "color": "#ffffff"}}
    header = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.naver.com/'}
    try:
        for code in ["KOSPI", "KOSDAQ"]:
            url = f"https://finance.naver.com/sise/sise_index.naver?code={code}"
            res = requests.get(url, headers=header, timeout=5); res.encoding = 'euc-kr'
            soup = BeautifulSoup(res.text, 'html.parser')
            data[code]["val"] = soup.select_one("#now_value").get_text(strip=True)
            diff_raw = soup.select_one("#change_value_and_rate").get_text(" ", strip=True)
            for w in ["상승", "하락", "보합"]: diff_raw = diff_raw.replace(w, "")
            data[code]["delta"] = diff_raw.strip()
            if "+" in diff_raw: data[code]["color"] = "#FF4B4B"
            elif "-" in diff_raw: data[code]["color"] = "#87CEEB"
            if code == "KOSPI": data["VOLUME"]["val"] = soup.select_one("#quant").get_text(strip=True)
        ex_res = requests.get("https://finance.naver.com/marketindex/", headers=header, timeout=5)
        ex_soup = BeautifulSoup(ex_res.text, 'html.parser')
        ex_val = ex_soup.select_one("span.value").get_text(strip=True)
        ex_change = ex_soup.select_one("span.change").get_text(strip=True)
        ex_blind = ex_soup.select_one("div.head_info > span.blind").get_text()
        sign = "+" if "상승" in ex_blind else ("-" if "하락" in ex_blind else "")
        data["USD/KRW"]["val"] = ex_val
        data["USD/KRW"]["delta"] = f"{sign}{ex_change}원"
        data["USD/KRW"]["color"] = "#FF4B4B" if sign == "+" else "#87CEEB"
    except: pass
    return data

# ---------------------------------------------------------
# 4. 실시간 현재가 및 시뮬레이션 엔진
# ---------------------------------------------------------
with st.sidebar:
    st.markdown("### ⚙️ 수익률 시뮬레이션")
    sim_rates = {}
    for _, row in private_assets.iterrows():
        name = row['종목명']
        d_rate = float(row.get('수익률(%)', 5.0))
        sim_rates[name] = st.slider(f"{name} (%)", 0.0, 20.0, d_rate, 0.1)

@st.cache_data(ttl=600)
def fetch_prices(tickers):
    data = yf.download(tickers, period="1y", interval="1d", progress=False)
    return data['Close'] if isinstance(data.columns, pd.MultiIndex) else pd.DataFrame({tickers[0]: data['Close']})

tickers = private_assets['종목코드'].unique().tolist()
price_hist = fetch_prices(tickers)
curr_prices = price_hist.iloc[-1]

# 데이터 바인딩 및 현금흐름 계산
sim_assets = private_assets.copy()
sim_assets['현재가'] = sim_assets['종목코드'].map(curr_prices)
sim_assets['현재가치'] = sim_assets['현재가'] * sim_assets['수량']
sim_assets['예상수입'] = sim_assets.apply(lambda x: (x['현재가치'] * sim_rates[x['종목명']] / 100 / 12), axis=1)

# 계좌별 요약 (원금 침식 분석)
summary = sim_assets.groupby('계좌 유형').agg({'예상수입':'sum', '현재가치':'sum'}).reset_index()
summary['인출목표'] = summary['계좌 유형'].map(WITHDRAWAL_TARGETS)
summary['원금침식액'] = (summary['인출목표'] - summary['예상수입']).clip(lower=0)

# ---------------------------------------------------------
# 5. 메인 레이아웃 (HUD -> KPI -> 분석 -> 탭)
# ---------------------------------------------------------
st.markdown("<h2 style='text-align: center; color: #87CEEB;'>🛡️ 현금흐름 방어 및 실시간 관제탑</h2>", unsafe_allow_html=True)

# [시장 HUD]
m_data = get_market_status()
h1, h2, h3, h4 = st.columns(4)
keys, titles = ["KOSPI", "KOSDAQ", "USD/KRW", "VOLUME"], ["KOSPI", "KOSDAQ", "USD/KRW", "MARKET VOL"]
for i, col in enumerate([h1, h2, h3, h4]):
    d = m_data[keys[i]]
    col.markdown(f"""<div class="market-box" style="border: 1px solid {d['color']}44;"><div class="market-title">{titles[i]}</div><div style="color: {d['color']}; font-size: 1.8rem; font-weight: bold;">{d['val']}</div><div style="color: {d['color']}; font-size: 1.0rem;">{d['delta']}</div></div>""", unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# [상단 KPI]
k1, k2, k3, k4 = st.columns(4)
total_inc = summary['예상수입'].sum()
total_erosion = summary['원금침식액'].sum()
k1.metric("월 예상 총 수입", f"{total_inc:,.0f}원", delta=f"{total_inc - TOTAL_WITHDRAWAL:,.0f}원")
k2.metric("월 원금 침식액", f"{total_erosion:,.0f}원", delta=f"-{total_erosion:,.0f}", delta_color="inverse")
k3.metric("실시간 자산가치", f"{summary['현재가치'].sum():,.0f}원")
k4.metric("방어 상태", "안전" if total_erosion == 0 else "주의")

st.markdown("---")

# [현금흐름 분석 섹션]
c_left, c_right = st.columns([3, 2])
with c_left:
    st.markdown("<div class='section-title'>📊 계좌별 현금흐름 방어 현황</div>", unsafe_allow_html=True)
    st.table(summary[['계좌 유형', '인출목표', '예상수입', '원금침식액']].style.format({
        '인출목표': '{:,.0f}원', '예상수입': '{:,.0f}원', '원금침식액': '{:,.0f}원'
    }))

with c_right:
    st.markdown("<div class='section-title'>🌊 수입 vs 원금침식 비중</div>", unsafe_allow_html=True)
    fig_pie = go.Figure(data=[go.Pie(labels=['실제수입', '원금침식'], values=[total_inc, total_erosion], 
                                     hole=.5, marker_colors=['#87CEEB', '#FF4B4B'])])
    fig_pie.update_layout(height=250, margin=dict(l=0,r=0,t=0,b=0))
    st.plotly_chart(fig_pie, use_container_width=True)

st.markdown("---")

# [종목별 탭 분석 섹션]
st.markdown("<div class='section-title'>🔍 종목별 딥다이브 관제 (추이 & 리스크)</div>", unsafe_allow_html=True)
tabs = st.tabs(sim_assets['종목명'].tolist())

for i, tab in enumerate(tabs):
    with tab:
        row = sim_assets.iloc[i]
        t_code = row['종목코드']
        col_chart, col_risk = st.columns([2, 1])
        
        with col_chart:
            period = st.radio("기간", ["3M", "6M", "1Y"], horizontal=True, key=f"btn_{t_code}")
            days = {"3M": 90, "6M": 180, "1Y": 365}[period]
            fig_l = px.line(price_hist[t_code].tail(days), title=f"{row['종목명']} ({period})")
            fig_l.update_layout(height=350, template="plotly_white", margin=dict(t=30, b=0))
            st.plotly_chart(fig_l, use_container_width=True)
            
        with col_risk:
            curr_p, buy_p, target_p = row['현재가'], row['매입단가'], row['목표가']
            if curr_p < buy_p * 0.9: msg, clr = "🚨 원금방어 경보", "#FF4B4B"
            elif curr_p >= target_p and target_p > 0: msg, clr = "💰 익절 검토", "#87CEEB"
            else: msg, clr = "✅ 정상 보유", "#aaa"
            
            st.markdown(f"""
                <div class="status-card" style="border: 1px solid {clr}66;">
                    <div style="font-size: 0.85rem; color: #888;">{row['종목명']}</div>
                    <div style="font-size: 1.4rem; font-weight: bold; color: {clr}; margin: 15px 0;">{msg}</div>
                    <hr style="border: 0.1px solid #333;">
                    <div style="display: flex; justify-content: space-between; margin-bottom: 8px;">
                        <span>현재가</span><span style="color: #FFD700; font-weight: bold;">{curr_p:,.0f}원</span>
                    </div>
                    <div style="display: flex; justify-content: space-between; margin-bottom: 8px;">
                        <span>매입가</span><span>{buy_p:,.0f}원</span>
                    </div>
                    <div style="display: flex; justify-content: space-between;">
                        <span>수익률</span><span style="color: {'#FF4B4B' if curr_p < buy_p else '#87CEEB'};">
                        {((curr_p/buy_p-1)*100):+.2f}%</span>
                    </div>
                    <div style="margin-top: 15px; font-size: 0.8rem; color: #666; border-top: 1px solid #333; padding-top: 10px;">
                        월 예상 기여: {row['예상수입']:,.0f}원
                    </div>
                </div>
            """, unsafe_allow_html=True)
