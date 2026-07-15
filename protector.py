import re
import base64
import random
import string
import json
import logging

logger = logging.getLogger(__name__)


class HTMLProtector:
    """3-layer HTML protection: Minify → Encrypt → DevTools block."""

    def __init__(self):
        self._key = "".join(
            random.choices(string.ascii_letters + string.digits, k=32)
        )

    # ═══════════════════════════════════════════
    #  PUBLIC
    # ═══════════════════════════════════════════

    def protect(self, html: str) -> str:
        html = self._layer1(html)
        logger.info("✓ Layer 1 — Minify + Obfuscate")
        html = self._layer2(html)
        logger.info("✓ Layer 2 — XOR Encrypt")
        html = self._layer3(html)
        logger.info("✓ Layer 3 — DevTools Block")
        return html

    # ═══════════════════════════════════════════
    #  UTILS
    # ═══════════════════════════════════════════

    @staticmethod
    def _rvar() -> str:
        n = random.randint(8, 16)
        return "_0x" + "".join(random.choices("lI1O0", k=n))

    @staticmethod
    def _b64(s: str) -> str:
        return base64.b64encode(s.encode("utf-8")).decode("ascii")

    @staticmethod
    def _xor(text: str, key: str) -> str:
        tb = text.encode("utf-8")
        kb = key.encode("utf-8")
        enc = bytes([b ^ kb[i % len(kb)] for i, b in enumerate(tb)])
        return base64.b64encode(enc).decode("ascii")

    def _obf(self, js: str) -> str:
        """JS obfuscate: base64 → split chunks → eval(atob(join))"""
        if not js or not js.strip():
            return js
        try:
            b64 = self._b64(js)
            sz = random.randint(12, 24)
            chunks = [b64[i: i + sz] for i in range(0, len(b64), sz)]
            va, vb = self._rvar(), self._rvar()
            return (
                f"(function(){{"
                f"var {va}={json.dumps(chunks)};"
                f"var {vb}={va}.join('');"
                f"eval(atob({vb}));"
                f"}})();"
            )
        except Exception as e:
            logger.warning(f"Obfuscation fallback: {e}")
            b64 = self._b64(js)
            return f'eval(atob("{b64}"));'

    # ═══════════════════════════════════════════
    #  LAYER 1 — MINIFY + OBFUSCATE
    # ═══════════════════════════════════════════

    def _min_css(self, css: str) -> str:
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
        css = re.sub(r"\s*([{}:;,>~+@])\s*", r"\1", css)
        css = re.sub(r"\s+", " ", css)
        css = css.replace(";}", "}")
        return css.strip()

    def _min_html(self, html: str) -> str:
        # Remove HTML comments (keep IE conditionals)
        html = re.sub(r"<!--(?!\s*\[if).*?-->", "", html, flags=re.DOTALL)
        # Collapse whitespace between tags
        html = re.sub(r">\s+<", "><", html)
        html = re.sub(r"\s{2,}", " ", html)
        html = html.replace("\n", "").replace("\r", "").replace("\t", " ")
        return html.strip()

    def _layer1(self, html: str) -> str:
        # Minify <style> blocks
        def do_style(m):
            return f"<style{m.group(1)}>{self._min_css(m.group(2))}</style>"

        html = re.sub(
            r"<style([^>]*)>(.*?)</style>",
            do_style, html,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # Obfuscate inline <script> blocks
        def do_script(m):
            attrs, code = m.group(1), m.group(2)
            if "src=" in attrs.lower() or not code.strip():
                return m.group(0)  # skip external scripts
            return f"<script{attrs}>{self._obf(code)}</script>"

        html = re.sub(
            r"<script([^>]*)>(.*?)</script>",
            do_script, html,
            flags=re.DOTALL | re.IGNORECASE,
        )

        return self._min_html(html)

    # ═══════════════════════════════════════════
    #  LAYER 2 — XOR ENCRYPT BODY
    # ═══════════════════════════════════════════

    def _layer2(self, html: str) -> str:
        dt_m = re.match(r"(<!DOCTYPE[^>]*>)", html, re.IGNORECASE)
        doctype = dt_m.group(1) if dt_m else "<!DOCTYPE html>"

        head_m = re.search(
            r"(<head[^>]*>)(.*?)(</head>)", html, re.DOTALL | re.IGNORECASE
        )
        body_m = re.search(
            r"<body([^>]*)>(.*?)</body>", html, re.DOTALL | re.IGNORECASE
        )

        if not body_m:
            logger.warning("No <body> tag found — skipping Layer 2")
            return html

        b_attrs = body_m.group(1)
        b_inner = body_m.group(2)

        if head_m:
            h_open  = head_m.group(1)
            h_inner = head_m.group(2)
            h_close = head_m.group(3)
        else:
            h_open, h_inner, h_close = "<head>", "", "</head>"

        # Encrypt body innerHTML with XOR + base64
        enc     = self._xor(b_inner, self._key)
        key_b64 = self._b64(self._key)

        # Obfuscated variable names
        vE  = self._rvar()  # encrypted blob
        vK  = self._rvar()  # key
        vFn = self._rvar()  # decrypt function
        vB  = self._rvar()  # bytes var
        vR  = self._rvar()  # result string
        vI  = self._rvar()  # loop index
        vD  = self._rvar()  # decoded HTML
        vSc = self._rvar()  # scripts list
        vNs = self._rvar()  # new script element
        vSi = self._rvar()  # script loop index

        decoder = (
            f"(function(){{"
            # Store encrypted blob and key
            f"var {vE}='{enc}';"
            f"var {vK}=atob('{key_b64}');"
            # XOR decrypt function
            f"function {vFn}(d,k){{"
            f"var {vB}=atob(d),{vR}='';"
            f"for(var {vI}=0;{vI}<{vB}.length;{vI}++)"
            f"{{{vR}+=String.fromCharCode({vB}.charCodeAt({vI})^k.charCodeAt({vI}%k.length));}}"
            f"return {vR};"
            f"}}"
            # Decode → eval inject (body content never sits in DOM as plaintext)
            f"var {vD}={vFn}({vE},{vK});"
            f"eval('document.body.innerHTML='+JSON.stringify({vD}));"
            # Re-execute any <script> tags that were in the decoded body
            f"var {vSc}=document.body.querySelectorAll('script');"
            f"for(var {vSi}=0;{vSi}<{vSc}.length;{vSi}++){{"
            f"var {vNs}=document.createElement('script');"
            f"if({vSc}[{vSi}].src){{{vNs}.src={vSc}[{vSi}].src;}}"
            f"else{{{vNs}.textContent={vSc}[{vSi}].textContent;}}"
            f"document.body.appendChild({vNs});"
            f"}}"
            f"}})();"
        )

        dec_obf = self._obf(decoder)

        return (
            f"{doctype}<html>"
            f"{h_open}{h_inner}{h_close}"
            f"<body{b_attrs}>"
            f"<script>{dec_obf}</script>"
            f"</body></html>"
        )

    # ═══════════════════════════════════════════
    #  LAYER 3 — DEVTOOLS DETECTION
    # ═══════════════════════════════════════════

    def _layer3(self, html: str) -> str:
        dt_js  = self._devtools_js()
        dt_obf = self._obf(dt_js)
        inject = f"<script>{dt_obf}</script>"

        # Inject as FIRST script inside <head> so it runs immediately
        m = re.search(r"<head[^>]*>", html, re.IGNORECASE)
        if m:
            pos  = m.end()
            html = html[:pos] + inject + html[pos:]
        else:
            html = inject + html
        return html

    def _devtools_js(self) -> str:
        return r"""
!function(){
'use strict';

var _gone = false;
var _ua   = (navigator.userAgent || '').toLowerCase();
var _th   = (_ua.indexOf('kiwi') !== -1) ? 80 : 100;

/* ──────────────────────────────────────────
   BLANK — nukes the page, broadcasts to
   same-origin tabs, keeps trying every 30ms
────────────────────────────────────────── */
function _blank() {
  if (_gone) return;
  _gone = true;
  try { document.body.innerHTML = ''; }                    catch(e){}
  try { document.head.innerHTML = ''; }                    catch(e){}
  try { document.documentElement.innerHTML = '<html><head></head><body></body></html>'; } catch(e){}
  try { window.location.replace('about:blank'); }          catch(e){}
  try { window.location.href = 'about:blank'; }            catch(e){}
  setInterval(function(){
    try { window.location.replace('about:blank'); } catch(e){}
  }, 30);
  /* Tell other same-origin tabs to blank too */
  try { (new BroadcastChannel('__shield__')).postMessage('nuke'); } catch(e){}
}

/* ──────────────────────────────────────────
   SAME-ORIGIN TAB SYNC
   If DevTools is opened in any tab on the
   same origin → all tabs go blank
────────────────────────────────────────── */
try {
  var _bch = new BroadcastChannel('__shield__');
  _bch.onmessage = function(ev) { if (ev.data === 'nuke') _blank(); };
} catch(e){}

/* ──────────────────────────────────────────
   TAB SWITCH DETECTION
   যখন user অন্য tab-এ যায় → page hidden হয়
   → সাথে সাথে blank করো
   Kiwi DevTools নতুন tab-এ খুললেও catch হবে
────────────────────────────────────────── */
document.addEventListener('visibilitychange', function() {
    if (document.hidden) _blank();
});

/* Window blur backup (for desktop DevTools popup windows) */
window.addEventListener('blur', function() {
    /* Small delay to avoid false positives on normal clicks */
    setTimeout(function() {
        if (document.hidden || !document.hasFocus()) _blank();
    }, 150);
});

/* ──────────────────────────────────────────
   PC DEVTOOLS — RESIZE OBSERVER
   Browser menu দিয়ে DevTools খুললেই
   viewport instantly ছোট হয় → catch করো
   60ms loop-এর চেয়ে অনেক দ্রুত
────────────────────────────────────────── */
try {
    new ResizeObserver(function() {
        if (_gone) return;
        var wDiff = window.outerWidth  - window.innerWidth;
        var hDiff = window.outerHeight - window.innerHeight;
        if (wDiff > _th || hDiff > _th) _blank();
    }).observe(document.documentElement);
} catch(e) {}

/* ──────────────────────────────────────────
   BLOCK RIGHT-CLICK
────────────────────────────────────────── */
document.addEventListener('contextmenu', function(e) {
  e.preventDefault();
  e.stopPropagation();
  return false;
}, { capture: true, passive: false });

/* ──────────────────────────────────────────
   BLOCK KEYBOARD SHORTCUTS
   F12 / Ctrl+Shift+I/J/C/K / Ctrl+U/S/P
────────────────────────────────────────── */
document.addEventListener('keydown', function(e) {
  var k = e.keyCode || e.which;
  /* F12 */
  if (k === 123) { e.preventDefault(); _blank(); return false; }
  /* Ctrl / Cmd + Shift combos */
  if ((e.ctrlKey || e.metaKey) && e.shiftKey) {
    if (k === 73 || k === 74 || k === 67 || k === 75) {
      e.preventDefault(); _blank(); return false;
    }
  }
  /* Ctrl+U (source), Ctrl+S (save), Ctrl+P (print) */
  if ((e.ctrlKey || e.metaKey) && (k === 85 || k === 83 || k === 80)) {
    e.preventDefault(); _blank(); return false;
  }
}, { capture: true });

/* ──────────────────────────────────────────
   SAVE ORIGINALS before overriding console
   (needed for the console-getter trick)
────────────────────────────────────────── */
var _origLog   = (typeof console !== 'undefined') ? console.log.bind(console)   : function(){};
var _origClear = (typeof console !== 'undefined') ? console.clear.bind(console) : function(){};

/* ──────────────────────────────────────────
   DETECTION 1 — WINDOW SIZE
   Works for docked & undocked DevTools
────────────────────────────────────────── */
function _chkSz() {
  if (window.outerWidth  - window.innerWidth  > _th ||
      window.outerHeight - window.innerHeight > _th) {
    _blank();
  }
}

/* ──────────────────────────────────────────
   DETECTION 2 — CONSOLE GETTER TRICK
   When DevTools is open, console.log() calls
   the element's id getter to show its props
────────────────────────────────────────── */
var _el = document.createElement('div');
Object.defineProperty(_el, 'id', {
  get: function() { if (!_gone) { _gone = true; _blank(); } }
});
function _chkCon() {
  try { _origLog(_el); _origClear(); } catch(e){}
}

/* ──────────────────────────────────────────
   DETECTION 3 — MOBILE DEVTOOLS
   Eruda, vConsole, Kiwi Browser extensions
────────────────────────────────────────── */
function _chkMob() {
  if (
    typeof eruda     !== 'undefined' ||
    typeof VConsole  !== 'undefined' ||
    typeof vconsole  !== 'undefined' ||
    document.getElementById('eruda') ||
    document.querySelector('.eruda-container') ||
    document.querySelector('#__vconsole') ||
    document.querySelector('[class*="eruda"]') ||
    document.querySelector('[id*="vconsole"]')
  ) { _blank(); }
}

/* ──────────────────────────────────────────
   DETECTION 4 — DEBUGGER TIMING
   If DevTools is open with pause-on-debugger,
   execution pauses → timing diff detected
────────────────────────────────────────── */
function _chkDbg() {
  var t = performance.now();
  (function(){ debugger; })();
  if (performance.now() - t > 100) _blank();
}

/* ──────────────────────────────────────────
   BLOCK PRINT DIALOG
────────────────────────────────────────── */
window.addEventListener('beforeprint', function(e) {
  e.preventDefault(); _blank();
}, { passive: false });
window.onbeforeprint = function() { _blank(); };

/* ──────────────────────────────────────────
   ANTI-IFRAME  (view-source trick)
────────────────────────────────────────── */
try {
  if (window !== window.top)
    window.top.location.replace(window.location.href);
} catch(e) { _blank(); }

/* ──────────────────────────────────────────
   SILENCE CONSOLE
   Overrides console after saving originals
────────────────────────────────────────── */
try {
  var _nop = function(){};
  ['log','warn','error','info','debug','dir','table','trace','assert',
   'group','groupEnd','time','timeEnd'].forEach(function(m) {
    try { console[m] = _nop; } catch(ex){}
  });
} catch(e){}

/* ──────────────────────────────────────────
   MAIN LOOP — 60ms
   Runs all detections continuously
────────────────────────────────────────── */
var _tick = 0;
setInterval(function() {
  if (_gone) return;
  _chkSz();
  _chkCon();
  _chkMob();
  _tick++;
  if (_tick % 100 === 0) _chkDbg(); // debugger check ~every 6s
}, 60);

/* ──────────────────────────────────────────
   INITIAL CHECK (after DOM ready)
────────────────────────────────────────── */
function _init() { _chkSz(); _chkMob(); }
if (document.readyState === 'loading')
  document.addEventListener('DOMContentLoaded', _init);
else
  _init();

}();
""".strip()
