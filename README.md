# tesProjek

Data teknikal harian saham BEI.

GitHub Actions menjalankan `screener.py` setiap hari bursa pukul 16:20 WIB (17:20 WITA), setelah penutupan. Field `final` bernilai false bila data diambil sebelum 16:15 WIB (masih intraday). Hasilnya di-commit ke folder `data/`:

| File | Isi |
|---|---|
| `data/latest.json` | Data terbaru: `tanggal_data`, `segar` (true bila tanggal data = hari ini), IHSG, kandidat teknikal, top gainers likuid, dan metrik semua saham likuid |
| `data/YYYY-MM-DD.json` | Arsip harian dengan isi yang sama |
| `data/harian-YYYY-MM-DD.csv` | OHLCV, perubahan saat buka (`open_pct`) dan saat tutup (`chg_pct`) untuk semua saham yang diperdagangkan |

## Skor teknikal (0-3)

- +1 ditutup naik dan dekat harga tertinggi harian (posisi tutup >= 0,8)
- +1 volume >= 1,5x rata-rata 20 hari
- +1 breakout di atas harga tertinggi 20 hari, atau tren naik (harga > MA20 > MA50) dengan RSI 50-70

Kandidat = saham likuid (rata-rata nilai transaksi 20 hari >= Rp10 miliar), skor >= 2, dan bukan kemungkinan ARA (kenaikan >= 19%).

## Catatan

- Sumber data: Yahoo Finance (kode `.JK`). Data bisa terlambat atau kosong; selalu cek `tanggal_data` dan `segar` sebelum dipakai.
- Daftar saham di `tickers.txt` berasal dari daftar emiten per Februari 2025. Saham yang baru IPO setelahnya perlu ditambahkan manual.
- Status papan pemantauan khusus, notasi khusus, suspensi, dan UMA tidak dicek di sini; itu dilakukan oleh laporan harian lewat berita.
- Jalankan manual dari tab Actions > Screener harian BEI > Run workflow.

Analisis berbasis data publik, bukan rekomendasi beli/jual.
