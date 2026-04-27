# rebuild.py — reconstruct clean flowchart.html and index.html
import os

HTML = r"""<!DOCTYPE html>
<html lang="th">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>IAMROOT AI Data Flow Diagram</title>
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #0d1b2a 0%, #1a3a2a 100%);
            min-height: 100vh;
            margin: 0;
            padding: 20px 16px 40px;
        }
        .page-header { text-align: center; margin-bottom: 28px; }
        .page-header h1 { color: #fff; font-size: 1.7rem; font-weight: 700; margin: 0 0 6px; letter-spacing: 1px; }
        .page-header p { color: #88b4a0; font-size: 0.9rem; margin: 0; }
        .tabs { display: flex; justify-content: center; gap: 10px; margin-bottom: 20px; flex-wrap: wrap; }
        .tab-btn { background: rgba(255,255,255,0.08); color: #aaa; border: 1px solid rgba(255,255,255,0.15); border-radius: 20px; padding: 7px 20px; cursor: pointer; font-size: 0.85rem; transition: all .2s; }
        .tab-btn.active, .tab-btn:hover { background: #1a7a3c; color: #fff; border-color: #1a7a3c; }
        .card { background: #fff; border-radius: 14px; box-shadow: 0 8px 32px rgba(0,0,0,0.35); padding: 24px 16px 20px; max-width: 1400px; margin: 0 auto; display: none; }
        .card.active { display: block; }
        .card h3 { text-align: center; color: #1a3a2a; font-size: 1.05rem; margin: 0 0 16px; padding-bottom: 10px; border-bottom: 2px solid #e8f5e9; }
        .mermaid { width: 100%; transform-origin: top left; transition: transform .2s; }
        .diagram-scroll { overflow-x: auto; overflow-y: visible; }
        .zoom-controls { display: flex; justify-content: flex-end; gap: 6px; margin-bottom: 8px; }
        .zoom-btn { background: #e8f5e9; border: 1px solid #2e7d32; color: #1a3a2a; border-radius: 6px; padding: 3px 12px; cursor: pointer; font-size: 0.85rem; font-weight: bold; }
        .zoom-btn:hover { background: #c8e6c9; }
        .legend { display: flex; flex-wrap: wrap; gap: 10px; justify-content: center; margin-top: 20px; padding-top: 16px; border-top: 1px solid #e0e0e0; font-size: 0.78rem; color: #555; }
        .legend-item { display: flex; align-items: center; gap: 6px; }
        .legend-dot { width: 14px; height: 14px; border-radius: 3px; flex-shrink: 0; }
    </style>
    <script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
    <script>
        mermaid.initialize({
            startOnLoad: false,
            theme: 'base',
            themeVariables: {
                primaryColor: '#e8f5e9',
                primaryTextColor: '#1a3a2a',
                primaryBorderColor: '#2e7d32',
                lineColor: '#555',
                secondaryColor: '#e3f2fd',
                tertiaryColor: '#fff8e1',
                fontSize: '13px'
            },
            flowchart: { curve: 'basis', htmlLabels: true }
        });
        window.addEventListener('DOMContentLoaded', function() {
            var activeEl = document.querySelector('.card.active .mermaid');
            if (activeEl) mermaid.run({ nodes: [activeEl] });
        });
        function showTab(id) {
            document.querySelectorAll('.card').forEach(c => c.classList.remove('active'));
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.getElementById(id).classList.add('active');
            event.target.classList.add('active');
            var el = document.getElementById(id).querySelector('.mermaid');
            if (el) {
                el.style.transform = 'scale(1)'; el._scale = 1; el.parentElement.style.height = 'auto';
                if (!el.hasAttribute('data-processed')) { mermaid.run({ nodes: [el] }); }
            }
        }
        function zoom(cardId, delta) {
            var el = document.getElementById(cardId).querySelector('.mermaid');
            if (!el) return;
            var s = (el._scale || 1) + delta;
            s = Math.max(0.3, Math.min(2.0, s));
            el._scale = s;
            el.style.transform = 'scale(' + s + ')';
            el.parentElement.style.height = Math.round(el.scrollHeight * s + 30) + 'px';
        }
        function resetZoom(cardId) {
            var el = document.getElementById(cardId).querySelector('.mermaid');
            if (!el) return;
            el._scale = 1;
            el.style.transform = 'scale(1)';
            el.parentElement.style.height = 'auto';
        }
    </script>
</head>
<body>

<div class="page-header">
    <h1>🌿 IAMROOT AI — Data Flow Diagram</h1>
    <p>ระบบวิเคราะห์สวนทุเรียน อ.นายายอาม จ.จันทบุรี | Sentinel-1/2 · SRTM · Open-Meteo · SWAB v3</p>
</div>

<div class="tabs">
    <button class="tab-btn active" onclick="showTab('tab-main')">🔄 ภาพรวมระบบ</button>
    <button class="tab-btn" onclick="showTab('tab-satellite')">🛰️ การวิเคราะห์ดาวเทียม</button>
    <button class="tab-btn" onclick="showTab('tab-swab')">💧 SWAB Algorithm</button>
    <button class="tab-btn" onclick="showTab('tab-alert')">⚠️ ระบบแจ้งเตือน</button>
</div>

<!-- Tab 1: Main System Flow -->
<div class="card active" id="tab-main">
    <h3>ภาพรวมระบบ IAMROOT AI — End-to-End Data Flow</h3>
    <div class="zoom-controls">
        <button class="zoom-btn" onclick="zoom('tab-main',-0.15)">−</button>
        <button class="zoom-btn" onclick="resetZoom('tab-main')">Reset</button>
        <button class="zoom-btn" onclick="zoom('tab-main',0.15)">+</button>
    </div>
    <div class="diagram-scroll">
    <div class="mermaid">
flowchart TD
    classDef user    fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px,color:#1a3a2a
    classDef api     fill:#e3f2fd,stroke:#1565c0,stroke-width:2px,color:#0d2a5e
    classDef sat     fill:#fff3e0,stroke:#e65100,stroke-width:2px,color:#3e1000
    classDef weather fill:#e0f7fa,stroke:#00695c,stroke-width:2px,color:#00363a
    classDef ai      fill:#f3e5f5,stroke:#6a1b9a,stroke-width:2px,color:#2a0045
    classDef db      fill:#fce4ec,stroke:#c62828,stroke-width:2px,color:#3e0000
    classDef out     fill:#f9fbe7,stroke:#558b2f,stroke-width:2px,color:#1b2e00
    classDef sched   fill:#fff8e1,stroke:#f57f17,stroke-width:2px,color:#3e2000

    U1([เกษตรกร LINE OA]):::user
    U2([LIFF App Map]):::user

    WH[webhook.py HMAC-SHA256]:::api
    MAIN[main.py FastAPI /analyze]:::api
    CACHE{Cache Hit?}:::api

    subgraph GEE ["Google Earth Engine"]
        direction LR
        S1[Sentinel-1 GRD VV/VH]:::sat
        S2[Sentinel-2 SR B2-B11]:::sat
        DEM[SRTM DEM 30m]:::sat
    end

    subgraph CALC ["gee_analysis.py"]
        direction TB
        NDVI[NDVI B8-B4/B8+B4]:::ai
        BSI[BSI Bare Soil Index]:::ai
        MNDWI[MNDWI B3-B11/B3+B11]:::ai
        VV[Soil Moisture VV dB]:::ai
        ELEV[Elevation Diff plot vs 500m]:::ai
        DISP[Land Displacement VV/VH YoY]:::ai
        SWAB[_calc_swab SWAB Index]:::ai
    end

    OM[Open-Meteo API 7-day rain]:::weather
    ML[ml_model.py RandomForest v3]:::ai
    RE[rule_engine.py 8 rules]:::ai
    DB[(Supabase Postgres)]:::db
    FM[flex_messages.py LINE Flex]:::out
    LINE([LINE Chat]):::out
    DASH([Dashboard index.html]):::out
    SCHED[scheduler.py ทุกวันจันทร์ 07:00]:::sched

    U1 -->|Location| WH
    U2 -->|POST /analyze| MAIN
    WH --> MAIN
    MAIN --> CACHE
    CACHE -->|Miss| S1 & S2 & DEM
    S1 --> VV & DISP
    S2 --> NDVI & BSI & MNDWI
    DEM --> ELEV
    VV & BSI & MNDWI & ELEV --> SWAB
    NDVI & BSI & ELEV & SWAB --> ML
    SWAB & NDVI & DISP & ML --> RE
    RE --> DB
    RE --> FM
    FM --> LINE
    CACHE -->|Hit| FM
    DB --> DASH
    SCHED -->|get_all_reports| DB
    SCHED --> OM
    OM --> FM
    </div>
    </div>
    <div class="legend">
        <div class="legend-item"><div class="legend-dot" style="background:#e3f2fd;border:2px solid #1565c0"></div>FastAPI Backend</div>
        <div class="legend-item"><div class="legend-dot" style="background:#fff3e0;border:2px solid #e65100"></div>ดาวเทียม (GEE)</div>
        <div class="legend-item"><div class="legend-dot" style="background:#f3e5f5;border:2px solid #6a1b9a"></div>AI / Algorithm</div>
        <div class="legend-item"><div class="legend-dot" style="background:#fce4ec;border:2px solid #c62828"></div>Database</div>
        <div class="legend-item"><div class="legend-dot" style="background:#e0f7fa;border:2px solid #00695c"></div>Weather API</div>
        <div class="legend-item"><div class="legend-dot" style="background:#fff8e1;border:2px solid #f57f17"></div>Scheduler (Cron)</div>
    </div>
</div>

<!-- Tab 2: Satellite Analysis -->
<div class="card" id="tab-satellite">
    <h3>🛰️ Pipeline การวิเคราะห์ดาวเทียม — IAMROOT AI gee_analysis.py</h3>
    <div class="zoom-controls">
        <button class="zoom-btn" onclick="zoom('tab-satellite',-0.15)">−</button>
        <button class="zoom-btn" onclick="resetZoom('tab-satellite')">Reset</button>
        <button class="zoom-btn" onclick="zoom('tab-satellite',0.15)">+</button>
    </div>
    <div class="diagram-scroll">
    <div class="mermaid">
flowchart LR
    classDef src  fill:#fff3e0,stroke:#e65100,stroke-width:2px,color:#3e1000
    classDef proc fill:#f3e5f5,stroke:#6a1b9a,stroke-width:2px,color:#2a0045
    classDef val  fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px,color:#1a3a2a

    subgraph S1SRC ["Sentinel-1 GRD C-band SAR"]
        VV_RAW["VV Backscatter dB IW 10m"]:::src
        VH_RAW["VH Backscatter dB IW 10m"]:::src
    end

    subgraph S2SRC ["Sentinel-2 SR Harmonized"]
        B4["B4 Red 665nm"]:::src
        B3["B3 Green 560nm"]:::src
        B2["B2 Blue 490nm"]:::src
        B8["B8 NIR 842nm"]:::src
        B11["B11 SWIR1 1610nm"]:::src
    end

    subgraph DEMSRC ["SRTM DEM NASA/USGS"]
        ELEV_RAW["Elevation 30m"]:::src
    end

    subgraph INDICES ["คำนวณ Index"]
        NDVI_C["NDVI = B8-B4/B8+B4"]:::proc
        BSI_C["BSI = B11+B4-B8-B2/B11+B4+B8+B2"]:::proc
        MNDWI_C["MNDWI = B3-B11/B3+B11"]:::proc
        VV_C["Soil Moisture VV"]:::proc
        ELEV_C["Elevation Diff plot-500m"]:::proc
        DISP_C["Land Displacement VV/VH YoY"]:::proc
    end

    subgraph INTERP ["ความหมาย"]
        N1["NDVI > 0.6 สุขภาพดี / < 0.3 วิกฤต"]:::val
        B1["BSI > 0.2 ดินโล่ง / < 0 พืชหนาแน่น"]:::val
        M1["MNDWI > 0 เปียก / < 0 แห้ง"]:::val
        V1["VV > -10dB ชื้นมาก / < -15dB แห้ง"]:::val
        E1["Diff < -1.5m แอ่งน้ำ / > 0 เนินระบายดี"]:::val
        D1["dVV >= 3dB ดินทรุดสูง"]:::val
    end

    VV_RAW --> VV_C --> V1
    VH_RAW --> DISP_C --> D1
    B4 & B8 --> NDVI_C --> N1
    B4 & B2 & B8 & B11 --> BSI_C --> B1
    B3 & B11 --> MNDWI_C --> M1
    ELEV_RAW --> ELEV_C --> E1

    VV_C --> SWAB_BOX["_calc_swab SWAB Index"]:::proc
    BSI_C --> SWAB_BOX
    MNDWI_C --> SWAB_BOX
    ELEV_C --> SWAB_BOX

    N1 & B1 & E1 --> ML_BOX["RandomForest v3 5 features"]:::proc
    SWAB_BOX --> ML_BOX
    </div>
    </div>
</div>

<!-- Tab 3: SWAB Algorithm -->
<div class="card" id="tab-swab">
    <h3>💧 SWAB Algorithm — Soil Water-Air Balance (_calc_swab)</h3>
    <div class="zoom-controls">
        <button class="zoom-btn" onclick="zoom('tab-swab',-0.15)">−</button>
        <button class="zoom-btn" onclick="resetZoom('tab-swab')">Reset</button>
        <button class="zoom-btn" onclick="zoom('tab-swab',0.15)">+</button>
    </div>
    <div class="diagram-scroll">
    <div class="mermaid">
flowchart TD
    classDef inp  fill:#e3f2fd,stroke:#1565c0,stroke-width:2px,color:#0d2a5e
    classDef calc fill:#f3e5f5,stroke:#6a1b9a,stroke-width:2px,color:#2a0045
    classDef cond fill:#fff8e1,stroke:#f57f17,stroke-width:2px,color:#3e2000
    classDef high fill:#ffebee,stroke:#c62828,stroke-width:2px,color:#3e0000
    classDef med  fill:#fff3e0,stroke:#e65100,stroke-width:2px,color:#3e1000
    classDef ok   fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px,color:#1a3a2a
    classDef dry  fill:#fff8e1,stroke:#f9a825,stroke-width:2px,color:#3e2000

    IN1["Sentinel-1 VV backscatter dB"]:::inp
    IN2["Sentinel-2 BSI Bare Soil Index"]:::inp
    IN3["SRTM elevation_diff m"]:::inp
    IN4["Sentinel-2 MNDWI"]:::inp

    STEP1["แปลง VV to soil_water_pct\nwater_raw = 20 + VV+20/15 x 45"]:::calc
    ADJ1{"ndwi > 0?"}:::cond
    ADJ2{"elevation_diff &lt; 0?"}:::cond
    ADD1["water_raw += ndwi x 15"]:::calc
    ADD2["water_raw += min 15 elev x 5"]:::calc
    CLAMP["soil_water_pct = clamp 5 to 90"]:::calc

    STEP2["Air Pore Space\ntotal_pore = max 28 48-BSI x22\nsoil_air = total_pore - water_in_pore"]:::calc
    STEP3["SWAB Index\nOPTIMAL = 42 percent\nswab = water-42 / 42 clamped -1 to 1"]:::calc

    GATE1{"swab > 0.30?"}:::cond
    GATE2{"swab > 0.10?"}:::cond
    GATE3{"swab >= -0.15?"}:::cond
    GATE4{"swab >= -0.30?"}:::cond

    R1["WATERLOGGED น้ำขังวิกฤต HIGH\nขุดร่องด่วน + Metalaxyl"]:::high
    R2["WET ชื้นเกิน MEDIUM\nตรวจร่องระบาย"]:::med
    R3["OPTIMAL สมดุลดี LOW\nรักษาระดับ"]:::ok
    R4["DRY แห้งเกิน MEDIUM\nเพิ่มน้ำ + คลุมฟาง"]:::dry
    R5["DROUGHT แล้งวิกฤต HIGH\nให้น้ำทันที"]:::high

    IN1 --> STEP1
    IN4 --> ADJ1
    IN3 --> ADJ2
    STEP1 --> ADJ1
    ADJ1 -->|ใช่| ADD1 --> ADJ2
    ADJ1 -->|ไม่| ADJ2
    ADJ2 -->|ใช่| ADD2 --> CLAMP
    ADJ2 -->|ไม่| CLAMP
    IN2 --> STEP2
    CLAMP --> STEP2
    STEP2 --> STEP3
    STEP3 --> GATE1
    GATE1 -->|ใช่| R1
    GATE1 -->|ไม่| GATE2
    GATE2 -->|ใช่| R2
    GATE2 -->|ไม่| GATE3
    GATE3 -->|ใช่| R3
    GATE3 -->|ไม่| GATE4
    GATE4 -->|ใช่| R4
    GATE4 -->|ไม่| R5
    </div>
    </div>
    <div class="legend">
        <div class="legend-item"><div class="legend-dot" style="background:#ffebee;border:2px solid #c62828"></div>HIGH severity (SWAB &gt; 0.30 หรือ &lt; −0.30)</div>
        <div class="legend-item"><div class="legend-dot" style="background:#fff3e0;border:2px solid #e65100"></div>MEDIUM severity</div>
        <div class="legend-item"><div class="legend-dot" style="background:#e8f5e9;border:2px solid #2e7d32"></div>OPTIMAL (SWAB −0.15 ถึง +0.10)</div>
        <div class="legend-item"><div class="legend-dot" style="background:#fff8e1;border:2px solid #f9a825"></div>DRY</div>
    </div>
</div>

<!-- Tab 4: Alert Pipeline -->
<div class="card" id="tab-alert">
    <h3>⚠️ ระบบแจ้งเตือนอัตโนมัติ — scheduler.py + weather_alert.py</h3>
    <div class="zoom-controls">
        <button class="zoom-btn" onclick="zoom('tab-alert',-0.15)">−</button>
        <button class="zoom-btn" onclick="resetZoom('tab-alert')">Reset</button>
        <button class="zoom-btn" onclick="zoom('tab-alert',0.15)">+</button>
    </div>
    <div class="diagram-scroll">
    <div class="mermaid">
flowchart TD
    classDef sched fill:#fff8e1,stroke:#f57f17,stroke-width:2px,color:#3e2000
    classDef api   fill:#e3f2fd,stroke:#1565c0,stroke-width:2px,color:#0d2a5e
    classDef cond  fill:#f5f5f5,stroke:#757575,stroke-width:2px,color:#212121
    classDef crit  fill:#ffebee,stroke:#c62828,stroke-width:2px,color:#3e0000
    classDef warn  fill:#fff3e0,stroke:#e65100,stroke-width:2px,color:#3e1000
    classDef ok    fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px,color:#1a3a2a
    classDef out   fill:#f3e5f5,stroke:#6a1b9a,stroke-width:2px,color:#2a0045

    CRON1(["ทุกวันจันทร์ 07:00 weekly_scan_job"]):::sched
    CRON2(["ทุกวันจันทร์ 07:30 rain_alert_job"]):::sched

    subgraph SCAN ["weekly_scan_job"]
        GET_PLOTS["get_all_reports ดึงแปลงจาก DB"]:::api
        ANALYZE["analyze_durian_plot IAMROOT SWAB ML"]:::api
        RISK{"_is_high_risk NDVI-10pct หรือ elevation-low หรือ SWAB=high"}:::cond
        ESC{"เสี่ยงสูง >= 2 สัปดาห์?"}:::cond
        PUSH_ESC["CRITICAL FlexBubble + ผู้เชี่ยวชาญ"]:::crit
        PUSH_WARN["WARNING FlexBubble รายสัปดาห์"]:::warn
        SKIP["ข้าม ไม่แจ้งเตือน"]:::ok
    end

    subgraph RAIN ["rain_alert_job"]
        GET_USERS["get_notifiable_users notify_weekly=true"]:::api
        GET_LATEST["get_latest_plot_analysis"]:::api
        METEO["get_7day_rain Open-Meteo threshold 60mm"]:::api
        SOIL["assess_soil_waterlog_risk BSI+SWAB"]:::api
        COMBO["evaluate_combined_risk forecast+soil"]:::api

        C1{"SWAB ชื้น + ฝน >= 40mm?"}:::cond
        C2{"ฝนหนัก + ดินเสี่ยง?"}:::cond
        C3{"ฝนหนักอย่างเดียว?"}:::cond
        C4{"ฝนปานกลาง + ดินแย่?"}:::cond

        R_CRIT["CRITICAL SWAB+ฝน รากเน่า"]:::crit
        R_CRIT2["CRITICAL น้ำขัง+ดินทรุด+ฝนหนัก"]:::crit
        R_WARN["WARNING ฝนหนักกำลังมา งดปุ๋ย"]:::warn
        R_WATCH["WATCH ดินไม่พร้อมรับน้ำ"]:::warn
        R_NONE["NONE ไม่แจ้งเตือน"]:::ok
    end

    LINE_SEND["line_sender.py send_line_message"]:::out
    LINE_OUT(["LINE Chat Flex Bubble"]):::out

    CRON1 --> GET_PLOTS --> ANALYZE --> RISK
    RISK -->|เสี่ยงสูง| ESC
    RISK -->|ปลอดภัย| SKIP
    ESC -->|ใช่| PUSH_ESC
    ESC -->|ไม่| PUSH_WARN
    PUSH_ESC --> LINE_SEND
    PUSH_WARN --> LINE_SEND

    CRON2 --> GET_USERS --> GET_LATEST --> METEO & SOIL
    METEO & SOIL --> COMBO
    COMBO --> C1
    C1 -->|ใช่| R_CRIT --> LINE_SEND
    C1 -->|ไม่| C2
    C2 -->|ใช่| R_CRIT2 --> LINE_SEND
    C2 -->|ไม่| C3
    C3 -->|ใช่| R_WARN --> LINE_SEND
    C3 -->|ไม่| C4
    C4 -->|ใช่| R_WATCH --> LINE_SEND
    C4 -->|ไม่| R_NONE

    LINE_SEND --> LINE_OUT
    </div>
    </div>
    <div class="legend">
        <div class="legend-item"><div class="legend-dot" style="background:#fff8e1;border:2px solid #f57f17"></div>Scheduler (Cron)</div>
        <div class="legend-item"><div class="legend-dot" style="background:#ffebee;border:2px solid #c62828"></div>CRITICAL alert</div>
        <div class="legend-item"><div class="legend-dot" style="background:#fff3e0;border:2px solid #e65100"></div>WARNING / WATCH</div>
        <div class="legend-item"><div class="legend-dot" style="background:#e8f5e9;border:2px solid #2e7d32"></div>ไม่แจ้งเตือน (ปลอดภัย)</div>
        <div class="legend-item"><div class="legend-dot" style="background:#f3e5f5;border:2px solid #6a1b9a"></div>LINE Output</div>
    </div>
</div>

</body>
</html>"""

# Write clean version
for path in [r'C:/GEOai/flowchart.html', r'C:/GEOai/pages-deploy/index.html']:
    with open(path, 'w', encoding='utf-8') as f:
        f.write(HTML)
    print(f'Written {path}: {os.path.getsize(path)} bytes')
