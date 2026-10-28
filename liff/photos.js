// photos.js — Phase 2c: รูปถ่ายภาคสนาม
// - อ่านพิกัด/เวลาที่ถ่ายจาก EXIF ในเครื่อง (ไม่ต้องใช้ไลบรารีเพิ่ม) แล้วส่งแยกเป็นข้อมูล
// - บีบอัดด้วย canvas → JPEG (ไฟล์ที่ได้ไม่มี EXIF) ด้านยาวไม่เกิน 1600 px
// - คิวอัปโหลดใน IndexedDB: สัญญาณหลุดไม่ทำรูปหาย ส่งใหม่เองเมื่อออนไลน์ / เปิดแอปครั้งถัดไป
// ใช้ฟังก์ชันของสคริปต์หลัก: apiFetch, API_URL, userId, escHtml, showToast, showSpinner, hideSpinner, friendlyErrorMessage

const PHOTO_MAX_SIDE = 1600;
const PHOTO_QUALITY = 0.8;
let photoTags = [
  ["waterlogged", "น้ำขัง"], ["soggy_soil", "ดินแฉะ"], ["dry_soil", "ดินแห้ง/ขาดน้ำ"], ["yellow_leaves", "ใบเหลือง"],
  ["leaf_drop", "ใบร่วง"], ["root_rot", "โคนเน่า/รากเน่า"], ["wilting", "ต้นเหี่ยว"], ["pest", "แมลง/โรคพืช"],
  ["drainage", "ร่องน้ำ/ทางระบายน้ำ"], ["normal", "ปกติ"], ["other", "อื่น ๆ"],
].map(([id, name_th]) => ({ id, name_th }));

// ─── EXIF (JPEG → TIFF → IFD0 → Exif IFD / GPS IFD) ─────────────────
function readExif(buf) {
  try {
    const v = new DataView(buf);
    if (v.byteLength < 4 || v.getUint16(0) !== 0xFFD8) return {};
    let off = 2;
    while (off + 10 < v.byteLength) {
      const marker = v.getUint16(off);
      if ((marker & 0xFF00) !== 0xFF00) break;
      const len = v.getUint16(off + 2);
      if (marker === 0xFFE1 && v.getUint32(off + 4) === 0x45786966) return parseTiff(v, off + 10);
      off += 2 + len;
    }
  } catch {}
  return {};
}

function parseTiff(v, start) {
  const little = v.getUint16(start) === 0x4949;
  const u16 = o => v.getUint16(start + o, little);
  const u32 = o => v.getUint32(start + o, little);
  const ifd = o => {
    const out = {};
    const n = u16(o);
    for (let i = 0; i < n; i++) {
      const e = o + 2 + i * 12;
      out[u16(e)] = { type: u16(e + 2), count: u32(e + 4), at: e + 8 };
    }
    return out;
  };
  const ascii = t => {
    const p = t.count > 4 ? u32(t.at) : t.at;
    let s = "";
    for (let i = 0; i < t.count - 1; i++) s += String.fromCharCode(v.getUint8(start + p + i));
    return s;
  };
  const rationals = t => {
    const p = u32(t.at);
    return [0, 1, 2].map(i => u32(p + i * 8) / (u32(p + i * 8 + 4) || 1));
  };
  const out = {};
  const ifd0 = ifd(u32(4));
  if (ifd0[0x8769]) {
    const ex = ifd(u32(ifd0[0x8769].at));
    const dt = ex[0x9003] || ex[0x9004];
    if (dt) {   // "2026:10:25 14:03:11" (เวลาเครื่อง — ถือเป็นเวลาไทย)
      const m = ascii(dt).match(/^(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})/);
      if (m) out.taken_at = `${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6]}+07:00`;
    }
  }
  if (ifd0[0x8825]) {
    const g = ifd(u32(ifd0[0x8825].at));
    if (g[2] && g[4]) {
      const [a, b, c] = rationals(g[2]);
      const [d, e, f] = rationals(g[4]);
      let lat = a + b / 60 + c / 3600, lng = d + e / 60 + f / 3600;
      if (g[1] && ascii(g[1]).startsWith("S")) lat = -lat;
      if (g[3] && ascii(g[3]).startsWith("W")) lng = -lng;
      if (Number.isFinite(lat) && Number.isFinite(lng) && (lat !== 0 || lng !== 0)) { out.lat = lat; out.lng = lng; }
    }
  }
  return out;
}

// ─── บีบอัด ─────────────────────────────────────
async function compressImage(file) {
  let src, w, h;
  try {
    src = await createImageBitmap(file);   // เบราว์เซอร์ใหม่หมุนตาม EXIF ให้เอง
    w = src.width; h = src.height;
  } catch {
    src = await new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error("เปิดรูปไม่ได้"));
      img.src = URL.createObjectURL(file);
    });
    w = src.naturalWidth; h = src.naturalHeight;
  }
  const scale = Math.min(1, PHOTO_MAX_SIDE / Math.max(w, h));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(w * scale);
  canvas.height = Math.round(h * scale);
  canvas.getContext("2d").drawImage(src, 0, 0, canvas.width, canvas.height);
  const blob = await new Promise(r => canvas.toBlob(r, "image/jpeg", PHOTO_QUALITY));
  if (!blob) throw new Error("บีบอัดรูปไม่สำเร็จ");
  return await new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(fr.result);
    fr.onerror = () => reject(new Error("อ่านรูปไม่สำเร็จ"));
    fr.readAsDataURL(blob);
  });
}

function deviceLocation() {
  return new Promise(resolve => {
    if (!navigator.geolocation) return resolve(null);
    navigator.geolocation.getCurrentPosition(
      p => resolve({ lat: p.coords.latitude, lng: p.coords.longitude }),
      () => resolve(null), { enableHighAccuracy: true, timeout: 8000, maximumAge: 60000 });
  });
}

// ─── คิวอัปโหลด (IndexedDB) ──────────────────────────
function queueDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open("iamroot", 1);
    req.onupgradeneeded = () => req.result.createObjectStore("photoQueue", { keyPath: "client_id" });
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}
async function queueOp(mode, fn) {
  const db = await queueDb();
  return await new Promise((resolve, reject) => {
    const tx = db.transaction("photoQueue", mode);
    const result = fn(tx.objectStore("photoQueue"));
    tx.oncomplete = () => resolve(result && "result" in result ? result.result : undefined);
    tx.onerror = () => reject(tx.error);
  });
}
const queueAll = () => queueOp("readonly", s => s.getAll()).catch(() => []);
const queuePut = item => queueOp("readwrite", s => s.put(item));
const queueDel = id => queueOp("readwrite", s => s.delete(id));

let queueBusy = false, queueTimer = null;
const photoListeners = new Set();

async function processPhotoQueue() {
  if (queueBusy || !userId) return;
  queueBusy = true;
  let retryIn = 0;
  try {
    for (const item of await queueAll()) {
      if (item.owner !== userId) continue;
      let res;
      try {
        res = await apiFetch(`${API_URL}/plots/${encodeURIComponent(userId)}/${item.plot_id}/photos`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ client_id: item.client_id, image_base64: item.data_url, lat: item.lat, lng: item.lng,
                                 gps_source: item.gps_source, taken_at: item.taken_at, tags: item.tags, note: item.note,
                                 tree_id: item.tree_id || null }),
        });
      } catch { res = null; }   // ไม่มีสัญญาณ
      if (res && res.ok) {
        await queueDel(item.client_id);
        photoListeners.forEach(fn => fn(item.plot_id));
        showToast("📷 อัปโหลดรูปแล้ว");
      } else if (res && [400, 403, 404, 409, 413, 422, 428].includes(res.status)) {
        await queueDel(item.client_id);   // ส่งใหม่ก็ไม่ผ่าน — แจ้งแล้วทิ้ง
        showToast("❌ รูปส่งไม่ได้: " + friendlyErrorMessage(await res.text(), `รหัส ${res.status}`));
      } else {
        item.tries = (item.tries || 0) + 1;
        await queuePut(item);
        retryIn = Math.min(600, 15 * 2 ** Math.min(item.tries, 5));
        break;   // เครือข่ายมีปัญหา — หยุดรอบนี้ ลองใหม่ทีหลัง
      }
    }
  } finally {
    queueBusy = false;
    updatePendingBadge();
  }
  if (retryIn) { clearTimeout(queueTimer); queueTimer = setTimeout(processPhotoQueue, retryIn * 1000); }
}
window.addEventListener("online", processPhotoQueue);

async function pendingFor(plotId) {
  return (await queueAll()).filter(i => i.owner === userId && (!plotId || i.plot_id === plotId));
}
async function updatePendingBadge() {
  const el = document.getElementById("photo-pending");
  if (!el) return;
  const n = (await pendingFor(Number(el.dataset.plotId))).length;
  el.textContent = n ? `⏳ รอส่ง ${n} รูป — จะส่งเองเมื่อมีสัญญาณ` : "";
}

// ─── หน้าแนบรูป ─────────────────────────────────────
function photoSheet(html) {
  let modal = document.getElementById("photo-modal");
  if (!modal) {
    modal = document.createElement("div");
    modal.id = "photo-modal";
    modal.className = "sheet-modal";
    modal.innerHTML = `<div class="sheet-box" id="photo-box"></div>`;
    document.body.appendChild(modal);
    modal.addEventListener("click", e => { if (e.target === modal) modal.classList.remove("show"); });
  }
  document.getElementById("photo-box").innerHTML = html;
  modal.classList.add("show");
  return document.getElementById("photo-box");
}

// point (ถ้ามี) = {lat, lng} จุดบนแผนที่ที่แตะ (2d) · tree (ถ้ามี) = {id, label} รูปของต้นไม้ต้นนั้น (6.3)
async function openPhotoSheet(plot, point, tree) {
  const box = photoSheet(`
    <h3>📷 รูปของแปลง "${escHtml(plot.name || "แปลง")}"</h3>
    ${tree ? `<div class="form-hint" style="margin-top:0">🌳 แนบกับ ${escHtml(tree.label)}</div>`
      : point ? `<div class="form-hint" style="margin-top:0">แนบกับจุดที่แตะบนแผนที่</div>` : ""}
    <label class="btn-primary sheet-btn" style="display:flex;align-items:center;justify-content:center;cursor:pointer">
      ➕ ถ่ายรูป / เลือกรูป<input type="file" accept="image/*" id="photo-input" hidden>
    </label>
    <div id="photo-form"></div>
    <div id="photo-pending" class="form-hint" data-plot-id="${plot.id}"></div>
    <div class="form-label">รูปที่แนบแล้ว</div>
    <div id="photo-grid"><p class="form-hint">กำลังโหลด...</p></div>
    <button class="close-modal-btn" id="photo-close">ปิด</button>`);
  box.querySelector("#photo-close").onclick = () => document.getElementById("photo-modal").classList.remove("show");
  box.querySelector("#photo-input").onchange = e => { if (e.target.files[0]) preparePhoto(plot, e.target.files[0], point, tree); };
  updatePendingBadge();
  loadPhotoGrid(plot);
  const refresh = id => { if (id === plot.id && document.getElementById("photo-grid")) loadPhotoGrid(plot); };
  photoListeners.clear();
  photoListeners.add(refresh);
}

async function preparePhoto(plot, file, point, tree) {
  const form = document.getElementById("photo-form");
  form.innerHTML = `<p class="form-hint">⏳ กำลังเตรียมรูป...</p>`;
  let dataUrl, exif = {};
  try {
    exif = readExif(await file.slice(0, 256 * 1024).arrayBuffer());
    dataUrl = await compressImage(file);
  } catch (e) {
    form.innerHTML = "";
    return showToast("❌ " + (e.message || "เปิดรูปไม่ได้"));
  }
  let loc = null, src = null;
  if (point) { loc = point; src = "map_point"; }
  else if (exif.lat != null) { loc = { lat: exif.lat, lng: exif.lng }; src = "exif"; }
  const selected = new Set();
  const render = () => {
    form.innerHTML = `
      <img src="${dataUrl}" alt="" style="width:100%;max-height:260px;object-fit:contain;border-radius:12px;margin-top:10px;background:#111">
      <div class="form-hint">${loc ? (src === "exif" ? "📍 ใช้ตำแหน่งที่บันทึกในรูป" : src === "map_point" ? "📍 ใช้จุดที่แตะบนแผนที่" : "📍 ใช้ตำแหน่งมือถือตอนนี้")
        : `ไม่มีตำแหน่งในรูป · <button type="button" id="photo-use-gps" style="background:none;border:none;color:var(--green);font-weight:700;padding:0;font:inherit;cursor:pointer;min-height:32px">ใช้ตำแหน่งมือถือตอนนี้</button>`}
        ${exif.taken_at ? ` · ถ่ายเมื่อ ${new Date(exif.taken_at).toLocaleString("th-TH", { dateStyle: "medium", timeStyle: "short" })}` : ""}</div>
      <div class="form-label">เห็นอะไรในรูป (เลือกได้หลายอย่าง)</div>
      <div class="chip-row">${photoTags.map(t => `<button type="button" class="chip${selected.has(t.id) ? " on" : ""}" data-tag="${t.id}">${escHtml(t.name_th)}</button>`).join("")}</div>
      <div class="input-row" style="margin:10px 0 0"><input id="photo-note" maxlength="300" placeholder="บันทึกเพิ่ม (ไม่บังคับ)"></div>
      <button type="button" class="btn-primary sheet-btn" id="photo-save">💾 บันทึกรูป</button>`;
    form.querySelectorAll("[data-tag]").forEach(b => b.onclick = () => {
      const note = form.querySelector("#photo-note").value;
      selected.has(b.dataset.tag) ? selected.delete(b.dataset.tag) : selected.add(b.dataset.tag);
      render();
      form.querySelector("#photo-note").value = note;
    });
    form.querySelector("#photo-use-gps")?.addEventListener("click", async () => {
      showSpinner("📍 กำลังหาตำแหน่ง...");
      loc = await deviceLocation();
      hideSpinner();
      if (loc) { src = "device"; render(); } else showToast("หาตำแหน่งไม่ได้ — บันทึกรูปโดยไม่มีตำแหน่งได้");
    });
    form.querySelector("#photo-save").onclick = async () => {
      const item = {
        client_id: (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(36).slice(2)).replace(/[^A-Za-z0-9_-]/g, ""),
        owner: userId, plot_id: plot.id, tree_id: tree ? tree.id : null, data_url: dataUrl,
        lat: loc ? loc.lat : null, lng: loc ? loc.lng : null, gps_source: loc ? src : null,
        taken_at: exif.taken_at || null, tags: [...selected], note: form.querySelector("#photo-note").value.trim(),
        tries: 0, added: Date.now(),
      };
      try { await queuePut(item); }
      catch { return showToast("❌ บันทึกรูปในเครื่องไม่สำเร็จ"); }
      form.innerHTML = "";
      showToast(navigator.onLine === false ? "⏳ ยังไม่มีสัญญาณ — เก็บรูปไว้ จะส่งเองเมื่อออนไลน์" : "⏳ กำลังส่งรูป...");
      updatePendingBadge();
      processPhotoQueue();
    };
  };
  render();
}

async function loadPhotoGrid(plot) {
  const grid = document.getElementById("photo-grid");
  if (!grid) return;
  try {
    const res = await apiFetch(`${API_URL}/plots/${encodeURIComponent(userId)}/${plot.id}/photos`);
    if (!res.ok) throw new Error();
    const photos = (await res.json()).photos || [];
    grid.innerHTML = photos.length ? photos.map(p => `
      <div class="rec-card">
        ${p.url ? `<img src="${escHtml(p.url)}" alt="" loading="lazy" style="width:100%;max-height:240px;object-fit:cover;border-radius:10px">` : ""}
        <div style="font-size:13.5px;margin-top:6px">${p.tags_th.length ? escHtml(p.tags_th.join(" · ")) : "ไม่ได้แท็ก"}</div>
        ${p.note ? `<div class="rec-reason">${escHtml(p.note)}</div>` : ""}
        <div class="rec-reason">${new Date(p.taken_at || p.created_at).toLocaleString("th-TH", { dateStyle: "medium", timeStyle: "short" })}
          · ${escHtml(p.uploader_role_th)}${p.lat != null ? " · 📍 มีตำแหน่ง" : ""}</div>
        ${p.can_delete ? `<button type="button" data-del="${p.id}" style="margin-top:6px;background:none;border:1px solid #f0dcdc;color:#b91c1c;border-radius:9px;padding:6px 10px;font:inherit;font-size:12.5px;min-height:36px;cursor:pointer">🗑️ ลบรูป</button>` : ""}
      </div>`).join("") : `<p class="form-hint">ยังไม่มีรูป</p>`;
    grid.querySelectorAll("[data-del]").forEach(b => b.onclick = async () => {
      if (!confirm("ลบรูปนี้?")) return;
      const r = await apiFetch(`${API_URL}/plots/${encodeURIComponent(userId)}/${plot.id}/photos/${b.dataset.del}`, { method: "DELETE" });
      if (r.ok) { showToast("🗑️ ลบรูปแล้ว"); loadPhotoGrid(plot); } else showToast("❌ ลบไม่สำเร็จ");
    });
  } catch {
    grid.innerHTML = `<p class="form-hint">โหลดรูปไม่สำเร็จ (ถ้าไม่มีสัญญาณ รูปที่รอส่งยังอยู่ในเครื่อง)</p>`;
  }
}

async function loadPhotoTags() {
  try {
    const res = await fetch(`${API_URL}/photo-tags`);
    if (res.ok) photoTags = (await res.json()).tags || photoTags;
  } catch {}
}

window.openPhotoSheet = openPhotoSheet;
window.photosInit = () => { loadPhotoTags(); processPhotoQueue(); };
