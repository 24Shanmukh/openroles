(() => {
  "use strict";
  const PAGE = 40;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const safeUrl = (u) => (/^https:\/\//i.test(u || "") ? u : "#");
  const PALETTE = ["#1f5eff", "#0f8a6a", "#c2410c", "#7c3aed", "#0e7490", "#b45309", "#be123c", "#475569", "#4d7c0f", "#a21caf"];
  const colorFor = (s) => { let h = 0; for (const ch of s) h = (h * 31 + ch.charCodeAt(0)) >>> 0; return PALETTE[h % PALETTE.length]; };
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const ageDays = (d) => (d ? Math.max(0, Math.round((today - new Date(d + "T00:00:00")) / 864e5)) : 9999);
  const ago = (d) => { const n = ageDays(d); return n === 9999 ? "Date not given" : n === 0 ? "Today" : n === 1 ? "1 day ago" : n + " days ago"; };

  let JOBS = [], COS = [], byId = new Map(), shown = PAGE, current = null, lastResults = [];
  let saved = new Set();
  try { saved = new Set(JSON.parse(localStorage.getItem("or_saved") || "[]")); } catch (e) {}
  const persist = () => { try { localStorage.setItem("or_saved", JSON.stringify([...saved])); } catch (e) {} $("savedN").textContent = saved.size; };

  // site name from config.js
  const NAME = (window.SITE && window.SITE.name) || "OpenRoles";
  document.querySelectorAll("[data-sitename]").forEach((el) => {
    el.innerHTML = NAME === "OpenRoles" ? "Open<span>Roles</span>" : esc(NAME);
  });

  // ------------------------------------------------------------ data
  Promise.all([fetch("data/jobs.json").then((r) => r.json()), fetch("data/companies.json").then((r) => r.json())])
    .then(([j, c]) => {
      JOBS = j.jobs || []; COS = c.companies || [];
      JOBS.forEach((x) => { x._hay = (x.title + " " + x.company + " " + x.department + " " + (x.skills || []).join(" ") + " " + x.industry).toLowerCase(); x._loc = (x.location + " " + x.country + " " + x.mode).toLowerCase(); byId.set(x.id, x); });
      $("sJobs").textContent = JOBS.length.toLocaleString("en-IN");
      $("sCos").textContent = COS.filter((x) => x.open_roles > 0).length.toLocaleString("en-IN");
      const g = new Date(j.generated);
      $("sTime").textContent = isNaN(g) ? "–" : g.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
      fillSelect("fInd", uniq(COS.map((x) => x.industry)));
      fillSelect("coInd", uniq(COS.map((x) => x.industry)));
      fillSelect("fCountry", uniq(JOBS.map((x) => x.country)));
      fillSelect("fCo", COS.filter((x) => x.open_roles).map((x) => x.name).sort((a, b) => a.localeCompare(b)));
      persist(); route();
    })
    .catch(() => { $("count").textContent = "Couldn't load jobs. Please refresh the page."; });

  const uniq = (a) => [...new Set(a.filter(Boolean))].sort((x, y) => x.localeCompare(y));
  function fillSelect(id, values) { const s = $(id); values.forEach((v) => { const o = document.createElement("option"); o.textContent = v; s.appendChild(o); }); }

  // ------------------------------------------------------------ filtering
  function filtered() {
    const q = $("q").value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const loc = $("loc").value.trim().toLowerCase();
    const modes = [...document.querySelectorAll("input[name=mode]:checked")].map((x) => x.value);
    const f = { exp: $("fExp").value, days: +$("fDate").value, emp: $("fEmp").value, lvl: $("fLvl").value, ind: $("fInd").value, country: $("fCountry").value, co: $("fCo").value, sal: $("fSal").checked };
    const r = JOBS.filter((j) =>
      q.every((w) => j._hay.includes(w)) &&
      (!loc || j._loc.includes(loc)) &&
      (!f.exp || j.experience === f.exp) &&
      (!modes.length || modes.includes(j.mode)) &&
      (f.days > 9000 || ageDays(j.posted) <= f.days) &&
      (!f.emp || j.employment === f.emp) &&
      (!f.lvl || j.level === f.lvl) &&
      (!f.ind || j.industry === f.ind) &&
      (!f.country || j.country === f.country) &&
      (!f.co || j.company === f.co) &&
      (!f.sal || j.salary));
    if ($("sort").value === "co") r.sort((a, b) => a.company.localeCompare(b.company) || a.title.localeCompare(b.title));
    else r.sort((a, b) => (b.posted || "").localeCompare(a.posted || ""));
    return r;
  }

  const mark = (name) => `<div class="mark" style="background:${colorFor(name)}" aria-hidden="true">${esc(name[0] || "?")}</div>`;
  function card(j) {
    return `<button class="job" type="button" data-id="${esc(j.id)}" aria-current="${current === j.id}">${mark(j.company)}
      <div><div class="jt">${esc(j.title)}</div><div class="jc">${esc(j.company)} · ${esc(j.location)}</div>
      <div class="chips">${ageDays(j.posted) <= 1 ? '<span class="chip new">New</span>' : ""}${j.experience ? `<span class="chip">${esc(j.experience)}${j.experience === "Fresher" ? "" : " yrs"}</span>` : ""}<span class="chip">${esc(j.mode)}</span><span class="chip">${esc(j.employment)}</span><span class="chip">${ago(j.posted)}</span></div></div></button>`;
  }

  function renderList(resetPaging = true) {
    if (resetPaging) shown = PAGE;
    lastResults = filtered();
    const n = lastResults.length;
    $("count").textContent = `${n.toLocaleString("en-IN")} role${n === 1 ? "" : "s"} found`;
    $("results").innerHTML = n ? lastResults.slice(0, shown).map(card).join("") : '<div class="empty">No roles match these filters. Try removing one.</div>';
    $("more").hidden = n <= shown;
    if (!current || !lastResults.some((j) => j.id === current)) current = lastResults[0]?.id || null;
    markCurrent(); renderDetail();
  }
  function markCurrent() { document.querySelectorAll("#results .job").forEach((b) => b.setAttribute("aria-current", b.dataset.id === current)); }

  function renderDetail() {
    const el = $("detail"), j = byId.get(current);
    if (!j) { el.innerHTML = '<p class="jc">Select a role to see details.</p>'; return; }
    const isS = saved.has(j.id);
    const contact = (window.SITE && window.SITE.contactEmail) || "";
    el.innerHTML = `<button class="btn back" id="back" type="button">← Back to results</button>
      <div style="display:flex;gap:10px;align-items:center">${mark(j.company)}<div><div style="font-weight:600">${esc(j.company)}</div><div class="jc">${esc(j.industry)}${j.department && j.department !== j.industry ? " · " + esc(j.department) : ""}</div></div></div>
      <h2>${esc(j.title)}</h2><div class="jc">${esc(j.location)} · Posted ${ago(j.posted).toLowerCase()}</div>
      <div class="facts">
        <div><div class="label">Salary</div>${esc(j.salary || "Not disclosed")}</div>
        <div><div class="label">Experience</div>${j.experience ? esc(j.experience) + (j.experience === "Fresher" ? "" : " years") + ' <span class="jc">(estimated)</span>' : "Not stated"}</div>
        <div><div class="label">Employment</div>${esc(j.employment)} · ${esc(j.mode)}</div>
        <div><div class="label">Level</div>${esc(j.level)} <span class="jc">(estimated)</span></div>
        <div style="grid-column:1/-1"><div class="label">Skills mentioned</div>${j.skills && j.skills.length ? esc(j.skills.join(", ")) : "See full description"}</div>
      </div>
      <h4>Summary</h4><p>${esc(j.summary || "Open the company page for the full description.")}</p>
      <div class="actions"><a class="btn primary" href="${esc(safeUrl(j.url))}" target="_blank" rel="noopener nofollow">Apply on ${esc(j.company)} website ↗</a>
        <button class="btn" id="save" type="button">${isS ? "Saved ✓" : "Save job"}</button>
        <button class="btn" id="share" type="button">Copy link</button></div>
      <div class="prov"><span>Source: <b>${esc(sourceName(j.source))}</b></span><span>Checked: <b>${esc($("sTime").textContent)}</b> · listed on the company's site at that time</span><span>Original posting © ${esc(j.company)}${contact ? ` · Problem with this listing? Email <b>${esc(contact)}</b>` : ""}</span></div>`;
    $("save").onclick = () => { isS ? saved.delete(j.id) : saved.add(j.id); persist(); renderDetail(); };
    $("share").onclick = () => {
      const link = location.href.split("#")[0] + "#job-" + j.id;
      const done = () => { $("share").textContent = "Link copied"; };
      navigator.clipboard ? navigator.clipboard.writeText(link).then(done, () => prompt("Copy this link:", link)) : prompt("Copy this link:", link);
    };
    $("back").onclick = () => { document.body.classList.remove("show-detail"); history.replaceState(null, "", location.pathname); };
  }
  const sourceName = (s) => ({ greenhouse: "Greenhouse public job board", lever: "Lever public job board", ashby: "Ashby public job board", smartrecruiters: "SmartRecruiters public postings", workday: "Company career site (Workday)", oracle: "Company career site (Oracle)", successfactors: "Company career site (SAP SuccessFactors)", sitemap: "Company careers site (job pages)", jsonld: "Company careers page (structured data)", auto: "Company careers page" }[s] || s);

  // ------------------------------------------------------------ companies & saved
  function renderCos() {
    const q = $("coQ").value.trim().toLowerCase(), ind = $("coInd").value;
    const list = COS.filter((c) => (!q || c.name.toLowerCase().includes(q)) && (!ind || c.industry === ind));
    $("coGrid").innerHTML = list.length ? list.map((c) => `<div class="co"><div class="row">${mark(c.name)}<div style="min-width:0"><div style="font-weight:600">${esc(c.name)}</div><div class="jc">${esc(c.industry)}</div></div></div>
      <dl><dt>Based in</dt><dd>${esc(c.country || "–")}</dd><dt>Hiring via</dt><dd>${esc(sourceName(c.source))}</dd><dt>Open roles</dt><dd>${c.open_roles}</dd></dl>
      ${c.status === "error" ? '<div class="err">Couldn\'t reach this company\'s job feed today.</div>' : ""}
      ${c.open_roles ? `<button class="btn" type="button" data-co="${esc(c.name)}">View ${c.open_roles} role${c.open_roles === 1 ? "" : "s"}</button>` : '<div class="jc">No open roles right now.</div>'}</div>`).join("") : '<div class="empty">No companies match.</div>';
  }
  function renderSaved() {
    const s = [...saved].map((id) => byId.get(id)).filter(Boolean);
    const gone = saved.size - s.length;
    $("savedList").innerHTML = (s.length ? s.map(card).join("") : '<div class="empty">No saved jobs yet. Open a role and choose “Save job”.</div>') +
      (gone ? `<p class="jc">${gone} saved job${gone === 1 ? " is" : "s are"} no longer open and ${gone === 1 ? "was" : "were"} removed by the company.</p>` : "");
  }

  // ------------------------------------------------------------ routing
  function show(view) {
    ["jobs", "companies", "saved"].forEach((v) => { $("v-" + v).hidden = v !== view; });
    document.querySelectorAll("nav.tabs a[data-view]").forEach((a) => {
      if (a.dataset.view === view) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    if (view !== "jobs") document.body.classList.remove("show-detail");
    if (view === "companies") renderCos();
    if (view === "saved") renderSaved();
  }
  function route() {
    const h = location.hash.slice(1);
    if (h === "companies" || h === "saved") { show(h); return; }
    show("jobs");
    if (h.startsWith("job-") && byId.has(h.slice(4))) { current = h.slice(4); renderList(); document.body.classList.add("show-detail"); }
    else renderList();
  }
  window.addEventListener("hashchange", route);

  // ------------------------------------------------------------ events
  let t;
  const debounced = () => { clearTimeout(t); t = setTimeout(() => renderList(), 150); };
  $("q").addEventListener("input", debounced);
  $("loc").addEventListener("input", debounced);
  $("searchForm").addEventListener("submit", (e) => { e.preventDefault(); if (location.hash) history.pushState(null, "", location.pathname); show("jobs"); renderList(); });
  document.querySelectorAll("aside select, aside input, #sort").forEach((x) => x.addEventListener("change", () => renderList()));
  $("reset").onclick = () => { ["fExp", "fEmp", "fLvl", "fInd", "fCountry", "fCo"].forEach((id) => ($(id).value = "")); $("fDate").value = "99999"; $("fSal").checked = false; document.querySelectorAll("input[name=mode]").forEach((x) => (x.checked = false)); $("q").value = ""; $("loc").value = ""; renderList(); };
  $("more").onclick = () => { shown += PAGE; renderList(false); };
  const openJob = (e) => { const b = e.target.closest(".job"); if (!b) return; current = b.dataset.id; history.replaceState(null, "", "#job-" + current); if ($("v-jobs").hidden) show("jobs"); renderList(false); document.body.classList.add("show-detail"); if (innerWidth <= 720) window.scrollTo(0, $("v-jobs").offsetTop); };
  $("results").addEventListener("click", openJob);
  $("savedList").addEventListener("click", openJob);
  $("coQ").addEventListener("input", renderCos);
  $("coInd").addEventListener("change", renderCos);
  $("coGrid").addEventListener("click", (e) => { const b = e.target.closest("[data-co]"); if (!b) return; $("fCo").value = b.dataset.co; history.pushState(null, "", location.pathname); show("jobs"); renderList(); });
})();
