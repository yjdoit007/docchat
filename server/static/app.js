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

  // ---------- 접속 화면 ----------
  function go(id) {
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
    $("who").textContent = (user.name || "?").slice(0, 1);
    $("who").setAttribute("aria-label", user.name + " 계정 메뉴");
    $("whoinfo").innerHTML = "<b>" + esc(user.name) + "</b><small>" + esc(user.email) + " · " +
      (user.role === "admin" ? "관리자" : "사용자") + "</small>";
    loadDocs();
    $("q").focus();
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
          var row = '<button class="doc" type="button" data-vid="' + v.id + '" title="원본 PDF 열기">' + esc(d.title) +
            (i ? " v" + v.version : "") + "<small>v" + v.version + " · " + day + " · " + v.page_count + "쪽</small></button>";
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
    if (b) openPdf(b.dataset.vid || b.dataset.pdf, b.dataset.page);
  });
  function openPdf(vid, page) {
    var w = window.open("", "_blank");
    fetch("/api/versions/" + vid + "/file", { headers: { Authorization: "Bearer " + token } })
      .then(function (r) { if (!r.ok) throw new Error(); return r.blob(); })
      .then(function (b) {
        var url = URL.createObjectURL(b) + (page ? "#page=" + page : "");
        if (w) w.location.href = url; else location.href = url;
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
  $("ask").addEventListener("submit", function (e) {
    e.preventDefault();
    var q = $("q").value.trim();
    if (!q) return;
    var asof = $("asof").value || null, send = this.querySelector(".send");
    add(esc(q), "me");
    $("q").value = ""; send.disabled = true;
    var t0 = Date.now(), box = add('<div class="thinking"><span class="dot" aria-hidden="true"></span><span>근거를 찾고 답을 쓰는 중이에요… <span class="sec">0</span>초</span></div>' +
      '<div class="hint">이 서버는 답변에 30초~1분쯤 걸려요.</div>', "bot");
    var timer = setInterval(function () { var s = box.querySelector(".sec"); if (s) s.textContent = Math.round((Date.now() - t0) / 1000); }, 1000);
    api("POST", "/api/ask", { question: q, as_of: asof }).then(function (r) {
      renderAnswer(box, r);
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
    if (hasDocs) html += '<div class="caveat">' + CAVEAT + (r.as_of ? " · " + esc(r.as_of) + " 버전 기준" : "") + "</div>";
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
      var pages = a.page_from === a.page_to ? a.page_from + "쪽" : a.page_from + "~" + a.page_to + "쪽";
      el.innerHTML = '<div class="bar"><b>' + esc(a.title) + '</b><span class="badge">v' + a.version + " · " + esc(a.uploaded_at.slice(0, 10)) + " 등록 · " + pages + "</span>" +
        '<button type="button" class="lnk" data-pdf="' + s.version_id + '" data-page="' + a.page_from + '">원본 PDF 보기</button></div>' +
        '<div class="sheet">' + highlight(a.text, s.quotes) +
        '<div class="no">' + (s.quotes && s.quotes.length ? "형광펜: 원문과 대조해 확인된 인용" : "관련 조문 (확인된 인용 없음)") + "</div></div>";
      el.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }, function (err) { el.querySelector(".sheet").textContent = err.message; });
  }

  // ---------- 시작 ----------
  if (token) {
    api("GET", "/api/me").then(start, function () { logout(); });
  } else {
    go("login");
  }
})();
