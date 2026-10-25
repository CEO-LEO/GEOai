// validation.js — Phase 2d: ยืนยันในแปลงจริง "จริง / ไม่จริง / ไม่แน่ใจ" (รายจุดบนแผนที่ และทั้งแปลง)
// คำตอบใช้วัดว่าระบบบอกถูกแค่ไหน และเป็นข้อมูลสอนระบบในอนาคต — server ตรวจสิทธิ์ (เจ้าของ/เจ้าหน้าที่ในพื้นที่)
// ใช้ฟังก์ชันของสคริปต์หลัก: apiFetch, API_URL, userId, escHtml, showToast, friendlyErrorMessage, userPlots

function findPlotById(id) {
  const all = [...(typeof userPlots !== "undefined" ? userPlots : []),
               ...(typeof officerPlots !== "undefined" ? officerPlots : [])];
  return all.find(p => p.id === id) || { id, name: "แปลง" };
}

function validationBlockHtml(opts) {
  return `
    <div class="form-label">${opts.scope === "point" ? "ไปดูที่จุดนี้แล้ว เป็นแบบนี้จริงไหม?" : "ในแปลงจริงเป็นแบบนี้ไหม?"}</div>
    <div class="type-row" data-validate>
      <button type="button" class="chip" data-answer="true">✅ จริง</button>
      <button type="button" class="chip" data-answer="false">❌ ไม่จริง</button>
      <button type="button" class="chip" data-answer="unsure">🤔 ไม่แน่ใจ</button>
    </div>
    ${opts.scope === "point" ? `<button type="button" class="btn-secondary sheet-btn" data-photo-point>📷 แนบรูปจุดนี้</button>` : ""}`;
}

// opts = {plotId, scope: "point"|"plot", problem, severity, lat, lng, context}
function bindValidation(container, opts) {
  const row = container.querySelector("[data-validate]");
  if (!row) return;
  row.querySelectorAll("[data-answer]").forEach(btn => btn.onclick = async () => {
    row.querySelectorAll("button").forEach(b => { b.disabled = true; });
    try {
      const res = await apiFetch(`${API_URL}/plots/${encodeURIComponent(userId)}/${opts.plotId}/validations`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scope: opts.scope, problem: opts.problem, severity: opts.severity || null,
                               answer: btn.dataset.answer, lat: opts.lat ?? null, lng: opts.lng ?? null,
                               context: opts.context || null }),
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
  container.querySelector("[data-photo-point]")?.addEventListener("click", () => {
    window.openPhotoSheet?.(findPlotById(opts.plotId), { lat: opts.lat, lng: opts.lng });
  });
}

window.validationBlockHtml = validationBlockHtml;
window.bindValidation = bindValidation;
