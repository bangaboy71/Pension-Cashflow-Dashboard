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
    .strategy-card { padding: 20px; border-radius: 12px; background: rgba(255,255,255,0.02); text-align: center; height: 160px; margin-bottom: 10px; }
    .section-title { font-size: 1.1rem; font-weight: bold; color: #87CEEB; margin-bottom: 15px; border-left: 4px solid #87CEEB; padding-left: 10px; }
    </style>
    """, unsafe_allow_html=True)

# 2. 데이터 로드 및 정제
conn = st.connection("gsheets", type=GSheetsConnection)
try:
    df = conn.read(ttl=0)
    df.columns = df.columns.str.strip().str.replace('\ufeff', '', regex=False)
    numeric_cols = ['투자원금', '현재 가치', '목표인출액', '매입단가', '목표가', '수량', '수익률(%)']
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

# ---------------------------------------------------------
# [추가] 사이드바 시뮬레이션 세팅
# ---------------------------------------------------------
with st.sidebar:
    st.markdown("### 📉 수익률 시뮬레이션")
    st.write("각 종목별 분배율/이자율 조절")
    
    sim_settings = {}
    for _, row in private_assets.iterrows():
        # 시트의 기본 수익률을 슬라이더 초기값으로 설정
        default_val = float(row.get('수익률(%)', 0))
        sim_settings[row['종목명']] = st.slider(
            f"{row['종목명']} (%)", 0.0, 20.0, default_val, 0.1
        )
    
    if st.button("🔄 시나리오 초기화"):
        st.rerun()

# ---------------------------------------------------------
# [추가] 시뮬레이션 계산 로직 (기존 변수와 분리)
# ---------------------------------------------------------
sim_assets = private_assets.copy()
# 슬라이더 값에 기반하여 월 목표인출액 재계산
sim_assets['목표인출액'] = sim_assets.apply(
    lambda x: (x['현재 가치'] * (sim_settings[x['종목명']] / 100) / 12), axis=1
)

sim_total_income = sim_assets['목표인출액'].sum()
sim_achievement = (sim_total_income / TARGET_PRIVATE * 100)
actual_total_income = private_assets['목표인출액'].sum() # 시트 원본 합계

# 3. 시장 지표 엔진 (네이버 크롤링)
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

# 4. HUD 및 상단 메트릭
st.markdown("<h2 style='text-align: center; color: #87CEEB;'>🛡️ 현금흐름 통합 관제탑</h2>", unsafe_allow_html=True)
m_data = get_market_status()
h1, h2, h3, h4 = st.columns(4)
keys, titles = ["KOSPI", "KOSDAQ", "USD/KRW", "VOLUME"], ["KOSPI", "KOSDAQ", "USD/KRW", "MARKET VOL"]
for i, col in enumerate([h1, h2, h3, h4]):
    d = m_data[keys[i]]
    col.markdown(f"""<div class="market-box" style="border: 1px solid {d['color']}44;"><div class="market-title">{titles[i]}</div><div style="color: {d['color']}; font-size: 1.8rem; font-weight: bold;">{d['val']}</div><div style="color: {d['color']}; font-size: 1.0rem;">{d['delta']}</div></div>""", unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)
k1, k2, k3, k4 = st.columns(4)
# 시뮬레이션 결과(Sim) 반영 및 실제 데이터와의 차이(delta) 표시
k1.metric("월 사적 수입 (Sim)", f"{sim_total_income:,.0f}원", delta=f"{sim_total_income - actual_total_income:,.0f}원")
k2.metric("목표 달성률 (Sim)", f"{sim_achievement:.1f}%", delta=f"{(sim_total_income - TARGET_PRIVATE):+,.0f}원")
k3.metric("사적 자산 가치", f"{sim_assets['현재 가치'].sum():,.0f}원")
k4.metric("세금 성격", "절세 중심", delta="비과세/이연")

# 5. 주가 추이 및 전략 카드
st.markdown("---")
with st.expander("🔍 보유 종목별 6개월 주가 추이 및 리스크 판정"):
    t_cols = st.columns(len(sim_assets))
    for i, (idx, row) in enumerate(sim_assets.iterrows()):
        with t_cols[i]:
            ticker = row['종목코드']
            hist = yf.download(ticker, period="6mo", interval="1d", progress=False)
            if not hist.empty:
                if isinstance(hist.columns, pd.MultiIndex): hist.columns = hist.columns.get_level_values(0)
                fig = px.line(hist, y='Close', title=f"{row['종목명']}")
                fig.update_layout(height=150, margin=dict(l=0,r=0,t=30,b=0), xaxis_title="", yaxis_title="")
                st.plotly_chart(fig, use_container_width=True)
            
            curr_p = row['현재 가치'] / row['수량'] if row['수량'] > 0 else 0
            buy_p, target_p = row['매입단가'], row['목표가']
            if curr_p < buy_p * 0.9: sig, clr = "⚠️ 손절 검토", "#FF4B4B"
            elif curr_p >= target_p and target_p > 0: sig, clr = "✅ 이익 실현", "#87CEEB"
            else: sig, clr = "⚓ 보유/관찰", "#aaa"
            st.markdown(f"""<div class="strategy-card" style="border: 1px solid {clr}44;"><div style="color: {clr}; font-size: 1.1rem; font-weight: bold;">{sig}</div><div style="font-size: 0.8rem; margin-top:5px; color:#999;">비중: {(row['현재 가치']/sim_assets['현재 가치'].sum()*100):.1f}%</div></div>""", unsafe_allow_html=True)

# 6. 하단 횡열 병렬 배치 (3 Columns) - 시뮬레이션 데이터 반영
st.markdown("<br>", unsafe_allow_html=True)
col_a, col_b, col_c = st.columns(3)

with col_a:
    st.markdown("<div class='section-title'>📊 자산 구조 (비중)</div>", unsafe_allow_html=True)
    fig_sun = px.sunburst(sim_assets, path=['계좌 유형', '투자성격', '종목명'], values='투자원금',
                          color='투자성격', color_discrete_map={'안전':'#0D47A1', '위험':'#B71C1C'}, template="plotly_white")
    fig_sun.update_layout(height=400, margin=dict(l=0, r=0, t=0, b=0))
    st.plotly_chart(fig_sun, use_container_width=True)

with col_b:
    st.markdown("<div class='section-title'>🌊 수입 폭포 (시뮬레이션)</div>", unsafe_allow_html=True)
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
    st.markdown("<div class='section-title'>📅 입금 일정 (시뮬레이션)</div>", unsafe_allow_html=True)
    sched_df = sim_assets.sort_values('입금예정일')
    fig_bar = px.bar(sched_df, x='입금예정일', y='목표인출액', color='계좌 유형', text='종목명',
                     color_discrete_sequence=px.colors.qualitative.Safe)
    fig_bar.update_layout(height=400, xaxis_type='category', margin=dict(l=0, r=0, t=20, b=0), template="plotly_white")
    st.plotly_chart(fig_bar, use_container_width=True)
