# Trigger Saham IDX → Telegram

Scan saham IDX otomatis via GitHub Actions dan kirim alert ke Telegram.

- **Intraday:** tiap 30 menit (09:00–15:30 WIB), hanya kirim sinyal *baru*
- **EOD:** 16:30 WIB, rangkuman lengkap

## Trigger
| Trigger | Intraday | EOD |
|---|---|---|
| SuperTrend (10,3) flip bullish | ✅ | ✅ |
| Breakout high 20 hari | ✅ | ✅ |
| Volume spike (1.5x intraday / 2x EOD) | ✅ | ✅ |
| Naik ≥ 5% | ✅ | ✅ |
| Golden cross MA20/MA50 | | ✅ |
| RSI keluar oversold | | ✅ |
| Bullish engulfing / Hammer | | ✅ |

Filter: harga ≥ 50, rata-rata nilai transaksi 20 hari ≥ Rp 1 M, minimal 2 trigger.
Semua bisa diubah di `CFG` di atas `scanner.py`.

## Setup (±15 menit)

1. **Buat bot:** chat @BotFather → `/newbot` → simpan token.
2. **Ambil chat ID:** kirim pesan apa saja ke bot kamu, lalu buka
   `https://api.telegram.org/bot<TOKEN>/getUpdates` → cari `"chat":{"id": ...}`.
   (Untuk grup/channel: tambahkan bot ke grup, ID-nya biasanya diawali `-100`.)
3. **Buat repo GitHub** (public = Actions unlimited; private = 2.000 menit/bulan),
   upload semua file ini termasuk folder `.github/`.
4. **Settings → Secrets and variables → Actions → New secret:**
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
5. **Test:** tab Actions → Trigger Saham → *Run workflow* → mode `eod`.

## Daftar saham lengkap
Buka idx.co.id → Data Pasar → Data Saham → Daftar Saham → unduh Excel,
salin kolom **Kode** ke `tickers.txt` (boleh satu kode per baris).

## Test lokal
```bash
pip install -r requirements.txt
python scanner.py --mode eod --dry-run
```

## Catatan
- Data Yahoo Finance delay ±10–15 menit; jadwal cron GitHub juga bisa telat 5–20 menit.
- Kadang Yahoo membatasi request dari IP GitHub. Kalau banyak ticker gagal,
  kecilkan `chunk_size` di `CFG`.
- Alert bukan rekomendasi beli/jual.
