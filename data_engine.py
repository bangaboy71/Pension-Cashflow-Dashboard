"""
data_engine.py — 가족 자산 관제탑 데이터 엔진
외부 데이터 수집(크롤링), 정제, 지표 계산, 저장 로직을 담당합니다.
Streamlit import 없이 순수 Python/Pandas 로직만 포함합니다.
  → 테스트·재사용 가능 / UI 레이어와 완전 분리
"""
from __future__ import annotations

import calendar
import io
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

import pandas as pd
import requests
from bs4 import BeautifulSoup

from config import (
    STOCK_CODES, DIVIDEND_SCHEDULE, DIVIDEND_TAX_RATE,
    KOSPI_BASE_DATE_DEFAULT, STOP_LOSS_PCT, TRAILING_PCT, TARGET_ALERT_PCT,
    WS_PORTFOLIO, WS_TREND, WS_MEMO, WS_SNAPSHOT,
)


# ════════════════════════════════════════════════════════
# 1. 시간 헬퍼
# ════════════════════════════════════════════════════════

def get_now_kst() -> datetime:
    """현재 한국 시간 반환"""
    return datetime.now(timezone(timedelta(hours=9)))


# ════════════════════════════════════════════════════════
# 2. 시장 지수 수집
# ════════════════════════════════════════════════════════

def get_market_status() -> dict:
    """KOSPI·KOSDAQ·USD/KRW·거래량 실시간 수집"""
    data = {
        "KOSPI":   {"val": "-", "pct": "0.00%", "color": "#ffffff"},
        "KOSDAQ":  {"val": "-", "pct": "0.00%", "color": "#ffffff"},
        "USD/KRW": {"val": "-", "pct": "0원",   "color": "#ffffff"},
        "VOLUME":  {"val": "-", "pct": "천주",  "color": "#ffffff"},
    }
    header = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}
    try:
        for code in ["KOSPI", "KOSDAQ"]:
            url = f"https://finance.naver.com/sise/sise_index.naver?code={code}"
            res = requests.get(url, headers=header, timeout=5)
            res.encoding = "euc-kr"
            soup = BeautifulSoup(res.text, "html.parser")

            now_el = soup.select_one("#now_value")
            if now_el:
                data[code]["val"] = now_el.get_text(strip=True)

            diff_el = soup.select_one("#change_value_and_rate")
            if diff_el:
                raw = diff_el.get_text(" ", strip=True)
                for word in ["상승", "하락", "보합"]:
                    raw = raw.replace(word, "")
                if "+" in raw:
                    data[code]["color"] = "#FF4B4B"
                elif "-" in raw:
                    data[code]["color"] = "#87CEEB"
                data[code]["pct"] = raw.strip()

            if code == "KOSPI":
                vol_el = soup.select_one("#quant")
                if vol_el:
                    data["VOLUME"]["val"] = vol_el.get_text(strip=True)
                    data["VOLUME"]["pct"] = "천주"

        ex_res = requests.get(
            "https://finance.naver.com/marketindex/", headers=header, timeout=5
        )
        ex_soup = BeautifulSoup(ex_res.text, "html.parser")
        ex_val = ex_soup.select_one("span.value")
        if ex_val:
            data["USD/KRW"]["val"] = ex_val.get_text(strip=True)
            ex_change = ex_soup.select_one("span.change").get_text(strip=True)
            ex_blind  = ex_soup.select_one("div.head_info > span.blind").get_text()
            if "상승" in ex_blind:
                data["USD/KRW"]["color"], sign = "#FF4B4B", "+"
            elif "하락" in ex_blind:
                data["USD/KRW"]["color"], sign = "#87CEEB", "-"
            else:
                data["USD/KRW"]["color"], sign = "#ffffff", ""
            data["USD/KRW"]["pct"] = f"{sign}{ex_change}원"
    except Exception:
        pass
    return data


# ════════════════════════════════════════════════════════
# 3. 종목 주가 수집
# ════════════════════════════════════════════════════════

def get_stock_data(name: str) -> tuple[int, int]:
    """종목명 → (현재가, 전일종가) 반환. 실패 시 (0, 0)"""
    code = STOCK_CODES.get(str(name).replace(" ", ""))
    if not code:
        return 0, 0
    try:
        res = requests.get(
            f"https://finance.naver.com/item/main.naver?code={code}",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=3,
        )
        soup = BeautifulSoup(res.text, "html.parser")
        now_p  = int(soup.find("div", {"class": "today"})
                        .find("span", {"class": "blind"}).text.replace(",", ""))
        prev_p = int(soup.find("td", {"class": "first"})
                        .find("span", {"class": "blind"}).text.replace(",", ""))
        return now_p, prev_p
    except Exception:
        return 0, 0


def get_stock_data_parallel(
    names: list[str], on_progress=None
) -> list[tuple[int, int]]:
    """
    종목 리스트를 ThreadPoolExecutor로 병렬 수집 (원본 순서 보장).
    on_progress(done, total, name): 종목 1개 완료 시 호출되는 콜백.
    """
    results = {}
    total = len(names)
    done  = 0
    with ThreadPoolExecutor(max_workers=min(total, 10)) as executor:
        future_to_name = {
            executor.submit(get_stock_data, n): n for n in names
        }
        for future in as_completed(future_to_name):
            n = future_to_name[future]
            try:
                results[n] = future.result()
            except Exception:
                results[n] = (0, 0)
            done += 1
            if on_progress:
                on_progress(done, total, n)
    return [results.get(n, (0, 0)) for n in names]


# ════════════════════════════════════════════════════════
# 4. 뉴스 수집
# ════════════════════════════════════════════════════════

def get_stock_news(name: str) -> list[dict]:
    """종목명 → 최신 뉴스 6개 반환"""
    code = STOCK_CODES.get(str(name).replace(" ", ""))
    if not code:
        return []
    news_list = []
    header = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Referer": f"https://finance.naver.com/item/main.naver?code={code}",
    }
    try:
        url = f"https://finance.naver.com/item/news_news.naver?code={code}"
        res = requests.get(url, headers=header, timeout=5)
        res.encoding = "euc-kr"
        soup   = BeautifulSoup(res.text, "html.parser")
        titles = soup.find_all("td", class_="title")
        infos  = soup.find_all("td", class_="info")
        dates  = soup.find_all("td", class_="date")

        for i in range(min(len(titles), 6)):
            link_el  = titles[i].find("a")
            date_str = dates[i].get_text(strip=True) if i < len(dates) else "-"
            is_recent = False
            try:
                if "전" in date_str:
                    is_recent = True
                else:
                    n_time = datetime.strptime(date_str, "%Y.%m.%d %H:%M")
                    if (datetime.now() - n_time).total_seconds() < 86400:
                        is_recent = True
            except Exception:
                pass
            if link_el:
                news_list.append({
                    "title":     link_el.get_text(strip=True),
                    "link":      "https://finance.naver.com" + link_el["href"],
                    "info":      infos[i].get_text(strip=True) if i < len(infos) else "정보없음",
                    "date":      date_str,
                    "is_recent": is_recent,
                })
    except Exception:
        pass
    return news_list


# ════════════════════════════════════════════════════════
# 5. 구글 시트 데이터 로드
# ════════════════════════════════════════════════════════

def load_sheets(conn) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    구글 시트에서 세 워크시트를 읽어 반환.
    Returns: (full_df, history_df, memo_df)
    실패 시 ValueError 발생 → 호출부에서 st.stop() 처리
    """
    full_df    = conn.read(worksheet=WS_PORTFOLIO, ttl="1m")
    history_df = conn.read(worksheet=WS_TREND,     ttl=0)
    try:
        memo_df = conn.read(worksheet=WS_MEMO, ttl=0)
        if memo_df.empty or "종목명" not in memo_df.columns:
            memo_df = pd.DataFrame(columns=["종목명", "계좌명", "메모", "수정일시"])
    except Exception:
        memo_df = pd.DataFrame(columns=["종목명", "계좌명", "메모", "수정일시"])
    return full_df, history_df, memo_df


def load_snapshot(conn) -> dict:
    """
    구글 시트 'snapshot' 워크시트에서 날짜별 팩트 수치를 읽어 반환.

    시트 구조 (헤더: 날짜 | 항목 | 값)
    ─────────────────────────────────
    2026-03-09 | KOSPI                        | 5251.87
    2026-03-09 | 삼성전자                     | 111400
    2026-03-09 | KODEX200타겟위클리커버드콜   | 16515

    반환: {"2026-03-09": {"KOSPI": 5251.87, "삼성전자": 111400.0, ...}, ...}
    시트 없음 / 오류 시 빈 dict 반환 (앱은 현재가로 폴백)
    """
    try:
        df = conn.read(worksheet=WS_SNAPSHOT, ttl=0)
        if df.empty or not {"날짜", "항목", "값"}.issubset(df.columns):
            return {}
        result: dict = {}
        for _, row in df.iterrows():
            date_str = str(row["날짜"]).strip()
            item     = str(row["항목"]).strip()
            try:
                val = float(row["값"])
            except (ValueError, TypeError):
                continue
            result.setdefault(date_str, {})[item] = val
        return result
    except Exception:
        return {}


def load_overrides(path: str = "overrides.toml") -> dict:
    """
    로컬 overrides.toml에서 설정을 읽어 반환 (오프라인 폴백).
    파일 없으면 빈 dict 반환.

    overrides.toml 예시
    ───────────────────
    [app]
    kospi_base_date = "2026-03-03"

    [snapshots."2026-03-09"]
    KOSPI                        = 5251.87
    삼성전자                     = 111400.0
    KODEX200타겟위클리커버드콜   = 16515.0
    """
    try:
        import sys
        if sys.version_info >= (3, 11):
            import tomllib
            with open(path, "rb") as f:
                return tomllib.load(f)
        else:
            import tomli  # pip install tomli
            with open(path, "rb") as f:
                return tomli.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def resolve_settings(conn) -> dict:
    """
    설정값을 우선순위에 따라 병합해 반환.
    우선순위: secrets.toml  >  구글 시트 snapshot  >  overrides.toml  >  코드 기본값

    반환 dict 키
    ─────────────────────────────────────────────────────
    kospi_base_date : str   — KOSPI 상대비교 기준일
    snapshot        : dict  — { "날짜": {"항목": 값, ...} }
    """
    # 1. 코드 기본값
    settings = {
        "kospi_base_date": KOSPI_BASE_DATE_DEFAULT,
        "snapshot":        {},
    }

    # 2. overrides.toml (로컬 폴백)
    overrides = load_overrides()
    if "app" in overrides:
        if "kospi_base_date" in overrides["app"]:
            settings["kospi_base_date"] = overrides["app"]["kospi_base_date"]
    if "snapshots" in overrides:
        for date_str, vals in overrides["snapshots"].items():
            settings["snapshot"].setdefault(date_str, {}).update(vals)

    # 3. 구글 시트 snapshot (overrides보다 우선)
    sheet_snap = load_snapshot(conn)
    for date_str, vals in sheet_snap.items():
        settings["snapshot"].setdefault(date_str, {}).update(vals)

    # 4. Streamlit secrets.toml (최우선)
    try:
        import streamlit as st
        sec_app = st.secrets.get("app", {})
        if "kospi_base_date" in sec_app:
            settings["kospi_base_date"] = sec_app["kospi_base_date"]
        # secrets의 스냅샷 (선택적)
        sec_snap = st.secrets.get("snapshots", {})
        for date_str, vals in sec_snap.items():
            settings["snapshot"].setdefault(date_str, {}).update(dict(vals))
    except Exception:
        pass

    return settings


# ════════════════════════════════════════════════════════
# 6. 데이터 정제 및 지표 계산
# ════════════════════════════════════════════════════════

def process_portfolio(full_df: pd.DataFrame, prices: list[tuple]) -> pd.DataFrame:
    """
    주가 수집 결과를 받아 수익 지표·보유일수 계산 후 반환.
    prices: get_stock_data_parallel() 결과 리스트
    """
    df = full_df.copy()
    df.columns = [c.strip() for c in df.columns]

    # 숫자 컬럼 변환
    num_cols = ["수량", "매입단가", "52주최고가", "매입후최고가", "목표가", "주당 배당금", "목표수익률"]
    for c in num_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(
                df[c].astype(str).str.replace(",", "").str.replace("%", ""),
                errors="coerce",
            ).fillna(0)
        elif c == "목표수익률":
            df["목표수익률"] = 10.0

    # 주가 반영
    df["현재가"],  df["전일종가"] = zip(*prices)

    # 수익 지표
    df["매입금액"]       = df["수량"] * df["매입단가"]
    df["평가금액"]       = df["수량"] * df["현재가"]
    df["손익"]           = df["평가금액"] - df["매입금액"]
    df["전일대비손익"]   = df["평가금액"] - (df["수량"] * df["전일종가"])
    df["전일평가액"]     = df["평가금액"] - df["전일대비손익"]
    df["예상배당금"]     = df["수량"] * df["주당 배당금"]
    df["누적수익률"]     = (df["손익"] / df["매입금액"].replace(0, float("nan")) * 100).fillna(0)
    df["전일대비변동율"] = (
        df["전일대비손익"] / df["전일평가액"].replace(0, float("nan")) * 100
    ).fillna(0)
    df["목표대비상승여력"] = df.apply(
        lambda x: ((x["목표가"] / x["현재가"] - 1) * 100)
        if x["현재가"] > 0 and x["목표가"] > 0 else 0,
        axis=1,
    )

    # 보유일수
    if "최초매입일" in df.columns:
        df["최초매입일"] = pd.to_datetime(df["최초매입일"], errors="coerce")
        base = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        df["보유일수"] = (
            (base - df["최초매입일"].dt.tz_localize(None))
            .dt.days.fillna(365).astype(int).clip(lower=1)
        )
    else:
        df["보유일수"] = 365

    return df


def process_history(
    history_df: pd.DataFrame,
    kospi_base_date: str = KOSPI_BASE_DATE_DEFAULT,
) -> pd.DataFrame:
    """수익률 추이 정규화 및 KOSPI 상대 수익률 계산"""
    df = (
        history_df.copy()
        .pipe(lambda d: d.assign(Date=pd.to_datetime(d["Date"], errors="coerce")))
        .dropna(subset=["Date"])
        .sort_values("Date")
        .drop_duplicates("Date", keep="last")
        .reset_index(drop=True)
    )
    base_date = pd.Timestamp(kospi_base_date)
    base_row  = df[df["Date"] == base_date]
    base_val  = base_row["KOSPI"].values[0] if not base_row.empty else df["KOSPI"].iloc[0]
    df["KOSPI_Relative"] = (df["KOSPI"] / base_val - 1) * 100
    return df


# ════════════════════════════════════════════════════════
# 7. 헬퍼 함수
# ════════════════════════════════════════════════════════

def get_cashflow_grade(amount: float) -> str:
    """월 세후 수령액 기준 등급 반환"""
    if amount >= 1_000_000: return "💎 Diamond"
    if amount >= 300_000:   return "🥇 Gold"
    if amount >= 100_000:   return "🥈 Silver"
    return "🥉 Bronze"


def find_matching_col(df: pd.DataFrame, account: str, stock: str = None):
    """계좌명(+종목명) 기준으로 history_df 컬럼명 탐색"""
    prefix = account.replace("투자", "").replace(" ", "")
    target = (
        f"{prefix}{stock}수익률".replace(" ", "").replace("_", "")
        if stock
        else f"{prefix}수익률".replace(" ", "").replace("_", "")
    )
    for col in df.columns:
        if target == str(col).replace(" ", "").replace("_", "").replace("투자", ""):
            return col
    return None


def get_dividend_calendar(df: pd.DataFrame, now_kst: datetime) -> list[dict]:
    """보유 종목 기준 향후 배당·분배금 예정일 목록 계산.
    DIVIDEND_SCHEDULE은 (월, 일) 튜플 리스트.
    일(day)=0 이면 해당 월의 말일로 자동 계산.
    """
    today  = now_kst.date()
    events = []
    for _, row in df.iterrows():
        name    = row.get("종목명", "")
        acc     = row.get("계좌명", "")
        div_amt = float(row.get("예상배당금", 0))
        if div_amt <= 0:
            continue
        schedule = DIVIDEND_SCHEDULE.get(name, [])
        if not schedule:
            continue

        for entry in schedule:
            # 하위 호환: 기존 정수(월만) 형식도 허용
            if isinstance(entry, (list, tuple)) and len(entry) == 2:
                m, d = int(entry[0]), int(entry[1])
            else:
                m, d = int(entry), 0

            # 지급일 계산: d=0이면 말일
            for year in [today.year, today.year + 1]:
                last_day = calendar.monthrange(year, m)[1]
                pay_day  = last_day if d == 0 else min(d, last_day)
                pay_date = datetime(year, m, pay_day).date()
                if pay_date >= today:
                    break

            events.append({
                "종목명":    name,
                "계좌명":    acc,
                "배당월":    m,
                "지급일":    pay_day,
                "지급예정일": pay_date,
                "D_DAY":     (pay_date - today).days,
                "예상배당금": div_amt / len(schedule),
            })

    events.sort(key=lambda x: (x["D_DAY"], -x["예상배당금"]))
    return events


# ════════════════════════════════════════════════════════
# 8. 메모 CRUD
# ════════════════════════════════════════════════════════

def get_memo(memo_df: pd.DataFrame, stock_name: str, acc_name: str) -> str:
    """메모 DataFrame에서 특정 종목+계좌 메모 반환"""
    if memo_df.empty:
        return ""
    row = memo_df[
        (memo_df["종목명"] == stock_name) & (memo_df["계좌명"] == acc_name)
    ]
    return str(row["메모"].values[0]) if not row.empty else ""


def save_memo(
    conn, memo_df: pd.DataFrame,
    stock_name: str, acc_name: str,
    text: str, now_kst: datetime,
) -> tuple[bool, pd.DataFrame]:
    """
    메모 upsert 후 (성공여부, 갱신된 memo_df) 반환.
    conn: Streamlit GSheets 연결 객체 (app.py에서 전달)
    """
    now_str = now_kst.strftime("%Y-%m-%d %H:%M:%S")
    new_row = pd.DataFrame([{
        "종목명": stock_name, "계좌명": acc_name,
        "메모": text, "수정일시": now_str,
    }])
    mask        = ~((memo_df["종목명"] == stock_name) & (memo_df["계좌명"] == acc_name))
    updated_df  = pd.concat([memo_df[mask], new_row], ignore_index=True)
    try:
        conn.update(worksheet=WS_MEMO, data=updated_df)
        return True, updated_df
    except Exception as e:
        return False, memo_df


# ════════════════════════════════════════════════════════
# 9. 내보내기
# ════════════════════════════════════════════════════════

def build_export_df(df: pd.DataFrame) -> pd.DataFrame:
    """내보내기용 컬럼 추출 및 포맷 정리"""
    cols = [
        "계좌명", "종목명", "수량", "매입단가", "매입금액",
        "현재가", "평가금액", "손익", "누적수익률",
        "전일대비손익", "전일대비변동율", "예상배당금", "목표가", "목표대비상승여력",
    ]
    out = df[[c for c in cols if c in df.columns]].copy()
    for c in ["매입단가", "매입금액", "현재가", "평가금액", "손익",
              "전일대비손익", "예상배당금", "목표가"]:
        if c in out.columns:
            out[c] = out[c].apply(lambda x: round(x, 0) if pd.notna(x) else 0)
    for c in ["누적수익률", "전일대비변동율", "목표대비상승여력"]:
        if c in out.columns:
            out[c] = out[c].apply(lambda x: round(x, 2) if pd.notna(x) else 0)
    return out


def get_csv_bytes(df: pd.DataFrame) -> bytes:
    return build_export_df(df).to_csv(
        index=False, encoding="utf-8-sig"
    ).encode("utf-8-sig")


def get_excel_bytes(df: pd.DataFrame, history_df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        build_export_df(df).to_excel(writer, sheet_name="전체 종목 현황", index=False)
        for acc in df["계좌명"].unique():
            build_export_df(df[df["계좌명"] == acc]).to_excel(
                writer, sheet_name=acc.replace("투자", "")[:10], index=False
            )
        if not history_df.empty:
            history_df.copy().to_excel(writer, sheet_name="수익률 추이", index=False)
    return buf.getvalue()


# ════════════════════════════════════════════════════════
# 10. 목표가 도달 토스트 알림
# ════════════════════════════════════════════════════════

def check_and_toast_targets(df: pd.DataFrame):
    """
    현재가가 목표가에 도달·근접한 종목을 st.toast로 알림.
    Streamlit import는 이 함수 내에서만 사용 (data_engine 전체를 오염시키지 않음).
    """
    import streamlit as st
    if df.empty or "현재가" not in df.columns or "목표가" not in df.columns:
        return

    alerted = st.session_state.get("toasted_targets", set())
    for _, row in df.iterrows():
        name   = row.get("종목명", "")
        curr   = float(row.get("현재가", 0))
        target = float(row.get("목표가", 0))
        ret    = float(row.get("누적수익률", 0))
        if curr <= 0 or target <= 0 or name in alerted:
            continue
        ratio = curr / target
        if ratio >= 1.0:
            st.toast(
                f"🎯 **{name}** 목표가 달성!\n"
                f"현재가 **{curr:,.0f}원** ≥ 목표가 **{target:,.0f}원**\n"
                f"누적 수익률 **{ret:+.2f}%**",
                icon="🚀",
            )
            alerted.add(name)
        elif ratio >= TARGET_ALERT_PCT:
            st.toast(
                f"📡 **{name}** 목표가 근접 ({TARGET_ALERT_PCT*100:.0f}%)\n"
                f"현재가 {curr:,.0f}원 / 목표가 {target:,.0f}원\n"
                f"잔여 여력 **{(ratio-1)*100:+.1f}%**",
                icon="⚡",
            )
            alerted.add(name)
    st.session_state["toasted_targets"] = alerted
