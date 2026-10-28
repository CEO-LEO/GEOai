// cropguess.js — Phase 4a: "แปลงนี้น่าจะปลูกอะไร" แบบทาย + ยืนยัน (spec 7.6 — ระบบห้ามตัดสินเอง)
// ใช้ฟังก์ชันของสคริปต์หลัก: apiFetch, API_URL, userId, escHtml, cropList, cropNames, showToast, friendlyErrorMessage
// ยังไม่เปิดการทาย (status "collecting") → ถามผู้ใช้ว่าแปลงปลูกอะไร / ยืนยันพืชที่เลือกไว้ เพื่อเก็บเป็นข้อมูลสอนระบบ

function cropConfidenceTh(p) {
  if (p >= 0.7) return "ค่อนข้างมั่นใจ";
  if (p >= 0.5) return "พอใช้";
  return "ไม่แน่ใจ";
}

async function showCropGuess(plot, opts = {}) {
  document.getElementById("modal-title").textContent = `🌱 ชนิดพืช — ${plot.name || "แปลง"}`;
  const body = document.getElementById("modal-body");
  body.innerHTML = `<p style="color:#888;font-size:13px">⏳ กำลังโหลด...</p>`;
  document.getElementById("detail-modal").classList.add("show");
  let g;
  try {
    const res = await apiFetch(`${API_URL}/plots/${encodeURIComponent(userId)}/${plot.id}/crop-guess`);
    g = await res.json();
    if (!res.ok) throw new Error(friendlyErrorMessage(g.detail, "โหลดไม่สำเร็จ"));
  } catch (e) {
    body.innerHTML = `<p style="color:#e53935;font-size:13px">${escHtml(e.message || "โหลดไม่สำเร็จ")}</p>`;
    return;
  }
  const current = g.current_crop_ids || [];
  const guessed = ["ok", "uncertain", "not_orchard"].includes(g.status) ? g.top || [] : [];
  let head = "";
  if (g.status === "ok") {
    head = `<div class="form-label">ระบบทายจากภาพดาวเทียมว่าน่าจะเป็น</div>
      ${guessed.map((t, i) => `<div style="font-size:${i ? 13 : 16}px;${i ? "color:var(--muted)" : "font-weight:700"};margin:3px 0">
        ${i + 1}. ${escHtml(t.name_th)} <span style="font-weight:400;color:var(--muted)">· ${cropConfidenceTh(t.p)}</span></div>`).join("")}
      <div class="form-hint">ระบบทายเท่านั้น ยังไม่ได้บันทึกแทนคุณ — กดยืนยันด้านล่าง</div>`;
  } else if (g.status === "uncertain") {
    head = `<div class="result-section" style="font-size:14px">ระบบยังไม่แน่ใจ — อาจเป็น
      ${guessed.slice(0, 2).map(t => escHtml(t.name_th)).join(" หรือ ")} · ช่วยเลือกพืชที่ปลูกจริงด้านล่าง</div>`;
  } else if (g.status === "not_orchard") {
    head = `<div class="result-section" style="font-size:14px">ภาพดาวเทียมเห็นต้นไม้ในขอบเขตน้อย — อาจเป็นแปลงผัก/พืชไร่
      สวนที่เพิ่งปลูก หรือวาดขอบเขตเลยออกไป · ช่วยเลือกพืชที่ปลูกจริงด้านล่าง</div>`;
  } else if (current.length) {
    head = `<div class="result-section" style="font-size:15px">แปลงนี้ปลูก <b>${escHtml(cropNames(current).join(", "))}</b> ใช่ไหม?</div>`;
  } else {
    head = `<div class="result-section" style="font-size:15px">แปลงนี้ปลูกอะไร?</div>`;
  }
  const yesIds = g.status === "ok" && guessed[0] && cropList.some(c => c.id === guessed[0].crop_id)
    ? [guessed[0].crop_id] : (g.status === "collecting" || g.status === "unavailable") ? current : [];
  const pre = new Set(current.length ? current : (guessed[0] && cropList.some(c => c.id === guessed[0].crop_id) ? [guessed[0].crop_id] : []));
  // overflow-wrap: ข้อความไทยยาวไม่มีเว้นวรรคในเบราว์เซอร์ที่ตัดคำไทยไม่ได้ ไม่ดันแผ่นให้กว้างเกินจอ
  body.innerHTML = `<div style="overflow-wrap:anywhere;min-width:0">
    ${head}
    ${yesIds.length ? `<div class="sheet-actions">
      <button type="button" class="btn-primary" id="cg-yes">✅ ใช่ ${escHtml(cropNames(yesIds).join(", "))}</button>
      ${g.status === "ok" ? `<button type="button" class="btn-secondary" id="cg-no">❌ ไม่ใช่</button>` : ""}
    </div>` : ""}
    <div class="form-label">${yesIds.length ? "หรือเลือกพืชที่ปลูกจริง (เลือกได้หลายชนิด)" : "เลือกพืชที่ปลูก (เลือกได้หลายชนิด)"}</div>
    <div class="chip-row" id="cg-crops">${cropList.map(c =>
      `<button type="button" class="chip${pre.has(c.id) && !yesIds.length ? " on" : ""}" data-id="${escHtml(c.id)}">${escHtml(c.name_th)}</button>`).join("")}</div>
    <div class="sheet-actions">
      <button type="button" class="btn-primary" id="cg-save">💾 บันทึกพืชที่เลือก</button>
      <button type="button" class="btn-secondary" id="cg-unsure">ไม่แน่ใจ</button>
    </div>
    ${g.is_owner && g.owner_helps_training === false ? `
    <div class="result-section" id="cg-optin" style="font-size:13px;line-height:1.6;margin-top:12px">
      คำตอบของคุณจะใช้สอนระบบได้ เมื่อคุณยินยอมข้อ <b>"ใช้รูปถ่ายและการยืนยันในแปลงเพื่อปรับปรุงระบบ"</b>
      (ไม่เผยแพร่รูปหรือพิกัดของคุณต่อสาธารณะ · ถอนได้ทุกเมื่อใน ⚙️ ข้อมูลของฉัน) — ไม่ยินยอมก็ใช้แอปได้ตามปกติ
      <button type="button" class="btn-secondary" id="cg-optin-btn" style="width:100%;margin-top:8px">🤝 ยินยอมให้ใช้สอนระบบ</button>
    </div>` : ""}
    <div class="form-hint">คำตอบนี้ช่วยสอนระบบให้ทายชนิดพืชจากภาพดาวเทียมได้แม่นขึ้น
      ${g.attribution_th ? ` · ${escHtml(g.attribution_th)}` : ""}</div></div>`;
  // ยินยอมเฉพาะข้อนี้ — คงค่าข้ออื่นตามเดิม (อ่านค่าปัจจุบันก่อนบันทึก)
  body.querySelector("#cg-optin-btn")?.addEventListener("click", async () => {
    try {
      const cur = await (await apiFetch(`${API_URL}/me/privacy`)).json();
      // ปุ่มนี้ห้ามกลายเป็นการยอมรับนโยบายทั้งฉบับแทนผู้ใช้
      if (cur.needs_consent) throw new Error("กรุณาอ่านและยอมรับนโยบายความเป็นส่วนตัวก่อน (เปิดแอปใหม่)");
      const res = await apiFetch(`${API_URL}/me/consent`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ version: cur.version, share_with_officers: !!cur.choices?.share_with_officers,
                               photos_research: true }),
      });
      if (!res.ok) throw new Error(friendlyErrorMessage((await res.json().catch(() => ({}))).detail, "บันทึกไม่สำเร็จ"));
      body.querySelector("#cg-optin").innerHTML = "✅ ขอบคุณครับ — คำตอบของคุณจะช่วยสอนระบบ (ถอนได้ใน ⚙️ ข้อมูลของฉัน)";
    } catch (e) {
      showToast(`⚠️ ${e.message || "บันทึกไม่สำเร็จ"}`);
    }
  });
  body.querySelectorAll("#cg-crops .chip").forEach(b => b.addEventListener("click", () => b.classList.toggle("on")));
  const send = async (answer, ids, close = true) => {
    try {
      const res = await apiFetch(`${API_URL}/plots/${encodeURIComponent(userId)}/${plot.id}/crop-feedback`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answer, crop_ids: ids, prediction_id: g.prediction_id || null }),
      });
      const j = await res.json();
      if (!res.ok) throw new Error(friendlyErrorMessage(j.detail, "บันทึกไม่สำเร็จ"));
      if (answer === "yes" || answer === "other") plot.crop_ids = j.crop_ids;
      if (!close) return;
      document.getElementById("detail-modal").classList.remove("show");
      showToast(`🌱 ${j.message_th}`);
      (opts.onSaved || window.renderPlotList)?.();
    } catch (e) {
      showToast(`⚠️ ${e.message || "บันทึกไม่สำเร็จ"}`);
    }
  };
  body.querySelector("#cg-yes")?.addEventListener("click", () => send("yes", yesIds));
  body.querySelector("#cg-no")?.addEventListener("click", () => {
    send("no", [], false);     // "ไม่ใช่" เป็นคำตอบในตัว — แล้วให้เลือกพืชจริงต่อ (ไม่บังคับ)
    body.querySelector("#cg-yes")?.closest(".sheet-actions")?.remove();
    showToast("ขอบคุณครับ — ถ้ารู้ว่าปลูกอะไร เลือกแล้วกดบันทึก");
  });
  body.querySelector("#cg-save").addEventListener("click", () => {
    const ids = [...body.querySelectorAll("#cg-crops .chip.on")].map(b => b.dataset.id);
    if (!ids.length) { showToast("เลือกพืชอย่างน้อย 1 ชนิด"); return; }
    const answer = yesIds.length && ids.length === yesIds.length && ids.every(i => yesIds.includes(i)) ? "yes" : "other";
    send(answer, ids);
  });
  body.querySelector("#cg-unsure").addEventListener("click", () => send("unsure", []));
}

window.showCropGuess = showCropGuess;
