/* Verticlip — shared front end: page chrome, saved progress, and helpers used by every page. */
(() => {
  const PAGES = [
    ["home", "/", "Home"], ["discover", "/discover", "Discover"], ["studio", "/studio", "Studio"],
    ["library", "/library", "Library"], ["accounts", "/accounts", "Accounts"],
  ];
  const KEYS = { active: "verticlip.activeJob", settings: "verticlip.settings", picks: "verticlip.picks." };
  const DEFAULT_SETTINGS = { clip_len: 30, max_clips: 6, caption_style: "bold", auto_caption: true, logo_url: null };

  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];
  const sleep = (ms) => new Promise((res) => setTimeout(res, ms));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // Browser storage can be blocked or full; the app must still work without it.
  const saved = {
    get(key, fallback = null) {
      try { const raw = localStorage.getItem(key); return raw === null ? fallback : JSON.parse(raw); } catch { return fallback; }
    },
    set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* ignore */ } },
    del(key) { try { localStorage.removeItem(key); } catch { /* ignore */ } },
  };

  const short = (s, n = 160) => { s = String(s ?? ""); return s.length > n ? s.slice(0, n - 1).trimEnd() + "…" : s; };
  const fmtViews = (n) => { n = Number(n) || 0; return n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? Math.round(n / 1e3) + "K" : String(n); };
  const fmtClock = (s) => { s = Math.max(0, Math.round(s)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
  function timeAgo(epoch) {
    const d = Date.now() / 1000 - epoch;
    if (!(d >= 0)) return "just now";
    if (d < 60) return "just now";
    if (d < 3600) return `${Math.floor(d / 60)} min ago`;
    if (d < 86400) return `${Math.floor(d / 3600)} h ago`;
    return `${Math.floor(d / 86400)} d ago`;
  }
  async function errorText(res) {
    try { const d = await res.json(); return typeof d.detail === "string" ? d.detail : "Request failed."; } catch { return "Request failed."; }
  }
  async function api(path, options) {
    const res = await fetch(path, options);
    if (!res.ok) throw new Error(await errorText(res));
    return res.json();
  }
  const postJson = (path, body, method = "POST") =>
    api(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg; t.classList.add("show");
    clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), 3400);
  }

  // ---------- saved progress ----------
  const getActive = () => saved.get(KEYS.active);
  const setActive = (job) => saved.set(KEYS.active, { id: job.id, title: job.title || "", url: job.url || "" });
  const clearActive = () => saved.del(KEYS.active);
  const getSettings = () => ({ ...DEFAULT_SETTINGS, ...(saved.get(KEYS.settings, {}) || {}) });
  const setSettings = (s) => saved.set(KEYS.settings, s);
  const getPicks = (jobId) => saved.get(KEYS.picks + jobId);
  const setPicks = (jobId, ids) => saved.set(KEYS.picks + jobId, ids);

  /** Poll one job until it finishes. Calls onUpdate with each snapshot. Returns a stop function. */
  function watchJob(jobId, onUpdate, onGone) {
    let stopped = false;
    (async () => {
      while (!stopped) {
        let res;
        try { res = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`); } catch { await sleep(3000); continue; }
        if (stopped) return;
        if (res.status === 404) { onGone && onGone(); return; }
        if (res.ok) {
          const job = await res.json();
          if (stopped) return;
          onUpdate(job);
          if (job.status === "done" || job.status === "error") return;
        }
        await sleep(1500);
      }
    })();
    return () => { stopped = true; };
  }

  // ---------- page chrome ----------
  const MARK = `<svg viewBox="0 0 32 32" aria-hidden="true"><defs><linearGradient id="mk" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#8ff3df"/><stop offset="1" stop-color="#ff7a59"/></linearGradient></defs><rect x="9" y="2" width="14" height="28" rx="5" fill="none" stroke="url(#mk)" stroke-width="2.6"/><path d="M14 11l6 5-6 5z" fill="url(#mk)"/></svg>`;

  function mountChrome() {
    const page = document.body.dataset.page;
    document.body.insertAdjacentHTML("afterbegin", `
      <div class="backdrop" aria-hidden="true"><i></i><i></i><i></i></div>
      <div class="nav-shell"><div class="wrap"><nav class="nav glass" aria-label="Main">
        <a class="brand" href="/" aria-label="Verticlip home">${MARK}<span>Verticlip</span></a>
        <div class="nav-links">${PAGES.map(([id, href, label]) =>
          `<a href="${href}"${id === page ? ' aria-current="page"' : ""}>${label}</a>`).join("")}</div>
      </nav></div></div>`);
    document.body.insertAdjacentHTML("beforeend", `
      <footer><div class="wrap">
        <span>Only clip videos you own or have permission to use.</span>
        <span><a href="https://emphaticfir25.github.io/verticlip/terms.html">Terms</a> &nbsp; <a href="https://emphaticfir25.github.io/verticlip/privacy.html">Privacy</a></span>
      </div></footer>
      <div class="toast glass" id="toast" role="status" aria-live="polite"></div>
      <aside class="dock glass" id="dock" aria-live="polite"></aside>`);
    if (page !== "studio") mountDock();
  }

  /** A small glass card on every page except the studio that follows the job you started. */
  function mountDock() {
    const dock = $("#dock");
    let stop = null;
    const hide = () => { dock.classList.remove("show"); stop && stop(); stop = null; };
    const draw = (job) => {
      const active = getActive();
      const name = esc(job.title || active?.title || job.url || "Your video");
      const finished = job.status === "done", failed = job.status === "error";
      dock.innerHTML = `
        <div class="row"><span class="name">${name}</span><button class="x" aria-label="Dismiss" data-act="dismiss">×</button></div>
        <div class="msg" title="${esc(failed ? job.message : "")}">${esc(finished ? `${job.clips.length} clips are ready.` : failed ? VC.short(job.message, 140) : job.message || "Working…")}</div>
        ${finished || failed ? "" : `<div class="meter"><div style="width:${Number(job.progress) || 0}%"></div></div>`}
        <div class="actions"><a class="btn small" href="/studio?job=${encodeURIComponent(job.id)}">${finished ? "See clips" : "Open studio"}</a></div>`;
      dock.classList.add("show");
    };
    dock.addEventListener("click", (e) => {
      if (e.target.closest("[data-act=dismiss]")) { clearActive(); hide(); }
    });
    const active = getActive();
    if (!active) return;
    stop = watchJob(active.id, draw, () => { clearActive(); hide(); });
  }

  window.VC = {
    $, $$, sleep, esc, short, saved, fmtViews, fmtClock, timeAgo, errorText, api, postJson, toast,
    getActive, setActive, clearActive, getSettings, setSettings, getPicks, setPicks, watchJob,
  };
  mountChrome();
})();
