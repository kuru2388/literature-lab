/* Reading studio page: library + PDF + Keshav guide + per-paper tutor. */
(function () {
  const bannerEl = document.getElementById("banner");
  let openaiConfigured = false;
  let geminiConfigured = false;
  let preferredProvider = "openai";
  let marksTimer = null;
  let lastLibrary = { agent: [], you: [] };
  let activePass = 0;
  let tutorBusy = false;
  let readerState = {
    kind: "agent",
    uploadId: "",
    taskId: "",
    rank: "",
    pdfUrl: "",
    jumps: [],
    page: 1,
    pageCount: 1,
    pdfDoc: null,
    scale: 1,
    renderObserver: null,
    renderedPages: new Set(),
    renderingPages: new Set(),
    marks: [],
    tutor: [],
    meta: { research_name: "", title: "", year: "", authors: "" },
  };

  if (window.pdfjsLib) {
    pdfjsLib.GlobalWorkerOptions.workerSrc = "/static/vendor/pdf.worker.min.js";
  }

  function escapeHtml(value) {
    return String(value || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function renderMarkdown(raw) {
    if (typeof marked !== "undefined" && marked && typeof marked.parse === "function") {
      try {
        return marked.parse(raw || "");
      } catch (e) {
        console.warn("marked.parse error, using fallback:", e);
      }
    }
    return fallbackMarkdown(raw);
  }

  function fallbackMarkdown(raw) {
    const text = String(raw || "");
    if (!text.trim()) return "";
    const lines = text.split("\n");
    let inList = false;
    let inCode = false;
    let codeBuffer = [];
    const out = [];

    for (let line of lines) {
      if (line.trim().startsWith("```")) {
        if (inCode) {
          out.push(`<pre><code>${escapeHtml(codeBuffer.join("\n"))}</code></pre>`);
          codeBuffer = [];
          inCode = false;
        } else {
          if (inList) { out.push("</ul>"); inList = false; }
          inCode = true;
        }
        continue;
      }
      if (inCode) {
        codeBuffer.push(line);
        continue;
      }
      const trimmed = line.trim();
      if (!trimmed) {
        if (inList) { out.push("</ul>"); inList = false; }
        continue;
      }
      if (trimmed.startsWith("### ")) {
        if (inList) { out.push("</ul>"); inList = false; }
        out.push(`<h3>${escapeHtml(trimmed.slice(4))}</h3>`);
      } else if (trimmed.startsWith("## ")) {
        if (inList) { out.push("</ul>"); inList = false; }
        out.push(`<h2>${escapeHtml(trimmed.slice(3))}</h2>`);
      } else if (trimmed.startsWith("# ")) {
        if (inList) { out.push("</ul>"); inList = false; }
        out.push(`<h1>${escapeHtml(trimmed.slice(2))}</h1>`);
      } else if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
        if (!inList) { out.push("<ul>"); inList = true; }
        let item = escapeHtml(trimmed.slice(2));
        item = item.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
        item = item.replace(/\*(.+?)\*/g, "<em>$1</em>");
        item = item.replace(/`(.+?)`/g, "<code>$1</code>");
        out.push(`<li>${item}</li>`);
      } else {
        if (inList) { out.push("</ul>"); inList = false; }
        let p = escapeHtml(trimmed);
        p = p.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
        p = p.replace(/\*(.+?)\*/g, "<em>$1</em>");
        p = p.replace(/`(.+?)`/g, "<code>$1</code>");
        out.push(`<p>${p}</p>`);
      }
    }
    if (inCode && codeBuffer.length) {
      out.push(`<pre><code>${escapeHtml(codeBuffer.join("\n"))}</code></pre>`);
    }
    if (inList) { out.push("</ul>"); }
    return out.join("\n");
  }

  function showBanner(text) {
    bannerEl.textContent = text || "";
    bannerEl.hidden = !text;
  }

  function jumpFor(jumps, id) {
    return (jumps || []).find((item) => item.id === id) || null;
  }

  function fieldJumpId(field) {
    if (field === "problem" || field === "why_read" || field === "key_findings") return "intro";
    if (field === "datasets" || field === "features" || field === "method") return "method";
    if (field === "metrics") return "results";
    if (field === "limitations") return "limitations";
    if (field === "related") return "related";
    return "intro";
  }

  function primaryFieldForJump(jumpId) {
    if (jumpId === "method") return "method";
    if (jumpId === "results") return "metrics";
    if (jumpId === "intro") return "problem";
    if (jumpId === "related") return "related";
    if (jumpId === "limitations") return "limitations";
    return "";
  }

  const FIELD_HELP = {
    problem: {
      related: ["key_findings", "why_read"],
      tip: "You opened the problem. Intros usually say: world as it is → what is broken → this paper’s claim. Check that story on this page.",
    },
    key_findings: {
      related: ["problem", "metrics"],
      tip: "A finding is a result, not a slogan. You picked Findings. Confirm the number or comparison in Results, then see if the Problem actually needed it.",
    },
    why_read: {
      related: ["problem", "limitations"],
      tip: "Why-read is a hint, not a citation. Ask: does this paper fill a gap you still have? Check the problem and the limits.",
    },
    method: {
      related: ["datasets", "features"],
      tip: "Method, dataset, and features usually share one section. You picked Method. On this page ask: what did they actually do? Then match Dataset and Features to the same pages.",
    },
    datasets: {
      related: ["method", "features"],
      tip: "Dataset names often hide inside Method or Experiments. You picked Dataset. Find the name in the PDF, then see which Features come from it.",
    },
    features: {
      related: ["method", "datasets"],
      tip: "Features are what the method uses from the data. You picked Features. Check they are measured, not just listed, on this page.",
    },
    metrics: {
      related: ["key_findings"],
      tip: "Metrics tell you how they scored the claim. You picked Metrics. In Results, find the table that uses these numbers.",
    },
    limitations: {
      related: ["problem"],
      tip: "Limits often sit in Discussion or Conclusion. You picked Limitations. Ask: what did they not solve that your next paper still must?",
    },
    related: {
      related: ["problem"],
      tip: "Related work is where the gap is argued. Look for however / unlike / we address, then check that claim against the Problem.",
    },
    title: { tip: "Analyze the domain, core approach, and problem scope indicated in the title." },
    abstract: { tip: "Scan headline problem, proposed method, and key empirical takeaway." },
    introduction: { tip: "Check how authors frame the gap and why prior approaches fall short." },
    contribution: { tip: "Identify authors' explicitly claimed novel contributions." },
    results: { tip: "Check empirical numbers, benchmark improvements, and evidence strength." },
    conclusion: { tip: "Review closing summary and see if claimed contributions hold up." },
    dataset: { tip: "Evaluate benchmark sizes, diversity, collection methodology, and train/test splits." },
    preprocessing: { tip: "Check data cleaning, tokenization, filtering, or augmentation pipelines." },
    methodology: { tip: "Understand theoretical mechanics and algorithmic formulation." },
    architecture: { tip: "Trace system blocks, components, and tensor/data pipelines." },
    "experimental setup": { tip: "Verify hyperparameters, hardware resources, training regime, and fairness." },
    baselines: { tip: "Verify whether comparisons are made against state-of-the-art competitors." },
    "results & ablations": { tip: "Isolate which design decision or module drove real gains." },
    "record to matrix": { tip: "Synthesize paper into your Literature Survey Matrix row." },
    "backward snowballing": { tip: "Trace seminal prior papers cited in the references." },
    "forward snowballing": { tip: "Use Google Scholar citations to see how recent papers built on this." },
    "defensible gap": { tip: "Synthesize cross-paper limitations into a defensible research gap." },
  };

  function highlightActive(jumpId, field) {
    const activeField = field || primaryFieldForJump(jumpId);
    const help = FIELD_HELP[activeField] || {};
    const related = new Set(help.related || []);
    document.querySelectorAll("#readerJumps [data-jump-id]").forEach((el) => {
      el.classList.toggle("on", Boolean(jumpId) && el.dataset.jumpId === jumpId);
    });
    document.querySelectorAll("#readerExtract [data-field]").forEach((el) => {
      const name = el.dataset.field;
      const isOn = Boolean(activeField) && name === activeField;
      const isRelated = related.has(name);
      el.classList.toggle("on", isOn);
      el.classList.toggle("related", isRelated && !isOn);
      let badge = el.querySelector(".same-sec");
      if (isRelated && !isOn) {
        if (!badge) {
          badge = document.createElement("em");
          badge.className = "same-sec";
          badge.textContent = "same section";
          el.appendChild(badge);
        }
      } else if (badge) {
        badge.remove();
      }
    });
    const tip = document.getElementById("sectionTip");
    if (tip) {
      tip.hidden = !help.tip;
      tip.textContent = help.tip || "";
    }
  }

  function jumpPage(id) {
    const jump = jumpFor(readerState.jumps, id);
    return jump ? jump.page : readerState.page || 1;
  }

  function marksUrl() {
    if (readerState.kind === "you" && readerState.uploadId) {
      return `/read/you/${encodeURIComponent(readerState.uploadId)}/marks`;
    }
    return `/task/${encodeURIComponent(readerState.taskId)}/paper/${encodeURIComponent(readerState.rank)}/marks`;
  }

  function tutorUrl() {
    if (readerState.kind === "you" && readerState.uploadId) {
      return `/read/you/${encodeURIComponent(readerState.uploadId)}/tutor`;
    }
    return `/read/agent/${encodeURIComponent(readerState.taskId)}/${encodeURIComponent(readerState.rank)}/tutor`;
  }

  function setHubTab(name) {
    const you = name === "you";
    document.getElementById("navAgent").classList.toggle("active", !you);
    document.getElementById("navYou").classList.toggle("active", you);
    document.getElementById("tabHubAgent").classList.toggle("on", !you);
    document.getElementById("tabHubYou").classList.toggle("on", you);
    document.getElementById("hubAgentList").hidden = you;
    document.getElementById("hubYouList").hidden = !you;
    document.getElementById("readHeading").textContent = you ? "Your papers" : "AI Agent papers";
    document.getElementById("readLead").textContent = you
      ? "Drop a PDF you already have, then click Read. The gold ring shows marks, notes, and tutor chat."
      : "Every paper the agent gathered. The gold ring fills as you read, add marks, and ask the tutor.";
  }

  function setReaderTab(name) {
    const which = name === "extract" ? "extract" : name === "marks" ? "marks" : "guide";
    document.getElementById("tabGuide").classList.toggle("on", which === "guide");
    document.getElementById("tabMarks").classList.toggle("on", which === "marks");
    document.getElementById("tabExtract").classList.toggle("on", which === "extract");
    document.getElementById("guidePanel").hidden = which !== "guide";
    document.getElementById("marksPanel").hidden = which !== "marks";
    document.getElementById("readerExtract").hidden = which !== "extract";
  }

  function setMobileReaderView(mode) {
    const split = document.getElementById("readerSplit") || document.querySelector(".reader-split");
    const toggleGuide = document.getElementById("viewToggleGuide");
    const togglePdf = document.getElementById("viewTogglePdf");
    const toggleTutor = document.getElementById("viewToggleTutor");
    if (!split) return;

    split.classList.remove("view-guide", "view-pdf", "view-tutor", "view-notes");
    if (toggleGuide) toggleGuide.classList.remove("active");
    if (togglePdf) togglePdf.classList.remove("active");
    if (toggleTutor) toggleTutor.classList.remove("active");

    if (mode === "guide" || mode === "notes") {
      split.classList.add("view-guide");
      if (toggleGuide) toggleGuide.classList.add("active");
    } else if (mode === "tutor") {
      split.classList.add("view-tutor");
      if (toggleTutor) toggleTutor.classList.add("active");
    } else {
      split.classList.add("view-pdf");
      if (togglePdf) togglePdf.classList.add("active");
    }
  }

  function selectedPdfText() {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed) return { text: "", page: readerState.page || 1 };
    const holder = document.getElementById("readerPdf");
    if (!holder.contains(sel.anchorNode)) return { text: "", page: readerState.page || 1 };

    let markPage = readerState.page || 1;
    if (sel.anchorNode) {
      const pageSlot = sel.anchorNode.parentElement ? sel.anchorNode.parentElement.closest(".reader-page") : null;
      if (pageSlot && pageSlot.dataset.page) {
        markPage = Number(pageSlot.dataset.page);
      }
    }

    const text = String(sel.toString() || "").replace(/\s+/g, " ").trim();
    return { text, page: markPage };
  }

  function highlightMarksForPageSlot(slot, pageNum) {
    const layer = slot.querySelector(".textLayer");
    if (!layer) return;
    const quotes = (readerState.marks || [])
      .filter((mark) => Number(mark.page) === Number(pageNum) && (mark.text || "").length > 8)
      .map((mark) => String(mark.text).toLowerCase());
    layer.querySelectorAll("span").forEach((span) => {
      const text = (span.textContent || "").toLowerCase();
      const chunk = text.trim();
      const hit = quotes.some((quote) => chunk.length >= 8 && (quote.includes(chunk) || chunk.includes(quote.slice(0, 40))));
      span.classList.toggle("mark-hit", Boolean(chunk) && hit);
    });
  }

  function highlightMarksOnPage() {
    document.querySelectorAll("#readerPdf .reader-page[data-rendered='true']").forEach((slot) => {
      const pageNum = Number(slot.dataset.page);
      highlightMarksForPageSlot(slot, pageNum);
    });
  }

  function updateCurrentPageOnScroll() {
    const holder = document.getElementById("readerPdf");
    if (!holder) return;
    const holderRect = holder.getBoundingClientRect();
    const pages = holder.querySelectorAll(".reader-page");
    let activePage = readerState.page || 1;

    for (const page of pages) {
      const rect = page.getBoundingClientRect();
      if (rect.top <= holderRect.top + holderRect.height * 0.45 && rect.bottom >= holderRect.top + 60) {
        activePage = Number(page.dataset.page);
      }
    }

    if (activePage !== readerState.page) {
      readerState.page = activePage;
      const input = document.getElementById("pdfPageInput");
      if (input && document.activeElement !== input) {
        input.value = String(activePage);
      }
    }
  }

  async function renderPdfPageSlot(pageNum, slotEl) {
    if (!readerState.pdfDoc) return;
    const pNum = Number(pageNum);
    if (!pNum || pNum < 1 || pNum > readerState.pageCount) return;
    if (readerState.renderedPages.has(pNum) || readerState.renderingPages.has(pNum)) return;

    readerState.renderingPages.add(pNum);
    const slot = slotEl || document.getElementById(`pdf-page-${pNum}`);
    if (!slot) {
      readerState.renderingPages.delete(pNum);
      return;
    }

    try {
      const pdfPage = await readerState.pdfDoc.getPage(pNum);
      const scale = readerState.scale || 1;
      const viewport = pdfPage.getViewport({ scale });

      slot.style.width = `${viewport.width}px`;
      slot.style.maxWidth = "100%";
      slot.style.minHeight = `${viewport.height}px`;

      const canvas = document.createElement("canvas");
      const context = canvas.getContext("2d");
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.floor(viewport.width * dpr);
      canvas.height = Math.floor(viewport.height * dpr);
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;
      context.scale(dpr, dpr);

      const textLayer = document.createElement("div");
      textLayer.className = "textLayer";
      textLayer.style.width = `${viewport.width}px`;
      textLayer.style.height = `${viewport.height}px`;
      textLayer.style.setProperty("--scale-factor", String(scale));

      const pageBadge = document.createElement("div");
      pageBadge.className = "reader-page-num-pill";
      pageBadge.textContent = `Page ${pNum} of ${readerState.pageCount}`;

      slot.innerHTML = "";
      slot.appendChild(canvas);
      slot.appendChild(textLayer);
      slot.appendChild(pageBadge);

      await pdfPage.render({ canvasContext: context, viewport }).promise;

      const textContent = await pdfPage.getTextContent();
      if (window.pdfjsLib && pdfjsLib.renderTextLayer) {
        const task = pdfjsLib.renderTextLayer({
          textContent,
          textContentSource: textContent,
          container: textLayer,
          viewport,
          textDivs: [],
        });
        if (task && task.promise) await task.promise;
      }

      slot.dataset.rendered = "true";
      readerState.renderedPages.add(pNum);
      highlightMarksForPageSlot(slot, pNum);
    } catch (err) {
      console.warn(`Error rendering page ${pNum}:`, err);
    } finally {
      readerState.renderingPages.delete(pNum);
    }
  }

  function scrollToPage(page, jumpId, field) {
    const safePage = Math.max(1, Math.min(readerState.pageCount || 1, Number(page) || 1));
    readerState.page = safePage;
    const input = document.getElementById("pdfPageInput");
    if (input) input.value = String(safePage);
    highlightActive(jumpId, field);

    const doScroll = () => {
      const target = document.getElementById(`pdf-page-${safePage}`);
      if (target) {
        target.scrollIntoView({ behavior: "smooth", block: "start" });
        renderPdfPageSlot(safePage, target);
      }
    };

    if (window.innerWidth <= 960) {
      setMobileReaderView("pdf");
      requestAnimationFrame(doScroll);
    } else {
      doScroll();
    }
  }

  async function showPdfPage(page, jumpId, field) {
    const holder = document.getElementById("readerPdf");
    const missing = document.getElementById("readerMissing");
    const bar = document.getElementById("pdfBar");
    if (!readerState.pdfUrl) return;
    missing.hidden = true;
    holder.hidden = false;
    bar.hidden = false;

    // If PDF document is already loaded, smoothly scroll to page
    if (readerState.pdfDoc) {
      scrollToPage(page, jumpId, field);
      return;
    }

    if (window.pdfjsLib) {
      readerState.pdfDoc = await pdfjsLib.getDocument(readerState.pdfUrl).promise;
      readerState.pageCount = readerState.pdfDoc.numPages || readerState.pageCount || 1;
      document.getElementById("pdfPageCount").textContent = `/ ${readerState.pageCount}`;
    }
    if (!readerState.pdfDoc) return;

    const safePage = Math.max(1, Math.min(readerState.pageCount || 1, Number(page) || 1));
    readerState.page = safePage;
    document.getElementById("pdfPageInput").value = String(safePage);

    // Calculate responsive scale based on available container width
    const clientW = holder.clientWidth || (document.getElementById("readerSplit") ? document.getElementById("readerSplit").clientWidth : 0) || window.innerWidth || 360;
    const wrapWidth = Math.max(260, clientW - 24);
    const page1 = await readerState.pdfDoc.getPage(1);
    const unscaled = page1.getViewport({ scale: 1 });
    const scale = Math.min(1.6, Math.max(0.35, wrapWidth / unscaled.width));
    const viewport1 = page1.getViewport({ scale });
    readerState.scale = scale;
    readerState.pageWidth = viewport1.width;
    readerState.pageHeight = viewport1.height;

    // Pre-create placeholder slots for ALL pages to give the single vertical scrollbar its full true height
    holder.innerHTML = "";
    readerState.renderedPages = new Set();
    readerState.renderingPages = new Set();

    for (let p = 1; p <= readerState.pageCount; p++) {
      const pageSlot = document.createElement("div");
      pageSlot.className = "reader-page";
      pageSlot.id = `pdf-page-${p}`;
      pageSlot.dataset.page = String(p);
      pageSlot.style.width = `${viewport1.width}px`;
      pageSlot.style.maxWidth = "100%";
      pageSlot.style.minHeight = `${viewport1.height}px`;
      pageSlot.innerHTML = `
        <div class="reader-page-loading">
          <span class="reader-page-badge">Page ${p}</span>
          <span class="reader-page-spinner">Loading…</span>
        </div>
      `;
      holder.appendChild(pageSlot);
    }

    // Set up IntersectionObserver to lazy-render pages as the user scrolls
    if (readerState.renderObserver) {
      readerState.renderObserver.disconnect();
    }
    readerState.renderObserver = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          const pNum = Number(entry.target.dataset.page);
          renderPdfPageSlot(pNum, entry.target);
        }
      });
    }, {
      root: holder,
      rootMargin: "450px 0px 450px 0px"
    });

    holder.querySelectorAll(".reader-page").forEach((slot) => {
      readerState.renderObserver.observe(slot);
    });

    // Bind scroll listener for tracking current active page number
    if (!holder.dataset.scrollBound) {
      holder.dataset.scrollBound = "true";
      let scrollTicking = false;
      holder.addEventListener("scroll", () => {
        if (!scrollTicking) {
          scrollTicking = true;
          requestAnimationFrame(() => {
            updateCurrentPageOnScroll();
            scrollTicking = false;
          });
        }
      }, { passive: true });
    }

    // Immediately render page 1 (and target jump page if different)
    await renderPdfPageSlot(1);
    if (safePage > 1) {
      await renderPdfPageSlot(safePage);
      scrollToPage(safePage, jumpId, field);
    } else {
      highlightActive(jumpId, field);
    }
  }

  function readerNote(label, text, jumps, field) {
    const value = String(text || "").trim();
    if (!value) return "";
    const jump = jumpFor(jumps, fieldJumpId(field));
    const page = jump ? jump.page : 1;
    const id = jump ? jump.id : "intro";
    return `<button type="button" class="reader-field" data-jump-page="${page}" data-jump-id="${id}" data-field="${escapeHtml(field)}">
      <strong>${escapeHtml(label)}</strong>
      <span>${escapeHtml(value)}</span>
    </button>`;
  }

  function renderReaderMeta() {
    const meta = readerState.meta || {};
    const year = meta.year ? String(meta.year) : "";
    document.getElementById("readerMeta").innerHTML = `
      <p class="notes-kicker">${escapeHtml(meta.research_name || "Untitled research")}</p>
      <h3>${escapeHtml(meta.title || "Untitled paper")}</h3>
      <p class="meta">${year ? escapeHtml(year) : "Year not set"}${meta.authors ? " · " + escapeHtml(meta.authors) : ""}</p>`;
  }

  function renderMarksList() {
    const list = document.getElementById("marksList");
    const marks = readerState.marks || [];
    const countEl = document.getElementById("readerMarkCount");
    if (countEl) countEl.textContent = String(marks.length);
    if (!marks.length) {
      list.innerHTML = `<p class="empty">Select a sentence in the PDF, then click Mark.</p>`;
      return;
    }
    list.innerHTML = marks.map((mark, index) => `
      <article class="mark-card" data-mark-id="${escapeHtml(mark.id)}">
        <div class="mark-card-top">
          <button type="button" class="note-page" data-jump-mark="${escapeHtml(mark.page)}">p.${escapeHtml(mark.page)}</button>
          <span class="mark-move">
            <button type="button" data-move="-1" ${index === 0 ? "disabled" : ""}>↑</button>
            <button type="button" data-move="1" ${index === marks.length - 1 ? "disabled" : ""}>↓</button>
          </span>
          <button type="button" class="ghost mark-remove" data-delete-mark="${escapeHtml(mark.id)}">Remove</button>
        </div>
        <blockquote>${escapeHtml(mark.text || "")}</blockquote>
        <textarea data-note-mark="${escapeHtml(mark.id)}" placeholder="Your note in your own words">${escapeHtml(mark.note || "")}</textarea>
      </article>
    `).join("");
  }

  function marksPayload() {
    const meta = readerState.meta || {};
    return {
      research_name: meta.research_name || "",
      title: meta.title || "",
      year: meta.year || "",
      authors: meta.authors || "",
      marks: readerState.marks || [],
    };
  }

  async function persistMarks() {
    if (!readerState.taskId && !readerState.uploadId) return;
    const res = await fetch(marksUrl(), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(marksPayload()),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "Could not save marks.");
    readerState.marks = data.marks || [];
    document.getElementById("marksHint").textContent = "Saved.";
    highlightMarksOnPage();
  }

  let exportTimer = null;

  function setExportPopup(state, title, text) {
    const popup = document.getElementById("exportPopup");
    const titleEl = document.getElementById("exportPopupTitle");
    const textEl = document.getElementById("exportPopupText");
    if (!popup) return;
    popup.hidden = false;
    popup.removeAttribute("hidden");
    document.body.appendChild(popup);
    popup.classList.toggle("is-busy", state === "busy");
    popup.classList.toggle("is-ready", state === "ready");
    popup.classList.toggle("is-error", state === "error");
    titleEl.textContent = title;
    textEl.textContent = text;
  }

  function hideExportPopup(delay) {
    clearTimeout(exportTimer);
    exportTimer = setTimeout(() => {
      const popup = document.getElementById("exportPopup");
      if (popup) popup.hidden = true;
    }, delay || 0);
  }

  function exportFilename(res) {
    const raw = res.headers.get("Content-Disposition") || "";
    const match = raw.match(/filename="([^"]+)"/i);
    if (match && match[1]) return match[1];
    const title = (readerState.meta && readerState.meta.title) || "paper";
    return `Literature-Lab-notes-${title.replace(/[^\w.-]+/g, "-").slice(0, 60)}.pdf`;
  }

  async function exportNotesPdf() {
    const btn = document.getElementById("exportNotes");
    const uploadId = String(readerState.uploadId || "").trim();
    const taskId = String(readerState.taskId || "").replace(/^upload:/, "");
    const rank = String(readerState.rank || "").trim();
    if (!uploadId && (!taskId || !rank)) {
      setExportPopup("error", "Open a paper first", "Click Read, then export your notes.");
      hideExportPopup(2800);
      return;
    }
    if (btn.disabled) return;
    btn.disabled = true;
    clearTimeout(exportTimer);
    setExportPopup("busy", "Preparing your notes", "Building a Literature Lab PDF…");
    try {
      await persistMarks();
    } catch {
      /* still export what is already saved */
    }
    const pass = encodeURIComponent(String(activePass || 0));
    const focus = encodeURIComponent((document.getElementById("sectionTip")?.textContent || "").trim());
    const url = uploadId
      ? `/read/you/${encodeURIComponent(uploadId)}/notes.pdf?pass=${pass}&focus=${focus}`
      : `/read/agent/${encodeURIComponent(readerState.taskId)}/${encodeURIComponent(rank)}/notes.pdf?pass=${pass}&focus=${focus}`;
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error("Could not build the notes PDF.");
      const blob = await res.blob();
      const objectUrl = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = exportFilename(res);
      link.rel = "noopener";
      document.body.appendChild(link);
      setExportPopup("ready", "Your notes are ready", "Downloading the PDF now.");
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(objectUrl), 4000);
      hideExportPopup(2600);
    } catch (err) {
      setExportPopup("error", "Could not export notes", err.message || "Try again in a moment.");
      hideExportPopup(3600);
    } finally {
      btn.disabled = false;
    }
  }

  function queueSaveMarks() {
    clearTimeout(marksTimer);
    marksTimer = setTimeout(() => {
      persistMarks().catch((err) => {
        document.getElementById("marksHint").textContent = err.message || "Could not save.";
      });
    }, 400);
  }

  async function addCurrentMark() {
    const selData = selectedPdfText();
    const text = typeof selData === "string" ? selData : (selData && selData.text ? selData.text : "");
    const markPage = (selData && selData.page) ? selData.page : (readerState.page || 1);
    if (!text) {
      document.getElementById("marksHint").textContent = "Select text in the PDF first, then click Mark.";
      setReaderTab("marks");
      return;
    }
    readerState.marks = (readerState.marks || []).concat([{
      id: (window.crypto && crypto.randomUUID) ? crypto.randomUUID() : `m-${Date.now()}`,
      page: markPage,
      text,
      note: "",
    }]);
    renderMarksList();
    highlightMarksOnPage();
    setReaderTab("marks");
    if (window.innerWidth <= 960) setMobileReaderView("guide");
    try {
      await persistMarks();
      document.getElementById("marksHint").textContent = `Marked p.${markPage}. Add your own note below.`;
    } catch (err) {
      document.getElementById("marksHint").textContent = err.message || "Could not save that mark.";
    }
  }

  const READ_FLOW = {
    cite: "Staged Reading Strategy: 1) Relevance Filter (do not read word-by-word) → 2) Technical Depth (dataset, architecture, baselines) → 3) Survey Matrix & Snowballing. Build a defensible research gap.",
    passes: [
      {
        title: "Pass 1 · Relevance Filter",
        time: "5–10 min",
        goal: "Read Title → Abstract → Intro → Problem → Contribution → Results → Conclusion → Limits. Decide if genuinely relevant.",
        steps: [
          { label: "Title", jump: "intro", ask: "Analyze the title of this paper: what domain, core approach, and problem scope does it indicate?" },
          { label: "Abstract", jump: "intro", ask: "Teach me how to read this abstract in Pass 1: what is the problem, method, and key headline result?" },
          { label: "Introduction", jump: "intro", ask: "Where in the introduction do the authors argue why this problem matters and what prior work failed to do?" },
          { label: "Problem", jump: "intro", ask: "Help me locate the exact problem statement in this paper. What specific failure mode or gap is being tackled?" },
          { label: "Contribution", jump: "intro", ask: "What are the key contributions claimed by this paper? Help me find the bullet points or contribution paragraph." },
          { label: "Results", jump: "results", ask: "What are the headline results and claims? Does the paper show convincing empirical evidence for its claims?" },
          { label: "Conclusion", jump: "limitations", ask: "Walk me through the conclusion: what are the main takeaways, and did the authors deliver on what they promised?" },
          { label: "Limitations", jump: "limitations", ask: "Where are the limitations and future work discussed? What boundaries or caveats did the authors disclose?" },
        ],
      },
      {
        title: "Pass 2 · Technical Depth",
        time: "30–60 min",
        goal: "Investigate Dataset → Preprocessing → Methodology → Architecture → Experimental Setup → Baselines → Metrics → Results → Limitations.",
        steps: [
          { label: "Dataset", jump: "method", ask: "What dataset or benchmarks did this paper use? Teach me how to evaluate their size, diversity, and collection process." },
          { label: "Preprocessing", jump: "method", ask: "What preprocessing, data cleaning, filtering, or feature engineering steps did the authors apply before modeling?" },
          { label: "Methodology", jump: "method", ask: "Explain the core methodology and theoretical formulation. What is the fundamental mechanism behind this approach?" },
          { label: "Architecture", jump: "method", ask: "Explain the system architecture and model pipeline. What are the key modules and how do they pass data between each other?" },
          { label: "Experimental Setup", jump: "method", ask: "How is the experimental setup designed? (train/validation/test splits, hyperparameters, hardware, and training regime)" },
          { label: "Baselines", jump: "results", ask: "What baselines or competing models did the authors compare against? Are these fair, modern state-of-the-art baselines?" },
          { label: "Metrics", jump: "results", ask: "What quantitative metrics were used to evaluate performance (accuracy, F1, latency, error rates), and what do they show?" },
          { label: "Results & Ablations", jump: "results", ask: "Walk me through the results tables and ablation experiments. Which component contributed the most to the improvement?" },
          { label: "Limitations", jump: "limitations", ask: "What are the deep technical limitations, failure modes, edge cases, and computational trade-offs of this method?" },
        ],
      },
      {
        title: "Pass 3 · Survey Matrix & Snowballing",
        time: "synthesis",
        goal: "Record immediately in Literature Survey Matrix. Snowball references backward and forward to build a defensible research gap.",
        steps: [
          { label: "Record to Matrix", jump: "intro", ask: "Generate the Literature Survey Matrix row for this paper: Paper ID | Problem | Dataset | Method | Contribution | Results | Limitations | Relevance | Possible Gap." },
          { label: "Backward Snowballing", jump: "related", ask: "Guide me on Backward Snowballing for this paper: which foundational references cited in this paper should I read next to understand the origin of this problem?" },
          { label: "Forward Snowballing", jump: "related", ask: "Guide me on Forward Snowballing for this paper: how do I track newer papers that cite this work, and how do I verify if their gap has already been solved?" },
          { label: "Defensible Gap", jump: "related", ask: "Two papers are insufficient to prove a research gap. Teach me how to synthesize limitations across multiple papers into a defensible, novel research gap." },
        ],
      },
    ],
  };

  function renderReadFlow() {
    const flowEl = document.getElementById("readFlow");
    if (!flowEl) return;
    const jumps = readerState.jumps || [];
    const tracks = READ_FLOW.passes.map((pass, index) => ({
      id: index,
      short: ["1 Relevance", "2 Tech Depth", "3 Matrix & Snowballing"][index],
      ...pass,
    }));
    const current = tracks[activePass] || tracks[0];
    const pills = tracks.map((track) =>
      `<button type="button" class="pass-pill${track.id === activePass ? " on" : ""}" data-pass="${track.id}">${escapeHtml(track.short)}</button>`
    ).join("");
    const chips = current.steps.map((step) => {
      const jump = jumpFor(jumps, step.jump);
      const page = jump ? jump.page : "";
      const help = FIELD_HELP[step.label.toLowerCase()] || {};
      return `<span class="step-chip" data-pass-id="${activePass}">
        <button type="button" class="step-chip-jump" data-flow-jump="${escapeHtml(step.jump)}" data-flow-page="${escapeHtml(page)}">
          <b>${escapeHtml(step.label)}</b>${page ? ` &middot; p.${escapeHtml(page)}` : ""}
        </button>
        <button type="button" class="ask-mini" data-flow-ask="${escapeHtml(step.ask)}" data-flow-label="${escapeHtml(step.label)}" data-flow-tip="${escapeHtml(help.tip || '')}" title="Ask tutor: ${escapeHtml(step.label)}" aria-label="Ask tutor how to read ${escapeHtml(step.label)}">Ask</button>
      </span>`;
    }).join("");
    flowEl.innerHTML = `
      <p class="guide-kicker">STAGED READING STRATEGY</p>
      <div class="pass-pills">${pills}</div>
      <p class="guide-goal">${escapeHtml(current.goal)}</p>
      <div class="step-chips">${chips}</div>`;
  }

  /* ── Copilot Sidebar Cards Dynamic Population ── */
  function truncate(text, max) {
    const str = String(text || "").trim();
    if (str.length <= max) return str;
    return str.slice(0, max) + "…";
  }

  function splitIntoPoints(text) {
    if (!text) return [];
    const lines = String(text)
      .split(/\n|•|\*/)
      .map((s) => s.trim().replace(/^[-–—]\s*/, ""))
      .filter((s) => s.length > 15);
    if (lines.length >= 2) return lines;
    return String(text)
      .split(/(?<=[.?!])\s+/)
      .map((s) => s.trim())
      .filter((s) => s.length > 15);
  }

  function updateCopilotCards(data) {
    const contextTitle = document.getElementById("contextTitle");
    const contextMeta = document.getElementById("contextMeta");
    const miniContextTitle = document.getElementById("miniContextTitle");
    if (contextTitle) {
      contextTitle.textContent = data.title || "Read paper";
    }
    if (miniContextTitle) {
      miniContextTitle.textContent = truncate(data.title || "Paper Context", 42);
    }
    if (contextMeta) {
      const authors = data.authors || "";
      const year = data.year || "";
      contextMeta.textContent = [authors, year].filter(Boolean).join(" · ") || "Academic research paper";
    }

    const checkList = document.getElementById("checkList");
    if (checkList) {
      const problemSnippet = data.problem ? `<span class="check-highlight">(${escapeHtml(truncate(data.problem, 55))})</span>` : '<span class="check-highlight">(GUI element grounding)</span>';
      const methodSnippet = data.method ? `<span class="check-highlight">(${escapeHtml(truncate(data.method, 50))})</span>` : '<span class="check-highlight">(lightweight model)</span>';
      const evalSnippet = (data.metrics || data.datasets) ? `<span class="check-highlight">(${escapeHtml(truncate(data.metrics || data.datasets, 45))})</span>` : '<span class="check-highlight">(datasets, metrics, results)</span>';
      checkList.innerHTML = `
        <li>What problem are they solving? ${problemSnippet}</li>
        <li>What is their main idea / approach? ${methodSnippet}</li>
        <li>How do they evaluate? ${evalSnippet}</li>
        <li>What are the key findings and contributions?</li>
        <li>Any limitations or future work mentioned?</li>
      `;
    }

    const pointsList = document.getElementById("pointsList");
    if (pointsList) {
      let points = [];
      if (data.key_findings) points = splitIntoPoints(data.key_findings);
      if (points.length < 3 && data.why_read) points.push(...splitIntoPoints(data.why_read));
      if (points.length < 3 && data.features) points.push(...splitIntoPoints(data.features));
      if (!points.length) {
        points = [
          "Focuses on grounding GUI elements using natural language instructions.",
          "Designed for mobile / resource-constrained devices.",
          "Uses a lightweight model for low latency.",
          "Achieves strong performance in cloud-device collaboration.",
          "Effective for real-world GUI agent applications."
        ];
      }
      pointsList.innerHTML = points.slice(0, 5).map((pt) =>
        `<li><span class="point-check">✓</span> <span>${escapeHtml(pt)}</span></li>`
      ).join("");
    }
  }

  /* ── AI Step Modal ────────────────────────────── */
  function openAiModal(stepLabel, askText, tipText) {
    const modal = document.getElementById("aiStepModal");
    const titleEl = document.getElementById("aiModalTitle");
    const tipEl = document.getElementById("aiModalTipText");
    const tipRow = document.getElementById("aiModalTip");
    const inputEl = document.getElementById("aiModalInput");
    const sendBtn = document.getElementById("aiModalSend");
    if (!modal) return;
    titleEl.textContent = stepLabel || "Step";
    if (tipText) {
      tipEl.textContent = tipText;
      tipRow.hidden = false;
    } else {
      tipRow.hidden = true;
    }
    if (inputEl) {
      inputEl.value = askText || "";
      inputEl.readOnly = true;
    }
    modal.removeAttribute("hidden");
    requestAnimationFrame(() => {
      if (sendBtn) sendBtn.focus();
    });
  }

  function closeAiModal() {
    const modal = document.getElementById("aiStepModal");
    if (modal) modal.hidden = true;
  }

  function sendFromModal() {
    const modal = document.getElementById("aiStepModal");
    const inputEl = document.getElementById("aiModalInput");
    const text = (inputEl ? inputEl.value : "").trim();
    if (!text) return;
    closeAiModal();
    const tutorInput = document.getElementById("tutorInput");
    tutorInput.value = text;
    sendTutor();
  }

  function growTutorInput() {
    // Sleek single-line input
  }

  function scrollCopilotToBottom() {
    const body = document.getElementById("copilotBody") || document.querySelector(".copilot-body");
    if (body) {
      body.scrollTop = body.scrollHeight;
    }
  }

  function setCopilotTab(tabName) {
    const isChat = tabName !== "overview";
    const tabChat = document.getElementById("tabCopilotChat");
    const tabOverview = document.getElementById("tabCopilotOverview");
    const panelChat = document.getElementById("panelCopilotChat");
    const panelOverview = document.getElementById("panelCopilotOverview");

    if (tabChat) {
      tabChat.classList.toggle("on", isChat);
      tabChat.setAttribute("aria-selected", isChat ? "true" : "false");
    }
    if (tabOverview) {
      tabOverview.classList.toggle("on", !isChat);
      tabOverview.setAttribute("aria-selected", !isChat ? "true" : "false");
    }
    if (panelChat) panelChat.hidden = !isChat;
    if (panelOverview) panelOverview.hidden = isChat;

    if (isChat) {
      requestAnimationFrame(() => scrollCopilotToBottom(false));
    }
  }

  function renderTutorLog(messages) {
    const log = document.getElementById("tutorLog");
    const emptyState = document.getElementById("tutorEmptyState");
    const badge = document.getElementById("tutorChatBadge");
    const rows = messages || [];
    if (badge) {
      badge.textContent = String(rows.length);
      badge.hidden = rows.length === 0;
    }
    if (emptyState) {
      emptyState.hidden = rows.length > 0;
    }
    if (!rows.length) {
      if (log) log.innerHTML = "";
      return;
    }
    if (log) {
      log.innerHTML = rows.map((item) => {
        const mine = item.role === "user";
        const html = mine
          ? `<p>${escapeHtml(item.content || "")}</p>`
          : renderMarkdown(item.content || "");
        return `<div class="tutor-row${mine ? " me" : " ai"}"><div class="tutor-bubble ${mine ? "tutor-bubble-me" : "tutor-bubble-ai"}">${html}</div></div>`;
      }).join("");
    }
    scrollCopilotToBottom(false);
  }

  function fillTutorProvider() {
    const sel = document.getElementById("tutorProvider");
    const openai = document.getElementById("pillOpenai");
    const gemini = document.getElementById("pillGemini");
    if (openai) openai.disabled = !openaiConfigured;
    if (gemini) gemini.disabled = !geminiConfigured;
    if (!sel) return;
    Array.from(sel.options).forEach((opt) => {
      if (opt.value === "openai") opt.disabled = !openaiConfigured;
      if (opt.value === "gemini") opt.disabled = !geminiConfigured;
    });
    let value = sel.value || preferredProvider || "";
    if (value === "openai" && !openaiConfigured) value = geminiConfigured ? "gemini" : "";
    if (value === "gemini" && !geminiConfigured) value = openaiConfigured ? "openai" : "";
    if (!value) value = openaiConfigured ? "openai" : geminiConfigured ? "gemini" : "";
    sel.value = value;
    if (openai) openai.classList.toggle("on", value === "openai");
    if (gemini) gemini.classList.toggle("on", value === "gemini");
  }

  function setTutorProvider(name) {
    const sel = document.getElementById("tutorProvider");
    if (sel) sel.value = name;
    fillTutorProvider();
  }

  async function sendTutor() {
    const input = document.getElementById("tutorInput");
    const sendBtn = document.getElementById("tutorSend");
    const log = document.getElementById("tutorLog");
    const text = (input.value || "").trim();
    if (!text || tutorBusy) return;
    if (!readerState.taskId && !readerState.uploadId) return;

    // 1. Ensure right sidebar is uncollapsed
    const split = document.getElementById("readerSplit");
    if (split && split.classList.contains("copilot-collapsed")) {
      split.classList.remove("copilot-collapsed");
      const toggleBtn = document.getElementById("toggleCopilot");
      if (toggleBtn) {
        toggleBtn.innerHTML = "&minus;";
        toggleBtn.title = "Collapse AI Reading Tutor";
      }
    }

    // 2. Ensure Tutor Chat tab is active so conversation is immediately visible
    setCopilotTab("chat");
    if (window.innerWidth <= 960) {
      setMobileReaderView("tutor");
    }

    tutorBusy = true;
    if (sendBtn) sendBtn.disabled = true;
    input.value = "";
    growTutorInput();

    // 3. Render user message
    readerState.tutor = (readerState.tutor || []).concat([{ role: "user", content: text }]);
    renderTutorLog(readerState.tutor);
    scrollCopilotToBottom(true);

    // 4. Append live thinking placeholder
    const live = document.createElement("div");
    live.className = "tutor-row ai";
    live.innerHTML = `<div class="tutor-bubble tutor-bubble-ai tutor-live"><span class="tutor-typing-wrap"><span class="tutor-typing-label">✦ Analyzing paper&hellip;</span><span class="tutor-typing" aria-label="Thinking"><i></i><i></i><i></i></span></span></div>`;
    const bubble = live.querySelector(".tutor-bubble");
    log.appendChild(live);
    scrollCopilotToBottom(true);

    let full = "";
    let paintId = 0;
    const paint = (done) => {
      if (!full) return;
      bubble.classList.toggle("tutor-live", !done);
      bubble.innerHTML = renderMarkdown(full);
      scrollCopilotToBottom(false);
    };
    const queuePaint = () => {
      if (paintId) return;
      paintId = requestAnimationFrame(() => {
        paintId = 0;
        paint(false);
      });
    };

    try {
      const res = await fetch(tutorUrl(), {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify({
          message: text,
          provider: document.getElementById("tutorProvider").value || "",
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(typeof data.detail === "string" ? data.detail : "The tutor could not answer. Add an API key in Settings.");
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split("\n\n");
        buffer = chunks.pop() || "";
        for (const chunk of chunks) {
          const line = chunk.split("\n").find((item) => item.startsWith("data:"));
          if (!line) continue;
          let data = {};
          try {
            data = JSON.parse(line.replace(/^data:\s*/, ""));
          } catch (_err) {
            continue;
          }
          if (data.error) throw new Error(data.error);
          if (data.delta) {
            full += data.delta;
            queuePaint();
          }
        }
      }
      if (paintId) cancelAnimationFrame(paintId);
      paint(true);
      if (!full.trim()) throw new Error("The tutor had nothing to say. Try again.");
      readerState.tutor = (readerState.tutor || []).concat([{ role: "assistant", content: full }]);
      renderTutorLog(readerState.tutor);
    } catch (err) {
      if (full.trim()) {
        paint(true);
        readerState.tutor = (readerState.tutor || []).concat([{ role: "assistant", content: full }]);
        renderTutorLog(readerState.tutor);
      } else {
        live.remove();
        readerState.tutor = (readerState.tutor || []).concat([{
          role: "assistant",
          content: err.message || "Could not reach the tutor. Check your connection or provider settings.",
        }]);
        renderTutorLog(readerState.tutor);
      }
    } finally {
      tutorBusy = false;
      if (sendBtn) sendBtn.disabled = false;
      scrollCopilotToBottom(true);
      input.focus();
    }
  }

  function closeReader() {
    if (readerState.renderObserver) {
      try { readerState.renderObserver.disconnect(); } catch (_err) { }
      readerState.renderObserver = null;
    }
    readerState.renderedPages = new Set();
    readerState.renderingPages = new Set();
    document.getElementById("readerPanel").hidden = true;
    document.getElementById("libraryView").hidden = false;
    document.body.classList.remove("reader-open");
    document.getElementById("readerPdf").innerHTML = "";
    if (readerState.pdfDoc && readerState.pdfDoc.destroy) {
      try { readerState.pdfDoc.destroy(); } catch (_err) { /* ignore */ }
    }
    readerState.pdfDoc = null;
    const url = new URL(location.href);
    url.searchParams.delete("task");
    url.searchParams.delete("rank");
    url.searchParams.delete("upload");
    history.replaceState({}, "", url.pathname + url.search);
    hideExportPopup(0);
    loadLibrary();
  }

  async function openReader(taskId, rank, options) {
    options = options || {};
    const uploadId = String(options.uploadId || "").trim();
    const id = String(taskId || "").trim();
    const paperRank = String(rank || "").trim();
    if (!uploadId && (!id || !paperRank)) return;
    const panel = document.getElementById("readerPanel");
    const titleEl = document.getElementById("readerTitle");
    const hintEl = document.getElementById("readerHint");
    const jumpsEl = document.getElementById("readerJumps");
    const extractEl = document.getElementById("readerExtract");
    const missing = document.getElementById("readerMissing");
    const holder = document.getElementById("readerPdf");
    if (readerState.renderObserver) {
      try { readerState.renderObserver.disconnect(); } catch (_err) { }
      readerState.renderObserver = null;
    }
    if (readerState.pdfDoc && readerState.pdfDoc.destroy) {
      try { readerState.pdfDoc.destroy(); } catch (_err) { /* ignore */ }
    }
    readerState.pdfDoc = null;
    readerState.renderedPages = new Set();
    readerState.renderingPages = new Set();
    activePass = 0;
    document.getElementById("libraryView").hidden = true;
    panel.hidden = false;
    setMobileReaderView("pdf");
    document.body.classList.add("reader-open");
    titleEl.textContent = "Opening paper…";
    jumpsEl.innerHTML = "";
    extractEl.innerHTML = "<p class='empty'>Loading extract…</p>";
    holder.innerHTML = "";
    missing.hidden = true;
    document.getElementById("pdfBar").hidden = true;
    try {
      const outlineUrl = uploadId
        ? `/read/you/${encodeURIComponent(uploadId)}/outline`
        : `/task/${encodeURIComponent(id)}/paper/${encodeURIComponent(paperRank)}/outline`;
      const res = await fetch(outlineUrl);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Could not open this paper.");
      const saved = data.marks || {};
      readerState = {
        kind: uploadId ? "you" : "agent",
        uploadId,
        taskId: uploadId ? `upload:${uploadId}` : id,
        rank: uploadId ? "1" : paperRank,
        pdfUrl: data.has_pdf
          ? (uploadId
            ? `/read/you/${encodeURIComponent(uploadId)}/pdf`
            : `/task/${encodeURIComponent(id)}/paper/${encodeURIComponent(paperRank)}/pdf`)
          : "",
        jumps: data.section_jumps || [],
        page: 1,
        pageCount: data.page_count || 1,
        pdfDoc: null,
        renderedPages: new Set(),
        renderingPages: new Set(),
        renderObserver: null,
        marks: saved.marks || [],
        tutor: (data.tutor && data.tutor.messages) || [],
        meta: {
          research_name: saved.research_name || data.research_name || "",
          title: saved.title || data.title || "",
          year: saved.year || data.year || "",
          authors: saved.authors || data.authors || "",
        },
      };
      titleEl.textContent = data.title || "Read paper";
      hintEl.textContent = data.has_pdf
        ? "Follow the Guide. Ask the tutor to teach the technique, then check the PDF."
        : "No PDF was saved for this paper (abstract only). Open the web link to read it.";
      jumpsEl.innerHTML = (data.section_jumps || []).map((jump) => {
        const short = { Introduction: "Intro", "Related work": "Related", Method: "Method", Results: "Results", Limitations: "Limits" };
        const name = short[jump.label] || jump.label;
        return `
        <button type="button" class="reader-jump${jump.found ? "" : " guessed"}" data-jump-page="${jump.page}" data-jump-id="${escapeHtml(jump.id)}" ${data.has_pdf ? "" : "disabled"}>
          ${escapeHtml(name)} ${escapeHtml(jump.page)}
        </button>`;
      }).join("");
      renderReaderMeta();
      extractEl.innerHTML = [
        readerNote("Problem", data.problem, data.section_jumps, "problem"),
        readerNote("Dataset", data.datasets, data.section_jumps, "datasets"),
        readerNote("Features", data.features, data.section_jumps, "features"),
        readerNote("Method", data.method, data.section_jumps, "method"),
        readerNote("Metrics", data.metrics, data.section_jumps, "metrics"),
        readerNote("Findings", data.key_findings, data.section_jumps, "key_findings"),
        readerNote("Limitations", data.limitations, data.section_jumps, "limitations"),
        readerNote("Why read", data.why_read, data.section_jumps, "why_read"),
      ].filter(Boolean).join("") || "<p class='empty'>No extract yet. Use the Guide and the PDF.</p>";
      if (data.url) {
        extractEl.insertAdjacentHTML("beforeend", `<p class="meta"><a class="open-paper" href="${escapeHtml(data.url)}" target="_blank" rel="noreferrer">Open on the web</a></p>`);
      }
      renderMarksList();
      renderReadFlow();
      renderTutorLog(readerState.tutor);
      updateCopilotCards(data);
      fillTutorProvider();
      setCopilotTab(readerState.tutor && readerState.tutor.length > 0 ? "chat" : "overview");
      setReaderTab(options.tab || "guide");
      if (data.has_pdf) {
        const first = jumpFor(data.section_jumps, "intro") || (data.section_jumps || [])[0];
        await showPdfPage(first ? first.page : 1, first ? first.id : "intro");
      } else {
        holder.hidden = true;
        missing.hidden = false;
        missing.innerHTML = data.url
          ? `No local PDF. <a class="open-paper" href="${escapeHtml(data.url)}" target="_blank" rel="noreferrer">Open the paper on the web</a>.`
          : "No local PDF and no paper link.";
      }
    } catch (err) {
      titleEl.textContent = "Could not open paper";
      extractEl.innerHTML = `<p class="empty">${escapeHtml(err.message || "Error")}</p>`;
    }
  }

  function progressOf(paper) {
    return (paper && paper.progress) || { status: "unread", mark_count: 0, tutor_turns: 0, paper_key: "" };
  }

  function progressClass(paper) {
    const status = progressOf(paper).status || "unread";
    if (status === "completed") return " is-well-read";
    if (status === "reading") return " is-reading";
    return "";
  }

  function progressBlock(paper) {
    const progress = progressOf(paper);
    const status = progress.status || "unread";
    const paperKey = paper.paper_key || (paper.kind === "you" || paper.id ? `upload:${paper.id || paper.upload_id}` : `${paper.task_id}:${paper.rank}`);

    const chips = [];
    if (progress.mark_count) chips.push(`${progress.mark_count} mark${progress.mark_count === 1 ? "" : "s"}`);
    if (progress.note_count) chips.push("notes");
    if (progress.tutor_turns) chips.push("tutor");

    const statusIcon = status === "completed" ? "✓" : status === "reading" ? "●" : "○";
    const statusText = status === "completed" ? "Completed" : status === "reading" ? "Reading" : "Unread";

    return `
      <div class="read-status-bar">
        <button type="button" class="status-pill status-${status}" data-toggle-status="${escapeHtml(paperKey)}" data-current-status="${status}" title="Click to change reading status (Unread / Reading / Completed)">
          <span class="status-icon">${statusIcon}</span>
          <span class="status-label">${statusText}</span>
          <svg class="status-arrow" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M6 9l6 6 6-6"/></svg>
        </button>
        ${chips.length ? `<div class="read-chips-row">${chips.map((item) => `<span class="chip">${escapeHtml(item)}</span>`).join("")}</div>` : ""}
      </div>`;
  }

  function renderHubAgent(papers) {
    const list = document.getElementById("hubAgentList");
    document.querySelectorAll(".hub-agent-count").forEach((el) => {
      el.textContent = String((papers || []).length);
    });
    if (!(papers || []).length) {
      list.innerHTML = `<p class="empty">No agent papers yet. Run research on the home page, then they appear here.</p>`;
      return;
    }
    list.innerHTML = papers.map((paper) => `
      <article class="notes-card paper-card${progressClass(paper)}">
        ${progressBlock(paper)}
        <p class="notes-kicker">${escapeHtml(paper.research_name || "Research")}</p>
        <h3>${escapeHtml(paper.title || "Untitled paper")}</h3>
        <p class="meta">${paper.year ? escapeHtml(paper.year) : "Year not set"}${paper.authors ? " · " + escapeHtml(paper.authors) : ""}${paper.has_pdf ? " · PDF saved" : " · abstract only"}</p>
        <p class="starred-actions">
          <button type="button" class="btn-save btn-save--primary" data-reader-task="${escapeHtml(paper.task_id)}" data-open-reader="${escapeHtml(paper.rank)}">Read</button>
        </p>
      </article>
    `).join("");
  }

  function bindDropZone() {
    const input = document.getElementById("uploadPdf");
    const zone = document.getElementById("dropZone");
    if (input) input.addEventListener("change", () => {
      if (input.files && input.files[0]) uploadUserPdf(input.files[0]);
    });
    if (!zone) return;
    zone.addEventListener("dragover", (event) => {
      event.preventDefault();
      zone.classList.add("on");
    });
    zone.addEventListener("dragleave", () => zone.classList.remove("on"));
    zone.addEventListener("drop", (event) => {
      event.preventDefault();
      zone.classList.remove("on");
      const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
      if (file) uploadUserPdf(file);
    });
  }

  function renderHubYou(papers) {
    const list = document.getElementById("hubYouList");
    document.querySelectorAll(".hub-you-count").forEach((el) => {
      el.textContent = String((papers || []).length);
    });
    const cards = (papers || []).map((paper) => `
      <article class="notes-card paper-card${progressClass(paper)}">
        ${progressBlock(paper)}
        <p class="notes-kicker">Your papers</p>
        <h3>${escapeHtml(paper.title || "Untitled paper")}</h3>
        <p class="meta">${paper.page_count ? escapeHtml(paper.page_count) + " pages" : "PDF"}${paper.original_name ? " · " + escapeHtml(paper.original_name) : ""}</p>
        <p class="starred-actions">
          <button type="button" class="btn-save btn-save--primary" data-open-upload="${escapeHtml(paper.id)}">Read</button>
          <button type="button" class="open-paper send-to-agent" data-send-to-agent="${escapeHtml(paper.id)}" title="Send to AI agent to analyze findings, update Literature matrix & find matching datasets">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M22 2L11 13"/><path d="M22 2L15 22L11 13L2 9L22 2Z"/></svg>
            Send to AI agent
          </button>
          <button type="button" class="ghost" data-delete-upload="${escapeHtml(paper.id)}">Remove</button>
        </p>
      </article>
    `).join("");
    list.innerHTML = `
      <label class="drop-zone" id="dropZone">
        <input id="uploadPdf" type="file" accept="application/pdf,.pdf" hidden />
        <strong>Drop a PDF here</strong>
        <span>or click to upload a paper you already have. Then click Read.</span>
      </label>
      ${cards || `<p class="empty">No uploads yet. Drop a PDF above.</p>`}`;
    bindDropZone();
  }

  async function uploadUserPdf(file) {
    if (!file || !String(file.name || "").toLowerCase().endsWith(".pdf")) {
      showBanner("Upload a PDF file.");
      return;
    }
    showBanner("");
    const body = new FormData();
    body.append("file", file);
    const res = await fetch("/read/you", { method: "POST", body });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      showBanner(data.detail || "Could not upload that PDF.");
      return;
    }
    await loadLibrary();
    setHubTab("you");
  }

  async function loadLibrary() {
    const res = await fetch("/read/library");
    if (!res.ok) return;
    const data = await res.json();
    lastLibrary = data;
    renderHubAgent(data.agent || []);
    renderHubYou(data.you || []);
  }

  async function loadSettings() {
    try {
      const res = await fetch("/api/settings");
      if (!res.ok) return;
      const data = await res.json();
      openaiConfigured = Boolean(data.openai_configured);
      geminiConfigured = Boolean(data.gemini_configured);
      preferredProvider = data.preferred_provider || "openai";
      fillTutorProvider();
    } catch (_err) {
      /* ignore */
    }
  }

  function openFromQuery() {
    const params = new URLSearchParams(location.search);
    const upload = params.get("upload");
    const task = params.get("task");
    const rank = params.get("rank");
    const tab = params.get("tab");
    if (tab === "you") setHubTab("you");
    if (upload) {
      openReader("", "1", { tab: "guide", uploadId: upload });
      return;
    }
    if (task && rank) {
      openReader(task, rank, { tab: "guide" });
    }
  }

  async function togglePaperStatus(paperKey, currentStatus) {
    const nextStatus = currentStatus === "unread" ? "reading" : currentStatus === "reading" ? "completed" : "unread";
    try {
      await fetch("/read/status", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ paper_key: paperKey, status: nextStatus })
      });
      await loadLibrary();
    } catch (_err) {
      /* ignore */
    }
  }

  document.getElementById("navAgent").addEventListener("click", () => setHubTab("agent"));
  document.getElementById("navYou").addEventListener("click", () => setHubTab("you"));
  document.getElementById("tabHubAgent").addEventListener("click", () => setHubTab("agent"));
  document.getElementById("tabHubYou").addEventListener("click", () => setHubTab("you"));
  document.getElementById("hubAgentList").addEventListener("click", async (event) => {
    const statusBtn = event.target.closest("[data-toggle-status]");
    if (statusBtn) {
      event.preventDefault();
      await togglePaperStatus(statusBtn.dataset.toggleStatus, statusBtn.dataset.currentStatus);
      return;
    }
    const btn = event.target.closest("[data-open-reader]");
    if (!btn) return;
    openReader(btn.dataset.readerTask, btn.dataset.openReader, { tab: "guide" });
  });
  document.getElementById("hubYouList").addEventListener("click", async (event) => {
    const statusBtn = event.target.closest("[data-toggle-status]");
    if (statusBtn) {
      event.preventDefault();
      await togglePaperStatus(statusBtn.dataset.toggleStatus, statusBtn.dataset.currentStatus);
      return;
    }
    const remove = event.target.closest("[data-delete-upload]");
    if (remove) {
      await fetch(`/read/you/${encodeURIComponent(remove.dataset.deleteUpload)}`, { method: "DELETE" });
      loadLibrary();
      return;
    }
    const btn = event.target.closest("[data-open-upload]");
    if (!btn) return;
    openReader("", "1", { tab: "guide", uploadId: btn.dataset.openUpload });
  });
  document.getElementById("closeReader").addEventListener("click", closeReader);
  document.getElementById("exportNotes").addEventListener("click", () => {
    exportNotesPdf().catch(() => { });
  });
  document.getElementById("markBtn").addEventListener("click", addCurrentMark);
  document.getElementById("markBtnPdf").addEventListener("click", addCurrentMark);
  document.getElementById("saveMarks").addEventListener("click", () => persistMarks().catch((err) => {
    document.getElementById("marksHint").textContent = err.message || "Could not save.";
  }));
  document.getElementById("pdfPrev").addEventListener("click", () => showPdfPage((readerState.page || 1) - 1));
  document.getElementById("pdfNext").addEventListener("click", () => showPdfPage((readerState.page || 1) + 1));
  document.getElementById("pdfPageInput").addEventListener("change", (event) => showPdfPage(event.target.value));
  const readerViewToggle = document.getElementById("readerViewToggle");
  if (readerViewToggle) {
    readerViewToggle.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-view]");
      if (btn) setMobileReaderView(btn.dataset.view);
    });
  }
  document.querySelector(".reader-notes .reader-tabs").addEventListener("click", (event) => {
    const tab = event.target.closest("[data-reader-tab]");
    if (tab) {
      setReaderTab(tab.dataset.readerTab);
      if (window.innerWidth <= 960) setMobileReaderView("guide");
    }
  });
  document.getElementById("readerJumps").addEventListener("click", (event) => {
    const jump = event.target.closest("[data-jump-page]");
    if (jump) {
      showPdfPage(Number(jump.dataset.jumpPage), jump.dataset.jumpId || "");
      if (window.innerWidth <= 960) setMobileReaderView("pdf");
    }
  });
  document.getElementById("readerExtract").addEventListener("click", (event) => {
    const field = event.target.closest("[data-jump-page]");
    if (field) {
      showPdfPage(Number(field.dataset.jumpPage), field.dataset.jumpId || "", field.dataset.field || "");
      if (window.innerWidth <= 960) setMobileReaderView("pdf");
    }
  });
  document.getElementById("marksList").addEventListener("click", (event) => {
    const jump = event.target.closest("[data-jump-mark]");
    if (jump) {
      showPdfPage(Number(jump.dataset.jumpMark));
      if (window.innerWidth <= 960) setMobileReaderView("pdf");
      return;
    }
    const remove = event.target.closest("[data-delete-mark]");
    if (remove) {
      readerState.marks = (readerState.marks || []).filter((mark) => mark.id !== remove.dataset.deleteMark);
      renderMarksList();
      queueSaveMarks();
      highlightMarksOnPage();
      return;
    }
    const move = event.target.closest("[data-move]");
    if (!move) return;
    const card = event.target.closest("[data-mark-id]");
    if (!card) return;
    const index = (readerState.marks || []).findIndex((mark) => mark.id === card.dataset.markId);
    const next = index + Number(move.dataset.move);
    if (index < 0 || next < 0 || next >= readerState.marks.length) return;
    const copy = readerState.marks.slice();
    const [item] = copy.splice(index, 1);
    copy.splice(next, 0, item);
    readerState.marks = copy;
    renderMarksList();
    queueSaveMarks();
  });
  document.getElementById("marksList").addEventListener("input", (event) => {
    const area = event.target.closest("[data-note-mark]");
    if (!area) return;
    const mark = (readerState.marks || []).find((item) => item.id === area.dataset.noteMark);
    if (!mark) return;
    mark.note = area.value;
    queueSaveMarks();
  });
  document.getElementById("tutorSend").addEventListener("click", sendTutor);
  document.getElementById("tutorInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendTutor();
    }
  });
  const tutorInputEl = document.getElementById("tutorInput");
  if (tutorInputEl) {
    tutorInputEl.addEventListener("input", growTutorInput);
    tutorInputEl.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        sendTutor();
      }
    });
  }
  const toggleCopilotBtn = document.getElementById("toggleCopilot");
  if (toggleCopilotBtn) {
    toggleCopilotBtn.addEventListener("click", () => {
      const split = document.getElementById("readerSplit");
      if (split) {
        split.classList.toggle("copilot-collapsed");
        toggleCopilotBtn.innerHTML = split.classList.contains("copilot-collapsed") ? "&plus;" : "&minus;";
        toggleCopilotBtn.title = split.classList.contains("copilot-collapsed") ? "Expand AI Reading Tutor" : "Collapse AI Reading Tutor";
        setTimeout(() => {
          if (readerState.pdfDoc) showPdfPage(readerState.page || 1, null, null);
        }, 200);
      }
    });
  }
  document.getElementById("tutorPrompts").addEventListener("click", (event) => {
    const chip = event.target.closest("[data-quick]");
    if (!chip) return;
    document.getElementById("tutorInput").value = chip.dataset.quick;
    sendTutor();
  });
  document.getElementById("pillOpenai").addEventListener("click", () => setTutorProvider("openai"));
  document.getElementById("pillGemini").addEventListener("click", () => setTutorProvider("gemini"));
  const tabChatBtn = document.getElementById("tabCopilotChat");
  if (tabChatBtn) tabChatBtn.addEventListener("click", () => setCopilotTab("chat"));
  const tabOverviewBtn = document.getElementById("tabCopilotOverview");
  if (tabOverviewBtn) tabOverviewBtn.addEventListener("click", () => setCopilotTab("overview"));
  const miniContextSwitch = document.getElementById("miniContextSwitch");
  if (miniContextSwitch) miniContextSwitch.addEventListener("click", () => setCopilotTab("overview"));
  const btnAskFromOverview = document.getElementById("btnAskFromOverview");
  if (btnAskFromOverview) {
    btnAskFromOverview.addEventListener("click", () => {
      setCopilotTab("chat");
      const input = document.getElementById("tutorInput");
      if (input) input.focus();
    });
  }
  document.getElementById("readFlow").addEventListener("click", (event) => {
    const pass = event.target.closest("[data-pass]");
    if (pass) {
      activePass = Number(pass.dataset.pass) || 0;
      renderReadFlow();
      return;
    }
    const ask = event.target.closest("[data-flow-ask]");
    if (ask) {
      openAiModal(
        ask.dataset.flowLabel || "Step",
        ask.dataset.flowAsk || "",
        ask.dataset.flowTip || ""
      );
      return;
    }
    const step = event.target.closest("[data-flow-jump]");
    if (step) {
      const targetPage = Number(step.dataset.flowPage || 0) || jumpPage(step.dataset.flowJump);
      showPdfPage(targetPage, step.dataset.flowJump);
      if (window.innerWidth <= 960) setMobileReaderView("pdf");
      return;
    }
  });
  document.getElementById("aiModalClose").addEventListener("click", closeAiModal);
  document.getElementById("aiModalSend").addEventListener("click", sendFromModal);
  document.getElementById("aiStepModal").addEventListener("click", (event) => {
    if (event.target === event.currentTarget) closeAiModal();
  });
  document.getElementById("aiModalInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); sendFromModal(); }
    if (event.key === "Escape") closeAiModal();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      const modal = document.getElementById("aiStepModal");
      if (modal && !modal.hidden) { closeAiModal(); return; }
      if (!document.getElementById("readerPanel").hidden) closeReader();
    }
  });

  let resizeDebounce = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeDebounce);
    resizeDebounce = setTimeout(() => {
      const panel = document.getElementById("readerPanel");
      if (!panel || panel.hidden || !readerState.pdfDoc) return;
      const holder = document.getElementById("readerPdf");
      if (!holder) return;
      const clientW = holder.clientWidth || (document.getElementById("readerSplit") ? document.getElementById("readerSplit").clientWidth : 0) || window.innerWidth || 360;
      const wrapWidth = Math.max(260, clientW - 24);
      if (!readerState.pageWidth || Math.abs(wrapWidth - readerState.pageWidth) > 30) {
        showPdfPage(readerState.page || 1, null, null);
      }
    }, 250);
  });

  Promise.all([loadSettings(), loadLibrary()]).then(openFromQuery);
})();
