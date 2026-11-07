// Progressive enhancement only: every page works without JavaScript.
(function () {
  "use strict";

  // Don't send empty parameters: keeps URLs clean and shareable.
  // form.elements includes controls attached with the form="" attribute.
  function prune(form) {
    Array.prototype.forEach.call(form.elements, function (el) {
      if (el.name && el.value === "" && (el.type !== "radio" || el.checked)) el.disabled = true;
    });
  }

  // Forms marked data-client are handled entirely in JS (static catalogue).
  document.querySelectorAll("form[method=get]:not([data-client])").forEach(function (form) {
    form.addEventListener("submit", function () { prune(form); });
  });

  // Filters apply as soon as they change.
  document.querySelectorAll("[data-autosubmit]").forEach(function (el) {
    el.addEventListener("change", function () {
      var form = el.form;
      if (!form) return;
      var clears = el.getAttribute("data-clears");
      if (clears) {
        // Changing the domain resets the specialization; the governorate resets the city.
        Array.prototype.forEach.call(form.elements, function (dep) { if (dep.name === clears) dep.disabled = true; });
      }
      prune(form);
      form.submit();
    });
  });

  // Static company directory: filter cards by name as you type.
  var cf = document.querySelector("[data-company-filter]");
  if (cf) {
    var fold = function (s) { return s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase(); };
    var cards = document.querySelectorAll(".company[data-name]");
    var counter = document.getElementById("company-count");
    cf.addEventListener("input", function () {
      var q = fold(cf.value.trim()), n = 0;
      cards.forEach(function (c) {
        var show = !q || c.getAttribute("data-name").indexOf(q) >= 0 || fold(c.textContent).indexOf(q) >= 0;
        c.hidden = !show;
        if (show) n++;
      });
      if (counter) counter.textContent = n;
    });
  }

  // Mobile filter drawer.
  var toggle = document.querySelector(".filters-toggle");
  var facets = document.getElementById("facets");
  if (toggle && facets) {
    toggle.addEventListener("click", function () {
      var open = facets.classList.toggle("is-open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }
})();
