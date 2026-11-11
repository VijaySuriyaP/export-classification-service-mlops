/* Export Classification Check - browser client (A8, L6).
 *
 * Plain JavaScript, no build step. Talks to the API through /api/ on the same origin (nginx proxies
 * it to the api service), so no CORS is needed and the API host port does not matter.
 *
 * Flow: POST /runs -> 202 {run_id} -> poll GET /runs/{id} until completed|escalated|failed
 *       -> GET /runs/{id}/events (reconstruction) -> POST /runs/{id}/feedback.
 * Every value from the server is rendered with textContent / text nodes, never parsed as HTML, so a
 * description or quote containing markup cannot inject script.
 */
(() => {
  "use strict";

  const API = "/api";
  const TERMINAL = new Set(["completed", "escalated", "failed"]);
  const RUN_ID_RE = /^[0-9a-f]{32}$/;
  const DEMO = "Stainless steel vacuum flask with a moulded plastic outer body.";
  const POLL_MS = 1000;
  const POLL_MAX_MS = 5 * 60 * 1000;
  const HISTORY_KEY = "ecs.history.v1";
  const CLIENT_KEY = "ecs.clientId.v1";
  const ROUTE_LABEL = {
    "clear-for-filing": "Clear for filing",
    "seek-product-clarification": "Seek product clarification",
    "specialist-classification-review": "Specialist classification review",
  };

  const $ = (id) => document.getElementById(id);
  let currentRun = null;    // run_id being shown
  let pollToken = 0;        // bumps to cancel an older poll loop

  // ------------------------------------------------------------------ helpers
  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) {
      if (c === null || c === undefined || c === false) continue;
      node.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return node;
  }

  function store(key, value) {
    try {
      if (value === undefined) return JSON.parse(localStorage.getItem(key) || "null");
      localStorage.setItem(key, JSON.stringify(value));
    } catch { /* private mode or storage blocked: the page still works without history */ }
    return null;
  }

  // A stable per-browser client id: the API rate-limits per X-Client-ID, and behind the proxy every
  // browser would otherwise share nginx's IP and one rate-limit bucket.
  function clientId() {
    let id = store(CLIENT_KEY);
    if (!id || !/^[A-Za-z0-9._-]{1,64}$/.test(id)) {
      id = "web-" + (crypto.randomUUID ? crypto.randomUUID().replace(/-/g, "").slice(0, 16)
                                       : Math.random().toString(16).slice(2, 18));
      store(CLIENT_KEY, id);
    }
    return id;
  }

  async function api(method, path, body) {
    const headers = { "X-Client-ID": clientId() };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    let resp;
    try {
      resp = await fetch(API + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    } catch (err) {
      return { status: 0, data: { error: "network", detail: String(err) }, headers: new Headers() };
    }
    let data = null;
    const text = await resp.text();
    try { data = text ? JSON.parse(text) : null; } catch { data = { error: "non_json", detail: text.slice(0, 300) }; }
    return { status: resp.status, data, headers: resp.headers };
  }

  function showMsg(box, kind, ...content) {
    box.hidden = false;
    box.className = "msg msg-" + kind;
    box.replaceChildren(...content);
  }

  function describeHttpError(r) {
    if (r.status === 0) return "Cannot reach the API. Is the stack running (docker compose ps)?";
    if (r.status === 429) return `Rate limited: wait ${r.data?.retry_after_s ?? r.headers.get("Retry-After") ?? "?"} s `
      + `(limit ${r.data?.limit_per_minute ?? "?"} submissions/minute per client).`;
    if (r.status === 503) return "Service unavailable (queue backend down). Nothing was created; retry shortly.";
    if (r.status === 502 || r.status === 504) return "The web proxy could not reach the API service.";
    return `Unexpected response ${r.status}: ${JSON.stringify(r.data).slice(0, 300)}`;
  }

  function fmtSeconds(s) {
    if (s === null || s === undefined) return "–";
    return s < 1 ? `${Math.round(s * 1000)} ms` : `${Number(s).toFixed(1)} s`;
  }

  function opsLinks() {
    const host = location.hostname || "localhost";
    $("link-grafana").href = `${location.protocol}//${host}:3000/`;
    $("link-jaeger").href = `${location.protocol}//${host}:16686/`;
  }

  // ------------------------------------------------------------------ API status
  async function refreshStatus() {
    const r = await api("GET", "/ready");
    const pill = $("api-status");
    if (r.status === 200 && r.data.workers_alive === 0) {
      pill.className = "pill pill-bad";
      pill.textContent = `No workers running · ${r.data.queue_depth} queued`;
      pill.title = "Runs are accepted but will wait in the queue until an agent-worker starts (see docs/runbook.md, RB-3).";
    } else if (r.status === 200) {
      pill.className = "pill pill-ok";
      pill.textContent = `API ready · ${r.data.workers_alive ?? "?"} worker(s) · queue ${r.data.queue_depth}`;
      pill.title = "GET /ready";
    } else {
      pill.className = "pill pill-bad";
      pill.textContent = r.status === 0 ? "API unreachable" : `API not ready (${r.status})`;
    }
  }

  // ------------------------------------------------------------------ submit form
  function clearErrors(scope, attr) {
    scope.querySelectorAll(`[${attr}]`).forEach((n) => { n.textContent = ""; });
    scope.querySelectorAll(".invalid").forEach((n) => n.classList.remove("invalid"));
  }

  function showFieldErrors(scope, attr, errors) {
    const unplaced = [];
    for (const e of errors) {
      // "materials.2" -> materials; "hs_code_hint+chapter_hint" -> both fields
      const names = e.field.split("+").map((f) => f.split(".")[0]);
      let placed = false;
      for (const name of names) {
        const slot = scope.querySelector(`[${attr}="${CSS.escape(name)}"]`);
        if (!slot) continue;
        slot.append(el("div", { text: `${e.message} (${e.type})` }));
        const input = scope.querySelector(`[name="${CSS.escape(name)}"]`);
        if (input) input.classList.add("invalid");
        placed = true;
      }
      if (!placed) unplaced.push(e);
    }
    const other = scope.querySelector(`[${attr}="_other"]`);
    if (other) unplaced.forEach((e) => other.append(el("div", { text: `${e.field}: ${e.message} (${e.type})` })));
    return unplaced;
  }

  function readForm() {
    const v = (id) => $(id).value.trim();
    const body = { product_description: v("product_description") };
    const materials = v("materials").split(",").map((s) => s.trim()).filter(Boolean);
    if (materials.length) body.materials = materials;
    for (const id of ["chapter_hint", "hs_code_hint", "quantity_unit", "client_reference"]) if (v(id)) body[id] = v(id);
    if (v("destination_country")) body.destination_country = v("destination_country").toUpperCase();
    if (v("quantity")) body.quantity = Number(v("quantity"));
    return body;
  }

  async function onSubmit(ev) {
    ev.preventDefault();
    const form = $("run-form");
    clearErrors(form, "data-for");
    const msg = $("submit-msg");
    msg.hidden = true;
    const btn = $("submit-btn");
    btn.disabled = true;
    btn.textContent = "Submitting…";
    try {
      const body = readForm();
      const r = await api("POST", "/runs", body);
      if (r.status === 202) {
        addHistory(r.data.run_id, body.product_description);
        showMsg(msg, "ok", `Accepted. Run ${r.data.run_id.slice(0, 8)}… queued.`);
        openRun(r.data.run_id);
      } else if (r.status === 422 && r.data?.error === "validation_failed") {
        const injection = r.data.errors.some((e) => e.type === "prompt_injection_suspected");
        showFieldErrors(form, "data-for", r.data.errors);
        if (r.data.errors.some((e) => !["product_description"].includes(e.field))) $("advanced").open = true;
        showMsg(msg, "bad",
          el("strong", { text: injection ? "Rejected by the prompt-injection guardrail." : "Rejected: fix the highlighted fields." }),
          el("br"),
          `No run was created (run_created: ${r.data.run_created}). The request was quarantined as `,
          el("code", { text: r.data.quarantine_id }), ".");
      } else {
        showMsg(msg, "bad", describeHttpError(r));
      }
    } finally {
      btn.disabled = false;
      btn.textContent = "Submit for classification";
      refreshStatus();
    }
  }

  // ------------------------------------------------------------------ history
  function addHistory(runId, description) {
    const items = (store(HISTORY_KEY) || []).filter((h) => h.run_id !== runId);
    items.unshift({ run_id: runId, description: description.slice(0, 80), at: new Date().toISOString(), status: "queued" });
    store(HISTORY_KEY, items.slice(0, 15));
    renderHistory();
  }

  function updateHistoryStatus(runId, status) {
    const items = store(HISTORY_KEY) || [];
    const item = items.find((h) => h.run_id === runId);
    if (item && item.status !== status) { item.status = status; store(HISTORY_KEY, items); renderHistory(); }
  }

  function renderHistory() {
    const items = store(HISTORY_KEY) || [];
    const ul = $("history");
    if (!items.length) { ul.replaceChildren(el("li", { class: "muted", text: "None yet." })); return; }
    ul.replaceChildren(...items.map((h) => {
      const btn = el("button", { type: "button", class: "link-btn", title: h.run_id }, h.description || h.run_id);
      btn.addEventListener("click", () => openRun(h.run_id));
      return el("li", {}, el("span", { class: `pill pill-${h.status}`, text: h.status }), " ", btn,
        el("small", { class: "muted", text: " " + new Date(h.at).toLocaleString() }));
    }));
  }

  // ------------------------------------------------------------------ run view
  function openRun(runId) {
    if (!RUN_ID_RE.test(runId)) return;
    currentRun = runId;
    const url = new URL(location.href);
    url.searchParams.set("run", runId);
    history.replaceState(null, "", url);
    $("run-empty").hidden = true;
    $("run-view").hidden = false;
    $("result").replaceChildren();
    $("events").replaceChildren();
    $("ev-count").textContent = "0";
    $("m-run").textContent = runId;
    resetFeedback(false);
    poll(runId, ++pollToken);
  }

  async function poll(runId, token) {
    const started = Date.now();
    let delay = POLL_MS;
    while (token === pollToken) {
      const r = await api("GET", `/runs/${runId}`);
      if (token !== pollToken) return;
      if (r.status === 200) {
        renderRun(r.data);
        updateHistoryStatus(runId, r.data.status);
        if (TERMINAL.has(r.data.status)) {
          loadEvents(runId);
          resetFeedback(true);
          return;
        }
        delay = POLL_MS;
      } else if (r.status === 404) {
        setStatus("not found", "failed");
        $("result").replaceChildren(el("p", { class: "msg msg-bad", text: "Unknown or expired run (runs are kept 7 days)." }));
        return;
      } else {
        // transient (503, network): back off, keep polling
        $("m-elapsed").textContent = describeHttpError(r);
        delay = Math.min(delay * 2, 8000);
      }
      if (Date.now() - started > POLL_MAX_MS) {
        $("m-elapsed").textContent = "Stopped polling after 5 minutes; reopen the run to continue.";
        return;
      }
      await new Promise((res) => setTimeout(res, delay));
    }
  }

  function setStatus(label, status) {
    const pill = $("m-status");
    pill.className = `pill pill-${status}`;
    pill.textContent = label;
    const reached = { queued: 1, running: 2, completed: 3, escalated: 3, failed: 3 }[status] || 0;
    $("timeline").querySelectorAll("li").forEach((li, i) => {
      li.classList.toggle("done", i < reached);
      li.classList.toggle("current", i === reached - 1 && !TERMINAL.has(status));
    });
  }

  function renderRun(run) {
    setStatus(run.status, run.status);
    const created = new Date(run.created_at);
    const end = run.finished_at ? new Date(run.finished_at) : new Date();
    $("m-elapsed").textContent = `${fmtSeconds((end - created) / 1000)} since submission`
      + (run.attempts > 1 ? ` · attempt ${run.attempts}` : "") + (run.cache_hit ? " · served from cache" : "");
    $("m-cid").textContent = run.correlation_id;
    const trace = $("m-trace");
    if (run.trace_id) {
      const host = location.hostname || "localhost";
      trace.replaceChildren(el("a", { href: `${location.protocol}//${host}:16686/trace/${run.trace_id}`,
        target: "_blank", rel: "noopener" }, el("code", { text: run.trace_id })), " (Jaeger)");
    } else {
      trace.textContent = "–";
    }
    $("m-ref").textContent = run.client_reference || "–";
    if (TERMINAL.has(run.status)) renderResult(run);
  }

  function renderResult(run) {
    const box = $("result");
    const parts = [];
    if (run.status === "failed") {
      parts.push(el("div", { class: "msg msg-bad" },
        el("strong", { text: "The run failed. No result is returned." }), el("br"),
        run.error ? `${run.error.code}: ${run.error.message}` : ""));
    }
    const res = run.result;
    if (res) {
      parts.push(el("div", { class: `route route-${res.route}` },
        el("div", { class: "route-label", text: "Route" }),
        el("div", { class: "route-value", text: ROUTE_LABEL[res.route] || res.route }),
        el("div", { class: "route-basis", text: res.basis })));

      const code = res.hs_code
        ? el("div", { class: "hs" }, el("span", { class: "hs-code", text: res.hs_code }), " ", res.heading_title || "")
        : el("div", { class: "hs hs-none", text: "No HS code proposed" + (res.escalation_reason ? "." : " (not verified).") });
      parts.push(code);

      if (res.escalation_reason) parts.push(kv("Escalation reason", res.escalation_reason));
      if (res.gri_applied?.length) parts.push(kv("Rules applied (in order)", res.gri_applied.join(" → ")));
      if (res.reasoning_summary) parts.push(kv("Reasoning", res.reasoning_summary));

      parts.push(el("h4", { text: `Citations (${res.citations.length})` }));
      parts.push(res.citations.length ? citations(res.citations)
        : el("p", { class: "muted", text: "None. A code is only shown when its citations resolve to retrieved text." }));

      if (res.export_policy) {
        parts.push(el("h4", { text: "Export policy (DGFT)" }));
        parts.push(el("p", {}, el("span", { class: "pill pill-policy", text: res.export_policy.policy }),
          res.export_policy.conditions ? " " + res.export_policy.conditions : ""));
        if (res.export_policy.citations?.length) parts.push(citations(res.export_policy.citations));
      }

      const v = res.verification;
      parts.push(el("h4", { text: "Verification" }));
      parts.push(el("p", {}, el("span", { class: `pill ${v.verified ? "pill-ok" : "pill-bad"}`, text: v.verified ? "verified" : "not verified" }),
        ` · ${v.attempts} attempt(s)`, v.notes ? ` · ${v.notes}` : ""));
      if (v.strategy_log?.length) parts.push(el("ol", { class: "small" }, v.strategy_log.map((s) => el("li", { text: s }))));
      if (res.policy_adjustments?.length) {
        parts.push(el("div", { class: "msg msg-warn" }, el("strong", { text: "Output policy adjusted this result:" }),
          el("ul", {}, res.policy_adjustments.map((a) => el("li", { text: a })))));
      }
      parts.push(el("p", { class: "small muted", text: `Prompt version: ${res.prompt_version}` }));
      parts.push(el("p", { class: "disclaimer", text: res.disclaimer }));
    }
    const u = run.usage;
    parts.push(el("div", { class: "usage" },
      stat("End-to-end", fmtSeconds(u.end_to_end_seconds)), stat("Execution", fmtSeconds(u.execution_seconds)),
      stat("Steps", u.steps), stat("Tool calls", u.tool_calls), stat("Model calls", u.llm_calls),
      stat("Tokens", `${u.prompt_tokens} + ${u.completion_tokens}`), stat("Cost", `$${Number(u.cost_usd).toFixed(4)}`)));
    box.replaceChildren(...parts);
  }

  const kv = (k, v) => el("p", {}, el("strong", { text: k + ": " }), v);
  const stat = (k, v) => el("div", { class: "stat" }, el("div", { class: "stat-v", text: String(v) }), el("div", { class: "stat-k", text: k }));
  const citations = (list) => el("ul", { class: "cites" }, list.map((c) => el("li", {},
    el("div", { class: "cite-head" }, el("strong", { text: c.locator || c.chunk_id }), el("span", { class: "muted", text: ` ${c.source_id} · ${c.chunk_id}` })),
    el("blockquote", { text: c.quote }))));

  async function loadEvents(runId) {
    const r = await api("GET", `/runs/${runId}/events`);
    if (r.status !== 200 || runId !== currentRun) return;
    const rows = r.data.events.map((e, i) => {
      let who = e.agent || e.worker_id || "";
      let detail = "";
      switch (e.kind) {
        case "step_start": case "step_end": who = `${e.agent}.${e.step}`; detail = e.outcome || ""; break;
        case "tool_call":
          who = `${e.agent} → ${e.tool}`;
          detail = `${e.outcome} · query: ${e.query ?? "–"}` + (e.passages ? ` · ${e.passages.length} passage(s)` : "");
          break;
        case "llm_call":
          who = `${e.agent}.${e.step}`;
          detail = `${e.outcome} · ${e.prompt_version}` + (e.provider ? ` · ${e.provider}/${e.model} · ${e.prompt_tokens}+${e.completion_tokens} tok · attempts ${e.attempts}` : "")
            + (e.fallback_used ? " · FALLBACK" : "") + (e.error ? ` · ${e.error}` : "");
          break;
        case "run_started": detail = `attempt ${e.attempt} · ${e.prompt_version}`; break;
        case "run_finished": detail = `${e.status} · ${e.route}`; break;
        case "run_error": detail = `${e.error}: ${e.detail}`; break;
        default: detail = JSON.stringify(Object.fromEntries(Object.entries(e).filter(([k]) => !["ts", "kind", "seq"].includes(k)))).slice(0, 200);
      }
      return el("tr", { class: `ev-${e.kind}` }, el("td", { text: i + 1 }), el("td", { text: e.kind }), el("td", { text: who }),
        el("td", { text: detail }), el("td", { text: e.latency_ms !== undefined ? `${e.latency_ms} ms` : "" }));
    });
    $("events").replaceChildren(...rows);
    $("ev-count").textContent = rows.length;
  }

  // ------------------------------------------------------------------ feedback
  function resetFeedback(enabled) {
    const form = $("fb-form");
    form.reset();
    clearErrors(form, "data-fb-for");
    form.hidden = !enabled;
    $("fb-disabled").hidden = enabled;
    $("fb-msg").hidden = true;
  }

  async function onFeedback(ev) {
    ev.preventDefault();
    const form = $("fb-form");
    clearErrors(form, "data-fb-for");
    const fd = new FormData(form);
    const body = { rating: fd.get("rating"), reasons: fd.getAll("reasons") };
    for (const k of ["suggested_hs_code", "reviewer", "comment"]) {
      const v = String(fd.get(k) || "").trim();
      if (v) body[k] = v;
    }
    if (!body.rating) {
      showFieldErrors(form, "data-fb-for", [{ field: "rating", message: "choose 👍 or 👎", type: "missing" }]);
      return;
    }
    const r = await api("POST", `/runs/${currentRun}/feedback`, body);
    const msg = $("fb-msg");
    if (r.status === 201) {
      showMsg(msg, "ok", r.data.replaced_previous ? "Thanks. Your earlier feedback on this run was replaced." : "Thanks, feedback recorded.");
      loadSummary();
    } else if (r.status === 422) {
      showFieldErrors(form, "data-fb-for", r.data.errors || []);
      showMsg(msg, "bad", "Feedback not saved: fix the highlighted fields.");
    } else if (r.status === 409) {
      showMsg(msg, "bad", "The run has not finished yet.");
    } else {
      showMsg(msg, "bad", describeHttpError(r));
    }
  }

  async function loadSummary() {
    const box = $("fb-summary");
    const r = await api("GET", "/feedback/summary?recent=5");
    if (r.status !== 200) { box.textContent = "Summary unavailable: " + describeHttpError(r); return; }
    const s = r.data;
    if (!s.total.total) { box.replaceChildren(el("p", { class: "muted", text: "No feedback yet." })); return; }
    const pct = (b) => (b.satisfaction === null ? "–" : `${Math.round(b.satisfaction * 100)}%`);
    const table = (title, groups) => el("div", {}, el("h4", { text: title }),
      el("table", { class: "summary" },
        el("thead", {}, el("tr", {}, ["", "👍", "👎", "Satisfaction"].map((h) => el("th", { text: h })))),
        el("tbody", {}, Object.entries(groups).map(([k, b]) =>
          el("tr", {}, el("td", { text: k }), el("td", { text: b.up }), el("td", { text: b.down }), el("td", { text: pct(b) }))))));
    const reasons = Object.entries(s.reasons).sort((a, b) => b[1] - a[1]);
    box.replaceChildren(
      el("p", {}, el("strong", { text: `${s.total.total} rating(s), ${pct(s.total)} positive.` })),
      table("By prompt version", s.by_prompt_version),
      table("By day (UTC)", s.by_day),
      reasons.length ? el("p", { class: "small" }, "Most reported problems: ", reasons.map(([k, n]) => `${k} (${n})`).join(", ")) : null,
    );
  }

  // ------------------------------------------------------------------ wiring
  function init() {
    opsLinks();
    const desc = $("product_description");
    const count = () => { $("desc-count").textContent = desc.value.trim().length; };
    desc.addEventListener("input", count);
    $("demo-btn").addEventListener("click", () => { desc.value = DEMO; count(); desc.focus(); });
    $("reset-btn").addEventListener("click", () => { $("run-form").reset(); clearErrors($("run-form"), "data-for"); $("submit-msg").hidden = true; count(); });
    $("run-form").addEventListener("submit", onSubmit);
    $("fb-form").addEventListener("submit", onFeedback);
    $("fb-refresh").addEventListener("click", loadSummary);
    $("open-form").addEventListener("submit", (e) => {
      e.preventDefault();
      const id = $("open-run-id").value.trim().toLowerCase();
      if (RUN_ID_RE.test(id)) openRun(id);
    });
    renderHistory();
    refreshStatus();
    setInterval(refreshStatus, 15000);
    loadSummary();
    const fromUrl = new URLSearchParams(location.search).get("run");
    if (fromUrl && RUN_ID_RE.test(fromUrl)) openRun(fromUrl);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
