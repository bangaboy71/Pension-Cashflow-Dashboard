import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import yfinance as yf

# 1. 페이지 설정 및 스타일
st.set_page_config(page_title="현금흐름 340만 관제탑", layout="wide")

# 2. 데이터 로드 및 강력한 세척 (BOM, 공백, 쉼표 완벽 제거)
conn = st.connection("gsheets", type=GSheetsConnection)

try:
    df = conn.read(ttl=0)
    # 컬럼명 유령 문자 및 공백 제거
    df.columns = df.columns.str.strip().str.replace('\ufeff', '', regex=False)
    
    # 숫자형 데이터 강제 변환
    numeric_cols = ['투자원금', '현재 가치', '목표인출액', '목표가']
    for col in numeric_cols:
        if col in df.columns:
            df[col] = df[col].astype(str).str.replace(',', '').str.replace('원', '').str.strip()
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
except Exception as e:
    st.error(f"데이터 로드 실패: {e}")
    st.stop()

# 사적 자산 필터링 (340만 목표)
target_col = '계좌 유형'
if target_col not in df.columns:
    df.rename(columns={df.columns[0]: '계좌 유형'}, inplace=True)
private_assets = df[df['계좌 유형'].isin(['IRP', 'ISA', '일반'])].copy()

TARGET_PRIVATE = 3400000
current_total = private_assets['목표인출액'].sum()
achievement = (current_total / TARGET_PRIVATE) * 100

# ---------------------------------------------------------
# 3. [심미성] 중앙 집중형 헤더
# ---------------------------------------------------------
st.markdown("<h2 style='text-align: center; color: #1E3A8A;'>🛡️ 현금흐름 통합 관제탑</h2>", unsafe_allow_html=True)
st.markdown(f"<p style='text-align: center; color: #666; font-size: 1.1em;'>자산 목표: <b>월 {TARGET_PRIVATE/10000:,.0f}만 원</b> (공적연금 제외)</p>", unsafe_allow_html=True)

# ---------------------------------------------------------
# 4. [실시간성] 시장 지표 (가족 관제탑 스타일)
# ---------------------------------------------------------
@st.cache_data(ttl=600)
# --- [v40.21 시장 지수 엔진: 거래량 독립 및 텍스트 클리닝] ---
def get_market_status():
    data = {
        "KOSPI": {"val": "-", "pct": "0.00%", "color": "#ffffff"},
        "KOSDAQ": {"val": "-", "pct": "0.00%", "color": "#ffffff"},
        "USD/KRW": {"val": "-", "pct": "0원", "color": "#ffffff"},
        "VOLUME": {"val": "-", "pct": "천주", "color": "#ffffff"} # 거래량은 흰색 고정
    }
    
    header = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.naver.com/'}
    
    try:
        # 🎯 1. 코스피/코스닥 정밀 수집
        for code in ["KOSPI", "KOSDAQ"]:
            url = f"https://finance.naver.com/sise/sise_index.naver?code={code}"
            res = requests.get(url, headers=header, timeout=5)
            res.encoding = 'euc-kr'
            soup = BeautifulSoup(res.text, 'html.parser')
            
            # 현재 지수
            now_el = soup.select_one("#now_value")
            if now_el: data[code]["val"] = now_el.get_text(strip=True)
            
            # 변동치 정제 (상승/하락 글자 제거 및 부호 유지)
            diff_el = soup.select_one("#change_value_and_rate")
            if diff_el:
                raw_txt = diff_el.get_text(" ", strip=True) # 공백을 넣어 가독성 확보
                # '상승', '하락', '보합' 글자 완전 제거
                for word in ["상승", "하락", "보합"]: raw_txt = raw_txt.replace(word, "")
                
                # 색상 결정 (부호 기준)
                if "+" in raw_txt: data[code]["color"] = "#FF4B4B"
                elif "-" in raw_txt: data[code]["color"] = "#87CEEB"
                
                data[code]["pct"] = raw_txt.strip()

            # 🎯 2. [거래량] - 코스피 루프에서 독립적으로 수집
            if code == "KOSPI":
                vol_el = soup.select_one("#quant")
                if vol_el: 
                    data["VOLUME"]["val"] = vol_el.get_text(strip=True)
                    data["VOLUME"]["pct"] = "천주" # 단위 고정

        # 🎯 3. 환율 수집
        ex_res = requests.get("https://finance.naver.com/marketindex/", headers=header, timeout=5)
        ex_soup = BeautifulSoup(ex_res.text, 'html.parser')
        ex_val = ex_soup.select_one("span.value")
        if ex_val:
            data["USD/KRW"]["val"] = ex_val.get_text(strip=True)
            ex_change = ex_soup.select_one("span.change").get_text(strip=True)
            ex_blind = ex_soup.select_one("div.head_info > span.blind").get_text()
            
            if "상승" in ex_blind:
                data["USD/KRW"]["color"], sign = "#FF4B4B", "+"
            elif "하락" in ex_blind:
                data["USD/KRW"]["color"], sign = "#87CEEB", "-"
            else:
                data["USD/KRW"]["color"], sign = "#ffffff", ""
            data["USD/KRW"]["pct"] = f"{sign}{ex_change}원"
            
    except: pass
    return data

st.markdown("<hr style='border: 0.5px solid #eee;'>", unsafe_allow_html=True)

# 5. 핵심 KPI (가독성 강화)
k1, k2, k3, k4 = st.columns(4)
k1.metric("월 예상 수입", f"{current_total:,.0f}원")
k2.metric("목표 달성률", f"{achievement:.1f}%", delta=f"{achievement-100:.1f}%")
k3.metric("총 자산 평가액", f"{private_assets['현재 가치'].sum():,.0f}원")
k4.metric("세금 성격", "절세 중심", delta="비과세/이연")

# 6. 고도화 시각화 탭 (심미적 차트 적용)
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
        text = [f"{v/10000:,.0f}만" for v in private_assets['목표인출액']] + [f"{current_total/10000:,.0f}만"],
        connector = {"line":{"color":"#ddd"}},
    ))
    fig_water.update_layout(title="목표 340만 원을 채우는 수입 엔진들", template="plotly_white")
    st.plotly_chart(fig_water, use_container_width=True)

with t3:
    sched_df = private_assets.sort_values('입금예정일')
    fig_bar = px.bar(sched_df, x='입금예정일', y='목표인출액', color='계좌 유형', text='종목명',
                     color_discrete_sequence=px.colors.qualitative.Safe)
    fig_bar.update_layout(xaxis_type='category', title="월간 현금 유입 스케줄")
    st.plotly_chart(fig_bar, use_container_width=True)

with t4:
    tax_df = private_assets.groupby('세금성격')['투자원금'].sum().reset_index()
    fig_tax = px.pie(tax_df, values='투자원금', names='세금성격', hole=0.5,
                     color_discrete_sequence=px.colors.sequential.RdBu)
    st.plotly_chart(fig_tax, use_container_width=True)
