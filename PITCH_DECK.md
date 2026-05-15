# BolangTravel — AI Travel Agent
### Pitch Deck Summary

---

## Slide 1 — Problem Statement

> Merencanakan perjalanan di Indonesia masih menjadi pengalaman yang melelahkan dan terfragmentasi.

**4 Masalah Utama:**

1. **Informasi Tersebar di Banyak Platform**
   Pengguna harus berpindah-pindah antara Google Maps, aplikasi cuaca, situs BMKG, dan forum transportasi hanya untuk merencanakan satu perjalanan sederhana — membuang waktu dan menurunkan kenyamanan.

2. **Data Transportasi Umum Tidak Terintegrasi**
   Opsi transportasi seperti bus, angkot, atau KRL seringkali tidak muncul dalam satu tampilan terpadu. Pengguna kesulitan membandingkan waktu tempuh dan biaya antar moda secara langsung.

3. **Cuaca Tidak Diperhitungkan dalam Perencanaan Rute**
   Mayoritas aplikasi navigasi tidak menyertakan informasi prediksi cuaca dari sumber terpercaya (BMKG) — padahal kondisi hujan atau angin kencang sangat memengaruhi pilihan kendaraan dan waktu keberangkatan.

4. **Tidak Ada Asisten Travel Personal Berbahasa Indonesia**
   Chatbot dan asisten perjalanan yang ada umumnya berbasis bahasa Inggris, tidak memahami konteks lokal Indonesia (nama jalan, destinasi wisata lokal, jam operasional real-time), dan tidak mampu menanyakan informasi yang kurang dari pengguna secara natural.

---

## Slide 2 — Solution Overview

> **BolangTravel** adalah AI Travel Agent berbasis Telegram yang membantu pengguna merencanakan perjalanan secara lengkap cukup dengan percakapan natural dalam Bahasa Indonesia.

### Apa yang dilakukan BolangTravel?

| Kebutuhan Pengguna | Solusi BolangTravel |
|---|---|
| "Mau ke mana nih?" | Agent AI bertanya balik jika info belum lengkap (kendaraan, tanggal, titik awal) |
| Rute dari posisi saya | Gunakan lokasi GPS real-time dari Telegram |
| Perbandingan kendaraan | Motor, Mobil, Bus/Transportasi Umum, Jalan Kaki — sekaligus |
| Prediksi cuaca hari H | Data langsung dari BMKG, ditampilkan bersama itinerari |
| Link rute yang praktis | Short URL `maps.app.goo.gl` langsung bisa dibagikan |
| Pulang ke rumah | Simpan lokasi rumah sekali, pakai selamanya |

### Nilai Utama
- **Satu platform, semua informasi** — tidak perlu buka banyak aplikasi
- **Percakapan natural** — tanya seperti ke teman, bukan mengisi form
- **Konteks lokal** — memahami nama tempat, kondisi Indonesia, dan layanan BMKG

---

## Slide 3 — Technical Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     PENGGUNA (Telegram)                  │
│         Kirim pesan / Bagikan Lokasi GPS                 │
└────────────────────┬────────────────────────────────────┘
                     │  python-telegram-bot
                     ▼
┌─────────────────────────────────────────────────────────┐
│                  TELEGRAM BOT LAYER                      │
│  - Handle message, location, commands                    │
│  - Simpan lokasi saat ini & lokasi rumah (per chat_id)   │
│  - Inject konteks lokasi ke agent input                  │
│  - Parse [SCREENSHOT:path] → kirim foto                  │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│              LANGCHAIN AGENT CORE (GPT-4o)               │
│  - System Prompt: panduan langkah 0–7                    │
│  - Follow-up question logic (tujuan/asal/tanggal/PP)     │
│  - Tool orchestration (cari → validasi → rute → cuaca)   │
│  - Conversation history (6 turn rolling window)          │
└──┬──────────────┬───────────────────┬───────────────────┘
   │              │                   │
   ▼              ▼                   ▼
┌──────────┐ ┌──────────────┐ ┌─────────────────────────┐
│  Google  │ │     BMKG     │ │   Nominatim (OSM)       │
│  Maps    │ │  Weather API │ │   Reverse / Forward     │
│(Playwright│ │ (Playwright) │ │   Geocoding             │
│scraping) │ └──────────────┘ └─────────────────────────┘
│          │
│ Tools:   │
│ • search_places_on_maps
│ • get_place_details
│ • get_directions
│   - driving (Mobil)
│   - two-wheeler (Motor)
│   - transit (Bus)
│   - walking (Kaki)
│ • get_weather
│ • web_search
└──────────┘
```

### Stack Teknologi

| Layer | Teknologi |
|---|---|
| Interface | Telegram Bot API (`python-telegram-bot`) |
| AI Agent | LangChain `create_tool_calling_agent` + OpenAI GPT-4o |
| Browser Automation | Playwright (async, stealth mode) |
| Maps Data | Google Maps scraping (rute, tempat, short URL, screenshot) |
| Weather Data | BMKG Public API (`api.bmkg.go.id`) via Playwright |
| Geocoding | Nominatim / OpenStreetMap (tanpa API key) |
| Data Models | Pydantic v2 (`DirectionsOutput`, `PlaceDetails`, `WeatherOutput`) |
| Runtime | Python 3.11+, asyncio |

---

## Slide 4 — Features & Future Impact

### Fitur yang Sudah Berjalan

**🗺️ Perencanaan Rute Cerdas**
- Multi-waypoint route planning dengan urutan logis
- Perbandingan 4 moda kendaraan: Motor · Mobil · Bus/Trans.Um · Jalan Kaki
- Short link Google Maps (`maps.app.goo.gl`) siap dibagikan
- Screenshot rute & detail tempat otomatis

**🌦️ Cuaca Terintegrasi**
- Prediksi cuaca dari BMKG untuk kota tujuan di hari perjalanan
- Saran persiapan (bawa payung, dll.) berdasarkan kondisi

**📍 Konteks Lokasi Personal**
- Lokasi saat ini via GPS Telegram (reverse geocode otomatis)
- Penyimpanan lokasi rumah (`/setrumah`) — bisa digunakan untuk rute pulang
- Agent menolak melanjutkan jika lokasi belum tersedia dan diperlukan

**🤖 Conversational AI**
- Tanya balik jika input belum lengkap (tujuan / asal / tanggal / pulang-balik)
- Conversation history 6 turn — konteks percakapan terjaga
- Bahasa Indonesia natural, respons ramah dan ringkas

**🔍 Validasi Tempat Real-Time**
- Cek jam buka sesuai hari perjalanan — buang tempat yang tutup
- Rating, alamat, dan foto lokasi langsung dari Google Maps

---

### Dampak & Roadmap

**Dampak Saat Ini**
- Memangkas waktu riset perjalanan dari 15–30 menit menjadi < 2 menit
- Satu antarmuka menggantikan 4–5 aplikasi (Maps, BMKG, browser, dll.)
- Aksesibel — cukup punya Telegram, tanpa install tambahan

**Pengembangan Selanjutnya**

| Prioritas | Fitur |
|---|---|
| 🔴 High | Integrasi API ride-hailing (Gojek / Grab) untuk estimasi tarif real-time |
| 🔴 High | Database tempat wisata lokal terverifikasi (beyond Google Maps) |
| 🟡 Medium | Riwayat perjalanan & rekomendasi personal berbasis kebiasaan |
| 🟡 Medium | Notifikasi proaktif: pengingat keberangkatan + update cuaca H-1 |
| 🟢 Low | Web dashboard untuk trip planning yang lebih visual |
| 🟢 Low | Multi-language support (English, Javanese, Sundanese) |

---

*BolangTravel — Travel cerdas, cukup dari satu chat.*
