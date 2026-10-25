// officer.js — Phase 2a: แท็บเจ้าหน้าที่ "พื้นที่ของฉัน", ใช้รหัสเชิญ, สถิติการใช้งาน (events)
// โหลดหลังสคริปต์หลักใน index.html — ใช้ฟังก์ชัน/ตัวแปรระดับบนของสคริปต์หลักได้โดยตรง
// (apiFetch, API_URL, userId, switchTab, showMoistureGrid, showPlotRecommendations, shareReport,
//  submitAnalysis, openPlotForm, escHtml, showToast, showSpinner, hideSpinner, cropNames, plotTypeName, map)
// สิทธิ์ทั้งหมดตรวจที่ server — ไฟล์นี้แค่ซ่อน/แสดงปุ่ม

window.IAMROOT_ROLE = "farmer";
let officerPlots = [];

// สถิติการใช้งาน (B2G) — ส่งแบบไม่รอผล พังก็ไม่กระทบผู้ใช้
function logEvent(name, plotId, props) {
  try {
    apiFetch(`${API_URL}/events`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, plot_id: plotId || null, props: props || null }),
    }).catch(() => {});
  } catch {}
}
window.logEvent = logEvent;

async function redeemInviteFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const code = params.get("invite");
  if (!code) return;
  // เอารหัสออกจาก URL ทันที กันแชร์ลิงก์ต่อทั้งที่ใช้รหัสไปแล้ว
  try {
    params.delete("invite");
    history.replaceState(history.state, "", window.location.pathname + (params.toString() ? "?" + params : ""));
  } catch {}
  showSpinner("⏳ กำลังเปิดสิทธิ์เจ้าหน้าที่...");
  try {
    const res = await apiFetch(`${API_URL}/invites/redeem`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code }),
    });
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(friendlyErrorMessage(json.detail, "ใช้รหัสเชิญไม่สำเร็จ"));
    const names = (json.areas || []).map(a => a.name_th).join(", ");
    showToast(`✅ คุณเป็น${json.role_th}แล้ว${names ? " · อ." + names : ""}`);
  } catch (e) {
    showToast("❌ " + e.message);
  } finally {
    hideSpinner();
  }
}

async function loadMe() {
  try {
    const res = await apiFetch(`${API_URL}/me`);
    if (!res.ok) return null;
    return await res.json();
  } catch { return null; }
}

function ensureOfficerTab(me) {
  if (document.querySelector('.tab[data-tab="officer"]')) return;
  const tab = document.createElement("div");
  tab.className = "tab";
  tab.dataset.tab = "officer";
  tab.textContent = "👩‍🌾 พื้นที่ของฉัน";
  document.getElementById("tabs").appendChild(tab);
  tab.addEventListener("click", () => { switchTab("officer"); loadOfficerPlots(); logEvent("open_officer_tab"); });

  const panel = document.createElement("div");
  panel.className = "panel";
  panel.id = "panel-officer";
  const areaNames = (me.areas || []).map(a => "อ." + a.name_th).join(", ");
  panel.innerHTML = `
    <div id="officer-wrap" style="padding:12px 14px 90px;overflow-y:auto;height:100%">
      <div class="result-section" style="font-size:13.5px;line-height:1.6">
        <b>${escHtml(me.role_th)}</b>${areaNames ? ` · ${escHtml(areaNames)}` : ""}<br>
        เห็นแปลงที่คุณวาดเอง และแปลงในพื้นที่ของเกษตรกรที่ยินยอมแบ่งปันกับเจ้าหน้าที่ — การเปิดดูแปลงของผู้อื่นถูกบันทึกไว้
      </div>
      <div id="officer-list"></div>
    </div>`;
  document.getElementById("panel-my-plots").after(panel);
}

async function loadOfficerPlots() {
  const list = document.getElementById("officer-list");
  if (!list) return;
  list.innerHTML = `<p style="color:#888;font-size:13px;padding:12px">⏳ กำลังโหลด...</p>`;
  try {
    const res = await apiFetch(`${API_URL}/officer/plots`);
    if (!res.ok) throw new Error();
    officerPlots = (await res.json()).plots || [];
  } catch {
    list.innerHTML = `<p style="color:#e53935;font-size:13px;padding:12px">โหลดรายการไม่สำเร็จ ลองใหม่อีกครั้ง</p>`;
    return;
  }
  if (!officerPlots.length) {
    list.innerHTML = `<div class="result-section" style="font-size:14px;line-height:1.6">ยังไม่มีแปลงในพื้นที่ที่เปิดให้เจ้าหน้าที่เห็น<br>
      เพิ่มแปลงแทนเกษตรกรได้ที่แท็บ "➕ เพิ่มแปลงใหม่" (ใส่ชื่อเกษตรกรในหน้าข้อมูลแปลง)</div>`;
    return;
  }
  const groups = {};
  officerPlots.forEach(p => (groups[p.area_name || "ยังไม่ทราบอำเภอ"] ||= []).push(p));
  list.innerHTML = Object.entries(groups).map(([area, plots]) => `
    <div class="form-label" style="margin-top:12px">📍 ${escHtml(area)} (${plots.length})</div>
    ${plots.map(p => `
      <div class="plot-card" data-id="${p.id}" style="margin-bottom:9px">
        <div class="plot-info">
          <div class="plot-name">${escHtml(p.name || "แปลง")}</div>
          ${p.owner_label ? `<div class="plot-meta">👤 ${escHtml(p.owner_label)}</div>` : ""}
          <div class="plot-meta">🌱 ${escHtml(cropNames(p.crop_ids).join(", ") || "ยังไม่ระบุพืช")}${p.plot_type ? " · " + escHtml(plotTypeName(p.plot_type)) : ""}</div>
          <div class="plot-meta">${p.latest_analyzed_at ? "ตรวจล่าสุด " + new Date(p.latest_analyzed_at).toLocaleDateString("th-TH") : "ยังไม่เคยตรวจ"}${p.created_by_me ? " · คุณวาดแปลงนี้" : ""}</div>
        </div>
        <div class="plot-btn-col">
          ${p.polygon && p.polygon.length >= 3 ? `<button class="plot-check-btn" data-act="map">🗺️ แผนที่</button>` : ""}
          <button class="plot-hist-btn" data-act="check">🔍 ตรวจ</button>
          <button class="plot-rec-btn" data-act="rec">💡 คำแนะนำ</button>
          <button class="plot-edit-btn" data-act="edit">✏️ แก้ไข</button>
          <button class="plot-share-btn" data-act="report">📤 รายงาน</button>
          <button class="plot-photo-btn" data-act="photo">📷 รูป</button>
        </div>
      </div>`).join("")}`).join("");
}

document.addEventListener("click", async e => {
  const btn = e.target.closest("#officer-list button[data-act]");
  if (!btn) return;
  const plot = officerPlots.find(p => String(p.id) === btn.closest(".plot-card").dataset.id);
  if (!plot) return;
  const act = btn.dataset.act;
  if (act === "map") {
    switchTab("new-plot");
    await new Promise(r => setTimeout(r, 150));
    if (map) {
      map.resize();
      map.location({ lon: plot.lng, lat: plot.lat }, false);
      map.zoom(18, false);
    }
    showMoistureGrid(plot.polygon, plot.plot_type, plot.id);
  } else if (act === "check") {
    submitAnalysis(plot.lat, plot.lng, plot.name, null, plot.polygon || null, { plot_id: plot.id });
  } else if (act === "rec") {
    showPlotRecommendations(plot);
  } else if (act === "edit") {
    openPlotForm("edit", { ...plot, is_mine: plot.is_mine });
  } else if (act === "report") {
    shareReport(plot);
  } else if (act === "photo") {
    window.openPhotoSheet?.(plot);
  }
});

// เรียกจากสคริปต์หลักหลังยืนยันตัวตนและโหลดแปลงเสร็จ
async function phase2Init() {
  logEvent("app_open");
  window.photosInit?.();   // photos.js: ส่งรูปที่ค้างในคิวจากครั้งก่อน
  await redeemInviteFromUrl();
  const me = await loadMe();
  if (!me) return;
  window.IAMROOT_ROLE = me.role;
  window.accountInit?.(me.privacy);   // account.js: ขอความยินยอม (PDPA) + เมนูข้อมูลของฉัน
  if (me.role === "officer" || me.role === "admin") ensureOfficerTab(me);
}
window.phase2Init = phase2Init;
