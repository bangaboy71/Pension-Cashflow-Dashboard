import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import requests
from bs4 import BeautifulSoup
import plotly.express as px
import plotly.graph_objects as go
import yfinance as yf  # <--- 이 부분이 NameError를 해결합니다.
from datetime import datetime

# 1. 페이지 설정 및 UI 스타일 (가족 관제탑 스타일)
st.set_page_config(page_title="현금흐름 340만 관제탑", layout="wide")

st.markdown("""
    <style>
    [data-testid="stMetricValue"] { font-size: 1.8rem !important; font-weight: bold !important; }
    .market-box { text-align: center; padding: 15px; border-radius: 12px; background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.1); }
    .market-title { color: #aaa; font-size: 0.85rem; font-weight: bold; margin-bottom: 5px; }
    .strategy-card { padding: 20px; border-radius: 12px; background: rgba(255,255,255,0.02); text-align: center; height: 180px; }
    </style>
    """, unsafe_allow_html=True)

# 2. 데이터 로드 및 정제
conn = st.connection("gsheets", type=GSheetsConnection)
try:
    df = conn.read(ttl=0)
    df.columns = df.columns.str.strip().str.replace('\ufeff', '', regex=False)
    
    numeric_cols = ['투자원금', '현재 가치', '목표인출액', '매입단가', '목표가', '수량']
    for col in numeric_cols:
        if col in df.columns:
            df[col] = df[col].astype(str).str.replace(',', '').str.replace('원', '').str.strip()
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
except Exception as e:
    st.error(f"데이터 로드 실패: {e}")
    st.stop()

# 사적 자산 필터링
private_assets = df[df['계좌 유형'].isin(['IRP', 'ISA', '일반'])].copy()
TARGET_PRIVATE = 3400000
current_total = private_assets['목표인출액'].sum()

# ---------------------------------------------------------
# 3. 시장 지표 엔진 (가족 관제탑 로직)
# ---------------------------------------------------------
@st.cache_data(ttl=600)
def get_market_status():
    data = {
        "KOSPI": {"val": "-", "pct": "0.00%", "color": "#ffffff"},
        "KOSDAQ": {"val": "-", "pct": "0.00%", "color": "#ffffff"},
        "USD/KRW": {"val": "-", "pct": "0원", "color": "#ffffff"},
        "VOLUME": {"val": "-", "pct": "천주", "color": "#ffffff"}
    }
    header = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.naver.com/'}
    try:
        for code in ["KOSPI", "KOSDAQ"]:
            url = f"https://finance.naver.com/sise/sise_index.naver?code={code}"
            res = requests.get(url, headers=header, timeout=5); res.encoding = 'euc-kr'
            soup = BeautifulSoup(res.text, 'html.parser')
            now_el = soup.select_one("#now_value")
            if now_el: data[code]["val"] = now_el.get_text(strip=True)
            diff_el = soup.select_one("#change_value_and_rate")
            if diff_el:
                raw_txt = diff_el.get_text(" ", strip=True)
                for word in ["상승", "하락", "보합"]: raw_txt = raw_txt.replace(word, "")
                if "+" in raw_txt: data[code]["color"] = "#FF4B4B"
                elif "-" in raw_txt: data[code]["color"] = "#87CEEB"
                data[code]["pct"] = raw_txt.strip()
            if code == "KOSPI":
                vol_el = soup.select_one("#quant")
                if vol_el: data["VOLUME"]["val"] = vol_el.get_text(strip=True)
        # 환율
        ex_res = requests.get("https://finance.naver.com/marketindex/", headers=header, timeout=5)
        ex_soup = BeautifulSoup(ex_res.text, 'html.parser')
        ex_val = ex_soup.select_one("span.value")
        if ex_val:
            data["USD/KRW"]["val"] = ex_val.get_text(strip=True)
            ex_change = ex_soup.select_one("span.change").get_text(strip=True)
            ex_blind = ex_soup.select_one("div.head_info > span.blind").get_text()
            sign = "+" if "상승" in ex_blind else ("-" if "하락" in ex_blind else "")
            data["USD/KRW"]["color"] = "#FF4B4B" if sign == "+" else "#87CEEB"
            data["USD/KRW"]["pct"] = f"{sign}{ex_change}원"
    except: pass
    return data

# ---------------------------------------------------------
# 4. 화면 구성 (HUD 및 메인 지표)
# ---------------------------------------------------------
st.markdown("<h2 style='text-align: center; color: #87CEEB;'>🛡️ 현금흐름 통합 관제탑</h2>", unsafe_allow_html=True)
m_status = get_market_status()
h1, h2, h3, h4 = st.columns(4)
keys, titles = ["KOSPI", "KOSDAQ", "USD/KRW", "VOLUME"], ["KOSPI", "KOSDAQ", "USD/KRW", "MARKET VOL"]
for i, col in enumerate([h1, h2, h3, h4]):
    d = m_status[keys[i]]
    col.markdown(f"""<div class="market-box" style="border: 1px solid {d['color']}44;"><div class="market-title">{titles[i]}</div><div style="color: {d['color']}; font-size: 1.8rem; font-weight: bold;">{d['val']}</div><div style="color: {d['color']}; font-size: 1.0rem;">{d['pct']}</div></div>""", unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)
k1, k2, k3, k4 = st.columns(4)
k1.metric("월 수입", f"{current_total:,.0f}원")
k2.metric("목표 달성률", f"{(current_total/TARGET_PRIVATE*100):.1f}%", delta=f"{(current_total-TARGET_PRIVATE):+,.0f}원")
k3.metric("자산 평가액", f"{private_assets['현재 가치'].sum():,.0f}원")
k4.metric("세금 성격", "절세 중심", delta="비과세/이연")

# ---------------------------------------------------------
# 5. [신규] 리스크 관리 및 모니터링 섹션
# ---------------------------------------------------------
st.markdown("---")
st.markdown("### 📈 자산 모니터링 및 전략 관제")

# (1) 주가 추이 모니터링
with st.expander("🔍 종목별 6개월 주가 추이 (클릭하여 확장)"):
    t_cols = st.columns(len(private_assets))
    for i, (idx, row) in enumerate(private_assets.iterrows()):
        with t_cols[i]:
            ticker = row['종목코드']
            hist = yf.download(ticker, period="6mo", interval="1d", progress=False)
            if not hist.empty:
                fig = px.line(hist, y='Close', title=f"{row['종목명']}")
                fig.update_layout(height=180, margin=dict(l=0,r=0,t=30,b=0), xaxis_title="", yaxis_title="")
                st.plotly_chart(fig, use_container_width=True)

# (2) 원금 손실 최소화 가이드 및 비중 분석
st.markdown("<br>", unsafe_allow_html=True)
total_v = private_assets['현재 가치'].sum()
s_cols = st.columns(len(private_assets))

for i, (idx, row) in enumerate(private_assets.iterrows()):
    curr_p = row['현재 가치'] / row['수량'] if row['수량'] > 0 else 0
    buy_p, target_p = row['매입단가'], row['목표가']
    weight = (row['현재 가치'] / total_v * 100)
    
    # 전략 판정
    if curr_p < buy_p * 0.9: sig, clr = "⚠️ 손절 검토", "#FF4B4B"
    elif curr_p >= target_p and target_p > 0: sig, clr = "✅ 이익 실현", "#87CEEB"
    else: sig, clr = "⚓ 보유/관찰", "#aaa"

    with s_cols[i]:
        st.markdown(f"""
            <div class="strategy-card" style="border: 1px solid {clr}44;">
                <div style="color: #aaa; font-size: 0.8rem;">{row['종목명']}</div>
                <div style="color: {clr}; font-size: 1.4rem; font-weight: bold; margin: 10px 0;">{sig}</div>
                <div style="font-size: 0.85rem;">비중: {weight:.1f}%</div>
                <div style="font-size: 0.75rem; color: #666; margin-top: 5px;">매입가: {buy_p:,.0f}<br>목표가: {target_p:,.0f}</div>
            </div>
        """, unsafe_allow_html=True)

# ---------------------------------------------------------
# 6. 시각화 탭
# ---------------------------------------------------------
st.markdown("<br>", unsafe_allow_html=True)
t1, t2, t3 = st.tabs(["📊 자산 구조", "🌊 수입 폭포", "📅 입금 일정"])
with t1:
    fig_sun = px.sunburst(private_assets, path=['계좌 유형', '투자성격', '종목명'], values='투자원금',
                          color='투자성격', color_discrete_map={'안전':'#0D47A1', '위험':'#B71C1C'}, template="plotly_white")
    st.plotly_chart(fig_sun, use_container_width=True)
with t2:
    fig_water = go.Figure(go.Waterfall(measure=["relative"]*len(private_assets)+["total"], x=list(private_assets['종목명'])+["총 수입"], y=list(private_assets['목표인출액'])+[0], text=[f"{v/10000:,.1f}만" for v in private_assets['목표인출액']]+[f"{current_total/10000:,.1f}만"], connector={"line":{"color":"#ddd"}}))
    fig_water.update_layout(title="340만 원 목표 달성 엔진", template="plotly_white")
    st.plotly_chart(fig_water, use_container_width=True)
with t3:
    sched_df = private_assets.sort_values('입금예정일')
    fig_bar = px.bar(sched_df, x='입금예정일', y='목표인출액', color='계좌 유형', text='종목명', color_discrete_sequence=px.colors.qualitative.Safe)
    fig_bar.update_layout(xaxis_type='category', title="월간 현금 유입 일정")
    st.plotly_chart(fig_bar, use_container_width=True)
