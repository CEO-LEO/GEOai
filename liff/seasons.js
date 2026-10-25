// seasons.js — Phase 3c: ติดตามระยะยาว — ความชื้นรายเดือนจากเรดาร์ + เทียบหน้าฝน/หน้าแล้งกับปีก่อน (แปลงเดียวกัน)
// ใช้ฟังก์ชันของสคริปต์หลัก: apiFetch, API_URL, userId, escHtml (+ Chart.js ที่โหลดแบบ defer ในหน้า)
// กราฟ: ชุดเดียว (ไม่มีกล่องคำอธิบาย ชื่ออยู่ที่หัวกราฟ) · ค่า 0–100 เทียบกับแปลงนี้เอง · ตารางฤดูอยู่ใต้กราฟ

let seasonsChart = null;

async function showSeasons(plot) {
  document.getElementById("modal-title").textContent = `📅 เทียบฤดู — ${plot.name || "แปลง"}`;
  const body = document.getElementById("modal-body");
  body.innerHTML = `<p style="color:#888;font-size:13px">⏳ กำลังดึงข้อมูลดาวเทียมย้อนหลัง 2 ปี (ประมาณ 10–30 วินาที)...</p>`;
  document.getElementById("detail-modal").classList.add("show");
  let data;
  try {
    const res = await apiFetch(`${API_URL}/plots/${encodeURIComponent(userId)}/${plot.id}/seasons?months=24`);
    data = await res.json();
    if (!res.ok) throw new Error(friendlyErrorMessage(data.detail, "ดึงข้อมูลไม่สำเร็จ"));
  } catch (e) {
    body.innerHTML = `<p style="color:#e53935;font-size:13px">${escHtml(e.message || "ดึงข้อมูลไม่สำเร็จ")}</p>`;
    return;
  }
  if (data.status !== "ok" || !data.months.length) {
    body.innerHTML = `<p style="color:#888;font-size:13px;text-align:center;padding:20px">ข้อมูลย้อนหลังยังไม่พอสำหรับเทียบฤดู</p>`;
    return;
  }
  body.innerHTML = `
    <div class="result-section" style="font-size:14px;line-height:1.7">
      ${data.sentences.map(s => `<div>• ${escHtml(s)}</div>`).join("") || "ยังเทียบฤดูไม่ได้"}
    </div>
    <div class="sparkline-label" style="margin-top:6px">ความชื้นรายเดือนของแปลงนี้ (0 = แห้งที่สุด · 100 = ชื้นที่สุด ในช่วง 2 ปี) · ● จุดทึบ = หน้าฝน · ○ จุดกลวง = หน้าแล้ง</div>
    <div style="position:relative;width:100%;height:220px"><canvas id="seasons-chart" aria-label="กราฟความชื้นรายเดือน — ตัวเลขเดียวกับตารางด้านล่าง"></canvas></div>
    <div class="form-label">รายฤดู</div>
    <table style="width:100%;border-collapse:collapse;font-size:13px">
      ${data.seasons.filter(s => s.enough).slice().reverse().map(s => `<tr style="border-bottom:1px solid var(--line)">
        <td style="padding:6px 2px">${escHtml(s.label)}${s.complete ? "" : " · ยังไม่จบฤดู"}</td>
        <td style="padding:6px 2px;text-align:right;color:var(--muted)">${s.images} ภาพ</td></tr>`).join("")}
    </table>
    <div class="form-hint">${escHtml(data.note_th)} · ข้อมูล: Sentinel-1</div>`;
  if (typeof Chart === "undefined") return;   // Chart.js ยังโหลดไม่เสร็จ — ยังมีประโยค/ตารางให้อ่าน
  if (seasonsChart) seasonsChart.destroy();
  const rainy = new Set([5, 6, 7, 8, 9, 10]);
  seasonsChart = new Chart(document.getElementById("seasons-chart").getContext("2d"), {
    type: "line",
    data: {
      labels: data.months.map(m => m.label),
      datasets: [{
        data: data.months.map(m => m.index),
        borderColor: "#1565c0", borderWidth: 2, tension: 0.25, fill: false,
        pointRadius: 4, pointHoverRadius: 6,
        // หน้าฝน (พ.ค.–ต.ค.) จุดทึบ · หน้าแล้งจุดกลวง — ไม่ใช้สีเพิ่ม
        pointBackgroundColor: data.months.map(m => rainy.has(+m.month.slice(5)) ? "#1565c0" : "#ffffff"),
        pointBorderColor: "#1565c0",
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: {
          label: ctx => {
            const m = data.months[ctx.dataIndex];
            return `ความชื้นเทียบในแปลง ${m.index} · ${rainy.has(+m.month.slice(5)) ? "หน้าฝน" : "หน้าแล้ง"} · ${m.images} ภาพ`;
          },
        } },
      },
      scales: {
        x: { ticks: { font: { size: 10 }, maxRotation: 45, autoSkip: true, maxTicksLimit: 12 }, grid: { display: false } },
        y: { min: 0, max: 100, ticks: { font: { size: 10 }, stepSize: 50 } },
      },
    },
  });
}

window.showSeasons = showSeasons;
