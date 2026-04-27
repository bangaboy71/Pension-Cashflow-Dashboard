"""
data_engine.py — 가족 자산 관제탑 데이터 엔진 (v2 — pykrx 고도화)
====================================================================
변경 이력
─────────────────────────────────────────────────────────────
v2 (2026-03)  pykrx + yfinance 연동, SQLite 캐시 통합
              get_stock_data()        → pykrx 우선, 네이버 폴백
              get_stock_data_parallel → 캐시 → pykrx → 네이버 3단 폴백
              get_market_status()     → yfinance 우선, 네이버 폴백
              ※ 모든 기존 함수 시그니처·반환 타입 유지 (app.py 수정 불필요)
─────────────────────────────────────────────────────────────
Streamlit import 없이 순수 Python/Pandas 로직만 포함합니다.
"""
from __future__ import annotations

import calendar
import io
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

import pandas as pd
import requests
from bs4 import BeautifulSoup

from config import (
    STOCK_CODES, DIVIDEND_SCHEDULE, DIVIDEND_PAY_DAY, DIVIDEND_TAX_RATE,
    KOSPI_BASE_DATE_DEFAULT, STOP_LOSS_PCT, TRAILING_PCT, TARGET_ALERT_PCT,
    WS_PORTFOLIO, WS_TREND, WS_MEMO, WS_SNAPSHOT, WS_DIVIDEND,
)

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════
# 1. 시간 헬퍼
# ════════════════════════════════════════════════════════

def get_now_kst() -> datetime:
    """현재 한국 시간 반환"""
    return datetime.now(timezone(timedelta(hours=9)))


# ════════════════════════════════════════════════════════
# 2. 시장 지수 수집 (yfinance 우선 → 네이버 폴백)
# ════════════════════════════════════════════════════════

def get_market_status() -> dict:
    """
    KOSPI·KOSDAQ·USD/KRW·미국 10년물 국채금리 실시간 수집.
    수집 전략:
      1차: pykrx (KRX 공식 데이터 — 가장 정확)
      2차: yfinance market_collector
      3차: 네이버 크롤링 폴백
    반환 형식 유지 — app.py / ui_components.py 수정 불필요.
    """
    data = {
        "KOSPI":   {"val": "-", "pct": "0.00%",  "color": "#ffffff"},
        "KOSDAQ":  {"val": "-", "pct": "0.00%",  "color": "#ffffff"},
        "USD/KRW": {"val": "-", "pct": "0원",    "color": "#ffffff"},
        "US10Y":   {"val": "-", "pct": "0.00%p", "color": "#ffffff"},
    }

    # ── 1차: pykrx — KRX 공식 지수 (가장 정확) ──────────
    try:
        from pykrx import stock as _pykrx
        import datetime as _dt
        _today = _dt.date.today().strftime("%Y%m%d")
        # 최근 5거래일 중 데이터 있는 날 탐색
        for _offset in range(5):
            _d = (_dt.date.today() - _dt.timedelta(days=_offset)).strftime("%Y%m%d")
            try:
                _df = _pykrx.get_index_ohlcv_by_date(_d, _d, "1001")  # KOSPI
                if not _df.empty:
                    _cur  = float(_df["종가"].iloc[-1])
                    _prev = float(_df["시가"].iloc[-1])  # 당일 시가 대신 전일종가 필요
                    # 전일종가: 2일치 조회
                    _d2  = (_dt.date.today() - _dt.timedelta(days=_offset+5)).strftime("%Y%m%d")
                    _df2 = _pykrx.get_index_ohlcv_by_date(_d2, _d, "1001")
                    if len(_df2) >= 2:
                        _prev = float(_df2["종가"].iloc[-2])
                    _chg  = _cur - _prev
                    _pct  = (_chg / _prev * 100) if _prev > 0 else 0.0
                    _sign = "+" if _chg >= 0 else ""
                    data["KOSPI"]["val"]   = f"{_cur:,.2f}"
                    data["KOSPI"]["pct"]   = f"{_sign}{_pct:.2f}%"
                    data["KOSPI"]["color"] = "#FF4B4B" if _chg >= 0 else "#87CEEB"
                    break
            except Exception:
                continue

        for _offset in range(5):
            _d = (_dt.date.today() - _dt.timedelta(days=_offset)).strftime("%Y%m%d")
            try:
                _df = _pykrx.get_index_ohlcv_by_date(_d, _d, "2001")  # KOSDAQ
                if not _df.empty:
                    _cur  = float(_df["종가"].iloc[-1])
                    _d2   = (_dt.date.today() - _dt.timedelta(days=_offset+5)).strftime("%Y%m%d")
                    _df2  = _pykrx.get_index_ohlcv_by_date(_d2, _d, "2001")
                    if len(_df2) >= 2:
                        _prev = float(_df2["종가"].iloc[-2])
                    else:
                        _prev = _cur
                    _chg  = _cur - _prev
                    _pct  = (_chg / _prev * 100) if _prev > 0 else 0.0
                    _sign = "+" if _chg >= 0 else ""
                    data["KOSDAQ"]["val"]   = f"{_cur:,.2f}"
                    data["KOSDAQ"]["pct"]   = f"{_sign}{_pct:.2f}%"
                    data["KOSDAQ"]["color"] = "#FF4B4B" if _chg >= 0 else "#87CEEB"
                    break
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"pykrx 지수 수집 실패: {e}")

    # ── 2차: market_collector (yfinance 기반) ────────────
    try:
        from market_collector import get_yf_market_status_compatible
        yf_data = get_yf_market_status_compatible()
        for key in data:
            if key in yf_data and yf_data[key]["val"] != "-":
                # KOSPI/KOSDAQ는 pykrx가 성공한 경우 덮어쓰지 않음
                if key in ("KOSPI", "KOSDAQ") and data[key]["val"] != "-":
                    continue
                data[key] = yf_data[key]
    except Exception as e:
        logger.warning(f"yfinance 시장 지표 수집 실패: {e}")

    # ── 2.5차: US10Y — yfinance 직접 (market_collector 없어도) ─
    if data["US10Y"]["val"] == "-":
        try:
            import yfinance as _yf2
            _tnx  = _yf2.Ticker("^TNX")
            _hist = _tnx.history(period="5d")
            if len(_hist) >= 2:
                _cur  = float(_hist["Close"].iloc[-1])
                _prev = float(_hist["Close"].iloc[-2])
                _d    = _cur - _prev
                data["US10Y"]["val"]   = f"{_cur:.2f}"
                data["US10Y"]["pct"]   = f"{_d:+.2f}%p"
                data["US10Y"]["color"] = "#FF4B4B" if _d >= 0 else "#87CEEB"
        except Exception:
            pass

    # ── 3차: 네이버 크롤링 폴백 (여전히 "-"인 지표만) ───
    missing = [k for k, v in data.items() if v["val"] == "-"]
    if missing:
        logger.info(f"네이버 폴백: {missing}")
        naver_data = _get_market_status_naver()
        for key in missing:
            if key in naver_data and naver_data[key]["val"] != "-":
                data[key] = naver_data[key]

    return data


def _get_market_status_naver() -> dict:
    """네이버 크롤링 기반 시장 지표 수집 (폴백 전용, 기존 로직 그대로 유지)"""
    data = {
        "KOSPI":   {"val": "-", "pct": "0.00%",  "color": "#ffffff"},
        "KOSDAQ":  {"val": "-", "pct": "0.00%",  "color": "#ffffff"},
        "USD/KRW": {"val": "-", "pct": "0원",    "color": "#ffffff"},
        "US10Y":   {"val": "-", "pct": "0.00%p", "color": "#ffffff"},
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
                import re as _re
                raw = diff_el.get_text(" ", strip=True)
                for word in ["상승", "하락", "보합"]:
                    raw = raw.replace(word, "")
                raw = raw.strip()
                # "12.34 +0.46%" → "+0.46%" 퍼센트만 추출
                _m = _re.search(r"([+][0-9]+\.?[0-9]*%|[-][0-9]+\.?[0-9]*%)", raw)
                if _m:
                    pct_str = _m.group(1)
                else:
                    _m2 = _re.search(r"([0-9]+\.?[0-9]*%)", raw)
                    pct_str = _m2.group(1) if _m2 else raw
                if "+" in pct_str:
                    data[code]["color"] = "#FF4B4B"
                elif "-" in pct_str:
                    data[code]["color"] = "#87CEEB"
                data[code]["pct"] = pct_str

        ex_res  = requests.get("https://finance.naver.com/marketindex/", headers=header, timeout=5)
        ex_soup = BeautifulSoup(ex_res.text, "html.parser")
        ex_val  = ex_soup.select_one("span.value")
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

    try:
        import yfinance as yf
        tnx  = yf.Ticker("^TNX")
        hist = tnx.history(period="5d")
        if len(hist) >= 2:
            cur   = float(hist["Close"].iloc[-1])
            prev  = float(hist["Close"].iloc[-2])
            delta = cur - prev
            data["US10Y"]["val"]   = f"{cur:.2f}"
            data["US10Y"]["pct"]   = f"{delta:+.2f}%p"
            data["US10Y"]["color"] = "#FF4B4B" if delta > 0 else "#87CEEB"
    except Exception:
        pass

    return data


# ════════════════════════════════════════════════════════
# 3. 종목 주가 수집 (캐시 → pykrx → 네이버 3단 폴백)
# ════════════════════════════════════════════════════════

def get_stock_data(name: str) -> tuple[int, int]:
    """
    종목명 → (현재가, 전일종가) 반환.
    수집 전략: SQLite 캐시 → pykrx → 네이버 크롤링 순으로 시도.
    실패 시 (0, 0).
    """
    code = STOCK_CODES.get(str(name).replace(" ", ""))
    if not code:
        return 0, 0

    # ── 1단: SQLite 캐시 조회 ──
    try:
        from data_store import get_cached_price
        cached = get_cached_price(code)
        if cached is not None:
            return cached
    except Exception:
        pass

    # ── 2단: pykrx (KRX 공식) ──
    try:
        from market_collector import get_krx_price
        current, prev = get_krx_price(code)
        if current > 0:
            try:
                from data_store import set_cached_price
                set_cached_price(code, current, prev)
            except Exception:
                pass
            return current, prev
    except Exception as e:
        logger.warning(f"pykrx {name}({code}) 실패: {e}")

    # ── 3단: 네이버 크롤링 폴백 ──
    return _get_stock_data_naver(code)


def _get_stock_data_naver(code: str) -> tuple[int, int]:
    """네이버 크롤링 주가 수집 (폴백 전용, 기존 로직 유지)"""
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
    종목 리스트를 병렬 수집 (원본 순서 보장).
    캐시 히트 종목은 즉시 반환, 나머지만 pykrx 병렬 수집.
    on_progress(done, total, name): 종목 1개 완료 시 호출되는 콜백.
    """
    results: dict[str, tuple[int, int]] = {}
    total = len(names)
    done  = 0

    # ── 캐시 선조회 (빠른 종목 먼저 처리) ──
    cache_miss = []
    for n in names:
        code = STOCK_CODES.get(str(n).replace(" ", ""))
        if not code:
            results[n] = (0, 0)
            done += 1
            if on_progress:
                on_progress(done, total, n)
            continue
        try:
            from data_store import get_cached_price
            cached = get_cached_price(code)
            if cached:
                results[n] = cached
                done += 1
                if on_progress:
                    on_progress(done, total, n)
                continue
        except Exception:
            pass
        cache_miss.append(n)

    # ── 캐시 미스 종목만 병렬 수집 ──
    if cache_miss:
        max_workers = min(len(cache_miss), 5)   # pykrx: 5 이하 권장
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_name = {
                executor.submit(get_stock_data, n): n for n in cache_miss
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
# 4. 뉴스 수집 (기존 유지 — 대체 무료 소스 없음)
# ════════════════════════════════════════════════════════

def get_stock_news(name: str) -> list[dict]:
    """종목명 → 최신 뉴스 6개 반환 (네이버 크롤링 유지)"""
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
# 5. 구글 시트 데이터 로드 (기존 유지)
# ════════════════════════════════════════════════════════

def load_sheets(conn) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """구글 시트에서 세 워크시트를 읽어 반환 (기존 로직 유지)"""
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
    """구글 시트 'snapshot' 워크시트에서 날짜별 팩트 수치 반환 (기존 유지)"""
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
    """overrides.toml 설정 로드 (기존 유지)"""
    try:
        import sys
        if sys.version_info >= (3, 11):
            import tomllib
            with open(path, "rb") as f:
                return tomllib.load(f)
        else:
            import tomli
            with open(path, "rb") as f:
                return tomli.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def resolve_settings(conn) -> dict:
    """설정값 우선순위 병합 (기존 유지)"""
    settings = {
        "kospi_base_date": KOSPI_BASE_DATE_DEFAULT,
        "snapshot":        {},
    }
    overrides = load_overrides()
    if "app" in overrides:
        if "kospi_base_date" in overrides["app"]:
            settings["kospi_base_date"] = overrides["app"]["kospi_base_date"]
    if "snapshots" in overrides:
        for date_str, vals in overrides["snapshots"].items():
            settings["snapshot"].setdefault(date_str, {}).update(vals)

    # ── [Rate Limit 방어] load_snapshot → 캐시 버전 사용 ──────────
    # 기존: load_snapshot(conn) → 매 재실행마다 conn.read() 호출 (TTL=0)
    # 변경: load_snapshot_cached(conn) → TTL=10분 캐시로 읽기 횟수 대폭 감소
    try:
        from mem_cache import load_snapshot_cached
        sheet_snap = load_snapshot_cached(conn)
    except Exception:
        sheet_snap = load_snapshot(conn)   # 폴백: 캐시 모듈 import 실패 시

    for date_str, vals in sheet_snap.items():
        settings["snapshot"].setdefault(date_str, {}).update(vals)

    try:
        import streamlit as st
        sec_app  = st.secrets.get("app", {})
        if "kospi_base_date" in sec_app:
            settings["kospi_base_date"] = sec_app["kospi_base_date"]
        sec_snap = st.secrets.get("snapshots", {})
        for date_str, vals in sec_snap.items():
            settings["snapshot"].setdefault(date_str, {}).update(dict(vals))
    except Exception:
        pass

    return settings


# ════════════════════════════════════════════════════════
# 6. 데이터 정제 및 지표 계산 (기존 유지)
# ════════════════════════════════════════════════════════

def process_portfolio(full_df: pd.DataFrame, prices: list[tuple]) -> pd.DataFrame:
    """주가 수집 결과를 받아 수익 지표·보유일수 계산 후 반환 (기존 유지)"""
    df = full_df.copy()
    df.columns = [c.strip() for c in df.columns]

    num_cols = ["수량", "매입단가", "52주최고가", "매입후최고가", "목표가", "주당 배당금", "목표수익률"]
    for c in num_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(
                df[c].astype(str).str.replace(",", "").str.replace("%", ""),
                errors="coerce",
            ).fillna(0)
        elif c == "목표수익률":
            df["목표수익률"] = 10.0

    df["현재가"],  df["전일종가"] = zip(*prices)

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
    """수익률 추이 정규화 및 KOSPI 상대 수익률 계산 (기존 유지)"""
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
# 7. 헬퍼 함수 (기존 유지)
# ════════════════════════════════════════════════════════

def get_cashflow_grade(amount: float) -> str:
    if amount >= 1_000_000: return "💎 Diamond"
    if amount >= 300_000:   return "🥇 Gold"
    if amount >= 100_000:   return "🥈 Silver"
    return "🥉 Bronze"


def find_matching_col(df: pd.DataFrame, account: str, stock: str = None):
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
    today  = now_kst.date()
    events = []
    for _, row in df.iterrows():
        name    = row.get("종목명", "")
        acc     = row.get("계좌명", "")
        div_amt = float(row.get("예상배당금", 0))
        if div_amt <= 0:
            continue
        months = DIVIDEND_SCHEDULE.get(name, [])
        if not months:
            continue
        pay_day_fixed = DIVIDEND_PAY_DAY.get(name, 0)
        for m in months:
            for year in [today.year, today.year + 1]:
                last_day = calendar.monthrange(year, m)[1]
                pay_day  = last_day if pay_day_fixed == 0 \
                           else min(pay_day_fixed, last_day)
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
                "예상배당금": div_amt / len(months),
            })
    events.sort(key=lambda x: (x["D_DAY"], -x["예상배당금"]))
    return events


# ════════════════════════════════════════════════════════
# 8. 메모 CRUD (기존 유지)
# ════════════════════════════════════════════════════════

def get_memo(memo_df: pd.DataFrame, stock_name: str, acc_name: str) -> str:
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
    now_str = now_kst.strftime("%Y-%m-%d %H:%M:%S")
    new_row = pd.DataFrame([{
        "종목명": stock_name, "계좌명": acc_name,
        "메모": text, "수정일시": now_str,
    }])
    mask       = ~((memo_df["종목명"] == stock_name) & (memo_df["계좌명"] == acc_name))
    updated_df = pd.concat([memo_df[mask], new_row], ignore_index=True)
    try:
        conn.update(worksheet=WS_MEMO, data=updated_df)
        return True, updated_df
    except Exception:
        return False, memo_df


# ════════════════════════════════════════════════════════
# 8-1. 배당 실적 로드 (기존 유지)
# ════════════════════════════════════════════════════════

def load_dividend_actual(conn, portfolio_df: pd.DataFrame = None) -> pd.DataFrame:
    try:
        df = conn.read(worksheet=WS_DIVIDEND, ttl=0)
        if df.empty or "입금일" not in df.columns:
            return pd.DataFrame()
        for col in ["주당금액", "세후금액"]:
            if col in df.columns:
                df[col] = pd.to_numeric(
                    df[col].astype(str).str.replace(",", "").str.replace("-", ""),
                    errors="coerce"
                ).fillna(0)
        if "세전금액" in df.columns and "주당금액" not in df.columns:
            df = df.rename(columns={"세전금액": "주당금액"})

        def _get_shares(row) -> float:
            if portfolio_df is None or portfolio_df.empty:
                return 0.0
            mask = (
                (portfolio_df["종목명"] == row["종목명"]) &
                (portfolio_df["계좌명"] == row["계좌명"])
            )
            matched = portfolio_df.loc[mask, "수량"]
            return float(matched.values[0]) if not matched.empty else 0.0

        _sheet_net = df["세후금액"].copy() if "세후금액" in df.columns else pd.Series(dtype=float)
        df["수량"]    = df.apply(_get_shares, axis=1)
        df["세전금액"] = df["주당금액"] * df["수량"]
        if "세후금액" not in df.columns:
            df["세후금액"] = 0.0
        if not _sheet_net.empty:
            df["세후금액"] = _sheet_net.values
        mask = df["세후금액"] == 0
        df.loc[mask, "세후금액"] = df.loc[mask, "세전금액"] * (1 - DIVIDEND_TAX_RATE)
        df["입금일"] = pd.to_datetime(df["입금일"], errors="coerce")
        df = df.dropna(subset=["입금일"]).sort_values("입금일")
        df["연도"] = df["입금일"].dt.year
        df["월"]   = df["입금일"].dt.month
        df["연월"] = df["입금일"].dt.strftime("%Y-%m")
        return df
    except Exception:
        return pd.DataFrame()


# ════════════════════════════════════════════════════════
# 8-2. 거래내역 & 평균단가 (기존 유지)
# ════════════════════════════════════════════════════════

def load_trades(conn) -> pd.DataFrame:
    try:
        from config import WS_TRADES
        df = conn.read(worksheet=WS_TRADES, ttl=0)
        if df.empty or "종목명" not in df.columns:
            return pd.DataFrame()
        for col in ["수량", "단가", "수수료"]:
            if col in df.columns:
                df[col] = pd.to_numeric(
                    df[col].astype(str).str.replace(",", ""),
                    errors="coerce"
                ).fillna(0)
        df["날짜"]   = pd.to_datetime(df["날짜"], errors="coerce")
        df["구분"]   = df["구분"].astype(str).str.strip()
        df["계좌명"] = df["계좌명"].astype(str).str.strip()
        df["종목명"] = df["종목명"].astype(str).str.strip()
        df = df.dropna(subset=["날짜"]).sort_values("날짜").reset_index(drop=True)
        return df
    except Exception:
        return pd.DataFrame()


def calc_avg_cost(trades_df: pd.DataFrame,
                  portfolio_df: pd.DataFrame = None) -> pd.DataFrame:
    """
    거래내역 → 계좌·종목별 평균단가·보유수량·실현손익 계산.

    매도 시 매입단가 조회 우선순위:
      1순위: 거래내역 내 이전 매수 기록 (qty_hold 누적)
      2순위: 매도 행의 메모 열에 숫자 입력 (예: '124000')
      3순위: portfolio_df(종목현황 시트)에서 조회
    """
    if trades_df.empty:
        return pd.DataFrame(
            columns=["계좌명","종목명","보유수량","평균단가","총매입금액","실현손익"]
        )
    results      = []
    sell_records = []
    groups       = trades_df.groupby(["계좌명","종목명"])

    for (acc, nm), grp in groups:
        qty_hold   = 0.0
        cost_total = 0.0
        realized   = 0.0
        first_buy_date = None

        for _, row in grp.iterrows():
            q     = float(row["수량"])
            price = float(row["단가"])
            fee   = float(row.get("수수료", 0) or 0)
            구분  = row["구분"]
            dt    = row.get("날짜", None)
            memo  = str(row.get("메모", "") or "").strip()

            if 구분 == "매수":
                if first_buy_date is None and pd.notna(dt):
                    first_buy_date = dt
                cost_total += q * price + fee
                qty_hold   += q
            elif 구분 == "매도":
                # 매입단가 조회 우선순위 ──────────────────────────
                # 2순위: 메모 열에 숫자 입력 → 매입단가로 사용
                # (매도 행의 메모에 매입단가만 숫자로 기입하면 됨)
                if qty_hold == 0 and memo:
                    try:
                        memo_price = float(memo.replace(",", ""))
                        if memo_price > 0:
                            qty_hold   = q
                            cost_total = q * memo_price
                    except ValueError:
                        pass

                # 3순위: portfolio_df(종목현황 시트)에서 조회
                if qty_hold == 0 and portfolio_df is not None and not portfolio_df.empty:
                    _ac = next((c for c in ["계좌명","계좌"] if c in portfolio_df.columns), None)
                    if _ac:
                        _pm = ((portfolio_df[_ac].astype(str).str.strip() == acc) &
                               (portfolio_df["종목명"].astype(str).str.strip() == nm))
                    else:
                        _pm = portfolio_df["종목명"].astype(str).str.strip() == nm
                    if _pm.any():
                        qty_hold   = float(portfolio_df.loc[_pm, "수량"].iloc[0])
                        cost_total = qty_hold * float(portfolio_df.loc[_pm, "매입단가"].iloc[0])
                        if first_buy_date is None:
                            _rd = portfolio_df.loc[_pm].get(
                                "최초매입일", pd.Series([None])).iloc[0]
                            if _rd is not None and pd.notna(_rd):
                                first_buy_date = _rd

                if qty_hold > 0:
                    avg      = cost_total / qty_hold
                    sold_q   = min(q, qty_hold)
                    gain     = (price - avg) * sold_q - fee
                    realized += gain
                    cost_total = max(0, cost_total - avg * sold_q)
                    qty_hold   = max(0, qty_hold - sold_q)
                    hold_days  = None
                    if first_buy_date is not None and pd.notna(dt):
                        try:
                            hold_days = (pd.Timestamp(dt) - pd.Timestamp(first_buy_date)).days
                        except Exception:
                            hold_days = None
                    sell_records.append({
                        "매도일":    pd.Timestamp(dt).strftime("%Y-%m-%d") if pd.notna(dt) else "",
                        "계좌명":    acc,
                        "종목명":    nm,
                        "매도수량":  int(sold_q),
                        "매입단가":  round(avg),
                        "매도단가":  int(price),
                        "매도금액":  int(price * sold_q),
                        "실현손익":  round(gain),
                        "수익률(%)": round((price / avg - 1) * 100, 2) if avg > 0 else 0,
                        "보유일수":  hold_days,
                        "수수료":    int(fee),
                    })

        avg_cost = cost_total / qty_hold if qty_hold > 0 else 0.0
        if qty_hold <= 0:
            avg_cost = cost_total = 0.0
        results.append({
            "계좌명":    acc,
            "종목명":    nm,
            "보유수량":  qty_hold,
            "평균단가":  round(avg_cost),
            "총매입금액": round(cost_total),
            "실현손익":  round(realized),
        })

    avg_df  = pd.DataFrame(results)
    sell_df = pd.DataFrame(sell_records) if sell_records else pd.DataFrame(
        columns=["매도일","계좌명","종목명","매도수량","매입단가","매도단가",
                 "매도금액","실현손익","수익률(%)","보유일수","수수료"]
    )
    avg_df.attrs["sell_df"] = sell_df
    return avg_df


def merge_trades_to_portfolio(portfolio_df: pd.DataFrame,
                               avg_df: pd.DataFrame) -> pd.DataFrame:
    """거래내역(추가분) + 종목현황 시트(기존분) 합산 병합 (기존 유지)"""
    if avg_df.empty:
        return portfolio_df
    df = portfolio_df.copy()
    acc_col = None
    for candidate in ["계좌명", "계좌", "account", "Account"]:
        if candidate in df.columns:
            acc_col = candidate
            break
    avg_acc_col = "계좌명" if "계좌명" in avg_df.columns else "계좌"
    use_acc = acc_col is not None

    for _, row in avg_df.iterrows():
        row_acc = str(row.get(avg_acc_col, "")).strip()
        row_nm  = str(row.get("종목명", "")).strip()
        if use_acc:
            mask = (
                df[acc_col].astype(str).str.strip() == row_acc
            ) & (
                df["종목명"].astype(str).str.strip() == row_nm
            )
        else:
            mask = df["종목명"].astype(str).str.strip() == row_nm
        if not mask.any():
            continue
        trade_qty_net = float(row.get("보유수량", 0))
        trade_cost    = float(row.get("총매입금액", 0))
        sheet_qty     = float(df.loc[mask, "수량"].iloc[0])
        sheet_price   = float(df.loc[mask, "매입단가"].iloc[0])
        sheet_cost    = sheet_qty * sheet_price
        new_qty       = sheet_qty + trade_qty_net
        if new_qty <= 0:
            df.loc[mask, "수량"] = 0
            continue
        if trade_qty_net > 0 and trade_cost > 0:
            new_avg = (sheet_cost + trade_cost) / new_qty
        elif trade_qty_net < 0:
            new_avg = sheet_price
        else:
            new_avg = sheet_price
        df.loc[mask, "수량"]     = new_qty
        df.loc[mask, "매입단가"] = round(new_avg)
        df.loc[mask, "매입금액"] = new_qty * round(new_avg)
    return df


# ════════════════════════════════════════════════════════
# 9. 내보내기 (기존 유지)
# ════════════════════════════════════════════════════════

def build_export_df(df: pd.DataFrame) -> pd.DataFrame:
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
# 10. 목표가 도달 토스트 알림 (기존 유지)
# ════════════════════════════════════════════════════════

def check_and_toast_targets(df: pd.DataFrame):
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
