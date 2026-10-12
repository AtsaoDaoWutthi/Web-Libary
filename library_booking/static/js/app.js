/* ระบบจองที่นั่งห้องสมุด: ส่วนโต้ตอบฝั่งเบราว์เซอร์
   - นาฬิกานับถอยหลังเช็คอิน 15 นาที (ซิงก์กับเวลาของเซิร์ฟเวอร์)
   - หน้าต่าง dialog, แท็บวิธีเช็คอิน, จำลองการสแกน QR, แจ้งเตือน */
(function () {
  "use strict";
  var serverNow = parseInt(document.body.dataset.serverNow, 10);
  var skew = isNaN(serverNow) ? 0 : serverNow - Date.now();   // เวลาเซิร์ฟเวอร์ (รวมโหมดทดสอบ) - เวลาเครื่อง
  var now = function () { return Date.now() + skew; };
  var reloading = false;
  function reloadSoon() {
    if (reloading) return;
    reloading = true;
    setTimeout(function () { window.location.reload(); }, 900);
  }
  var pad = function (n) { return String(n).padStart(2, "0"); };
  function thaiDuration(ms) {
    var m = Math.max(0, Math.ceil(ms / 60000)), h = Math.floor(m / 60), mm = m % 60;
    if (!h && !mm) return "ไม่ถึง 1 นาที";
    return (h ? h + " ชั่วโมง" : "") + (h && mm ? " " : "") + (mm ? mm + " นาที" : "");
  }

  // ---------- นาฬิกานับถอยหลัง ----------
  var WINDOW_MS = 15 * 60 * 1000;
  function tick() {
    var t = now();
    document.querySelectorAll("[data-countdown]").forEach(function (el) {
      var rem = parseInt(el.dataset.countdown, 10) - t;
      var s = Math.max(0, Math.ceil(rem / 1000));
      var text = el.querySelector("[data-cd-text]"), bar = el.querySelector("[data-cd-bar]");
      if (text) text.textContent = pad(Math.floor(s / 60)) + ":" + pad(s % 60);
      if (bar) bar.style.width = Math.max(0, Math.min(100, rem / WINDOW_MS * 100)) + "%";
      el.classList.toggle("warn5", rem <= 300000 && rem > 120000);
      el.classList.toggle("warn2", rem <= 120000);
      if (rem <= 0) reloadSoon();   // หมดเวลา: ให้เซิร์ฟเวอร์ยกเลิกและปล่อยโต๊ะ
    });
    document.querySelectorAll("[data-until]").forEach(function (el) {
      var rem = parseInt(el.dataset.until, 10) - t;
      el.textContent = thaiDuration(rem);
      if (rem <= 0) reloadSoon();   // ถึงเวลาเริ่ม: เปิดให้เช็คอิน
    });
    var clock = document.querySelector("[data-clock]");
    if (clock) { var d = new Date(t); clock.textContent = pad(d.getHours()) + ":" + pad(d.getMinutes()); }
  }
  tick();
  setInterval(tick, 1000);

  // ---------- dialog ----------
  function openDialog(d) {
    if (!d) return;
    if (typeof d.showModal === "function") { if (!d.open) d.showModal(); }
    else d.setAttribute("open", "");
  }
  function closeDialog(d) {
    if (!d) return;
    if (typeof d.close === "function") d.close(); else d.removeAttribute("open");
  }
  document.querySelectorAll("dialog[data-autoopen]").forEach(openDialog);
  document.querySelectorAll("dialog").forEach(function (d) {
    d.addEventListener("click", function (e) { if (e.target === d) closeDialog(d); }); // แตะพื้นหลังเพื่อปิด
  });

  document.addEventListener("click", function (e) {
    var t = e.target.closest("[data-open],[data-close],[data-toggle],[data-pane],[data-fill],.toast-x");
    if (!t) return;
    if (t.dataset.open) { openDialog(document.getElementById(t.dataset.open)); return; }
    if (t.hasAttribute("data-close")) { closeDialog(t.closest("dialog")); return; }
    if (t.dataset.toggle) {
      var box = document.getElementById(t.dataset.toggle);
      if (box) box.hidden = !box.hidden;
      return;
    }
    if (t.dataset.pane) {
      var dlg = t.closest("dialog");
      dlg.querySelectorAll("[data-pane]").forEach(function (b) {
        var on = b === t;
        b.setAttribute("aria-selected", on ? "true" : "false");
        document.getElementById(b.dataset.pane).hidden = !on;
      });
      var input = document.getElementById(t.dataset.pane).querySelector("input[name=value]:not([type=hidden])");
      if (input) input.focus();
      return;
    }
    if (t.dataset.fill) {
      var sid = document.getElementById("student_id");
      if (sid) { sid.value = t.dataset.fill; sid.form.submit(); }
      return;
    }
    if (t.classList.contains("toast-x")) t.parentElement.remove();
  });

  // ---------- จำลองการสแกน QR ----------
  document.querySelectorAll("form[data-scan]").forEach(function (f) {
    f.addEventListener("submit", function (e) {
      if (f.dataset.scanned) return;
      e.preventDefault();
      f.classList.add("scanning");
      var label = f.querySelector("[data-scan-label]");
      if (label) label.textContent = "กำลังสแกน…";
      f.querySelector("button").disabled = true;
      setTimeout(function () { f.dataset.scanned = "1"; f.submit(); }, 1300);
    });
  });

  // ---------- แจ้งเตือนหายเอง ----------
  document.querySelectorAll(".toast").forEach(function (el) {
    setTimeout(function () { el.classList.add("hide"); setTimeout(function () { el.remove(); }, 400); }, 5000);
  });
})();
