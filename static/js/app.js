(function () {
  "use strict";

  // Sidebar on small screens
  var sidebar = document.getElementById("sidebar");
  var scrim = document.querySelector(".scrim");
  function setSidebar(open) {
    if (!sidebar) return;
    sidebar.classList.toggle("is-open", open);
    if (scrim) scrim.classList.toggle("is-open", open);
  }
  document.addEventListener("click", function (e) {
    if (e.target.closest("[data-toggle-sidebar]")) setSidebar(!sidebar.classList.contains("is-open"));
    if (e.target.closest("[data-close-sidebar]")) setSidebar(false);
    // close the user menu when clicking elsewhere
    document.querySelectorAll("details.usermenu[open]").forEach(function (d) {
      if (!d.contains(e.target)) d.removeAttribute("open");
    });
  });

  // Selects that submit their form on change (filters, branch switch)
  document.addEventListener("change", function (e) {
    var el = e.target.closest("[data-autosubmit]");
    if (el && el.form) el.form.requestSubmit ? el.form.requestSubmit() : el.form.submit();
  });

  // Fade out success messages
  document.querySelectorAll(".toast-success").forEach(function (t) {
    setTimeout(function () { t.classList.add("is-hiding"); }, 4000);
    setTimeout(function () { t.remove(); }, 4400);
  });

  // Forms that ask "are you sure?" before submitting
  document.addEventListener("submit", function (e) {
    var msg = e.target.getAttribute && e.target.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) e.preventDefault();
  });

  // ---- Tooth chart: click a tooth to set its condition ----
  var toothDialog = document.querySelector("[data-tooth-dialog]");
  if (toothDialog && toothDialog.showModal) {
    document.querySelectorAll(".chart .tooth").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var n = btn.getAttribute("data-tooth");
        toothDialog.querySelector("[data-tooth-input]").value = n;
        toothDialog.querySelector("[data-tooth-label]").textContent = n;
        toothDialog.querySelector("[data-tooth-note]").value = btn.getAttribute("data-note") || "";
        var cond = btn.getAttribute("data-condition");
        toothDialog.querySelectorAll("input[name=condition]").forEach(function (r) { r.checked = r.value === cond; });
        toothDialog.showModal();
      });
    });
    toothDialog.querySelector("[data-tooth-close]").addEventListener("click", function () { toothDialog.close(); });
  }

  // ---- Package form: add/remove service rows, live totals ----
  var pkg = document.querySelector("[data-package]");
  if (!pkg) return;
  var prices = JSON.parse((document.getElementById("service-prices") || {}).textContent || "{}");
  var body = pkg.querySelector("[data-formset-body]");
  var tpl = pkg.querySelector("[data-formset-template]");
  var addBtn = pkg.querySelector("[data-formset-add]");
  var prefix = addBtn.getAttribute("data-prefix");
  var total = document.getElementById("id_" + prefix + "-TOTAL_FORMS");
  var priceInput = document.getElementById("id_price");

  function fmt(n) {
    var whole = Math.round(n * 100) % 100 === 0;
    return n.toLocaleString("en-US", { minimumFractionDigits: whole ? 0 : 2, maximumFractionDigits: whole ? 0 : 2 });
  }

  function recalc() {
    var sum = 0;
    body.querySelectorAll(".formset-row").forEach(function (row) {
      var sel = row.querySelector("select");
      var qty = row.querySelector("input[type=number]");
      var del = row.querySelector("input[type=checkbox][name$=DELETE]");
      var out = row.querySelector("[data-line-total]");
      var unit = sel && prices[sel.value] ? parseFloat(prices[sel.value]) : 0;
      var line = unit * (qty ? parseInt(qty.value || "0", 10) : 0);
      if (del && del.checked) line = 0;
      out.textContent = unit ? fmt(line) : "—";
      sum += line;
    });
    var price = parseFloat((priceInput && priceInput.value) || "0") || 0;
    pkg.querySelector("[data-original-total]").textContent = fmt(sum);
    pkg.querySelector("[data-package-price]").textContent = fmt(price);
    pkg.querySelector("[data-savings]").textContent = fmt(Math.max(sum - price, 0));
  }

  addBtn.addEventListener("click", function () {
    var index = parseInt(total.value, 10);
    var html = tpl.innerHTML.replace(/__prefix__/g, String(index));
    body.insertAdjacentHTML("beforeend", html);
    total.value = String(index + 1);
    var last = body.lastElementChild.querySelector("select");
    if (last) last.focus();
    recalc();
  });

  body.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-remove-row]");
    if (!btn) return;
    // Reset the row to its untouched state instead of removing it, so form
    // indexes stay continuous and the server skips it as an empty row.
    var row = btn.closest(".formset-row");
    row.querySelectorAll("select").forEach(function (el) { el.value = ""; });
    row.querySelectorAll("input[type=number]").forEach(function (el) { el.value = el.defaultValue || "1"; });
    row.hidden = true;
    recalc();
  });

  pkg.addEventListener("input", recalc);
  pkg.addEventListener("change", recalc);
  if (priceInput) priceInput.addEventListener("input", recalc);
  recalc();
})();
