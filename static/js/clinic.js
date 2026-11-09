/* Stage 2: patient picker, booking form helpers, calendar click-to-book, queue screen. */
(function () {
  "use strict";

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  // ---------- patient picker (booking form) ----------
  var picker = document.querySelector("[data-patient-picker]");
  if (picker) {
    var hidden = picker.querySelector("input[name=patient]");
    var picked = picker.querySelector("[data-picked]");
    var searchBox = picker.querySelector("[data-picker-search]");
    var input = picker.querySelector("[data-picker-input]");
    var list = picker.querySelector("[data-picker-results]");
    var empty = picker.querySelector("[data-picker-empty]");
    var newLink = picker.querySelector("[data-picker-new]");
    var newBase = picker.getAttribute("data-new-url");
    var url = picker.getAttribute("data-search-url");
    var timer = null, seq = 0;

    function choose(p) {
      hidden.value = p.id;
      picked.querySelector("[data-picked-name]").textContent = p.name;
      picked.querySelector("[data-picked-phone]").textContent = p.phone;
      picked.querySelector("[data-picked-file]").textContent = p.file_no;
      var allergy = picked.querySelector("[data-picked-allergy]");
      allergy.hidden = !p.allergies;
      allergy.querySelector("span").textContent = p.allergies || "";
      picked.hidden = false;
      searchBox.hidden = true;
      list.innerHTML = "";
    }

    function render(results, q) {
      list.innerHTML = "";
      empty.hidden = results.length > 0 || q.length < 2;
      results.forEach(function (p) {
        var li = el("li");
        var b = el("button", "picker-item");
        b.type = "button";
        b.appendChild(el("strong", null, p.name));
        b.appendChild(el("small", "num", p.phone + " · " + p.file_no));
        b.addEventListener("click", function () { choose(p); });
        li.appendChild(b);
        list.appendChild(li);
      });
    }

    input.addEventListener("input", function () {
      var q = input.value.trim();
      if (newLink) newLink.href = newBase + (q ? "&q=" + encodeURIComponent(q) : "");
      clearTimeout(timer);
      if (q.length < 2) { render([], q); return; }
      timer = setTimeout(function () {
        var mine = ++seq;
        fetch(url + "?q=" + encodeURIComponent(q), { headers: { "X-Requested-With": "fetch" } })
          .then(function (r) { return r.json(); })
          .then(function (data) { if (mine === seq) render(data.results || [], q); })
          .catch(function () { render([], q); });
      }, 220);
    });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {  // pick the first match instead of submitting the form
        e.preventDefault();
        var first = list.querySelector("button");
        if (first) first.click();
      }
    });
    picker.querySelector("[data-picker-change]").addEventListener("click", function () {
      hidden.value = "";
      picked.hidden = true;
      searchBox.hidden = false;
      input.value = "";
      input.focus();
    });
    if (!hidden.value) input.focus();
  }

  // ---------- service picks its usual duration ----------
  var durations = document.getElementById("service-durations");
  var serviceSel = document.getElementById("id_service");
  var durationInput = document.getElementById("id_duration_minutes");
  if (durations && serviceSel && durationInput) {
    var map = JSON.parse(durations.textContent);
    serviceSel.addEventListener("change", function () {
      if (map[serviceSel.value]) durationInput.value = map[serviceSel.value];
    });
  }

  // ---------- calendar: click an empty spot to book ----------
  document.querySelectorAll(".cal-col.is-bookable").forEach(function (col) {
    col.addEventListener("click", function (e) {
      if (e.target.closest(".cal-appt")) return;
      var rect = col.getBoundingClientRect();
      var px = parseFloat(col.getAttribute("data-px"));
      var minutes = (e.clientY - rect.top) / px;
      var total = parseInt(col.getAttribute("data-first-hour"), 10) * 60 + Math.floor(minutes / 15) * 15;
      var hh = String(Math.floor(total / 60)).padStart(2, "0");
      var mm = String(total % 60).padStart(2, "0");
      location.href = col.getAttribute("data-book-url") + "?doctor=" + col.getAttribute("data-doctor") +
        "&date=" + col.getAttribute("data-date") + "&time=" + hh + ":" + mm;
    });
  });

  // ---------- queue screen ----------
  var queue = document.querySelector("[data-queue]");
  if (queue) {
    var grid = queue.querySelector("[data-queue-grid]");
    var clock = queue.querySelector("[data-queue-clock]");
    var labels = {
      now: queue.getAttribute("data-now-label"),
      wait: queue.getAttribute("data-wait-label"),
      none: queue.getAttribute("data-none-label"),
      empty: document.querySelector("[data-queue-empty]").innerHTML
    };
    var tick = function () {
      clock.textContent = new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
    };
    tick();
    setInterval(tick, 15000);

    var draw = function (doctors) {
      grid.innerHTML = "";
      if (!doctors.length) { grid.appendChild(el("p", "queue-empty", labels.empty)); return; }
      doctors.forEach(function (d) {
        var card = el("section", "queue-card");
        card.style.setProperty("--c", d.color);
        card.appendChild(el("h2", null, d.doctor));
        var now = el("div", "queue-now");
        now.appendChild(el("span", "queue-label", labels.now));
        if (d.now.length) {
          d.now.forEach(function (p) {
            var big = el("div", "queue-big");
            big.appendChild(el("span", "num", p.n));
            big.appendChild(el("small", null, p.name));
            now.appendChild(big);
          });
        } else { now.appendChild(el("div", "queue-big is-empty", "—")); }
        card.appendChild(now);
        var wait = el("div", "queue-wait");
        wait.appendChild(el("span", "queue-label", labels.wait));
        var ol = el("ol");
        if (d.waiting.length) {
          d.waiting.forEach(function (p) {
            var li = el("li");
            li.appendChild(el("span", "num", p.n));
            li.appendChild(document.createTextNode(" " + p.name));
            ol.appendChild(li);
          });
        } else { ol.appendChild(el("li", "muted", labels.none)); }
        wait.appendChild(ol);
        card.appendChild(wait);
        grid.appendChild(card);
      });
    };
    setInterval(function () {
      fetch(queue.getAttribute("data-url"), { headers: { "X-Requested-With": "fetch" } })
        .then(function (r) { if (r.redirected || !r.ok) throw new Error("signed out"); return r.json(); })
        .then(function (data) { draw(data.doctors || []); })
        .catch(function () { /* keep showing the last state */ });
    }, 10000);

    var fs = queue.querySelector("[data-fullscreen]");
    if (fs && document.documentElement.requestFullscreen) {
      fs.addEventListener("click", function () { document.documentElement.requestFullscreen(); });
    } else if (fs) { fs.hidden = true; }
  }
})();
