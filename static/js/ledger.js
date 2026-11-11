/* Accounting pages: live debit/credit totals on journal entries, bill totals, voucher allocations. */
(function () {
  "use strict";

  function num(v) { var n = parseFloat(v); return isNaN(n) ? 0 : n; }
  function fmt(n) {
    return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function json(id) {
    var el = document.getElementById(id);
    return el ? JSON.parse(el.textContent) : null;
  }

  // ---------- journal entry: totals, balance check, foreign-currency box ----------
  var form = document.querySelector("[data-entry-form]");
  if (form) {
    var info = json("account-info") || {};
    var chip = form.querySelector("[data-balance-chip]");
    var lang = document.documentElement.lang;
    var txt = lang === "en" ? { ok: "Balanced", diff: "Difference" } : { ok: "متوازن", diff: "الفرق" };

    var rows = function () {
      return Array.prototype.filter.call(form.querySelectorAll("[data-formset-body] .formset-row"),
        function (r) { return !r.hidden; });
    };

    var recalc = function () {
      var d = 0, c = 0;
      rows().forEach(function (r) {
        d += num(r.querySelector("input[name$=-debit]").value);
        c += num(r.querySelector("input[name$=-credit]").value);
        var sel = r.querySelector("select[name$=-account]");
        var meta = info[sel && sel.value] || {};
        var box = r.querySelector("[data-fx-box]");
        if (box) {
          box.hidden = !meta.fx;
          var code = box.querySelector("[data-fx-code]");
          if (code) code.textContent = meta.fx || "";
        }
        var party = r.querySelector("input[name$=-party]");
        if (party) party.classList.toggle("is-required", !!meta.control);
      });
      form.querySelector("[data-total-debit]").textContent = fmt(d);
      form.querySelector("[data-total-credit]").textContent = fmt(c);
      var diff = Math.round((d - c) * 100) / 100;
      chip.textContent = diff === 0 && d > 0 ? "✓ " + txt.ok : txt.diff + ": " + fmt(Math.abs(diff));
      chip.className = "balance-chip " + (diff === 0 && d > 0 ? "is-ok" : "is-bad");
      return diff;
    };

    // typing in one side clears the other
    form.addEventListener("input", function (e) {
      var t = e.target;
      if (/-debit$/.test(t.name) && t.value) {
        var other = t.closest(".formset-row").querySelector("input[name$=-credit]");
        if (other) other.value = "";
      } else if (/-credit$/.test(t.name) && t.value) {
        var other2 = t.closest(".formset-row").querySelector("input[name$=-debit]");
        if (other2) other2.value = "";
      }
      recalc();
    });
    form.addEventListener("change", recalc);
    form.addEventListener("rows-changed", recalc);

    var balBtn = form.querySelector("[data-balance-last]");
    if (balBtn) balBtn.addEventListener("click", function () {
      var all = rows();
      if (!all.length) return;
      var last = all[all.length - 1];
      var dIn = last.querySelector("input[name$=-debit]"), cIn = last.querySelector("input[name$=-credit]");
      dIn.value = ""; cIn.value = "";
      var diff = recalc();
      if (diff > 0) cIn.value = diff.toFixed(2); else if (diff < 0) dIn.value = (-diff).toFixed(2);
      recalc();
    });
    recalc();
  }

  // ---------- bill: line totals with tax ----------
  var bill = document.querySelector("[data-bill-form]");
  if (bill) {
    var taxes = json("tax-rates") || {};
    var recalcBill = function () {
      var sub = 0, tax = 0;
      bill.querySelectorAll("[data-formset-body] .formset-row").forEach(function (r) {
        if (r.hidden) return;
        var del = r.querySelector("input[name$=-DELETE]");
        var q = num((r.querySelector("input[name$=-quantity]") || {}).value);
        var p = num((r.querySelector("input[name$=-unit_price]") || {}).value);
        var tsel = r.querySelector("select[name$=-tax]");
        var net = Math.round(q * p * 100) / 100;
        var t = tsel && taxes[tsel.value] ? Math.round(net * num(taxes[tsel.value])) / 100 : 0;
        if (del && del.checked) { net = 0; t = 0; }
        var out = r.querySelector("[data-line-total]");
        if (out) out.textContent = net ? fmt(net + t) : "—";
        sub += net; tax += t;
      });
      bill.querySelector("[data-subtotal]").textContent = fmt(sub);
      bill.querySelector("[data-tax]").textContent = fmt(tax);
      bill.querySelector("[data-total]").textContent = fmt(sub + tax);
    };
    bill.addEventListener("input", recalcBill);
    bill.addEventListener("change", recalcBill);
    bill.addEventListener("rows-changed", recalcBill);
    recalcBill();
  }

  // ---------- voucher: allocated vs. amount ----------
  var vf = document.querySelector("[data-voucher-form]");
  if (vf) {
    var amountIn = vf.querySelector("input[name=amount]");
    var recalcV = function () {
      var a = 0;
      vf.querySelectorAll("input[name^=alloc-][name$=-amount]").forEach(function (i) { a += num(i.value); });
      var out = vf.querySelector("[data-allocated]");
      if (out) out.textContent = fmt(a);
      var left = vf.querySelector("[data-unallocated]");
      if (left) left.textContent = fmt(num(amountIn && amountIn.value) - a);
    };
    vf.addEventListener("input", recalcV);
    vf.querySelectorAll("[data-fill]").forEach(function (b) {
      b.addEventListener("click", function () {
        var input = vf.querySelector("input[name='" + b.getAttribute("data-fill") + "']");
        var remaining = num(amountIn && amountIn.value);
        vf.querySelectorAll("input[name^=alloc-][name$=-amount]").forEach(function (i) {
          if (i !== input) remaining -= num(i.value);
        });
        input.value = Math.max(0, Math.min(num(b.getAttribute("data-balance")), remaining || num(b.getAttribute("data-balance")))).toFixed(2);
        recalcV();
      });
    });
    recalcV();
  }

  // ---------- permission matrix colours ----------
  document.querySelectorAll(".perm-table select").forEach(function (s) {
    var paint = function () {
      s.classList.toggle("is-allow", s.value === "1");
      s.classList.toggle("is-deny", s.value === "0");
    };
    s.addEventListener("change", paint);
    paint();
  });

  // ---------- reconciliation: live cleared total ----------
  var rec = document.querySelector("[data-reconcile]");
  if (rec) {
    var base = num(rec.getAttribute("data-base"));
    var stmt = num(rec.getAttribute("data-statement"));
    var recalcR = function () {
      var cleared = base;
      rec.querySelectorAll("input[name=line]:checked").forEach(function (c) { cleared += num(c.getAttribute("data-amount")); });
      rec.querySelector("[data-cleared]").textContent = fmt(cleared);
      var diff = Math.round((stmt - cleared) * 100) / 100;
      var out = rec.querySelector("[data-diff]");
      out.textContent = fmt(diff);
      out.classList.toggle("num-neg", diff !== 0);
    };
    rec.addEventListener("change", recalcR);
    recalcR();
  }
})();
