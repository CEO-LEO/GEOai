// trees.js — spec 6.3 (ตัวเลือก): ปักหมุดต้นไม้รายต้น + บันทึกอาการ / ใส่ปุ๋ย / พ่นยา / รูป ต่อต้น
// ใช้ฟังก์ชันของสคริปต์หลัก: apiFetch, API_URL, userId, escHtml, cropList, cropNames, showToast, friendlyErrorMessage
// + photos.js (openPhotoSheet, photoTags) · สัญญาณหลุด: คิวในเครื่อง ส่งใหม่เมื่อมีสัญญาณ (server กันซ้ำด้วย client_id)
// GPS มือถือคลาดได้หลายเมตร ต้นไม้ห่างกันไม่กี่เมตร → ชื่อ/เลขต้นคือตัวระบุหลัก ตำแหน่งใช้ช่วยหาต้นที่ใกล้

const TREE_QUEUE_KEY = "iamroot_tree_queue";
const TREE_KIND_ICON = { symptom: "🩺", fertilize: "🌱", spray: "💨", note: "📝" };
const TREE_KIND_TH = { symptom: "อาการ", fertilize: "ใส่ปุ๋ย", spray: "พ่นยา", note: "บันทึก" };

function treeQueue() {
  try { return JSON.parse(localStorage.getItem(TREE_QUEUE_KEY) || "[]"); } catch { return []; }
}
function setTreeQueue(q) {
  try { localStorage.setItem(TREE_QUEUE_KEY, JSON.stringify(q)); } catch {}
}
function treeClientId() {
  try { if (crypto.randomUUID) return crypto.randomUUID(); } catch {}
  return `t${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
}
const treeDateTh = d => new Date(d).toLocaleDateString("th-TH", { day: "numeric", month: "short", year: "2-digit" });
const todayIso = () => { const d = new Date(); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 10); };

// ส่งคิว · 4xx ที่ไม่ใช่เรื่องสิทธิ์ชั่วคราว = ข้อมูลไม่ถูกต้อง → เอาออกพร้อมแจ้ง (ไม่วนส่งซ้ำ)
async function flushTreeQueue() {
  if (!userId) return 0;
  let sent = 0;
  for (const item of treeQueue()) {
    if (item.owner !== userId) continue;
    let res;
    try {
      res = await apiFetch(item.url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(item.body) });
    } catch { break; }
    if (res.status >= 500 || res.status === 401 || res.status === 428) break;
    setTreeQueue(treeQueue().filter(q => q.body.client_id !== item.body.client_id));
    if (res.ok) sent++;
    else showToast(`⚠️ บันทึกต้นไม้ไม่สำเร็จ: ${friendlyErrorMessage((await res.json().catch(() => ({}))).detail, "ข้อมูลไม่ถูกต้อง")}`);
  }
  return sent;
}

async function queueTreePost(url, body) {
  setTreeQueue([...treeQueue(), { owner: userId, url, body }]);   // เก็บก่อนส่ง — สัญญาณหลุดกลางทางก็ไม่หาย
  const sent = await flushTreeQueue();
  showToast(sent ? "✅ บันทึกแล้ว" : "📶 บันทึกไว้ในเครื่องแล้ว จะส่งเมื่อมีสัญญาณ");
  return sent > 0;
}

function treeBase(plot) { return `${API_URL}/plots/${encodeURIComponent(userId)}/${plot.id}/trees`; }

function openTreeModal(title, html) {
  document.getElementById("modal-title").textContent = title;
  const body = document.getElementById("modal-body");
  body.innerHTML = `<div style="overflow-wrap:anywhere;min-width:0">${html}</div>`;
  document.getElementById("detail-modal").classList.add("show");
  return body;
}

// ─── รายการต้นไม้ของแปลง ───
async function showTrees(plot, sortBy) {
  const body = openTreeModal(`🌳 ต้นไม้รายต้น — ${plot.name || "แปลง"}`, `<p style="color:#888;font-size:13px">⏳ กำลังโหลด...</p>`);
  let data;
  try {
    const res = await apiFetch(treeBase(plot));
    data = await res.json();
    if (!res.ok) throw new Error(friendlyErrorMessage(data.detail, "โหลดไม่สำเร็จ"));
  } catch (e) {
    body.innerHTML = `<p style="color:#e53935;font-size:13px">${escHtml(e.message || "โหลดไม่สำเร็จ")}</p>`;
    return;
  }
  let list = data.trees;
  if (sortBy) list = [...list].sort((a, b) => window.treeDistance(sortBy, a) - window.treeDistance(sortBy, b));
  const pending = treeQueue().filter(q => q.url.startsWith(treeBase(plot))).length;
  body.innerHTML = `<div style="overflow-wrap:anywhere;min-width:0">
    <div class="form-hint" style="margin-top:0">ยืนที่ต้นไม้แล้วปักหมุด จากนั้นบันทึกอาการ วันใส่ปุ๋ย/พ่นยา และรูปของต้นนั้นได้
      · ตำแหน่ง GPS คลาดได้หลายเมตร ตั้งชื่อ/เลขต้นให้ตรงกับป้ายที่ต้นจะหาง่าย</div>
    <div class="sheet-actions">
      <button type="button" class="btn-primary" id="tr-pin">📍 ปักหมุดต้นที่ยืนอยู่</button>
      ${list.length ? `<button type="button" class="btn-secondary" id="tr-near">🧭 ต้นที่ใกล้ฉัน</button>` : ""}
    </div>
    ${pending ? `<div class="form-hint">รอส่ง ${pending} รายการ (ส่งเองเมื่อมีสัญญาณ)</div>` : ""}
    <div class="form-label">${list.length ? `ต้นไม้ ${list.length} ต้น${sortBy ? " · เรียงจากใกล้ไปไกล" : ""}` : "ยังไม่มีหมุดต้นไม้ในแปลงนี้"}</div>
    ${list.map(t => `<button type="button" class="rec-card" data-tree="${t.id}" style="display:block;width:100%;text-align:left;font:inherit;cursor:pointer;border:1px solid var(--line)">
      <div style="font-weight:700;font-size:15px">🌳 ${escHtml(t.label)}${t.crop_id ? ` <span style="font-weight:400;color:var(--muted)">· ${escHtml(cropNames([t.crop_id]).join(""))}</span>` : ""}
        ${sortBy ? `<span style="font-weight:400;color:var(--muted);font-size:12.5px"> · ~${Math.round(window.treeDistance(sortBy, t))} ม.</span>` : ""}</div>
      <div class="rec-reason">${t.last ? `ล่าสุด: ${escHtml(t.last.kind_th)}${t.last.tags_th.length ? " (" + escHtml(t.last.tags_th.join(", ")) + ")" : ""} · ${treeDateTh(t.last.happened_on)}` : "ยังไม่มีบันทึก"}
        · บันทึก ${t.logs} · รูป ${t.photos}</div>
    </button>`).join("")}
  </div>`;
  body.querySelector("#tr-pin").addEventListener("click", () => showPinForm(plot, data.next_label));
  body.querySelector("#tr-near")?.addEventListener("click", () => {
    if (!navigator.geolocation) return showToast("เครื่องนี้หาตำแหน่งไม่ได้");
    showToast("📍 กำลังหาตำแหน่ง...");
    navigator.geolocation.getCurrentPosition(p => showTrees(plot, { lat: p.coords.latitude, lng: p.coords.longitude }),
      () => showToast("หาตำแหน่งไม่ได้ — เปิดสิทธิ์ตำแหน่งให้ LINE แล้วลองใหม่"), { enableHighAccuracy: true, timeout: 20000 });
  });
  body.querySelectorAll("[data-tree]").forEach(b => b.addEventListener("click", () => showTree(plot, +b.dataset.tree)));
}

window.treeDistance = (here, t) => {
  const kx = 111320 * Math.cos(here.lat * Math.PI / 180);
  return Math.hypot((t.lng - here.lng) * kx, (t.lat - here.lat) * 111320);
};

// ─── ปักหมุด ───
function showPinForm(plot, nextLabel) {
  const crops = plot.crop_ids || [];
  const body = openTreeModal("📍 ปักหมุดต้นไม้", `
    <div class="result-section" style="font-size:13.5px;line-height:1.6">ยืนชิดโคนต้นไม้ แล้วรอให้ตำแหน่งแม่นก่อนบันทึก</div>
    <div id="tp-gps" style="font-size:14px;margin:8px 0">⏳ กำลังหาตำแหน่ง...</div>
    <div class="form-label">ชื่อ/เลขต้น</div>
    <div class="input-row" style="margin:0"><input id="tp-label" maxlength="40" value="${escHtml(nextLabel)}"></div>
    <div class="form-label">พืช (ไม่บังคับ)</div>
    <div class="chip-row" id="tp-crop">${cropList.filter(c => c.id !== "vegetables").map(c =>
      `<button type="button" class="chip${crops.length === 1 && crops[0] === c.id ? " on" : ""}" data-id="${escHtml(c.id)}">${escHtml(c.name_th)}</button>`).join("")}</div>
    <div class="sheet-actions"><button type="button" class="btn-primary" id="tp-save" disabled>💾 ปักหมุด</button>
      <button type="button" class="btn-secondary" id="tp-back">← กลับ</button></div>`);
  body.querySelectorAll("#tp-crop .chip").forEach(b => b.addEventListener("click", () => {
    const was = b.classList.contains("on");
    body.querySelectorAll("#tp-crop .chip").forEach(x => x.classList.remove("on"));   // ต้นละพืชเดียว
    if (!was) b.classList.add("on");
  }));
  let fix = null, watch = null;
  const gpsEl = body.querySelector("#tp-gps"), saveBtn = body.querySelector("#tp-save");
  const stop = () => { if (watch != null) { try { navigator.geolocation.clearWatch(watch); } catch {} watch = null; } };
  if (!navigator.geolocation) gpsEl.textContent = "⚠️ เครื่องนี้หาตำแหน่งไม่ได้";
  else watch = navigator.geolocation.watchPosition(p => {
    fix = { lat: p.coords.latitude, lng: p.coords.longitude, acc: p.coords.accuracy };
    gpsEl.innerHTML = fix.acc <= 15 ? `✅ ตำแหน่งแม่นพอแล้ว (±${Math.round(fix.acc)} ม.)`
      : `⏳ ตำแหน่งคลาด ±${Math.round(fix.acc)} ม. — รอสักครู่ (บันทึกได้ แต่หาต้นจากตำแหน่งจะยากขึ้น)`;
    saveBtn.disabled = fix.acc > 50;
  }, err => {
    gpsEl.textContent = err.code === 1 ? "⚠️ ยังไม่อนุญาตให้แอปใช้ตำแหน่ง — เปิดสิทธิ์ตำแหน่งให้ LINE แล้วลองใหม่" : "⚠️ หาตำแหน่งไม่ได้ ลองใหม่อีกครั้ง";
  }, { enableHighAccuracy: true, maximumAge: 0, timeout: 30000 });
  const closeObs = new MutationObserver(() => {
    if (!document.getElementById("detail-modal").classList.contains("show")) { stop(); closeObs.disconnect(); }
  });
  closeObs.observe(document.getElementById("detail-modal"), { attributes: true, attributeFilter: ["class"] });
  body.querySelector("#tp-back").addEventListener("click", () => { stop(); showTrees(plot); });
  saveBtn.addEventListener("click", async () => {
    stop();
    const crop = body.querySelector("#tp-crop .chip.on")?.dataset.id || null;
    await queueTreePost(treeBase(plot), { client_id: treeClientId(), lat: +fix.lat.toFixed(7), lng: +fix.lng.toFixed(7),
      accuracy_m: Math.round(fix.acc), label: body.querySelector("#tp-label").value.trim(), crop_id: crop });
    showTrees(plot);
  });
}

// ─── ประวัติของต้น ───
async function showTree(plot, treeId) {
  const body = openTreeModal("🌳 ต้นไม้", `<p style="color:#888;font-size:13px">⏳ กำลังโหลด...</p>`);
  let d;
  try {
    const res = await apiFetch(`${treeBase(plot)}/${treeId}`);
    d = await res.json();
    if (!res.ok) throw new Error(friendlyErrorMessage(d.detail, "โหลดไม่สำเร็จ"));
  } catch (e) {
    body.innerHTML = `<p style="color:#e53935;font-size:13px">${escHtml(e.message || "โหลดไม่สำเร็จ")}</p>`;
    return;
  }
  const t = d.tree;
  document.getElementById("modal-title").textContent = `🌳 ${t.label}`;
  const items = [
    ...d.logs.map(l => ({ at: l.happened_on, sort: l.happened_on + l.created_at, html: `
      <div class="rec-card"><div style="font-weight:700">${TREE_KIND_ICON[l.kind] || ""} ${escHtml(l.kind_th)}
        <span style="font-weight:400;color:var(--muted);font-size:12.5px">· ${treeDateTh(l.happened_on)}</span></div>
        ${l.tags_th.length ? `<div style="font-size:13.5px">${escHtml(l.tags_th.join(" · "))}</div>` : ""}
        ${l.product ? `<div style="font-size:13.5px">ใช้: ${escHtml(l.product)}</div>` : ""}
        ${l.note ? `<div class="rec-reason">${escHtml(l.note)}</div>` : ""}
        ${l.can_delete ? `<button type="button" data-del-log="${l.id}" style="margin-top:6px;background:none;border:1px solid #f0dcdc;color:#b91c1c;border-radius:9px;padding:6px 10px;font:inherit;font-size:12.5px;min-height:36px;cursor:pointer">🗑️ ลบ</button>` : ""}
      </div>` })),
    ...d.photos.map(p => ({ at: p.taken_at || p.created_at, sort: (p.taken_at || p.created_at), html: `
      <div class="rec-card">${p.url ? `<img src="${escHtml(p.url)}" alt="" loading="lazy" style="width:100%;max-height:240px;object-fit:cover;border-radius:10px">` : ""}
        <div class="rec-reason">📷 ${treeDateTh(p.taken_at || p.created_at)}${p.tags_th.length ? " · " + escHtml(p.tags_th.join(", ")) : ""}</div>
        ${p.note ? `<div class="rec-reason">${escHtml(p.note)}</div>` : ""}</div>` })),
  ].sort((a, b) => String(b.sort).localeCompare(String(a.sort)));
  body.innerHTML = `<div style="overflow-wrap:anywhere;min-width:0">
    <div class="form-hint" style="margin-top:0">${t.crop_id ? escHtml(cropNames([t.crop_id]).join("")) + " · " : ""}ปักหมุดเมื่อ ${treeDateTh(t.created_at)}</div>
    <div class="chip-row" style="margin-top:8px">
      ${Object.keys(TREE_KIND_TH).map(k => `<button type="button" class="chip" data-kind="${k}">${TREE_KIND_ICON[k]} ${TREE_KIND_TH[k]}</button>`).join("")}
      <button type="button" class="chip" id="tt-photo">📷 รูป</button>
    </div>
    <div class="form-label">ประวัติ (ล่าสุดก่อน)</div>
    ${items.length ? items.map(i => i.html).join("") : `<p class="form-hint">ยังไม่มีบันทึกของต้นนี้</p>`}
    <div class="sheet-actions">
      <button type="button" class="btn-secondary" id="tt-back">← รายการต้นไม้</button>
      <button type="button" class="btn-secondary" id="tt-rename">✏️ เปลี่ยนชื่อ</button>
      <button type="button" class="btn-secondary" id="tt-remove">🗑️ เอาหมุดออก</button>
    </div></div>`;
  body.querySelectorAll("[data-kind]").forEach(b => b.addEventListener("click", () => showLogForm(plot, t, b.dataset.kind)));
  body.querySelector("#tt-photo").addEventListener("click", () => {
    document.getElementById("detail-modal").classList.remove("show");
    window.openPhotoSheet?.(plot, null, { id: t.id, label: t.label });
  });
  body.querySelector("#tt-back").addEventListener("click", () => showTrees(plot));
  body.querySelector("#tt-rename").addEventListener("click", async () => {
    const label = (prompt("ชื่อ/เลขต้นใหม่", t.label) || "").trim();
    if (!label || label === t.label) return;
    const res = await apiFetch(`${treeBase(plot)}/${t.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" },
                                                            body: JSON.stringify({ label: label.slice(0, 40) }) }).catch(() => null);
    if (res?.ok) showTree(plot, t.id); else showToast("⚠️ เปลี่ยนชื่อไม่สำเร็จ");
  });
  body.querySelector("#tt-remove").addEventListener("click", async () => {
    if (!confirm(`เอาหมุด "${t.label}" ออก? (ประวัติยังเก็บไว้ในข้อมูล)`)) return;
    const res = await apiFetch(`${treeBase(plot)}/${t.id}`, { method: "DELETE" }).catch(() => null);
    if (res?.ok) showTrees(plot);
    else showToast(`⚠️ ${friendlyErrorMessage((await res?.json().catch(() => ({})))?.detail, "ไม่สำเร็จ")}`);
  });
  body.querySelectorAll("[data-del-log]").forEach(b => b.addEventListener("click", async () => {
    if (!confirm("ลบบันทึกนี้?")) return;
    const res = await apiFetch(`${treeBase(plot)}/${t.id}/logs/${b.dataset.delLog}`, { method: "DELETE" }).catch(() => null);
    if (res?.ok) showTree(plot, t.id); else showToast("⚠️ ลบไม่สำเร็จ");
  }));
}

// ─── บันทึกอาการ / ใส่ปุ๋ย / พ่นยา / บันทึก ───
function showLogForm(plot, tree, kind) {
  const tags = (typeof photoTags !== "undefined" ? photoTags : []).filter(t => !["drainage", "other"].includes(t.id));
  const body = openTreeModal(`${TREE_KIND_ICON[kind]} ${TREE_KIND_TH[kind]} — ${tree.label}`, `
    <div class="form-label">วันที่</div>
    <div class="input-row" style="margin:0"><input id="tl-date" type="date" value="${todayIso()}" max="${todayIso()}"></div>
    ${kind === "symptom" ? `<div class="form-label">เห็นอะไร (เลือกได้หลายอย่าง)</div>
      <div class="chip-row" id="tl-tags">${tags.map(t => `<button type="button" class="chip" data-tag="${escHtml(t.id)}">${escHtml(t.name_th)}</button>`).join("")}</div>` : ""}
    ${kind === "fertilize" || kind === "spray" ? `<div class="form-label">${kind === "fertilize" ? "ปุ๋ยที่ใช้" : "ยา/สารที่ใช้"} และปริมาณ (ไม่บังคับ)</div>
      <div class="input-row" style="margin:0"><input id="tl-product" maxlength="80" placeholder="${kind === "fertilize" ? "เช่น 15-15-15 ครึ่งกิโล" : "เช่น ชื่อยา อัตราต่อน้ำ 20 ลิตร"}"></div>` : ""}
    <div class="form-label">บันทึกเพิ่ม (ไม่บังคับ)</div>
    <div class="input-row" style="margin:0"><input id="tl-note" maxlength="300"></div>
    <div class="sheet-actions"><button type="button" class="btn-primary" id="tl-save">💾 บันทึก</button>
      <button type="button" class="btn-secondary" id="tl-back">← กลับ</button></div>
    <div class="form-hint">บันทึกสิ่งที่คุณทำ/เห็นเองไว้ดูย้อนหลัง — ระบบไม่ได้แนะนำปุ๋ยหรือยา</div>`);
  body.querySelectorAll("#tl-tags .chip").forEach(b => b.addEventListener("click", () => b.classList.toggle("on")));
  body.querySelector("#tl-back").addEventListener("click", () => showTree(plot, tree.id));
  body.querySelector("#tl-save").addEventListener("click", async () => {
    const chosen = [...body.querySelectorAll("#tl-tags .chip.on")].map(b => b.dataset.tag);
    if (kind === "symptom" && !chosen.length) { showToast("เลือกอาการที่เห็นอย่างน้อย 1 อย่าง"); return; }
    const happened = body.querySelector("#tl-date").value || todayIso();
    await queueTreePost(`${treeBase(plot)}/${tree.id}/logs`, {
      client_id: treeClientId(), kind, happened_on: happened, tags: chosen,
      product: body.querySelector("#tl-product")?.value.trim() || "", note: body.querySelector("#tl-note").value.trim() });
    showTree(plot, tree.id);
  });
}

window.showTrees = showTrees;
window.treesInit = () => { flushTreeQueue(); window.addEventListener("online", () => flushTreeQueue()); };
