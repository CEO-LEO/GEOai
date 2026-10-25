// account.js — Phase 2b (PDPA): หน้าขอความยินยอมครั้งแรก + เมนู "ข้อมูลของฉัน"
// (ดู/เปลี่ยนความยินยอม, ดาวน์โหลดข้อมูล, ใครเปิดดูแปลงของฉัน, ขอลบ/ยกเลิกคำขอลบข้อมูล)
// ข้อความรายการยินยอมมาจาก server (backend/privacy.py) — ไม่เขียนซ้ำในไฟล์นี้
// ใช้ฟังก์ชันของสคริปต์หลัก: apiFetch, API_URL, escHtml, showToast, showSpinner, hideSpinner, friendlyErrorMessage

let privacyState = null;

function accountSheet(html, { blocking = false } = {}) {
  let modal = document.getElementById("account-modal");
  if (!modal) {
    modal = document.createElement("div");
    modal.id = "account-modal";
    modal.className = "sheet-modal";
    modal.innerHTML = `<div class="sheet-box" id="account-box"></div>`;
    document.body.appendChild(modal);
    modal.addEventListener("click", e => {
      if (e.target === modal && modal.dataset.blocking !== "1") modal.classList.remove("show");
    });
  }
  modal.dataset.blocking = blocking ? "1" : "0";
  document.getElementById("account-box").innerHTML = html;
  modal.classList.add("show");
  return document.getElementById("account-box");
}
function closeAccountSheet() { document.getElementById("account-modal")?.classList.remove("show"); }

function optionalChecks(p) {
  return p.optional.map(o => `
    <label class="rec-card" style="display:flex;gap:10px;align-items:flex-start;cursor:pointer">
      <input type="checkbox" data-key="${o.key}" ${p.choices[o.key] ? "checked" : ""} style="width:22px;height:22px;margin-top:3px;flex:0 0 auto">
      <span><b>${escHtml(o.title_th)}</b><br><span class="rec-reason">${escHtml(o.body_th)}</span></span>
    </label>`).join("");
}
function readChecks(box) {
  const c = {};
  box.querySelectorAll("input[data-key]").forEach(i => { c[i.dataset.key] = i.checked; });
  return c;
}

async function postConsent(choices) {
  const res = await apiFetch(`${API_URL}/me/consent`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ version: privacyState.version, share_with_officers: !!choices.share_with_officers,
                           photos_research: !!choices.photos_research }),
  });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(friendlyErrorMessage(json.detail, "บันทึกไม่สำเร็จ"));
  privacyState = json;
  return json;
}

function showConsent() {
  const p = privacyState;
  const box = accountSheet(`
    <h3>🔒 ก่อนใช้งาน IAM ROOT</h3>
    <div class="form-hint" style="margin-top:0">${p.accepted_version ? "นโยบายความเป็นส่วนตัวมีการปรับปรุง กรุณาอ่านอีกครั้ง" : "เราขอแจ้งว่าใช้ข้อมูลอะไร และขอความยินยอมของคุณ"}</div>
    <div class="form-label">ข้อมูลที่ต้องใช้เพื่อให้บริการ</div>
    <ul style="margin:0 0 4px;padding-left:20px;font-size:14px;line-height:1.65">${p.required_th.map(t => `<li>${escHtml(t)}</li>`).join("")}</ul>
    <div class="form-label">เลือกได้ (ไม่บังคับ เปลี่ยนภายหลังได้)</div>
    ${optionalChecks(p)}
    <div class="form-hint">อ่านฉบับเต็ม: <a href="${escHtml(p.policy_url)}" target="_blank" rel="noopener">นโยบายความเป็นส่วนตัว</a> · ดู/ดาวน์โหลด/ขอลบข้อมูลได้ที่เมนู "⚙️ ข้อมูลของฉัน"</div>
    <div class="sheet-actions">
      <button type="button" class="btn-secondary" id="consent-decline">ไม่ยอมรับ</button>
      <button type="button" class="btn-primary" id="consent-accept">ยอมรับและใช้งานต่อ</button>
    </div>`, { blocking: true });
  box.querySelector("#consent-accept").onclick = async () => {
    showSpinner("⏳ กำลังบันทึก...");
    try {
      await postConsent(readChecks(box));
      closeAccountSheet();
      showToast("✅ บันทึกแล้ว ขอบคุณครับ");
    } catch (e) { showToast("❌ " + e.message); }
    finally { hideSpinner(); }
  };
  box.querySelector("#consent-decline").onclick = showDeclined;
}

function showDeclined() {
  const box = accountSheet(`
    <h3>ไม่ยอมรับนโยบาย</h3>
    <div style="font-size:14.5px;line-height:1.7">ถ้าไม่ยอมรับข้อมูลที่ต้องใช้เพื่อให้บริการ แอปจะตรวจแปลงให้ไม่ได้
      · ถ้าต้องการให้ลบข้อมูลที่มีอยู่แล้ว กด "ขอลบข้อมูลของฉัน"</div>
    <button type="button" class="btn-secondary sheet-btn" id="declined-delete">🗑️ ขอลบข้อมูลของฉัน</button>
    <button type="button" class="btn-primary sheet-btn" id="declined-back">← กลับไปอ่านอีกครั้ง</button>`, { blocking: true });
  box.querySelector("#declined-back").onclick = showConsent;
  box.querySelector("#declined-delete").onclick = () => requestDeletion(true);
}

async function requestDeletion(fromDecline) {
  if (!confirm("ขอลบข้อมูลของคุณ?\n• แปลงทั้งหมดจะถูกซ่อนและหยุดส่งข้อความทันที\n• ยกเลิกคำขอได้ภายใน 30 วัน หลังจากนั้นข้อมูลจะถูกลบถาวร")) return;
  showSpinner("⏳ กำลังส่งคำขอ...");
  try {
    const res = await apiFetch(`${API_URL}/me/deletion-request`, { method: "POST" });
    if (!res.ok) throw new Error(friendlyErrorMessage(await res.text(), "ส่งคำขอไม่สำเร็จ"));
    privacyState.deletion_requested_at = new Date().toISOString();
    closeAccountSheet();
    showToast("🗑️ รับคำขอลบข้อมูลแล้ว — ยกเลิกได้ภายใน 30 วันที่เมนู \"ข้อมูลของฉัน\"");
    if (typeof loadUserPlots === "function") loadUserPlots();
  } catch (e) { showToast("❌ " + e.message); }
  finally { hideSpinner(); }
}

async function cancelDeletion() {
  showSpinner("⏳ กำลังยกเลิกคำขอ...");
  try {
    const res = await apiFetch(`${API_URL}/me/deletion-request/cancel`, { method: "POST" });
    if (!res.ok) throw new Error(friendlyErrorMessage(await res.text(), "ยกเลิกไม่สำเร็จ"));
    privacyState.deletion_requested_at = null;
    closeAccountSheet();
    showToast("✅ ยกเลิกคำขอลบแล้ว แปลงของคุณกลับมาแล้ว");
    if (typeof loadUserPlots === "function") loadUserPlots();
  } catch (e) { showToast("❌ " + e.message); }
  finally { hideSpinner(); }
}

async function downloadMyData() {
  showSpinner("⏳ กำลังรวบรวมข้อมูล...");
  try {
    const res = await apiFetch(`${API_URL}/me/data`);
    if (!res.ok) throw new Error("ดาวน์โหลดไม่สำเร็จ");
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "iamroot-my-data.json";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
    showToast("📥 ดาวน์โหลดแล้ว (ถ้าไม่ขึ้น ลองเปิดแอปในเบราว์เซอร์)");
  } catch (e) { showToast("❌ " + e.message); }
  finally { hideSpinner(); }
}

const ACCESS_ACTION_TH = { view_plot: "เปิดดูแปลง", edit_plot: "แก้แปลง", analyze_plot: "ตรวจแปลง",
  view_recommendations: "ดูคำแนะนำ", create_report: "ทำรายงาน", photo_upload: "แนบรูป", field_validation: "ยืนยันในแปลง" };

async function showAccessLog() {
  const box = accountSheet(`<h3>👁️ ใครเปิดดูแปลงของฉัน (90 วัน)</h3><p class="form-hint">กำลังโหลด...</p>`);
  try {
    const res = await apiFetch(`${API_URL}/me/access-log`);
    const rows = res.ok ? (await res.json()).entries : [];
    box.innerHTML = `<h3>👁️ ใครเปิดดูแปลงของฉัน (90 วัน)</h3>` +
      (rows.length ? rows.map(r => `<div class="rec-card" style="font-size:14px">${escHtml(r.role_th)} · ${escHtml(ACCESS_ACTION_TH[r.action] || r.action)}${r.plot_id ? ` (แปลง #${escHtml(r.plot_id)})` : ""}
        <div class="rec-reason">${new Date(r.at).toLocaleString("th-TH", { dateStyle: "medium", timeStyle: "short" })}</div></div>`).join("")
        : `<div class="form-hint">ยังไม่มีใครนอกจากคุณเปิดดูแปลงของคุณ</div>`) +
      `<button class="close-modal-btn" id="access-close">ปิด</button>`;
    box.querySelector("#access-close").onclick = showMyData;
  } catch { box.innerHTML += `<p class="form-hint">โหลดไม่สำเร็จ</p>`; }
}

function showMyData() {
  const p = privacyState;
  if (!p) return showToast("⏳ กำลังโหลดข้อมูล ลองอีกครั้ง");
  const pending = p.deletion_requested_at;
  const box = accountSheet(`
    <h3>⚙️ ข้อมูลของฉัน</h3>
    ${pending ? `<div class="bwarn"><b>คุณขอลบข้อมูลไว้เมื่อ ${new Date(pending).toLocaleDateString("th-TH")}</b>
      แปลงถูกซ่อนและหยุดส่งข้อความแล้ว ยกเลิกได้ภายใน 30 วัน</div>
      <button type="button" class="btn-primary sheet-btn" id="my-cancel-del">↩️ ยกเลิกคำขอลบ</button>` : `
    <div class="form-label">ความยินยอม (ไม่บังคับ)</div>
    ${optionalChecks(p)}
    <button type="button" class="btn-primary sheet-btn" id="my-save">💾 บันทึกความยินยอม</button>`}
    <button type="button" class="btn-secondary sheet-btn" id="my-notify">🔔 การแจ้งเตือน</button>
    <button type="button" class="btn-secondary sheet-btn" id="my-access">👁️ ใครเปิดดูแปลงของฉัน</button>
    <button type="button" class="btn-secondary sheet-btn" id="my-download">📥 ดาวน์โหลดข้อมูลของฉัน</button>
    ${pending ? "" : `<button type="button" class="btn-secondary sheet-btn" id="my-delete" style="color:#b91c1c">🗑️ ขอลบข้อมูลของฉัน</button>`}
    <div class="form-hint"><a href="${escHtml(p.policy_url)}" target="_blank" rel="noopener">นโยบายความเป็นส่วนตัว</a> (ฉบับ ${escHtml(p.version)})</div>
    <button class="close-modal-btn" id="my-close">ปิด</button>`);
  box.querySelector("#my-close").onclick = closeAccountSheet;
  box.querySelector("#my-access").onclick = showAccessLog;
  box.querySelector("#my-notify").onclick = showNotifySettings;
  box.querySelector("#my-download").onclick = downloadMyData;
  box.querySelector("#my-cancel-del")?.addEventListener("click", cancelDeletion);
  box.querySelector("#my-delete")?.addEventListener("click", () => requestDeletion(false));
  box.querySelector("#my-save")?.addEventListener("click", async () => {
    showSpinner("⏳ กำลังบันทึก...");
    try { await postConsent(readChecks(box)); showToast("💾 บันทึกแล้ว"); closeAccountSheet(); }
    catch (e) { showToast("❌ " + e.message); }
    finally { hideSpinner(); }
  });
}

// ─── 3a: ตั้งค่าการแจ้งเตือน (เตือนฝนหนัก / ความถี่ / สรุปรายสัปดาห์) ───
async function showNotifySettings() {
  const box = accountSheet(`<h3>🔔 การแจ้งเตือน</h3><p class="form-hint">กำลังโหลด...</p>`);
  let s;
  try {
    const res = await apiFetch(`${API_URL}/user/${encodeURIComponent(userId)}/notify-settings`);
    if (!res.ok) throw new Error();
    s = await res.json();
  } catch {
    box.innerHTML = `<h3>🔔 การแจ้งเตือน</h3><p class="form-hint">โหลดไม่สำเร็จ ลองใหม่อีกครั้ง</p><button class="close-modal-btn" id="nt-close">ปิด</button>`;
    box.querySelector("#nt-close").onclick = showMyData;
    return;
  }
  box.innerHTML = `
    <h3>🔔 การแจ้งเตือน</h3>
    <label class="rec-card" style="display:flex;gap:10px;align-items:flex-start;cursor:pointer">
      <input type="checkbox" id="nt-rain" ${s.rain_alerts ? "checked" : ""} style="width:22px;height:22px;margin-top:3px;flex:0 0 auto">
      <span><b>เตือนเมื่อฝนหนักกำลังมา</b><br><span class="rec-reason">ส่งเข้าแชท LINE เมื่อพยากรณ์มีฝนหนักภายใน 3 วัน พร้อมบอกจุดเสี่ยงในแปลงและสิ่งที่ควรทำ</span></span>
    </label>
    <div class="form-label">ความถี่</div>
    <div class="type-row" id="nt-freq">${s.freq_options.map(o => `<button type="button" class="chip${s.rain_alert_freq === o.id ? " on" : ""}" data-freq="${o.id}">${escHtml(o.name_th)}</button>`).join("")}</div>
    <label class="rec-card" style="display:flex;gap:10px;align-items:flex-start;cursor:pointer;margin-top:12px">
      <input type="checkbox" id="nt-digest" ${s.weekly_digest ? "checked" : ""} style="width:22px;height:22px;margin-top:3px;flex:0 0 auto">
      <span><b>สรุปแปลงรายสัปดาห์</b><br><span class="rec-reason">ทุกวันจันทร์ สรุปทุกแปลงพร้อมแผนที่</span></span>
    </label>
    <button type="button" class="btn-primary sheet-btn" id="nt-save">💾 บันทึก</button>
    <button class="close-modal-btn" id="nt-close">กลับ</button>`;
  let freq = s.rain_alert_freq;
  box.querySelectorAll("[data-freq]").forEach(b => b.onclick = () => {
    freq = b.dataset.freq;
    box.querySelectorAll("[data-freq]").forEach(x => x.classList.toggle("on", x === b));
  });
  box.querySelector("#nt-close").onclick = showMyData;
  box.querySelector("#nt-save").onclick = async () => {
    showSpinner("⏳ กำลังบันทึก...");
    const u = encodeURIComponent(userId);
    try {
      const calls = [
        apiFetch(`${API_URL}/user/${u}/notify?enabled=${box.querySelector("#nt-rain").checked}`, { method: "PATCH" }),
        apiFetch(`${API_URL}/user/${u}/notify-digest?enabled=${box.querySelector("#nt-digest").checked}`, { method: "PATCH" }),
        apiFetch(`${API_URL}/user/${u}/rain-alert-freq?mode=${freq}`, { method: "PATCH" }),
      ];
      const results = await Promise.all(calls);
      if (results.some(r => !r.ok)) throw new Error("บางรายการบันทึกไม่สำเร็จ");
      showToast("💾 บันทึกการแจ้งเตือนแล้ว");
      showMyData();
    } catch (e) { showToast("❌ " + e.message); }
    finally { hideSpinner(); }
  };
}

function addAccountButton() {
  if (document.getElementById("account-btn")) return;
  const b = document.createElement("button");
  b.id = "account-btn";
  b.type = "button";
  b.textContent = "⚙️ ข้อมูลของฉัน";
  b.setAttribute("aria-label", "ข้อมูลของฉัน");
  b.style.cssText = "margin-left:auto;background:rgba(255,255,255,.18);color:#fff;border:1px solid rgba(255,255,255,.35);" +
                    "border-radius:999px;padding:8px 12px;font:inherit;font-size:13px;font-weight:600;min-height:40px;cursor:pointer";
  b.onclick = showMyData;
  const header = document.getElementById("header");
  header.style.display = "flex";
  header.style.alignItems = "center";
  header.appendChild(b);
}

// เรียกจาก officer.js::phase2Init หลังได้ /me
function accountInit(privacy) {
  privacyState = privacy;
  addAccountButton();
  if (!privacy) return;
  if (privacy.deletion_requested_at) return;   // ขอลบไว้: ไม่ถามความยินยอม (ดู/ยกเลิกได้ที่เมนู)
  if (privacy.needs_consent) showConsent();
}
window.accountInit = accountInit;
