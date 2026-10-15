"""Screener teknikal harian saham BEI.

Dijalankan oleh GitHub Actions setiap hari bursa setelah penutupan.
Menarik data harian semua saham di tickers.txt dari Yahoo Finance (kode.JK),
menghitung indikator teknikal, dan menulis:
  data/latest.json          ringkasan + kandidat + data semua saham likuid
  data/YYYY-MM-DD.json      salinan arsip untuk tanggal data tersebut
  data/harian-YYYY-MM-DD.csv  OHLCV + perubahan untuk semua saham yang diperdagangkan
"""
from __future__ import annotations

import json
import math
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd

WIB = timezone(timedelta(hours=7))
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

MIN_NILAI_RATA2 = 10_000_000_000  # Rp10 miliar rata-rata nilai transaksi 20 hari
ARA_PROXY = 19.0                  # kenaikan >= 19% ditandai kemungkinan ARA
CHUNK = 80


# ---------- indikator (fungsi murni, bisa diuji tanpa jaringan) ----------

def rsi(close: pd.Series, n: int = 14) -> float | None:
    d = close.diff().dropna()
    if len(d) < n:
        return None
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    if dn.iloc[-1] == 0:
        return 100.0
    rs = up.iloc[-1] / dn.iloc[-1]
    return round(100 - 100 / (1 + rs), 1)


def metrik(df: pd.DataFrame) -> dict | None:
    """df: kolom Open, High, Low, Close, Volume, index tanggal naik. Baris terakhir = hari data."""
    df = df.dropna(subset=["Close"])
    df = df[df["Volume"].fillna(0) >= 0]
    if len(df) < 25:
        return None
    t, p = df.iloc[-1], df.iloc[-2]
    c, o, h, l, v = map(float, (t["Close"], t["Open"], t["High"], t["Low"], t["Volume"] or 0))
    pc = float(p["Close"])
    if pc <= 0 or c <= 0:
        return None
    nilai = c * v
    hist = df.iloc[-21:-1]  # 20 hari sebelum hari ini
    nilai20 = float((hist["Close"] * hist["Volume"]).mean())
    vol20 = float(hist["Volume"].mean())
    hi20 = float(hist["High"].max())
    ma20 = float(df["Close"].iloc[-20:].mean())
    ma50 = float(df["Close"].iloc[-50:].mean()) if len(df) >= 50 else None
    r = rsi(df["Close"].iloc[-60:])
    rng = h - l
    pos = (c - l) / rng if rng > 0 else (1.0 if c >= pc else 0.0)
    chg = (c / pc - 1) * 100
    m = {
        "tanggal": df.index[-1].strftime("%Y-%m-%d"),
        "open": o, "high": h, "low": l, "close": c, "prev_close": pc,
        "chg_pct": round(chg, 2),
        "open_pct": round((o / pc - 1) * 100, 2),
        "volume": int(v),
        "nilai": round(nilai),
        "nilai_rata20": round(nilai20) if not math.isnan(nilai20) else None,
        "vol_ratio": round(v / vol20, 2) if vol20 > 0 else None,
        "posisi_tutup": round(pos, 2),          # 1 = tutup di harga tertinggi
        "breakout20": bool(c > hi20),
        "ma20": round(ma20, 2),
        "ma50": round(ma50, 2) if ma50 else None,
        "di_atas_ma": bool(ma50 and c > ma20 > ma50),
        "rsi14": r,
        "ret5_pct": round((c / float(df["Close"].iloc[-6]) - 1) * 100, 2) if len(df) >= 6 else None,
        "ret20_pct": round((c / float(df["Close"].iloc[-21]) - 1) * 100, 2) if len(df) >= 21 else None,
        "ara_suspect": bool(chg >= ARA_PROXY),
    }
    m["likuid"] = bool(m["nilai_rata20"] and m["nilai_rata20"] >= MIN_NILAI_RATA2)
    m["skor_teknikal"], m["alasan_teknikal"] = skor(m)
    return m


def skor(m: dict) -> tuple[int, list[str]]:
    """Skor penutupan 0-3, sama dengan komponen 'penutupan' di playbook."""
    s, why = 0, []
    if m["chg_pct"] > 0 and m["posisi_tutup"] >= 0.8:
        s += 1; why.append("tutup dekat high")
    if m["vol_ratio"] and m["vol_ratio"] >= 1.5:
        s += 1; why.append(f"volume {m['vol_ratio']}x rata-rata")
    if m["breakout20"]:
        s += 1; why.append("breakout high 20 hari")
    elif m["di_atas_ma"] and m["rsi14"] and 50 <= m["rsi14"] <= 70:
        s += 1; why.append(f"tren naik (MA20>MA50, RSI {m['rsi14']})")
    if m["ara_suspect"]:
        why.append("kemungkinan ARA - hati-hati")
    if m["rsi14"] and m["rsi14"] > 80:
        why.append(f"RSI {m['rsi14']} jenuh beli")
    return s, why


def susun(hasil: dict[str, dict], ihsg: dict | None, sekarang: datetime) -> dict:
    tanggal_data = max((m["tanggal"] for m in hasil.values()), default=None)
    hari_ini = [ (k, m) for k, m in hasil.items() if m["tanggal"] == tanggal_data ]
    likuid = [ (k, m) for k, m in hari_ini if m["likuid"] ]
    kandidat = sorted(
        [ (k, m) for k, m in likuid if m["skor_teknikal"] >= 2 and not m["ara_suspect"] ],
        key=lambda km: (-km[1]["skor_teknikal"], -km[1]["nilai"]),
    )[:30]
    gainers = sorted(likuid, key=lambda km: -km[1]["chg_pct"])[:20]
    return {
        "tanggal_data": tanggal_data,
        "dibuat": sekarang.isoformat(),
        "segar": tanggal_data == sekarang.strftime("%Y-%m-%d"),
        # final = diambil setelah penutupan (16:15 WIB). Sebelum itu data masih intraday.
        "final": tanggal_data != sekarang.strftime("%Y-%m-%d")
                 or (sekarang.hour, sekarang.minute) >= (16, 15),
        "ihsg": ihsg,
        "jumlah": {"universe": len(hasil), "diperdagangkan": len(hari_ini), "likuid": len(likuid)},
        "kriteria": {
            "likuid": f"rata-rata nilai transaksi 20 hari >= Rp{MIN_NILAI_RATA2/1e9:.0f} miliar",
            "skor_teknikal": "0-3: +1 tutup dekat high & naik, +1 volume >=1.5x, +1 breakout 20 hari atau tren naik dengan RSI 50-70",
            "kandidat": "likuid, skor >= 2, bukan kemungkinan ARA; urut skor lalu nilai transaksi",
        },
        "kandidat": [ {"kode": k, **m} for k, m in kandidat ],
        "top_gainers_likuid": [ {"kode": k, "chg_pct": m["chg_pct"], "nilai": m["nilai"]} for k, m in gainers ],
        "likuid": { k: m for k, m in likuid },
    }


# ---------- pengambilan data ----------

def unduh(kode: list[str]) -> dict[str, pd.DataFrame]:
    import yfinance as yf
    out: dict[str, pd.DataFrame] = {}
    for i in range(0, len(kode), CHUNK):
        part = kode[i:i + CHUNK]
        syms = [f"{k}.JK" for k in part]
        for attempt in range(3):
            try:
                df = yf.download(syms, period="6mo", interval="1d", group_by="ticker",
                                 auto_adjust=False, progress=False, threads=True)
                break
            except Exception as e:  # jaringan / rate limit
                print(f"chunk {i}: percobaan {attempt+1} gagal: {e}", file=sys.stderr)
                time.sleep(10 * (attempt + 1))
        else:
            continue
        for k, s in zip(part, syms):
            try:
                sub = df[s] if len(syms) > 1 else df
                sub = sub[["Open", "High", "Low", "Close", "Volume"]].dropna(how="all")
                if len(sub):
                    out[k] = sub
            except KeyError:
                pass
        time.sleep(2)
    return out


def unduh_ihsg() -> dict | None:
    import yfinance as yf
    try:
        df = yf.download("^JKSE", period="1mo", interval="1d", auto_adjust=False, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.dropna(subset=["Close"])
        c, pc = float(df["Close"].iloc[-1]), float(df["Close"].iloc[-2])
        return {"tanggal": df.index[-1].strftime("%Y-%m-%d"), "close": round(c, 2),
                "chg_pct": round((c / pc - 1) * 100, 2)}
    except Exception as e:
        print(f"IHSG gagal: {e}", file=sys.stderr)
        return None


def main() -> int:
    sekarang = datetime.now(WIB)
    kode = [k.strip() for k in (ROOT / "tickers.txt").read_text().split() if k.strip()]
    data = unduh(kode)
    hasil = {}
    for k, df in data.items():
        try:
            m = metrik(df)
            if m:
                hasil[k] = m
        except Exception as e:
            print(f"{k}: {e}", file=sys.stderr)
    if not hasil:
        print("Tidak ada data sama sekali; berhenti tanpa menulis.", file=sys.stderr)
        return 1
    out = susun(hasil, unduh_ihsg(), sekarang)
    DATA.mkdir(exist_ok=True)
    js = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
    (DATA / "latest.json").write_text(js)
    (DATA / f"{out['tanggal_data']}.json").write_text(js)
    rows = [ {"kode": k, **{f: m[f] for f in ("open","high","low","close","prev_close","chg_pct","open_pct","volume","nilai")}}
             for k, m in hasil.items() if m["tanggal"] == out["tanggal_data"] ]
    pd.DataFrame(rows).sort_values("kode").to_csv(DATA / f"harian-{out['tanggal_data']}.csv", index=False)
    print(f"tanggal_data={out['tanggal_data']} segar={out['segar']} "
          f"likuid={out['jumlah']['likuid']} kandidat={len(out['kandidat'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
