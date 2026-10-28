// cropsurvey.js — Phase 4a-7: เจ้าหน้าที่สำรวจพืชภาคสนาม (ยืนในแปลง → จุด GPS + พืชที่เห็น) = ข้อมูลสอนระบบทายชนิดพืช
// ใช้ฟังก์ชันของสคริปต์หลัก: apiFetch, API_URL, escHtml, cropList, showToast, friendlyErrorMessage
// สัญญาณหลุด: เก็บคิวในเครื่อง (localStorage) แล้วส่งใหม่เมื่อมีสัญญาณ — server กันซ้ำด้วย client_id
// สิทธิ์ตรวจที่ server (เฉพาะเจ้าหน้าที่/ผู้ดูแล)

const SURVEY_QUEUE_KEY = "iamroot_survey_queue";
const SURVEY_MAX_ACC_M = 30;   // ตรงกับ SURVEY_MAX_ACCURACY_M ใน main.py

function surveyQueue() {
  try { return JSON.parse(localStorage.getItem(SURVEY_QUEUE_KEY) || "[]"); } catch { return []; }
}
function setSurveyQueue(q) {
  try { localStorage.setItem(SURVEY_QUEUE_KEY, JSON.stringify(q)); } catch {}
}
function newClientId() {
  try { if (crypto.randomUUID) return crypto.randomUUID(); } catch {}
  return `s${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
}

// ส่งรายการในคิว · คืนจำนวนที่ส่งสำเร็จ · คำขอที่ server ปฏิเสธ (4xx) เอาออกจากคิวพร้อมแจ้ง ไม่วนส่งซ้ำ
async function flushSurveyQueue() {
  let sent = 0;
  for (const item of surveyQueue()) {
    let res;
    try {
      res = await apiFetch(`${API_URL}/officer/crop-survey`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(item),
      });
    } catch { break; }                                  // ยังไม่มีสัญญาณ — เก็บไว้ส่งรอบหน้า
    if (res.status >= 500 || res.status === 401 || res.status === 428) break;   // ชั่วคราว (token/ยินยอม) — เก็บไว้ส่งใหม่
    setSurveyQueue(surveyQueue().filter(q => q.client_id !== item.client_id));
    if (res.ok) sent++;
    else showToast(`⚠️ จุดสำรวจไม่ถูกบันทึก: ${friendlyErrorMessage((await res.json().catch(() => ({}))).detail, "ข้อมูลไม่ถูกต้อง")}`);
  }
  if (sent) renderSurveyBox();
  return sent;
}

async function renderSurveyBox() {
  const box = document.getElementById("officer-survey");
  if (!box) return;
  let data = { total: 0, items: [] };
  try {
    const res = await apiFetch(`${API_URL}/officer/crop-survey`);
    if (res.ok) data = await res.json();
  } catch {}
  const pending = surveyQueue().length;
  box.innerHTML = `
    <div class="result-section" style="font-size:13.5px;line-height:1.6">
      <b>🌱 สำรวจพืชภาคสนาม</b> — ยืนในแปลงแล้วบันทึกพืชที่เห็น ช่วยสอนระบบให้ทายชนิดพืชจากดาวเทียม<br>
      สำรวจแล้ว <b>${data.total}</b> จุด${pending ? ` · รอส่ง ${pending} จุด (ส่งเองเมื่อมีสัญญาณ)` : ""}
      <button type="button" class="btn-primary" id="survey-open-btn" style="width:100%;margin-top:8px">📍 สำรวจจุดนี้</button>
      ${data.items.length ? `<details style="margin-top:8px"><summary>จุดล่าสุดของฉัน</summary>
        ${data.items.slice(0, 15).map(it => `<div style="display:flex;gap:8px;align-items:center;padding:6px 0;border-bottom:1px solid var(--line)">
          <div style="flex:1">${escHtml(it.names_th.join(", "))}<br><span style="color:var(--muted);font-size:12px">
            ${escHtml(it.area_name || "ไม่ทราบอำเภอ")} · ${new Date(it.at).toLocaleDateString("th-TH")}</span></div>
          <button type="button" class="btn-secondary" data-survey-del="${it.id}" style="min-height:44px">🗑️</button></div>`).join("")}
      </details>` : ""}
    </div>`;
  box.querySelector("#survey-open-btn").addEventListener("click", openSurveySheet);
  box.querySelectorAll("[data-survey-del]").forEach(b => b.addEventListener("click", async () => {
    if (!confirm("ลบจุดสำรวจนี้?")) return;
    try {
      const res = await apiFetch(`${API_URL}/officer/crop-survey/${b.dataset.surveyDel}`, { method: "DELETE" });
      if (!res.ok) throw new Error();
      renderSurveyBox();
    } catch { showToast("⚠️ ลบไม่สำเร็จ"); }
  }));
}

function openSurveySheet() {
  document.getElementById("modal-title").textContent = "📍 สำรวจพืช — จุดนี้";
  const body = document.getElementById("modal-body");
  body.innerHTML = `<div style="overflow-wrap:anywhere;min-width:0">
    <div class="result-section" style="font-size:13.5px;line-height:1.6">
      ยืนใน<b>แปลงเดียว</b> ห่างขอบแปลงอย่างน้อย 20 เมตร แล้วรอให้ตำแหน่งแม่นก่อนบันทึก
    </div>
    <div id="sv-gps" style="font-size:14px;margin:8px 0">⏳ กำลังหาตำแหน่ง...</div>
    <div class="form-label">พืชที่เห็นในแปลงนี้ (เลือกได้หลายชนิด)</div>
    <div class="chip-row" id="sv-crops">${cropList.map(c =>
      `<button type="button" class="chip" data-id="${escHtml(c.id)}">${escHtml(c.name_th)}</button>`).join("")}</div>
    <label style="display:flex;gap:10px;align-items:flex-start;margin-top:12px;font-size:14px;line-height:1.5">
      <input type="checkbox" id="sv-perm" style="width:22px;height:22px;flex:none">
      เจ้าของแปลงอนุญาตให้บันทึกจุดและชนิดพืชนี้ เพื่อใช้สอนระบบ (ไม่บันทึกชื่อเจ้าของ)
    </label>
    <div class="sheet-actions"><button type="button" class="btn-primary" id="sv-save" disabled>💾 บันทึกจุดสำรวจ</button></div>
    <div class="form-hint">บันทึกเฉพาะพิกัดจุด ชนิดพืช และผู้สำรวจ · ไม่แสดงในแอปของใคร · ลบได้จากรายการ "จุดล่าสุดของฉัน"</div>
  </div>`;
  document.getElementById("detail-modal").classList.add("show");
  body.querySelectorAll("#sv-crops .chip").forEach(b => b.addEventListener("click", () => b.classList.toggle("on")));

  let fix = null, watch = null;
  const saveBtn = body.querySelector("#sv-save"), gpsEl = body.querySelector("#sv-gps");
  const refresh = () => { saveBtn.disabled = !(fix && fix.acc <= SURVEY_MAX_ACC_M); };
  const stop = () => { if (watch != null) { try { navigator.geolocation.clearWatch(watch); } catch {} watch = null; } };
  if (!navigator.geolocation) {
    gpsEl.textContent = "⚠️ เครื่องนี้หาตำแหน่งไม่ได้";
  } else {
    watch = navigator.geolocation.watchPosition(p => {
      fix = { lat: p.coords.latitude, lng: p.coords.longitude, acc: p.coords.accuracy };
      gpsEl.innerHTML = fix.acc <= SURVEY_MAX_ACC_M
        ? `✅ ตำแหน่งแม่นพอแล้ว (±${Math.round(fix.acc)} ม.)`
        : `⏳ ตำแหน่งยังไม่แม่น (±${Math.round(fix.acc)} ม.) — รอสักครู่ หรือออกมาที่โล่งใต้ฟ้า`;
      refresh();
    }, err => {
      gpsEl.textContent = err.code === 1 ? "⚠️ ยังไม่อนุญาตให้แอปใช้ตำแหน่ง — เปิดสิทธิ์ตำแหน่งให้ LINE แล้วลองใหม่"
                                         : "⚠️ หาตำแหน่งไม่ได้ ลองใหม่อีกครั้ง";
    }, { enableHighAccuracy: true, maximumAge: 0, timeout: 30000 });
  }
  // ปิดแผ่นเมื่อไหร่ก็หยุดใช้ GPS (ประหยัดแบต)
  const closeObs = new MutationObserver(() => {
    if (!document.getElementById("detail-modal").classList.contains("show")) { stop(); closeObs.disconnect(); }
  });
  closeObs.observe(document.getElementById("detail-modal"), { attributes: true, attributeFilter: ["class"] });

  saveBtn.addEventListener("click", async () => {
    const ids = [...body.querySelectorAll("#sv-crops .chip.on")].map(b => b.dataset.id);
    if (!ids.length) { showToast("เลือกพืชอย่างน้อย 1 ชนิด"); return; }
    if (!body.querySelector("#sv-perm").checked) { showToast("ต้องได้รับอนุญาตจากเจ้าของแปลงก่อน"); return; }
    const item = { client_id: newClientId(), lat: +fix.lat.toFixed(6), lng: +fix.lng.toFixed(6),
                   accuracy_m: Math.round(fix.acc), crop_ids: ids, owner_permission: true };
    setSurveyQueue([...surveyQueue(), item]);          // เก็บก่อนส่ง — สัญญาณหลุดกลางทางก็ไม่หาย
    stop();
    document.getElementById("detail-modal").classList.remove("show");
    const sent = await flushSurveyQueue();
    showToast(sent ? "🌱 บันทึกแล้ว — ขอบคุณครับ" : "📶 บันทึกไว้ในเครื่องแล้ว จะส่งเมื่อมีสัญญาณ");
    renderSurveyBox();
  });
}

function surveyInit() {
  renderSurveyBox();
  flushSurveyQueue();
  window.addEventListener("online", () => flushSurveyQueue());
}

window.surveyInit = surveyInit;
window.flushSurveyQueue = flushSurveyQueue;
