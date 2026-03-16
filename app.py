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
st.set_page_config(page_title="현금흐름 340만 관제탑", layout="wide")

st.markdown("""
    <style>
    [data-testid="stMetricValue"] { font-size: 1.8rem !important; font-weight: bold !important; }
    .market-box { text-align: center; padding: 15px; border-radius: 12px; background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.1); }
    .market-title { color: #aaa; font-size: 0.85rem; font-weight: bold; margin-bottom: 5px; }
    .strategy-card { padding: 15px; border-radius: 12px; background: rgba(255,255,255,0.02); text-align: center; border: 1px solid rgba(255,255,255,0.1); margin-bottom: 10px; }
    .section-title { font-size: 1.1rem; font-weight: bold; color: #87CEEB; margin-bottom: 15px; border-left: 4px solid #87CEEB; padding-left: 10px; }
    .price-label { font-size: 0.75rem; color: #888; margin-top: 8px; }
    .price-value { font-size: 1.1rem; font-weight: bold; color: #FFD700; }
    </style>
    """, unsafe_allow_html=True)

# 2. 데이터 로드 및 정제
conn = st.connection("gsheets", type=GSheetsConnection)
try:
    df = conn.read(ttl=0)
    df.columns = df.columns.str.strip().str.replace('\ufeff', '', regex=False)
    
    # 필수 숫자형 데이터 세척 (수량, 매입단가, 종목코드 등이 중요)
    numeric_cols = ['투자원금', '수량', '매입단가', '목표가', '수익률(%)']
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', '').str.replace('%', ''), errors='coerce').fillna(0)
except Exception as e:
    st.error(f"데이터 로드 실패: {e}")
    st.stop()

# 사적 자산 필터링 및 이름 정제
private_assets = df[df['계좌 유형'].isin(['IRP', 'ISA', '일반'])].copy()
private_assets['종목명'] = private_assets['종목명'].str.strip()
private_assets['종목코드'] = private_assets['종목코드'].str.strip()

# ---------------------------------------------------------
# 3. [신규] 실시간 현재가 수집 엔진 (yfinance)
# ---------------------------------------------------------
@st.cache_data(ttl=600)
def get_realtime_prices(ticker_list):
    if not ticker_list: return pd.Series()
    try:
        # 야후 파이낸스에서 일괄 수집
        data = yf.download(ticker_list, period="1d", interval="1m", progress=False)
        if isinstance(data.columns, pd.MultiIndex):
            return data['Close'].iloc[-1]
        # 종목이 하나일 경우 처리
        return pd.Series({ticker_list[0]: data['Close'].iloc[-1]})
    except:
        return pd.Series()

# 현재가 수집 및 실시간 가치 산출
tickers = private_assets['종목코드'].unique().tolist()
current_prices = get_realtime_prices(tickers)

# 실시간 데이터를 데이터프레임에 매핑
private_assets['현재가'] = private_assets['종목코드'].map(current_prices).fillna(0)
# [핵심] 현재 가치를 '현재가 * 수량'으로 실시간 계산 (시트 결측 방어)
private_assets['현재 가치'] = private_assets['현재가'] * private_assets['수량']

# ---------------------------------------------------------
# 4. [시뮬레이션 엔진] 사이드바 설정
# ---------------------------------------------------------
TARGET_PRIVATE = 3400000
with st.sidebar:
    st.markdown("### 📉 수익률 시뮬레이션")
    st.info("슬라이더 조절 시 실시간 가치 기반으로 수입이 재계산됩니다.")
    
    sim_settings = {}
    for _, row in private_assets.iterrows():
        s_name = row['종목명']
        default_rate = float(row.get('수익률(%)', 5.0))
        sim_settings[s_name] = st.slider(f"{s_name} (%)", 0.0, 20.0, default_rate, 0.1)
    
    if st.button("🔄 설정 초기화"):
        st.rerun()

# 시뮬레이션 결과 산출 (실시간 가치 기준)
sim_assets = private_assets.copy()
sim_assets['목표인출액'] = sim_assets.apply(
    lambda x: (x['현재 가치'] * (sim_settings[x['종목명']] / 100) / 12), axis=1
)

sim_total_income = sim_assets['목표인출액'].sum()
sim_achievement = (sim_total_income / TARGET_PRIVATE * 100)
# 원본(시트 수익률 기준) 합계 계산
actual_total_income = private_assets.apply(
    lambda x: (x['현재 가치'] * (x.get('수익률(%)', 0) / 100) / 12), axis=1
).sum()

# ---------------------------------------------------------
# 5. 시장 지표 엔진 및 HUD (네이버 크롤링)
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
# 6. 화면 출력 (KPI 및 전략 모니터)
# ---------------------------------------------------------
st.markdown("<h2 style='text-align: center; color: #87CEEB;'>🛡️ 실시간 통합 현금흐름 관제탑</h2>", unsafe_allow_html=True)

# HUD 렌더링
m_data = get_market_status()
h1, h2, h3, h4 = st.columns(4)
keys, titles = ["KOSPI", "KOSDAQ", "USD/KRW", "VOLUME"], ["KOSPI", "KOSDAQ", "USD/KRW", "MARKET VOL"]
for i, col in enumerate([h1, h2, h3, h4]):
    d = m_data[keys[i]]
    col.markdown(f"""<div class="market-box" style="border: 1px solid {d['color']}44;"><div class="market-title">{titles[i]}</div><div style="color: {d['color']}; font-size: 1.8rem; font-weight: bold;">{d['val']}</div><div style="color: {d['color']}; font-size: 1.0rem;">{d['delta']}</div></div>""", unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# 메인 KPI - 실시간 데이터 기반
k1, k2, k3, k4 = st.columns(4)
k1.metric("월 예상 수입 (Sim)", f"{sim_total_income:,.0f}원", delta=f"{sim_total_income - actual_total_income:,.0f}원")
k2.metric("목표 달성률 (Sim)", f"{sim_achievement:.1f}%", delta=f"{(sim_total_income - TARGET_PRIVATE):+,.0f}원")
k3.metric("실시간 자산 가치", f"{sim_assets['현재 가치'].sum():,.0f}원")
k4.metric("세금 성격", "절세 중심", delta="비과세/이연")

# 7. 주가 추이 및 전략 카드 (현재가 표출)
st.markdown("---")
with st.expander("🔍 보유 종목별 실시간 주가 및 리스크 판정"):
    t_cols = st.columns(len(sim_assets))
    for i, (idx, row) in enumerate(sim_assets.iterrows()):
        with t_cols[i]:
            ticker = row['종목코드']
            # 주가 차트 (yfinance)
            hist = yf.download(ticker, period="6mo", interval="1d", progress=False)
            if not hist.empty:
                if isinstance(hist.columns, pd.MultiIndex): hist.columns = hist.columns.get_level_values(0)
                fig = px.line(hist, y='Close', title=f"{row['종목명']}")
                fig.update_layout(height=150, margin=dict(l=0,r=0,t=30,b=0), xaxis_title="", yaxis_title="")
                st.plotly_chart(fig, use_container_width=True)
            
            curr_p, buy_p, target_p = row['현재가'], row['매입단가'], row['목표가']
            # 리스크 판정 로직
            if curr_p < buy_p * 0.9: sig, clr = "⚠️ 손절 검토", "#FF4B4B"
            elif curr_p >= target_p and target_p > 0: sig, clr = "✅ 이익 실현", "#87CEEB"
            else: sig, clr = "⚓ 보유/관찰", "#aaa"
            
            # 전략 카드 렌더링 (현재가 포함)
            st.markdown(f"""
                <div class="strategy-card" style="border: 1px solid {clr}44;">
                    <div style="color: #aaa; font-size: 0.8rem;">{row['종목명']}</div>
                    <div style="color: {clr}; font-size: 1.2rem; font-weight: bold; margin: 5px 0;">{sig}</div>
                    <div class="price-label">실시간 현재가</div>
                    <div class="price-value">{curr_p:,.0f}원</div>
                    <div style="font-size: 0.8rem; margin-top:5px; color:#999;">비중: {(row['현재 가치']/sim_assets['현재 가치'].sum()*100):.1f}%</div>
                </div>
            """, unsafe_allow_html=True)

# 8. 하단 3단 차트 (실시간 데이터 연동)
st.markdown("<br>", unsafe_allow_html=True)
col_a, col_b, col_c = st.columns(3)

with col_a:
    st.markdown("<div class='section-title'>📊 실시간 자산 비중</div>", unsafe_allow_html=True)
    fig_sun = px.sunburst(sim_assets, path=['계좌 유형', '투자성격', '종목명'], values='현재 가치',
                          color='투자성격', color_discrete_map={'안전':'#0D47A1', '위험':'#B71C1C'}, template="plotly_white")
    fig_sun.update_layout(height=400, margin=dict(l=0, r=0, t=0, b=0))
    st.plotly_chart(fig_sun, use_container_width=True)

with col_b:
    st.markdown("<div class='section-title'>🌊 수입 엔진 기여도</div>", unsafe_allow_html=True)
    fig_water = go.Figure(go.Waterfall(
        measure = ["relative"] * len(sim_assets) + ["total"],
        x = list(sim_assets['종목명']) + ["총 합계"],
        y = list(sim_assets['목표인출액']) + [0],
        text = [f"{v/10000:,.0f}만" for v in sim_assets['목표인출액']] + [f"{sim_total_income/10000:,.0f}만"],
        connector = {"line":{"color":"#ddd"}},
    ))
    fig_water.update_layout(height=400, margin=dict(l=10, r=10, t=20, b=0), template="plotly_white")
    st.plotly_chart(fig_water, use_container_width=True)

with col_c:
    st.markdown("<div class='section-title'>📅 월간 입금 스케줄</div>", unsafe_allow_html=True)
    sched_df = sim_assets.sort_values('입금예정일')
    fig_bar = px.bar(sched_df, x='입금예정일', y='목표인출액', color='계좌 유형', text='종목명',
                     color_discrete_sequence=px.colors.qualitative.Safe)
    fig_bar.update_layout(height=400, xaxis_type='category', margin=dict(l=0, r=0, t=20, b=0), template="plotly_white")
    st.plotly_chart(fig_bar, use_container_width=True)
