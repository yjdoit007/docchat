/* 문서 도우미 웹앱: 접속(로그인·가입·인증·비밀번호 찾기) + 대화(답변·출처 인라인 원문) */
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var MAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/, PW = /^(?=.*[A-Za-z])(?=.*\d).{8,}$/;
  var PW_MSG = "8자 이상, 영문과 숫자를 함께 써 주세요.";
  var CAVEAT = "답변은 요약이에요. 근거는 아래 인용문을 기준으로 확인해 주세요.";
  var store = {
    get: function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch (e) {} }
  };
  var token = store.get("docchat-token"), me = null, pending = {};

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // ---------- 서버 호출 ----------
  function api(method, path, body) {
    var opt = { method: method, headers: {} };
    if (token) opt.headers.Authorization = "Bearer " + token;
    if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
    return fetch(path, opt).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (r.status === 401 && token && path.indexOf("/api/auth/") !== 0) { logout("로그인이 만료됐어요. 다시 로그인해 주세요."); }
        if (!r.ok) { var e = new Error(data.detail || "요청을 처리하지 못했어요. 잠시 후 다시 시도해 주세요."); e.status = r.status; throw e; }
        return data;
      });
    }, function () { throw new Error("서버에 연결하지 못했어요. 서버가 켜져 있는지, 인터넷 연결을 확인해 주세요."); });
  }

  // ---------- 테마 ----------
  function theme(t) {
    document.documentElement.setAttribute("data-theme", t);
    document.querySelectorAll("[data-theme]").forEach(function (b) {
      if (b.tagName === "BUTTON") b.setAttribute("aria-pressed", b.dataset.theme === t);
    });
    store.set("docchat-theme", t);
  }
  document.addEventListener("click", function (e) {
    var b = e.target.closest("button[data-theme]");
    if (b) theme(b.dataset.theme);
  });
  theme(store.get("docchat-theme") || (window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));

  // ---------- 핸드폰 키보드 대응 ----------
  // 키보드가 올라오면 '보이는 높이'(visualViewport)에 맞춰 화면 높이를 줄이고, 페이지가 위로 밀리지 않게 되돌린다.
  // → 헤더는 맨 위, 입력창은 키보드 바로 위에 그대로 있고 대화 영역만 줄어든다.
  var vv = window.visualViewport;
  function fitViewport() {
    if (!document.documentElement.classList.contains("fixed-app")) return;
    var h = vv ? vv.height : window.innerHeight;
    document.documentElement.style.setProperty("--vvh", Math.round(h) + "px");
    if (window.scrollY || (vv && vv.offsetTop)) window.scrollTo(0, 0);
  }
  if (vv) { vv.addEventListener("resize", fitViewport); vv.addEventListener("scroll", fitViewport); }
  window.addEventListener("resize", fitViewport);
  function fixedApp(on) {
    document.documentElement.classList.toggle("fixed-app", on);
    if (on) fitViewport(); else document.documentElement.style.removeProperty("--vvh");
  }
  document.addEventListener("focusin", function (e) {
    if (e.target.id === "q") setTimeout(function () { fitViewport(); msgs.scrollTop = msgs.scrollHeight; }, 300);
  });

  // ---------- 접속 화면 ----------
  function go(id) {
    fixedApp(false);
    $("main").hidden = true; $("access").hidden = false;
    document.querySelectorAll(".scr").forEach(function (s) { s.classList.toggle("on", s.id === id); });
    var first = document.querySelector("#" + id + " input");
    if (first) setTimeout(function () { first.focus(); }, 0);
  }
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-go]");
    if (b) go(b.dataset.go);
  });
  function clearErr(ids) {
    ids.forEach(function (id) { $(id).removeAttribute("aria-invalid"); var e = $(id + "-e"); if (e) e.textContent = ""; });
  }
  function bad(id, msg) { $(id).setAttribute("aria-invalid", "true"); $(id + "-e").textContent = msg; }
  function busy(form, on) { form.querySelector(".pri").disabled = on; }
  function onSubmit(id, fn) {
    var f = document.querySelector("#" + id + " form");
    f.addEventListener("submit", function (e) { e.preventDefault(); fn(f); });
  }

  onSubmit("login", function (f) {
    var email = $("le").value.trim(), pw = $("lp").value;
    $("lerr").textContent = "";
    if (!MAIL.test(email) || !pw) { $("lerr").textContent = "이메일과 비밀번호를 입력해 주세요."; return; }
    busy(f, true);
    api("POST", "/api/auth/login", { email: email, password: pw }).then(function (r) {
      token = r.token; store.set("docchat-token", token); $("lp").value = "";
      start(r.user);
    }, function (err) {
      $("lerr").textContent = err.message;
      if (err.message.indexOf("이메일 인증") >= 0) { pending = { email: email }; }
    }).then(function () { busy(f, false); });
  });

  onSubmit("reg", function (f) {
    clearErr(["rn", "re", "rp", "rq"]); $("rerr").textContent = "";
    var name = $("rn").value.trim(), email = $("re").value.trim(), pw = $("rp").value, ok = true;
    if (!name) { bad("rn", "이름을 입력해 주세요."); ok = false; }
    if (!MAIL.test(email)) { bad("re", "이메일 형식을 확인해 주세요. 예: name@example.com"); ok = false; }
    if (!PW.test(pw)) { bad("rp", PW_MSG); ok = false; }
    if ($("rq").value !== pw) { bad("rq", "비밀번호가 서로 달라요. 다시 확인해 주세요."); ok = false; }
    if (!ok) return;
    busy(f, true);
    api("POST", "/api/auth/register", { name: name, email: email, password: pw }).then(function () {
      pending = { name: name, email: email };
      $("vm").textContent = email; $("vc").value = ""; $("vn").textContent = "";
      go("ver");
    }, function (err) { $("rerr").textContent = err.message; }).then(function () { busy(f, false); });
  });

  onSubmit("ver", function (f) {
    clearErr(["vc"]);
    var code = $("vc").value.trim();
    if (!/^\d{6}$/.test(code)) { bad("vc", "메일로 받은 6자리 숫자를 입력해 주세요."); return; }
    busy(f, true);
    api("POST", "/api/auth/verify-email", { email: pending.email, code: code }).then(function () {
      $("wsum").textContent = (pending.name ? pending.name + " · " : "") + pending.email;
      go("wait");
    }, function (err) { bad("vc", err.message); }).then(function () { busy(f, false); });
  });
  $("vre").addEventListener("click", function () {
    api("POST", "/api/auth/resend", { email: pending.email }).then(function () {
      $("vn").textContent = "코드를 다시 보냈어요. 이전 코드는 사용할 수 없어요.";
    }, function (err) { $("vn").textContent = err.message; });
  });

  onSubmit("rs1", function (f) {
    clearErr(["pe"]);
    var email = $("pe").value.trim();
    if (!MAIL.test(email)) { bad("pe", "이메일 형식을 확인해 주세요. 예: name@example.com"); return; }
    busy(f, true);
    api("POST", "/api/auth/forgot", { email: email }).then(function () {
      pending = { email: email }; $("pc").value = ""; go("rs2");
    }, function (err) { bad("pe", err.message); }).then(function () { busy(f, false); });
  });

  onSubmit("rs2", function (f) {
    clearErr(["pc", "pn", "pq"]); $("perr").textContent = "";
    var code = $("pc").value.trim(), pw = $("pn").value, ok = true;
    if (!/^\d{6}$/.test(code)) { bad("pc", "메일로 받은 6자리 숫자를 입력해 주세요."); ok = false; }
    if (!PW.test(pw)) { bad("pn", PW_MSG); ok = false; }
    if ($("pq").value !== pw) { bad("pq", "비밀번호가 서로 달라요. 다시 확인해 주세요."); ok = false; }
    if (!ok) return;
    busy(f, true);
    api("POST", "/api/auth/reset", { email: pending.email, code: code, new_password: pw }).then(function () {
      $("pn").value = $("pq").value = ""; go("rs3");
    }, function (err) { $("perr").textContent = err.message; }).then(function () { busy(f, false); });
  });

  // ---------- 로그인 상태 ----------
  function logout(msg) {
    token = null; me = null; store.set("docchat-token", null);
    go("login");
    if (msg) $("lerr").textContent = msg;
  }
  $("logout").addEventListener("click", function () { $("whopop").hidden = true; logout(); });
  $("who").addEventListener("click", function () {
    var p = $("whopop"); p.hidden = !p.hidden; this.setAttribute("aria-expanded", !p.hidden);
  });
  document.addEventListener("click", function (e) {
    if (!e.target.closest("#who, #whopop")) { $("whopop").hidden = true; $("who").setAttribute("aria-expanded", "false"); }
  });

  function start(user) {
    me = user;
    $("access").hidden = true; $("main").hidden = false;
    fixedApp(true);
    $("who").textContent = (user.name || "?").slice(0, 1);
    $("who").setAttribute("aria-label", user.name + " 계정 메뉴");
    $("whoinfo").innerHTML = "<b>" + esc(user.name) + "</b><small>" + esc(user.email) + " · " +
      (user.role === "admin" ? "관리자" : "사용자") + "</small>";
    $("toadmin").hidden = user.role !== "admin";
    showChat();
    loadDocs();
    loadModes();
    if (user.role === "admin") refreshPending();
  }

  // ---------- 문서 목록과 기준 버전 ----------
  var DOCS = [];
  function loadDocs() {
    api("GET", "/api/docs").then(function (docs) {
      DOCS = docs;
      var cur = [], old = [], dates = {};
      docs.forEach(function (d) {
        d.versions.forEach(function (v, i) {
          var day = v.uploaded_at.slice(0, 10);
          dates[day] = true;
          var row = '<button class="doc" type="button" data-vid="' + v.id + '" data-kind="' + esc(v.kind || "pdf") + '" title="' + fileLink(v.kind) + '">' + esc(d.title) +
            (i ? " v" + v.version : "") + "<small>v" + v.version + " · " + day + " · " + kindLabel(v) + "</small></button>";
          (i ? old : cur).push(row);
        });
      });
      $("docs").innerHTML = cur.join("") || '<div class="side-empty">등록된 문서가 없어요.</div>';
      $("olds").innerHTML = old.join("") || '<div class="side-empty">없음</div>';
      var sel = $("asof"), keep = sel.value;
      sel.innerHTML = '<option value="">최신 버전 기준</option>' + Object.keys(dates).sort().reverse().map(function (d) {
        return '<option value="' + d + '">' + d + " 버전 기준</option>";
      }).join("");
      sel.value = keep;
    }, function () {});
  }
  document.addEventListener("click", function (e) {
    var b = e.target.closest(".doc[data-vid], [data-pdf]");
    if (b) openFile(b.dataset.vid || b.dataset.pdf, b.dataset.page, b.dataset.kind);
  });
  function isPdf(kind) { return !kind || kind === "pdf"; }
  function kindLabel(v) { return isPdf(v.kind) ? (v.page_count + "쪽") : "한글 문서"; }
  function fileLink(kind) { return isPdf(kind) ? "원본 PDF 보기" : "원본 한글 파일 받기"; }
  // PDF는 새 탭에서 해당 페이지로 열고, 한글 파일은 내려받는다 (브라우저가 한글 파일을 바로 보여 주지 못함)
  function openFile(vid, page, kind) {
    var w = isPdf(kind) ? window.open("", "_blank") : null;
    fetch("/api/versions/" + vid + "/file", { headers: { Authorization: "Bearer " + token } })
      .then(function (r) {
        if (!r.ok) throw new Error();
        var cd = r.headers.get("Content-Disposition") || "", m = /filename\*=UTF-8''([^;]+)|filename="?([^";]+)/i.exec(cd);
        var name = m ? decodeURIComponent(m[1] || m[2]) : "document";
        return r.blob().then(function (b) { return { b: b, name: name }; });
      })
      .then(function (f) {
        var url = URL.createObjectURL(f.b);
        if (isPdf(kind)) { url += page ? "#page=" + page : ""; if (w) w.location.href = url; else location.href = url; return; }
        var a = document.createElement("a"); a.href = url; a.download = f.name;
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(function () { URL.revokeObjectURL(url); }, 10000);
      }, function () { if (w) w.close(); alert("원본 파일을 열지 못했어요."); });
  }

  // ---------- 질문과 답변 ----------
  var msgs = $("msgs"), n = 0;
  function add(html, cls) {
    var d = document.createElement("div");
    d.className = "m " + cls; d.innerHTML = html;
    msgs.appendChild(d); msgs.scrollTop = msgs.scrollHeight;
    return d;
  }
  // ---------- 답변 모드 (⚡ 빠름 Gemini / 🔒 내부 로컬) ----------
  var MODES = [], mode = null;
  function loadModes() {
    api("GET", "/api/modes").then(function (r) {
      MODES = r.modes || [];
      var saved = store.get("docchat-mode");
      mode = MODES.some(function (m) { return m.id === saved; }) ? saved : r.default;
      var box = $("modes");
      box.hidden = MODES.length < 2;  // 고를 게 하나뿐이면 숨긴다
      box.innerHTML = MODES.map(function (m) {
        return '<button type="button" role="radio" data-mode="' + esc(m.id) + '" aria-checked="' + (m.id === mode) + '" title="' + esc(m.note) + '">' + esc(m.label) + "</button>";
      }).join("") + '<span class="mode-note" id="modenote"></span>';
      modeNote();
    }, function () {});
  }
  function modeNote() {
    var m = MODES.filter(function (x) { return x.id === mode; })[0];
    if ($("modenote")) $("modenote").textContent = m ? m.note : "";
  }
  $("modes").addEventListener("click", function (e) {
    var b = e.target.closest("[data-mode]");
    if (!b) return;
    mode = b.dataset.mode; store.set("docchat-mode", mode);
    this.querySelectorAll("[data-mode]").forEach(function (x) { x.setAttribute("aria-checked", x === b); });
    modeNote();
  });

  // 답을 쓰는 대로 받기 (Server-Sent Events: delta · reset · done · error)
  function askStream(body, on) {
    return fetch("/api/ask/stream", {
      method: "POST", body: JSON.stringify(body),
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + token }
    }).then(function (r) {
      if (!r.ok) {
        return r.json().catch(function () { return {}; }).then(function (d) {
          if (r.status === 401) logout("로그인이 만료됐어요. 다시 로그인해 주세요.");
          throw new Error(d.detail || "질문을 보내지 못했어요.");
        });
      }
      var reader = r.body.getReader(), dec = new TextDecoder(), buf = "";
      function pump() {
        return reader.read().then(function (x) {
          if (x.done) return;
          buf += dec.decode(x.value, { stream: true });
          var i;
          while ((i = buf.indexOf("\n\n")) >= 0) {
            var block = buf.slice(0, i); buf = buf.slice(i + 2);
            var ev = (/^event: (.*)$/m.exec(block) || [])[1], data = (/^data: (.*)$/m.exec(block) || [])[1];
            if (ev && data) on(ev, JSON.parse(data));
          }
          return pump();
        });
      }
      return pump();
    }, function () { throw new Error("서버에 연결하지 못했어요. 서버가 켜져 있는지, 인터넷 연결을 확인해 주세요."); });
  }

  $("ask").addEventListener("submit", function (e) {
    e.preventDefault();
    var q = $("q").value.trim();
    if (!q) return;
    var asof = $("asof").value || null, send = this.querySelector(".send");
    var slow = mode === "ollama";
    add(esc(q), "me");
    $("q").value = ""; send.disabled = true;
    var t0 = Date.now(), box = add('<div class="thinking"><span class="dot" aria-hidden="true"></span><span>근거를 찾고 답을 쓰는 중이에요… <span class="sec">0</span>초</span></div>' +
      (slow ? '<div class="hint">내부(로컬) 모드는 답이 나오기 시작할 때까지 20초쯤 걸려요.</div>' : "") +
      '<p class="ans live" hidden></p>', "bot");
    var timer = setInterval(function () { var s = box.querySelector(".sec"); if (s) s.textContent = Math.round((Date.now() - t0) / 1000); }, 1000);
    var finished = false;
    askStream({ question: q, as_of: asof, mode: mode }, function (ev, d) {
      var live = box.querySelector(".ans.live");
      if (ev === "delta" && live) {
        live.hidden = false; live.textContent += d.text;
        var th = box.querySelector(".thinking span:last-child");
        if (th) th.firstChild.textContent = "답을 쓰는 중이에요… ";
        msgs.scrollTop = msgs.scrollHeight;
      } else if (ev === "reset" && live) {
        live.textContent = ""; live.hidden = true;
        var hint = box.querySelector(".hint") || box.insertBefore(document.createElement("div"), live);
        hint.className = "hint"; hint.textContent = d.note;
      } else if (ev === "done") {
        finished = true; renderAnswer(box, d);
      } else if (ev === "error") {
        finished = true; box.innerHTML = '<p class="warn">' + esc(d.detail) + "</p>";
      }
    }).then(function () {
      if (!finished) box.innerHTML = '<p class="warn">답을 받는 중에 연결이 끊겼어요. 다시 질문해 주세요.</p>';
    }, function (err) {
      box.innerHTML = '<p class="warn">' + esc(err.message) + "</p>";
    }).then(function () { clearInterval(timer); send.disabled = false; $("q").focus(); });
  });

  // 문서가 여러 개면 공통 앞부분을 뺀 짧은 이름을 붙인다 (예: '…보험법', '시행령', '시행규칙')
  function shortNames(titles) {
    var pre = titles.reduce(function (a, t) { var i = 0; while (i < a.length && a[i] === t[i]) i++; return a.slice(0, i); });
    var out = {};
    titles.forEach(function (t) { out[t] = titles.length > 1 ? (t.slice(pre.length).trim() || t) : ""; });
    return out;
  }
  function chipLabel(s, names) {
    var art = (s.article || "").replace(/\(.*\)$/, "");
    return ((names[s.doc] ? names[s.doc] + " " : "") + art + (s.page ? " p." + s.page : "")).trim();
  }

  function renderAnswer(box, r) {
    var id = "a" + (++n), html = "";
    var sources = r.sources || [], also = r.also || [];
    var titles = {}; sources.concat(also).forEach(function (s) { titles[s.doc] = 1; });
    var names = Object.keys(titles).length ? shortNames(Object.keys(titles)) : {};
    var hasDocs = sources.length || also.length;
    box.classList.toggle("unverified", !r.verified && !!hasDocs);
    if (!r.verified && hasDocs) {
      html += '<p class="warn">⚠ 확인된 원문 인용이 없어요. 아래 관련 조문을 직접 확인해 주세요.</p>';
    }
    html += '<p class="ans">' + esc(r.answer) + "</p>";
    var items = [];
    var cited = sources.filter(function (s) { return s.quotes && s.quotes.length; });
    var uncited = sources.filter(function (s) { return !s.quotes || !s.quotes.length; }).concat(also);
    if (cited.length) {
      html += '<div class="chips-label">근거 조문 (누르면 원문이 보여요)</div><div class="chips">' + cited.map(function (s) {
        items.push(s);
        return '<button type="button" aria-pressed="false" data-k="' + (items.length - 1) + '" title="' + esc(s.doc + " v" + s.version + " " + (s.article || "")) + '">' +
          esc(chipLabel(s, names)) + "</button>";
      }).join("") + "</div>";
    }
    if (uncited.length) {
      html += '<div class="chips-label">관련 조문</div><div class="chips also">' + uncited.map(function (s) {
        items.push(s);
        return '<button type="button" aria-pressed="false" data-k="' + (items.length - 1) + '" title="' + esc(s.doc + " v" + s.version + " " + (s.article || "")) + '">' +
          esc(chipLabel(s, names)) + "</button>";
      }).join("") + "</div>";
    }
    html += '<div class="src" id="' + id + '-src" hidden></div>';
    if (r.note) html += '<div class="hint">' + esc(r.note) + "</div>";
    if (hasDocs) html += '<div class="caveat">' + CAVEAT + (r.as_of ? " · " + esc(r.as_of) + " 버전 기준" : "") +
      (r.mode_label ? " · " + esc(r.mode_label) + "로 작성" : "") + "</div>";
    box.innerHTML = html;
    var open = -1;
    box.querySelectorAll(".chips button").forEach(function (b) {
      b.addEventListener("click", function () {
        var k = +b.dataset.k, src = $(id + "-src");
        box.querySelectorAll(".chips button").forEach(function (x) { x.setAttribute("aria-pressed", "false"); });
        if (open === k) { open = -1; src.hidden = true; return; }
        open = k; b.setAttribute("aria-pressed", "true");
        showSource(src, items[k]);
      });
    });
    // 확인된 인용이 있으면 첫 근거를 펼쳐서 보여 준다
    var first = box.querySelector(".chips:not(.also) button");
    if (first) first.click();
    msgs.scrollTop = msgs.scrollHeight;
  }

  function highlight(text, quotes) {
    var html = esc(text);
    (quotes || []).forEach(function (q) {
      var pat = esc(q).replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/ +/g, "\\s+");
      try { html = html.replace(new RegExp(pat), function (m) { return "<mark>" + m + "</mark>"; }); } catch (e) {}
    });
    return html;
  }

  function showSource(el, s) {
    el.hidden = false;
    el.innerHTML = '<div class="bar"><b>' + esc(s.doc) + "</b><span class=\"badge\">v" + s.version + " · " + esc(s.article || "") + "</span></div>" +
      '<div class="sheet">불러오는 중…</div>';
    api("GET", "/api/versions/" + s.version_id + "/article?label=" + encodeURIComponent(s.article)).then(function (a) {
      var pages = a.page_from == null ? "한글 문서" : a.page_from === a.page_to ? a.page_from + "쪽" : a.page_from + "~" + a.page_to + "쪽";
      el.innerHTML = '<div class="bar"><b>' + esc(a.title) + '</b><span class="badge">v' + a.version + " · " + esc(a.uploaded_at.slice(0, 10)) + " 등록 · " + pages + "</span>" +
        '<button type="button" class="lnk" data-pdf="' + s.version_id + '" data-kind="' + esc(a.kind || "pdf") + '" data-page="' + (a.page_from || "") + '">' + fileLink(a.kind) + "</button></div>" +
        '<div class="sheet">' + highlight(a.text, s.quotes) +
        '<div class="no">' + (s.quotes && s.quotes.length ? "형광펜: 원문과 대조해 확인된 인용" : "관련 조문 (확인된 인용 없음)") + "</div></div>";
      el.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }, function (err) { el.querySelector(".sheet").textContent = err.message; });
  }

  // ---------- 관리자 화면 ----------
  var ACTION = { approve: "가입 승인", reject: "가입 거절", grant_admin: "관리자 지정", revoke_admin: "관리자 해제", set_public_url: "서버 주소 변경", set_llm: "답변 모델 변경" };
  var PROVIDER = { gemini: "Gemini", ollama: "로컬", claude: "Claude" };
  function day(t) { return t ? t.slice(0, 16) : ""; }
  function showChat() {
    $("admin").hidden = true; document.querySelector(".chat").hidden = false;
    $("q").focus();
  }
  function showAdmin() {
    $("whopop").hidden = true;
    document.querySelector(".chat").hidden = true; $("admin").hidden = false;
    openTab(document.querySelector(".tabs [aria-selected=true]").dataset.tab);
  }
  $("toadmin").addEventListener("click", showAdmin);
  $("tochat").addEventListener("click", function () { loadDocs(); showChat(); });
  document.querySelectorAll(".tabs button").forEach(function (b) {
    b.addEventListener("click", function () { openTab(b.dataset.tab); });
  });
  function openTab(id) {
    document.querySelectorAll(".tabs button").forEach(function (b) { b.setAttribute("aria-selected", b.dataset.tab === id); });
    document.querySelectorAll(".adm .tab").forEach(function (t) { t.classList.toggle("on", t.id === id); });
    ({ "t-docs": loadDocList, "t-join": loadJoin, "t-members": loadMembers, "t-logs": loadLogs, "t-audit": loadAudit, "t-set": loadSettings })[id]();
  }
  function fail(el) { return function (err) { el.innerHTML = '<div class="empty">' + esc(err.message) + "</div>"; }; }

  // 문서
  function loadDocList() {
    var el = $("doclist");
    api("GET", "/api/docs").then(function (docs) {
      DOCS = docs;
      $("titles").innerHTML = docs.map(function (d) { return '<option value="' + esc(d.title) + '">'; }).join("");
      el.innerHTML = docs.length ? docs.map(function (d) {
        var v = d.versions[0];
        return '<div class="row"><div class="who"><b>' + esc(d.title) + "</b><small>최신 v" + v.version + " · " + day(v.uploaded_at) + " · " + kindLabel(v) + "</small>" +
          '<div class="vers">' + d.versions.map(function (x) {
            return '<button type="button" data-pdf="' + x.id + '" data-kind="' + esc(x.kind || "pdf") + '" title="' + fileLink(x.kind) + '">v' + x.version + " · " + x.uploaded_at.slice(0, 10) +
              (isPdf(x.kind) ? "" : " · 한글") + "</button>";
          }).join("") + "</div></div></div>";
      }).join("") : '<div class="empty">등록된 문서가 없어요. 위에서 첫 문서를 올려 주세요.</div>';
    }, fail(el));
  }
  $("upform").addEventListener("submit", function (e) {
    e.preventDefault();
    var f = this, title = $("uptitle").value.trim(), file = $("upfile").files[0], msg = $("upmsg");
    clearErr(["uptitle", "upfile"]); msg.textContent = ""; msg.className = "note";
    if (!title) { bad("uptitle", "문서 이름을 입력해 주세요."); return; }
    if (!file) { bad("upfile", "올릴 PDF 파일을 골라 주세요."); return; }
    if (!/\.(pdf|hwp|hwpx)$/i.test(file.name)) { bad("upfile", "PDF 또는 한글(HWP, HWPX) 파일만 올릴 수 있어요."); return; }
    var fd = new FormData(); fd.append("title", title); fd.append("file", file);
    busy(f, true); msg.textContent = "올리는 중이에요… 글자를 읽고 조문 단위로 나누는 데 몇 초 걸려요.";
    fetch("/api/docs", { method: "POST", headers: { Authorization: "Bearer " + token }, body: fd })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { if (!r.ok) throw new Error(d.detail || "올리지 못했어요."); return d; }); })
      .then(function (d) {
        msg.className = "note ok";
        msg.textContent = "올렸어요: " + d.document + " v" + d.version + " (" + (d.pages ? d.pages + "쪽" : "한글 문서") + ", 검색 조각 " + d.chunks + "개)";
        $("upfile").value = ""; loadDocList(); loadDocs();
      }, function (err) { msg.className = "note bad"; msg.textContent = err.message; })
      .then(function () { busy(f, false); });
  });

  // 가입 승인
  var joinFilter = "pending";
  document.querySelectorAll("#joinseg button").forEach(function (b) {
    b.addEventListener("click", function () {
      joinFilter = b.dataset.f;
      document.querySelectorAll("#joinseg button").forEach(function (x) { x.setAttribute("aria-pressed", x === b); });
      loadJoin();
    });
  });
  function refreshPending() {
    api("GET", "/api/admin/users?status=pending").then(function (l) { $("pendcnt").textContent = l.length || ""; }, function () {});
  }
  function loadJoin() {
    var el = $("joinlist");
    refreshPending();
    api("GET", "/api/admin/users?status=" + joinFilter).then(function (list) {
      el.innerHTML = list.length ? list.map(function (u) {
        var acts = joinFilter === "pending" ? '<button class="a" data-u="' + u.id + '" data-act="approve">승인</button><button data-u="' + u.id + '" data-act="reject">거절</button>'
          : joinFilter === "rejected" ? '<button data-u="' + u.id + '" data-act="approve">다시 승인</button>'
          : '<button class="d" data-u="' + u.id + '" data-act="reject">승인 취소</button>';
        return '<div class="row"><div class="who"><b>' + esc(u.name) + "</b><small>" + esc(u.email) + " · 가입 " + day(u.created_at) + "</small></div>" +
          '<div class="acts">' + acts + "</div></div>";
      }).join("") : '<div class="empty">' + (joinFilter === "pending" ? "대기 중인 요청이 없어요." : "해당하는 사람이 없어요.") + "</div>";
      el.querySelectorAll("[data-act]").forEach(function (b) {
        b.addEventListener("click", function () {
          if (b.dataset.act === "reject" && joinFilter === "approved" && !confirm("승인을 취소하면 이 사람은 더 이상 로그인할 수 없어요. 계속할까요?")) return;
          b.disabled = true;
          api("POST", "/api/admin/users/" + b.dataset.u + "/" + b.dataset.act).then(loadJoin, function (err) { alert(err.message); b.disabled = false; });
        });
      });
    }, fail(el));
  }

  // 사용자·관리자
  function loadMembers() {
    var el = $("memberlist");
    api("GET", "/api/admin/members").then(function (list) {
      var admins = list.filter(function (u) { return u.role === "admin"; }).length;
      el.innerHTML = list.map(function (u) {
        var isAdmin = u.role === "admin", self = me && u.email === me.email;
        var info = esc(u.email) + " · 가입 " + day(u.created_at) +
          (isAdmin ? (u.granted_by ? " · " + esc(u.granted_by) + "님이 " + day(u.granted_at) + "에 지정" : " · 처음 설정된 관리자") : "");
        var btn = isAdmin
          ? '<button class="d" data-u="' + u.id + '" data-name="' + esc(u.name) + '" data-self="' + (self ? 1 : "") + '" data-role="viewer"' + (admins <= 1 ? ' disabled title="마지막 관리자는 해제할 수 없어요"' : "") + ">관리자 해제</button>"
          : '<button data-u="' + u.id + '" data-name="' + esc(u.name) + '" data-role="admin">관리자로 지정</button>';
        return '<div class="row"><div class="who"><b>' + esc(u.name) + (self ? " (나)" : "") + ' <span class="tag ' + (isAdmin ? 'am">관리자' : 'ok">사용자') + "</span></b><small>" + info + "</small></div>" +
          '<div class="acts">' + btn + "</div></div>";
      }).join("") || '<div class="empty">승인된 사용자가 없어요.</div>';
      el.querySelectorAll("[data-role]").forEach(function (b) {
        b.addEventListener("click", function () {
          var name = b.dataset.name;
          var toAdmin = b.dataset.role === "admin";
          var self = !!b.dataset.self;
          var q = toAdmin ? name + "님을 관리자로 지정할까요? 문서 업로드, 가입 승인, 질문 기록 보기 등 모든 관리 기능을 쓸 수 있게 돼요."
            : self ? "내 관리자 권한을 해제할까요? 해제하면 이 화면을 더 이상 쓸 수 없어요." : name + "님의 관리자 권한을 해제할까요?";
          if (!confirm(q)) return;
          b.disabled = true;
          api("POST", "/api/admin/members/" + b.dataset.u + "/role", { role: b.dataset.role }).then(function () {
            if (self && !toAdmin) { me.role = "viewer"; $("toadmin").hidden = true; showChat(); return; }
            loadMembers();
          }, function (err) { alert(err.message); b.disabled = false; });
        });
      });
    }, fail(el));
  }

  // 질문 기록
  function loadLogs() {
    var el = $("loglist");
    api("GET", "/api/admin/logs?limit=200").then(function (list) {
      el.innerHTML = list.length ? list.map(function (l) {
        var src = (l.sources || []).map(function (s) { return esc(s.doc + " v" + s.version + " " + (s.article || "p." + s.page)) + (s.quotes && s.quotes.length ? "" : " (인용 미확인)"); }).join("<br>");
        return '<div class="log"><div class="meta"><span>' + day(l.created_at) + "</span><span>" + esc(l.name) + "</span>" +
          (l.as_of ? "<span>" + esc(l.as_of) + " 기준</span>" : "") +
          (l.provider ? "<span>" + esc(PROVIDER[l.provider] || l.provider) + "</span>" : "") +
          '<span class="tag ' + (l.verified ? 'ok">인용 확인' : 'no">인용 없음') + "</span></div>" +
          '<div class="q">' + esc(l.question) + "</div>" +
          "<details><summary>답변과 출처 보기</summary><p>" + esc(l.answer) + "</p><p>" + (src || "출처 없음") + "</p></details></div>";
      }).join("") : '<div class="empty">아직 질문 기록이 없어요.</div>';
    }, fail(el));
  }

  // 작업 기록
  function loadAudit() {
    var el = $("auditlist");
    api("GET", "/api/admin/audit").then(function (list) {
      el.innerHTML = list.length ? list.map(function (a) {
        return '<div class="log"><div class="meta"><span>' + day(a.created_at) + "</span><span>" + esc(a.actor || "?") + "</span></div>" +
          '<div class="q">' + esc(ACTION[a.action] || a.action) + (a.target ? " · " + esc(a.target) + " (" + esc(a.target_email) + ")" : "") + (a.detail ? " · " + esc(a.detail) : "") + "</div></div>";
      }).join("") : '<div class="empty">아직 기록이 없어요.</div>';
    }, fail(el));
  }

  // 서버 설정
  function drawLlm(l) {
    var order = ["gemini", "ollama", "claude"];
    $("llmlist").innerHTML = order.map(function (m) {
      var ok = l.available[m], on = l.allowed.indexOf(m) >= 0;
      return '<div class="row"><div class="who"><b>' + esc(l.labels[m]) + "</b><small>" + esc(l.notes[m]) +
        (ok ? "" : " · <span class=\"tag no\">.env에 키가 없어 쓸 수 없어요</span>") + "</small></div>" +
        '<div class="acts"><label class="chk"><input type="checkbox" data-allow="' + m + '"' + (on ? " checked" : "") + (ok ? "" : " disabled") + "> 허용</label>" +
        '<label class="chk"><input type="radio" name="llmdef" value="' + m + '"' + (l.default === m ? " checked" : "") + (ok ? "" : " disabled") + "> 기본</label></div></div>";
    }).join("");
  }
  $("llmform").addEventListener("submit", function (e) {
    e.preventDefault();
    var allowed = [].map.call(document.querySelectorAll("[data-allow]:checked"), function (x) { return x.dataset.allow; });
    var def = (document.querySelector("[name=llmdef]:checked") || {}).value;
    $("llm-e").textContent = ""; $("llmmsg").textContent = "";
    api("PUT", "/api/admin/llm", { default: def || "", allowed: allowed }).then(function () {
      $("llmmsg").className = "note ok"; $("llmmsg").textContent = "저장했어요. 팀원 화면은 새로 고침하면 바뀌어요.";
      loadModes();
    }, function (err) { $("llm-e").textContent = err.message; });
  });
  function loadSettings() {
    api("GET", "/api/admin/settings").then(function (s) {
      if (s.llm) drawLlm(s.llm);
      $("pubu").value = s.public_url || location.origin;
      var d = s.ddns || {};
      $("pubinfo").textContent = (s.updated_at ? "마지막 변경: " + day(s.updated_at) + " · " + (s.updated_by || "") : "아직 저장한 적 없어요. 지금 보이는 값은 이 화면을 연 주소예요.") +
        (d.domain ? " · 주소 자동 갱신(" + d.domain + "): " + (d.ok == null ? "확인 중" : d.ok ? "정상 (" + day(d.last_at) + ")" : "실패 — .env의 DuckDNS 설정을 확인해 주세요") : " · 주소 자동 갱신: 설정 안 됨");
    }, function (err) { $("pubinfo").textContent = err.message; });
  }
  $("setform").addEventListener("submit", function (e) {
    e.preventDefault();
    clearErr(["pubu"]);
    var v = $("pubu").value.trim();
    if (!/^https?:\/\/\S+$/.test(v)) { bad("pubu", "http:// 또는 https:// 로 시작하는 주소를 입력해 주세요."); return; }
    api("PUT", "/api/admin/settings", { public_url: v }).then(function () {
      loadSettings(); $("pubinfo").textContent = "저장했어요. 팀원에게 새 주소를 알려 주세요.";
    }, function (err) { bad("pubu", err.message); });
  });

  // ---------- 시작 ----------
  if (token) {
    api("GET", "/api/me").then(start, function () { logout(); });
  } else {
    go("login");
  }
})();
