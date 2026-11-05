// validation.js — Phase 2d: ยืนยันในแปลงจริง "จริง / ไม่จริง / ไม่แน่ใจ" (รายจุดบนแผนที่ และทั้งแปลง)
// คำตอบใช้วัดว่าระบบบอกถูกแค่ไหน และเป็นข้อมูลสอนระบบในอนาคต — server ตรวจสิทธิ์ (เจ้าของ/เจ้าหน้าที่ในพื้นที่)
// เกษตรกรอาสา (ยินยอมข้อ "เข้าร่วมทดสอบ") ตอบเพิ่มได้: "ต้นไม้ตรงจุดนี้มีอาการไหม" และ "จุดที่ระบบไม่เตือนมีน้ำขังไหม"
// ใช้ฟังก์ชันของสคริปต์หลัก: apiFetch, API_URL, userId, escHtml, showToast, friendlyErrorMessage, userPlots

function findPlotById(id) {
  const all = [...(typeof userPlots !== "undefined" ? userPlots : []),
               ...(typeof officerPlots !== "undefined" ? officerPlots : [])];
  return all.find(p => p.id === id) || { id, name: "แปลง" };
}

const VALIDATION_QUESTIONS = {
  main:    { point: "ไปดูที่จุดนี้แล้ว เป็นแบบนี้จริงไหม?", plot: "ในแปลงจริงเป็นแบบนี้ไหม?" },
  symptom: { point: "🌳 ต้นไม้ตรงจุดนี้มีอาการผิดปกติไหม? (ใบเหลือง ใบร่วง โคนเน่า)" },
  water:   { point: "💧 ตรงจุดนี้มีน้ำขังหรือดินแฉะไหม? (ระบบไม่ได้เตือนจุดนี้)" },
};

function isVolunteerTester() { return typeof privacyState !== "undefined" && !!privacyState?.choices?.volunteer_test; }

// opts = {scope, key: "main"|"symptom"|"water"}
function validationBlockHtml(opts) {
  const key = opts.key || "main";
  const q = VALIDATION_QUESTIONS[key][opts.scope];
  return `
    <div data-vblock="${key}">
    <div class="form-label">${escHtml(q)}</div>
    <div class="type-row" data-validate>
      <button type="button" class="chip" data-answer="true">${key === "main" ? "✅ จริง" : "✅ มี"}</button>
      <button type="button" class="chip" data-answer="false">${key === "main" ? "❌ ไม่จริง" : "❌ ไม่มี"}</button>
      <button type="button" class="chip" data-answer="unsure">🤔 ไม่แน่ใจ</button>
    </div>
    ${opts.scope === "point" && key !== "water" ? `<button type="button" class="btn-secondary sheet-btn" data-photo-point>📷 แนบรูปจุดนี้</button>` : ""}
    </div>`;
}

// opts = {plotId, scope: "point"|"plot", problem, severity, lat, lng, context, key}
function bindValidation(container, opts) {
  const wrap = container.querySelector(`[data-vblock="${opts.key || "main"}"]`);
  if (!wrap) return;
  const row = wrap.querySelector("[data-validate]");
  if (!row) return;
  row.querySelectorAll("[data-answer]").forEach(btn => btn.onclick = async () => {
    row.querySelectorAll("button").forEach(b => { b.disabled = true; });
    try {
      const res = await apiFetch(`${API_URL}/plots/${encodeURIComponent(userId)}/${opts.plotId}/validations`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope: opts.scope, problem: opts.problem, severity: opts.severity || null,
                               answer: btn.dataset.answer, lat: opts.lat ?? null, lng: opts.lng ?? null,
                               context: opts.context || null, sample_id: opts.sampleId || null }),
      });
      const json = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(friendlyErrorMessage(json.detail, "บันทึกไม่สำเร็จ"));
      btn.classList.add("on");
      row.insertAdjacentHTML("afterend", `<div class="form-hint">🙏 ${escHtml(json.message_th || "บันทึกแล้ว")}</div>`);
    } catch (e) {
      row.querySelectorAll("button").forEach(b => { b.disabled = false; });
      showToast("❌ " + e.message);
    }
  });
  wrap.querySelector("[data-photo-point]")?.addEventListener("click", () => {
    window.openPhotoSheet?.(findPlotById(opts.plotId), { lat: opts.lat, lng: opts.lng });
  });
}

// คำถามเพิ่มของเกษตรกรอาสา ต่อท้ายแผ่นข้อมูลจุด — flag = ปัญหาที่ระบบชี้ที่จุดนี้ (หรือ "none")
function volunteerBlocksHtml(flagged) {
  if (!isVolunteerTester()) return "";
  return validationBlockHtml({ scope: "point", key: "symptom" }) +
         (flagged ? "" : validationBlockHtml({ scope: "point", key: "water" }));
}
function bindVolunteerBlocks(container, base, flag) {
  if (!isVolunteerTester()) return;
  const ctx = { ...(base.context || {}), flag: flag || "none" };
  bindValidation(container, { ...base, key: "symptom", problem: "tree_symptom", severity: null, context: ctx });
  bindValidation(container, { ...base, key: "water", problem: "water_check_unflagged", severity: null, context: ctx });
}

// ── สุ่มจุดให้ตรวจ (วัด recall) ──
// ctx = {plotId, raised, candidates(): [{lat,lng,flag}], highlight(points), openPoint(point, sampleId)}
// จุดตั้งต้น = จุดที่ระบบไม่ได้ชี้ว่าชื้น/น้ำรวม · server สุ่มเอง (seed บันทึกไว้) และคืนชุดเดิมภายใน 14 วัน
function renderSampleBox(el, ctx) {
  if (!el) return;
  if (ctx.raised || !ctx.plotId || !isVolunteerTester()) { el.style.display = "none"; return; }
  el.style.display = "";
  el.innerHTML = `<button type="button" class="btn-secondary sheet-btn" id="sample-btn">🎲 สุ่มจุดให้ตรวจ</button>
    <div class="form-hint">ระบบสุ่ม 5 จุดที่ <b>ไม่ได้เตือน</b> กระจายทั่วแปลง ให้ไปดูว่าน้ำขังจริงไหม — ใช้วัดว่าระบบพลาดแค่ไหน</div>`;
  el.querySelector("#sample-btn").onclick = async () => {
    const btn = el.querySelector("#sample-btn"); btn.disabled = true;
    try {
      const res = await apiFetch(`${API_URL}/plots/${encodeURIComponent(userId)}/${ctx.plotId}/validation-sample`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ points: ctx.candidates() }),
      });
      const j = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(friendlyErrorMessage(j.detail, "สุ่มจุดไม่สำเร็จ"));
      ctx.highlight?.(j.points);
      showSampleList(j, ctx);
    } catch (e) { showToast("❌ " + e.message); }
    finally { btn.disabled = false; }
  };
}

function showSampleList(j, ctx) {
  const done = j.points.filter(p => p.answered).length;
  const rows = j.points.map((p, i) => `
    <div class="rec-card" style="display:flex;gap:8px;align-items:center;justify-content:space-between">
      <span><b>จุดที่ ${p.stratum}</b> ${p.answered ? "✅ ตอบแล้ว" : ""}<br>
        <span class="rec-reason">📍 ${p.lat.toFixed(6)}, ${p.lng.toFixed(6)}</span></span>
      <span style="display:flex;gap:6px;flex-wrap:wrap">
        <a class="btn-secondary" style="padding:8px 10px;text-decoration:none;font-size:13px" target="_blank" rel="noopener"
           href="https://www.google.com/maps?q=${p.lat},${p.lng}">🧭 นำทาง</a>
        <button type="button" class="btn-primary" style="padding:8px 10px;font-size:13px" data-i="${i}">ตอบจุดนี้</button>
      </span>
    </div>`).join("");
  const box = typeof accountSheet === "function" ? accountSheet(`
    <h3>🎲 จุดที่สุ่มให้ตรวจ (${done}/${j.points.length} จุดตอบแล้ว)</h3>
    <div class="form-hint" style="margin-top:0">${j.reused ? "ชุดเดิมที่สุ่มไว้แล้ว (คงไว้ 14 วัน) · " : ""}จุดละ 1 บริเวณของแปลง · วงสีเขียวบนแผนที่คือจุดเหล่านี้</div>
    ${rows}
    <button type="button" class="btn-secondary sheet-btn" id="sample-close">ปิด</button>`) : null;
  if (!box) return;
  box.querySelector("#sample-close").onclick = closeAccountSheet;
  box.querySelectorAll("[data-i]").forEach(b => b.onclick = () => {
    closeAccountSheet();
    ctx.openPoint?.(j.points[+b.dataset.i], j.sample_id);
  });
}

window.renderSampleBox = renderSampleBox;
window.validationBlockHtml = validationBlockHtml;
window.bindValidation = bindValidation;
window.volunteerBlocksHtml = volunteerBlocksHtml;
window.bindVolunteerBlocks = bindVolunteerBlocks;
window.isVolunteerTester = isVolunteerTester;
