/* Stage 3–4: add/remove rows in item tables, invoice totals, prices for the chosen patient. */
(function () {
  "use strict";

  function num(v) { var n = parseFloat(v); return isNaN(n) ? 0 : n; }
  function fmt(n) {
    var whole = Math.round(n * 100) % 100 === 0;
    return n.toLocaleString("en-US", { minimumFractionDigits: whole ? 0 : 2, maximumFractionDigits: whole ? 0 : 2 });
  }
  function json(id) {
    var el = document.getElementById(id);
    return el ? JSON.parse(el.textContent) : null;
  }

  // ---------- price for a service, using the patient's price list ----------
  var tables = json("price-tables");
  var picker = document.querySelector("[data-patient-picker]");
  var fixedList = document.querySelector("[data-price-list-fixed]");
  function priceFor(serviceId) {
    if (!tables || !serviceId) return null;
    var list = (picker && picker.getAttribute("data-price-list")) ||
               (fixedList && fixedList.getAttribute("data-price-list-fixed")) || tables["default"];
    if (list && tables.lists[list] && tables.lists[list][serviceId] != null) return tables.lists[list][serviceId];
    return tables.base[serviceId] != null ? tables.base[serviceId] : null;
  }

  // ---------- generic add/remove rows ----------
  document.querySelectorAll("[data-formset]").forEach(function (box) {
    var body = box.querySelector("[data-formset-body]");
    var tpl = box.querySelector("[data-formset-template]");
    var addBtn = box.querySelector("[data-formset-add]");
    if (!body || !tpl || !addBtn) return;
    var total = document.getElementById("id_" + addBtn.getAttribute("data-prefix") + "-TOTAL_FORMS");

    addBtn.addEventListener("click", function () {
      var index = parseInt(total.value, 10);
      body.insertAdjacentHTML("beforeend", tpl.innerHTML.replace(/__prefix__/g, String(index)));
      total.value = String(index + 1);
      var first = body.lastElementChild.querySelector("select, input:not([type=hidden])");
      if (first) first.focus();
      box.dispatchEvent(new Event("rows-changed", { bubbles: true }));
    });

    body.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-remove-row]");
      if (!btn) return;
      // Clear the row instead of removing it so form numbering stays continuous;
      // the server skips rows left empty.
      var row = btn.closest(".formset-row");
      row.querySelectorAll("select").forEach(function (el) { el.value = ""; });
      row.querySelectorAll("input:not([type=hidden]):not([type=checkbox])").forEach(function (el) { el.value = ""; });
      row.querySelectorAll("input[type=checkbox]").forEach(function (el) { el.checked = false; });
      row.hidden = true;
      box.dispatchEvent(new Event("rows-changed", { bubbles: true }));
    });

    // choosing a service fills its price
    body.addEventListener("change", function (e) {
      var sel = e.target;
      if (sel.tagName !== "SELECT" || !/-service$/.test(sel.name)) return;
      var row = sel.closest(".formset-row");
      var priceInput = row.querySelector("input[name$=-unit_price], input[name$=-price]");
      var p = priceFor(sel.value);
      if (priceInput && p != null) priceInput.value = p;
      var qty = row.querySelector("input[name$=-quantity]");
      if (qty && !qty.value) qty.value = 1;
      box.dispatchEvent(new Event("rows-changed", { bubbles: true }));
    });
  });

  // ---------- invoice totals ----------
  var inv = document.querySelector("[data-invoice]");
  if (inv) {
    var percents = json("doctor-percents") || {};
    var doctorSel = document.getElementById("id_doctor");
    var discount = document.getElementById("id_discount");
    var payAmount = document.getElementById("id_pay-amount");
    var lastTotal = 0;
    var set = function (sel, v) { var el = inv.querySelector(sel); if (el) el.textContent = typeof v === "number" ? fmt(v) : v; };

    var recalc = function () {
      var sub = 0, mat = 0;
      inv.querySelectorAll(".formset-row").forEach(function (row) {
        if (row.hidden || row.closest("template")) return;
        var del = row.querySelector("input[name$=DELETE]");
        var q = num((row.querySelector("input[name$=-quantity]") || {}).value);
        var p = num((row.querySelector("input[name$=-unit_price]") || {}).value);
        var m = num((row.querySelector("input[name$=-material_cost]") || {}).value);
        var line = q * p;
        if (del && del.checked) { line = 0; m = 0; }
        var out = row.querySelector("[data-line-total]");
        if (out) out.textContent = line ? fmt(line) : "—";
        sub += line; mat += m;
      });
      var disc = Math.min(num(discount && discount.value), sub);
      var total = Math.max(sub - disc, 0);
      var pct = doctorSel ? num(percents[doctorSel.value]) : 0;
      lastTotal = total;
      set("[data-subtotal]", sub); set("[data-discount]", disc); set("[data-total]", total);
      set("[data-material]", mat); set("[data-doctor-pct]", fmt(pct));
      set("[data-doctor-share]", Math.max(total - mat, 0) * pct / 100);
    };
    inv.addEventListener("input", recalc);
    inv.addEventListener("change", recalc);
    inv.addEventListener("rows-changed", recalc);
    var full = inv.querySelector("[data-pay-full]");
    if (full && payAmount) full.addEventListener("click", function () { payAmount.value = lastTotal.toFixed(2); payAmount.focus(); });
    // a new patient may have a different price list: refresh prices of rows already chosen
    inv.addEventListener("patientchosen", function () {
      inv.querySelectorAll(".formset-row select[name$=-service]").forEach(function (sel) {
        var p = priceFor(sel.value);
        var input = sel.closest(".formset-row").querySelector("input[name$=-unit_price]");
        if (p != null && input) input.value = p;
      });
      recalc();
    });
    recalc();
  }

  // ---------- treatment plan total ----------
  var plan = document.querySelector("[data-plan]");
  if (plan) {
    var planRecalc = function () {
      var sum = 0;
      plan.querySelectorAll(".formset-row").forEach(function (row) {
        if (row.hidden || row.closest("template")) return;
        var del = row.querySelector("input[name$=DELETE]");
        var line = num((row.querySelector("input[name$=-quantity]") || {}).value) *
                   num((row.querySelector("input[name$=-price]") || {}).value);
        if (del && del.checked) line = 0;
        sum += line;
      });
      var out = plan.querySelector("[data-plan-total]");
      if (out) out.textContent = fmt(sum);
    };
    plan.addEventListener("input", planRecalc);
    plan.addEventListener("change", planRecalc);
    plan.addEventListener("rows-changed", planRecalc);
    planRecalc();
  }

  // ---------- photo upload: shrink big phone photos before sending ----------
  var photoInput = document.querySelector("input[type=file][data-shrink]");
  if (photoInput && window.DataTransfer && window.HTMLCanvasElement) {
    var preview = document.querySelector("[data-photo-preview]");
    photoInput.addEventListener("change", function () {
      var file = photoInput.files[0];
      if (!file || !/^image\//.test(file.type)) return;
      var img = new Image();
      var url = URL.createObjectURL(file);
      img.onload = function () {
        var max = 1600, w = img.naturalWidth, h = img.naturalHeight;
        var scale = Math.min(1, max / Math.max(w, h));
        var c = document.createElement("canvas");
        c.width = Math.round(w * scale); c.height = Math.round(h * scale);
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
        URL.revokeObjectURL(url);
        c.toBlob(function (blob) {
          if (!blob) return;
          var dt = new DataTransfer();
          dt.items.add(new File([blob], (file.name || "photo").replace(/\.\w+$/, "") + ".jpg", { type: "image/jpeg" }));
          photoInput.files = dt.files;
          if (preview) { preview.src = URL.createObjectURL(blob); preview.hidden = false; }
        }, "image/jpeg", 0.85);
      };
      img.src = url;
    });
  }
})();
