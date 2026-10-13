"""
Trigger Saham IDX -> Telegram
Mode:
  python scanner.py --mode eod        # scan lengkap setelah market tutup
  python scanner.py --mode intraday   # scan cepat saat market buka (anti-duplikat)
  tambahkan --dry-run untuk print ke terminal tanpa kirim Telegram
"""
import os
import json
import time
import argparse
import datetime as dt
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import requests

WIB = dt.timezone(dt.timedelta(hours=7))
BASE = Path(__file__).parent
STATE_FILE = BASE / "state.json"

# ================== KONFIGURASI ==================
CFG = {
    "min_price": 50,              # buang saham gocap
    "min_avg_value": 1e9,         # rata2 nilai transaksi 20 hari >= Rp 1 M
    "vol_spike_eod": 2.0,         # volume >= 2x rata2 20 hari
    "vol_spike_intraday": 1.5,    # intraday lebih rendah karena candle belum selesai
    "breakout_len": 20,           # breakout high N hari
    "st_period": 10,              # SuperTrend ATR period
    "st_mult": 3.0,               # SuperTrend multiplier
    "big_move_pct": 5.0,          # kenaikan harian >= 5%
    "min_signals": 2,             # minimal jumlah trigger agar dikirim
    "max_alerts": 25,             # maksimal saham per pesan
    "chunk_size": 100,            # ticker per batch download
}
# =================================================


# ---------- Util ----------
def rp(x):
    return f"{x:,.0f}".replace(",", ".")


def load_tickers():
    out = []
    for line in (BASE / "tickers.txt").read_text().splitlines():
        line = line.split("#")[0].strip().upper()
        for t in line.replace(",", " ").split():
            out.append(t if t.endswith(".JK") else f"{t}.JK")
    return sorted(set(out))


def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=1, sort_keys=True))


# ---------- Data ----------
def download(tickers, period="1y"):
    import yfinance as yf

    frames = {}
    n = CFG["chunk_size"]
    for i in range(0, len(tickers), n):
        batch = tickers[i:i + n]
        df = None
        for attempt in range(3):
            try:
                df = yf.download(batch, period=period, interval="1d",
                                 group_by="ticker", auto_adjust=False,
                                 threads=True, progress=False)
                break
            except Exception as e:
                print(f"[batch {i}] gagal ({e}), retry...")
                time.sleep(5 * (attempt + 1))
        if df is None or df.empty:
            continue
        for t in batch:
            try:
                sub = df[t] if isinstance(df.columns, pd.MultiIndex) else df
            except KeyError:
                continue
            sub = sub.dropna(subset=["Close"])
            sub = sub[sub["Volume"] > 0]
            if len(sub) >= 60:
                frames[t] = sub
        print(f"Download {min(i + n, len(tickers))}/{len(tickers)} | valid: {len(frames)}")
        time.sleep(2)
    return frames


# ---------- Indikator ----------
def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def atr(df, n):
    pc = df["Close"].shift()
    tr = pd.concat([df["High"] - df["Low"],
                    (df["High"] - pc).abs(),
                    (df["Low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def supertrend(df, n, m):
    a = atr(df, n).values
    hl2 = ((df["High"] + df["Low"]) / 2).values
    c = df["Close"].values
    ub, lb = hl2 + m * a, hl2 - m * a
    fu, fl = ub.copy(), lb.copy()
    trend = np.ones(len(df), dtype=int)
    for i in range(1, len(df)):
        fu[i] = ub[i] if (ub[i] < fu[i - 1] or c[i - 1] > fu[i - 1]) else fu[i - 1]
        fl[i] = lb[i] if (lb[i] > fl[i - 1] or c[i - 1] < fl[i - 1]) else fl[i - 1]
        if trend[i - 1] == 1 and c[i] < fl[i]:
            trend[i] = -1
        elif trend[i - 1] == -1 and c[i] > fu[i]:
            trend[i] = 1
        else:
            trend[i] = trend[i - 1]
    return trend


# ---------- Analisa ----------
def analyze(ticker, df, mode):
    o, h, l, c, v = (df[k] for k in ["Open", "High", "Low", "Close", "Volume"])
    price, prev_close = float(c.iloc[-1]), float(c.iloc[-2])
    if price < CFG["min_price"]:
        return None

    vol_ma = float(v.iloc[-21:-1].mean())
    avg_value = float((c * v).iloc[-21:-1].mean())
    if not avg_value or avg_value < CFG["min_avg_value"]:
        return None

    chg = (price / prev_close - 1) * 100
    vr = float(v.iloc[-1]) / vol_ma if vol_ma > 0 else 0.0
    sigs = []

    # 1. SuperTrend flip bullish
    st = supertrend(df, CFG["st_period"], CFG["st_mult"])
    if st[-1] == 1 and st[-2] == -1:
        sigs.append("🟢 SuperTrend flip bullish")

    # 2. Breakout high N hari
    L = CFG["breakout_len"]
    hh = float(h.iloc[-(L + 1):-1].max())
    if price > hh:
        sigs.append(f"🚀 Breakout high {L} hari ({rp(hh)})")

    # 3. Volume spike (candle hijau / naik)
    th = CFG["vol_spike_eod"] if mode == "eod" else CFG["vol_spike_intraday"]
    if vr >= th and chg > 0:
        sigs.append(f"📈 Volume spike {vr:.1f}x rata2")

    # 4. Kenaikan besar
    if chg >= CFG["big_move_pct"]:
        sigs.append(f"🔥 Naik {chg:.1f}%")

    # Trigger yang butuh candle sudah close -> hanya EOD
    if mode == "eod":
        ma20, ma50 = c.rolling(20).mean(), c.rolling(50).mean()
        if ma20.iloc[-1] > ma50.iloc[-1] and ma20.iloc[-2] <= ma50.iloc[-2]:
            sigs.append("✨ Golden cross MA20/MA50")

        r = rsi(c)
        if r.iloc[-1] > 30 >= r.iloc[-2]:
            sigs.append(f"↗️ RSI keluar oversold ({r.iloc[-1]:.0f})")

        o1, c1, o0, c0 = o.iloc[-2], c.iloc[-2], o.iloc[-1], c.iloc[-1]
        if c1 < o1 and c0 > o0 and o0 <= c1 and c0 >= o1:
            sigs.append("🕯 Bullish engulfing")

        body = abs(c0 - o0)
        lower = min(o0, c0) - l.iloc[-1]
        upper = h.iloc[-1] - max(o0, c0)
        if body > 0 and lower >= 2 * body and upper <= body and c0 < ma20.iloc[-1]:
            sigs.append("🔨 Hammer di area bawah")

    if len(sigs) < CFG["min_signals"]:
        return None

    return {
        "ticker": ticker.replace(".JK", ""),
        "price": price,
        "chg": chg,
        "vr": vr,
        "value": price * float(v.iloc[-1]),
        "sigs": sigs,
    }


# ---------- Telegram ----------
def send_telegram(text, dry_run=False):
    if dry_run:
        print(text)
        return
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat = os.environ["TELEGRAM_CHAT_ID"]
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > 3900:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    chunks.append(cur)
    for ch in chunks:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": ch, "parse_mode": "HTML",
                                "disable_web_page_preview": True}, timeout=30)
        if not r.ok:
            print("Telegram error:", r.text)
        time.sleep(1)


def format_msg(mode, results, n_scanned, now):
    title = "EOD" if mode == "eod" else f"Intraday {now:%H:%M} WIB"
    lines = [f"<b>📊 Trigger Saham — {title}</b>",
             f"<i>{now:%d %b %Y} · scan {n_scanned} saham · {len(results)} trigger</i>", ""]
    for x in results:
        lines.append(f"<b>{x['ticker']}</b> {rp(x['price'])} ({x['chg']:+.1f}%) · "
                     f"Vol {x['vr']:.1f}x · Val Rp {x['value'] / 1e9:.1f} M")
        lines += [f"  • {s}" for s in x["sigs"]]
        lines.append("")
    lines.append("<i>Bukan rekomendasi beli/jual. DYOR.</i>")
    return "\n".join(lines)


# ---------- Main ----------
def run(mode, frames, dry_run=False, force=False):
    now = dt.datetime.now(WIB)
    if not frames:
        print("Tidak ada data.")
        return

    # Cek apakah ada data hari ini (deteksi libur bursa)
    last_dates = Counter(df.index[-1].date() for df in frames.values())
    latest = last_dates.most_common(1)[0][0]
    if latest != now.date() and not (dry_run or force):
        print(f"Data terakhir {latest}, bukan hari ini -> kemungkinan libur. Skip.")
        return
    frames = {t: df for t, df in frames.items() if df.index[-1].date() == latest}

    results = []
    for t, df in frames.items():
        try:
            res = analyze(t, df, mode)
            if res:
                results.append(res)
        except Exception as e:
            print(f"{t}: error {e}")

    # Anti-duplikat untuk intraday: hanya kirim sinyal baru hari ini
    state = load_state()
    today = str(now.date())
    if state.get("date") != today:
        state = {"date": today, "sent": {}}
    if mode == "intraday":
        fresh = []
        for x in results:
            seen = set(state["sent"].get(x["ticker"], []))
            keys = {s.split(" ")[1] for s in x["sigs"]}  # nama trigger tanpa angka
            if keys - seen:
                state["sent"][x["ticker"]] = sorted(seen | keys)
                fresh.append(x)
        results = fresh
    save_state(state)

    results.sort(key=lambda x: (len(x["sigs"]), x["value"]), reverse=True)
    results = results[:CFG["max_alerts"]]

    if not results:
        print("Tidak ada trigger baru.")
        if mode == "eod":
            send_telegram(f"📊 EOD {now:%d %b %Y}: tidak ada saham yang memenuhi trigger.", dry_run)
        return
    send_telegram(format_msg(mode, results, len(frames), now), dry_run)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["eod", "intraday"], default="eod")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="abaikan cek hari libur (untuk test manual)")
    args = ap.parse_args()

    tickers = load_tickers()
    print(f"Total ticker: {len(tickers)}")
    run(args.mode, download(tickers), args.dry_run, args.force)
