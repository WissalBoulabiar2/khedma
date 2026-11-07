// Client-side catalogue for the static site: loads the job index once, then
// search, filters, facet counts, sorting and pagination run in the browser.
// URL parameters are the same as the server version (?q=&domain=&contract=...).
(function () {
  "use strict";
  var root = document.getElementById("catalogue");
  if (!root) return;
  var BASE = root.getAttribute("data-base") || "";
  var PER_PAGE = 20;
  var MULTI = ["contract", "experience", "workplace"];
  var SINGLE = ["q", "domain", "specialization", "governorate", "city", "posted_within", "company", "sort", "page"];
  var data, L, jobs;

  function fold(s) {
    return (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase()
      .replace(/[’']/g, " ").replace(/œ/g, "oe");
  }
  function words(s) { return " " + fold(s).replace(/[^a-z0-9+#]+/g, " ").trim() + " "; }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // ---------------------------------------------------------------- state
  function readState() {
    var p = new URLSearchParams(location.search), st = {};
    SINGLE.forEach(function (k) { st[k] = p.get(k) || ""; });
    MULTI.forEach(function (k) { st[k] = p.getAll(k); });
    st.page = Math.max(1, parseInt(st.page, 10) || 1);
    st.sort = st.sort === "relevance" ? "relevance" : "recent";
    return st;
  }
  function applyChanges(st, changes) {
    Object.keys(changes).forEach(function (k) {
      if (k.charAt(0) === "-") { var key = k.slice(1); st[key] = st[key].filter(function (v) { return v !== changes[k]; }); }
      else st[k] = changes[k];
    });
    if (!("page" in changes)) st.page = 1;
    return st;
  }
  // Real URL for a link (works with middle-click / open in new tab).
  function hrefFor(changes) {
    var qs = toQuery(applyChanges(JSON.parse(JSON.stringify(state)), changes));
    return location.pathname + (qs ? "?" + qs : "");
  }
  function toQuery(st) {
    var p = new URLSearchParams();
    SINGLE.forEach(function (k) {
      if (k === "page" && st.page <= 1) return;
      if (k === "sort" && st.sort === "recent") return;
      if (st[k]) p.set(k, st[k]);
    });
    MULTI.forEach(function (k) { st[k].forEach(function (v) { p.append(k, v); }); });
    return p.toString();
  }
  function writeState(st, push) {
    var qs = toQuery(st);
    var url = location.pathname + (qs ? "?" + qs : "");
    if (push) history.pushState(null, "", url); else history.replaceState(null, "", url);
  }

  // -------------------------------------------------------------- filtering
  function matches(j, st, skip, terms, since) {
    if (terms.length) {
      for (var i = 0; i < terms.length; i++) if (j._hay.indexOf(" " + terms[i]) < 0) return false;
    }
    if (st.domain && skip !== "domain" && j.d !== st.domain) return false;
    if (st.specialization && skip !== "specialization" && j.sp !== st.specialization) return false;
    if (st.governorate && skip !== "governorate" && j.g !== st.governorate) return false;
    if (st.city && skip !== "city" && j.ci !== st.city) return false;
    if (st.company && j.cs !== st.company) return false;
    if (st.contract.length && skip !== "contract" && !st.contract.some(function (c) { return j.ct.indexOf(c) >= 0; })) return false;
    if (st.experience.length && skip !== "experience" && st.experience.indexOf(j.e) < 0) return false;
    if (st.workplace.length && skip !== "workplace" && st.workplace.indexOf(j.w) < 0) return false;
    if (since && skip !== "posted_within" && j._t < since) return false;
    return true;
  }

  function facet(st, terms, since, name, key) {
    var counts = {};
    jobs.forEach(function (j) {
      if (!matches(j, st, name, terms, since)) return;
      var v = j[key];
      (Array.isArray(v) ? v : [v]).forEach(function (x) { if (x) counts[x] = (counts[x] || 0) + 1; });
    });
    return counts;
  }

  // -------------------------------------------------------------- rendering
  function reldate(t) {
    var days = Math.floor((Date.now() - t) / 86400000);
    if (days <= 0) return "aujourd'hui";
    if (days === 1) return "hier";
    if (days < 7) return "il y a " + days + " jours";
    if (days < 31) { var w = Math.floor(days / 7); return "il y a " + w + " semaine" + (w > 1 ? "s" : ""); }
    return new Date(t).toLocaleDateString("fr-FR", { day: "numeric", month: "short", year: "numeric" });
  }
  function initials(name) {
    return (name || "?").replace(/-/g, " ").split(/\s+/).filter(function (w) { return /^[\p{L}\p{N}]/u.test(w); })
      .slice(0, 2).map(function (w) { return w[0]; }).join("").toUpperCase() || "?";
  }
  function card(j) {
    var logo = j.lg
      ? '<span class="logo logo--md"><img src="' + esc(j.lg) + '" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.parentNode.classList.add(\'logo--fallback\');this.remove()"><span class="logo__txt">' + esc(initials(j.c)) + "</span></span>"
      : '<span class="logo logo--md logo--fallback"><span class="logo__txt">' + esc(initials(j.c)) + "</span></span>";
    var tags = j.ct.map(function (c) { return '<span class="tag tag--contract">' + esc(L.contract[c]) + "</span>"; }).join("");
    if (j.w && j.w !== "sur-site") tags += '<span class="tag tag--remote">' + esc(L.workplace[j.w]) + "</span>";
    if (j.e) tags += '<span class="tag">' + esc(L.experience[j.e].split(" (")[0]) + "</span>";
    tags += '<span class="tag tag--quiet">' + esc(j.sp !== "autre" ? L.specialization[j.sp] : L.domain[j.d]) + "</span>";
    return '<article class="job">' + logo + '<div class="job__body"><h3 class="job__title"><a href="' + BASE + "/emplois/" + j.i + "-" + esc(j.s) + '/">' +
      esc(j.t) + '</a></h3><p class="job__meta"><span class="job__company">' + esc(j.c || "Entreprise confidentielle") +
      '</span><span class="dot">' + esc(j.l) + '</span></p><p class="tags">' + tags + '</p></div><time class="job__date">' + reldate(j._t) + "</time></article>";
  }

  function select(name, label, allLabel, counts, labels, value, order) {
    var keys = order || Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a] || String(labels[a] || a).localeCompare(labels[b] || b, "fr"); });
    var html = '<div class="facet"><label for="f-' + name + '" class="facet__title">' + label + '</label><select id="f-' + name + '" data-name="' + name + '"><option value="">' + allLabel + "</option>";
    keys.forEach(function (k) {
      if (!counts[k] && k !== value && !order) return;
      html += '<option value="' + esc(k) + '"' + (k === value ? " selected" : "") + ">" + esc(labels[k] || k) + (counts[k] ? " (" + counts[k] + ")" : "") + "</option>";
    });
    return html + "</select></div>";
  }
  function checks(name, label, counts, labels, selected, type) {
    var html = '<fieldset class="facet"><legend class="facet__title">' + label + "</legend>", any = false;
    Object.keys(labels).forEach(function (k) {
      var on = type === "radio" ? selected === k : selected.indexOf(k) >= 0;
      if (type !== "radio" && !counts[k] && !on) return;
      any = true;
      html += '<label class="check"><input type="' + (type || "checkbox") + '" name="' + name + '" data-name="' + name + '" value="' + esc(k) + '"' + (on ? " checked" : "") + "><span>" +
        esc(labels[k]) + "</span>" + (type === "radio" ? "" : '<span class="check__n">' + (counts[k] || 0) + "</span>") + "</label>";
    });
    if (!any) html += '<p class="muted small">Aucune information</p>';
    return html + "</fieldset>";
  }

  function render(st) {
    var terms = fold(st.q).split(/[^a-z0-9+#]+/).filter(Boolean);
    var since = st.posted_within ? Date.now() - parseInt(st.posted_within, 10) * 86400000 : 0;
    var hits = jobs.filter(function (j) { return matches(j, st, null, terms, since); });
    if (st.sort === "relevance" && terms.length) {
      hits.forEach(function (j) { j._score = terms.reduce(function (s, t) { return s + (j._title.indexOf(" " + t) >= 0 ? 3 : 1); }, 0); });
      hits.sort(function (a, b) { return b._score - a._score || b._t - a._t; });
    } else {
      hits.sort(function (a, b) { return b._t - a._t || b.i - a.i; });
    }
    var pages = Math.max(1, Math.ceil(hits.length / PER_PAGE));
    if (st.page > pages) st.page = pages;
    var slice = hits.slice((st.page - 1) * PER_PAGE, st.page * PER_PAGE);

    document.getElementById("count").innerHTML = "<strong>" + hits.length.toLocaleString("fr-FR") + "</strong> offre" + (hits.length !== 1 ? "s" : "");
    document.getElementById("results").innerHTML = slice.length ? slice.map(card).join("")
      : '<div class="empty"><p><strong>Aucune offre ne correspond à ces critères.</strong></p><p>Essayez de retirer un filtre ou d\'utiliser des mots-clés plus généraux.</p></div>';
    var pager = "";
    if (pages > 1) {
      var pageLink = function (n, label) {
        return '<a href="' + esc(hrefFor({ page: n })) + '" data-page="' + n + '">' + label + "</a>";
      };
      pager = (st.page > 1 ? pageLink(st.page - 1, "← Précédent") : "<span></span>") +
        "<span>Page " + st.page + " sur " + pages + "</span>" +
        (st.page < pages ? pageLink(st.page + 1, "Suivant →") : "<span></span>");
    }
    document.getElementById("pager").innerHTML = pager;

    // Facets (each ignores its own filter, like the server version).
    var specCounts = facet(st, terms, since, "specialization", "sp");
    var html = select("domain", "Domaine", "Tous les domaines", facet(st, terms, since, "domain", "d"), L.domain, st.domain);
    if (st.domain) {
      Object.keys(specCounts).forEach(function (k) { if (data.spec_domain[k] !== st.domain) delete specCounts[k]; });
      html += select("specialization", "Spécialité", "Toutes les spécialités", specCounts, L.specialization, st.specialization);
    }
    html += select("governorate", "Gouvernorat", "Toute la Tunisie", facet(st, terms, since, "governorate", "g"), L.governorate, st.governorate, data.governorate_order);
    if (st.governorate) {
      var cities = facet(st, terms, since, "city", "ci");
      if (Object.keys(cities).length) html += select("city", "Ville", "Toutes les villes", cities, {}, st.city);
    }
    html += checks("contract", "Type de contrat", facet(st, terms, since, "contract", "ct"), L.contract, st.contract);
    html += checks("experience", "Niveau d'expérience", facet(st, terms, since, "experience", "e"), L.experience, st.experience);
    html += checks("workplace", "Mode de travail", facet(st, terms, since, "workplace", "w"), L.workplace, st.workplace);
    var dates = { "": "Toutes les dates" };
    Object.keys(L.posted_within).forEach(function (k) { dates[k] = L.posted_within[k]; });
    html += checks("posted_within", "Date de publication", {}, dates, st.posted_within, "radio");
    document.getElementById("facet-body").innerHTML = html;

    // Active filter chips.
    var chips = [];
    if (st.q) chips.push(["« " + st.q + " »", { q: "" }]);
    if (st.domain) chips.push([L.domain[st.domain], { domain: "", specialization: "" }]);
    if (st.specialization) chips.push([L.specialization[st.specialization], { specialization: "" }]);
    if (st.governorate) chips.push([L.governorate[st.governorate], { governorate: "", city: "" }]);
    if (st.city) chips.push([st.city, { city: "" }]);
    MULTI.forEach(function (k) { st[k].forEach(function (v) { var ch = {}; ch["-" + k] = v; chips.push([(L[k][v] || v).split(" (")[0], ch]); }); });
    if (st.posted_within) chips.push([L.posted_within[st.posted_within], { posted_within: "" }]);
    if (st.company) chips.push([data.companies[st.company] || st.company, { company: "" }]);
    document.getElementById("chips").innerHTML = chips.map(function (c, i) {
      return '<li><a class="chip" href="' + esc(hrefFor(c[1])) + '" data-chip="' + i + '">' + esc(c[0]) + ' <span aria-hidden="true">×</span></a></li>';
    }).join("");
    document.getElementById("chips")._chips = chips;
    document.getElementById("clear-all").hidden = !chips.length;
    document.getElementById("q").value = st.q;
    document.getElementById("sort").value = st.sort;
    document.getElementById("sort").querySelector('[value="relevance"]').disabled = !terms.length;
    document.title = (st.q ? st.q + " — " : "") + "Offres d'emploi" + (st.governorate ? " à " + L.governorate[st.governorate] : " en Tunisie") + " | " + data.site_name;
  }

  // ----------------------------------------------------------------- events
  var state;
  function update(changes, push) {
    applyChanges(state, changes);
    writeState(state, push);
    render(state);
  }

  function bind() {
    document.getElementById("filters").addEventListener("submit", function (e) {
      e.preventDefault();
      update({ q: document.getElementById("q").value.trim() }, true);
    });
    document.getElementById("sort").addEventListener("change", function (e) { update({ sort: e.target.value }); });
    document.getElementById("facet-body").addEventListener("change", function (e) {
      var el = e.target, name = el.getAttribute("data-name"), ch = {};
      if (!name) return;
      if (MULTI.indexOf(name) >= 0) {
        var vals = state[name].filter(function (v) { return v !== el.value; });
        if (el.checked) vals.push(el.value);
        ch[name] = vals;
      } else {
        ch[name] = el.value;
        if (name === "domain") ch.specialization = "";
        if (name === "governorate") ch.city = "";
      }
      update(ch, true);
    });
    document.getElementById("chips").addEventListener("click", function (e) {
      var a = e.target.closest("[data-chip]");
      if (!a) return;
      e.preventDefault();
      update(this._chips[+a.getAttribute("data-chip")][1], true);
    });
    document.getElementById("pager").addEventListener("click", function (e) {
      var a = e.target.closest("[data-page]");
      if (!a) return;
      e.preventDefault();
      update({ page: +a.getAttribute("data-page") }, true);
      root.scrollIntoView({ behavior: "smooth" });
    });
    window.addEventListener("popstate", function () { state = readState(); render(state); });
    // (The mobile filter drawer toggle is handled by app.js.)
  }

  fetch(BASE + "/data/jobs.json?v=" + root.getAttribute("data-build"))
    .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(function (d) {
      data = d; L = d.labels; jobs = d.jobs;
      jobs.forEach(function (j) {
        j._t = Date.parse(j.p);
        j._title = words(j.t);
        j._hay = words([j.t, j.c, j.l, (j.k || []).join(" "), j.x].join(" "));
      });
      state = readState();
      bind();
      render(state);
    })
    .catch(function () {
      document.getElementById("results").innerHTML = '<div class="empty">Impossible de charger les offres. Réessayez dans un instant.</div>';
    });
})();
