import re
import base64
import random
import string
import json
import logging
import subprocess
import tempfile
import os

logger = logging.getLogger(__name__)


class HTMLProtector:
    """
    4-layer HTML protection:
    Layer 1 — Minify + obfuscator.io JS obfuscation
    Layer 2 — XOR encrypt body content + eval decoder
    Layer 3 — DevTools detection (60ms loop, visibilitychange, ResizeObserver)
    Layer 4 — Entire HTML → base64 → single <script> tag (CSS fully hidden)
    """

    def __init__(self):
        self._key1 = "".join(
            random.choices(string.ascii_letters + string.digits, k=32)
        )
        self._key2 = "".join(
            random.choices(string.ascii_letters + string.digits, k=32)
        )

    # ═══════════════════════════════════════════
    #  PUBLIC
    # ═══════════════════════════════════════════

    def protect(self, html: str) -> str:
        html = self._layer1(html)
        logger.info("✓ Layer 1 — Minify + JS Obfuscate (obfuscator.io)")
        html = self._layer2(html)
        logger.info("✓ Layer 2 — XOR Encrypt + eval Decode")
        html = self._layer3(html)
        logger.info("✓ Layer 3 — DevTools Detection")
        html = self._layer4(html)
        logger.info("✓ Layer 4 — Single <script> (CSS + HTML fully hidden)")
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

    @staticmethod
    def _double_xor(text: str, key1: str, key2: str) -> str:
        """
        Double XOR encrypt:
          pass1 = text  XOR key1  (byte-by-byte)
          pass2 = pass1 XOR key2  (byte-by-byte)
          → base64(pass2)
        Decode: atob → XOR key2 → XOR key1 → original
        Much harder to reverse than single XOR.
        """
        tb  = text.encode("utf-8")
        kb1 = key1.encode("utf-8")
        kb2 = key2.encode("utf-8")
        p1  = bytes([b ^ kb1[i % len(kb1)] for i, b in enumerate(tb)])
        p2  = bytes([b ^ kb2[i % len(kb2)] for i, b in enumerate(p1)])
        return base64.b64encode(p2).decode("ascii")

    # ─── obfuscator.io engine (full settings) ───────────────
    def _obf_simple(self, js: str) -> str:
        """Fallback: base64 chunks + eval(atob(...))"""
        if not js or not js.strip():
            return js
        try:
            b64 = self._b64(js)
            sz  = random.randint(12, 24)
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
            logger.warning(f"simple obf error: {e}")
            return f'eval(atob("{self._b64(js)}"));'

    def _run_obfuscator(self, js: str, extra_args: list = None) -> str:
        """Core helper — calls javascript-obfuscator CLI, returns obfuscated JS."""
        if not js or not js.strip():
            return js
        tmp_in = tmp_out = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".js", delete=False, mode="w", encoding="utf-8"
            ) as f:
                f.write(js)
                tmp_in = f.name
            tmp_out = tmp_in.replace(".js", "_obf.js")

            cmd = [
                "javascript-obfuscator", tmp_in,
                "--output", tmp_out,
            ] + (extra_args or [])

            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120
            )

            if r.returncode == 0 and os.path.exists(tmp_out):
                with open(tmp_out, "r", encoding="utf-8") as f:
                    return f.read()

            logger.warning(f"obfuscator error: {r.stderr[:200]}")
            return self._obf_simple(js)

        except FileNotFoundError:
            logger.warning("javascript-obfuscator not found → fallback")
            return self._obf_simple(js)
        except subprocess.TimeoutExpired:
            logger.warning("javascript-obfuscator timeout → fallback")
            return self._obf_simple(js)
        except Exception as e:
            logger.warning(f"obf error: {e} → fallback")
            return self._obf_simple(js)
        finally:
            for p in (tmp_in, tmp_out):
                if p and os.path.exists(p):
                    try:
                        os.unlink(p)
                    except OSError:
                        pass

    def _obf(self, js: str) -> str:
        """Full obfuscation (Layers 1-3 scripts): string-array + control flow + dead code."""
        before = len(js)
        out = self._run_obfuscator(js, [
            "--compact",                           "true",
            "--self-defending",                    "true",
            "--string-array",                      "true",
            "--string-array-encoding",             "rc4",   # RC4 >> base64
            "--string-array-threshold",            "1",
            "--string-array-calls-transform",      "true",
            "--string-array-rotate",               "true",
            "--string-array-shuffle",              "true",
            "--control-flow-flattening",           "true",
            "--control-flow-flattening-threshold", "0.75",
            "--dead-code-injection",               "true",
            "--dead-code-injection-threshold",     "0.4",
            "--identifier-names-generator",        "mangled",
            "--transform-object-keys",             "true",
            "--numbers-to-expressions",            "true",
        ])
        logger.info(f"  [obf] {before:,}b → {len(out):,}b")
        return out

    def _obf_l4(self, js: str) -> str:
        """
        Layer 4 obfuscation — lighter settings.
        The base64 payload is already opaque; we only obfuscate the code structure
        to keep output size manageable.
        """
        before = len(js)
        out = self._run_obfuscator(js, [
            "--compact",                           "true",
            "--self-defending",                    "true",
            "--string-array",                      "false",  # base64 is already opaque
            "--control-flow-flattening",           "true",
            "--control-flow-flattening-threshold", "0.5",
            "--dead-code-injection",               "true",
            "--dead-code-injection-threshold",     "0.3",
            "--identifier-names-generator",        "mangled",
            "--numbers-to-expressions",            "true",
        ])
        logger.info(f"  [obf-l4] {before:,}b → {len(out):,}b")
        return out

    # ═══════════════════════════════════════════
    #  LAYER 1 — MINIFY + OBFUSCATE JS/CSS
    # ═══════════════════════════════════════════

    def _min_css(self, css: str) -> str:
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
        css = re.sub(r"\s*([{}:;,>~+@])\s*", r"\1", css)
        css = re.sub(r"\s+", " ", css)
        css = css.replace(";}", "}")
        return css.strip()

    def _min_html(self, html: str) -> str:
        html = re.sub(r"<!--(?!\s*\[if).*?-->", "", html, flags=re.DOTALL)
        html = re.sub(r">\s+<", "><", html)
        html = re.sub(r"\s{2,}", " ", html)
        html = html.replace("\n", "").replace("\r", "").replace("\t", " ")
        return html.strip()

    def _layer1(self, html: str) -> str:
        def do_style(m):
            return f"<style{m.group(1)}>{self._min_css(m.group(2))}</style>"
        html = re.sub(
            r"<style([^>]*)>(.*?)</style>",
            do_style, html, flags=re.DOTALL | re.IGNORECASE,
        )

        def do_script(m):
            attrs, code = m.group(1), m.group(2)
            if "src=" in attrs.lower() or not code.strip():
                return m.group(0)
            return f"<script{attrs}>{self._obf(code)}</script>"
        html = re.sub(
            r"<script([^>]*)>(.*?)</script>",
            do_script, html, flags=re.DOTALL | re.IGNORECASE,
        )

        return self._min_html(html)

    # ═══════════════════════════════════════════
    #  LAYER 2 — XOR ENCRYPT BODY
    # ═══════════════════════════════════════════

    def _layer2(self, html: str) -> str:
        dt_m    = re.match(r"(<!DOCTYPE[^>]*>)", html, re.IGNORECASE)
        doctype = dt_m.group(1) if dt_m else "<!DOCTYPE html>"

        head_m = re.search(r"(<head[^>]*>)(.*?)(</head>)", html, re.DOTALL | re.IGNORECASE)
        body_m = re.search(r"<body([^>]*)>(.*?)</body>",   html, re.DOTALL | re.IGNORECASE)

        if not body_m:
            logger.warning("No <body> tag — skipping Layer 2")
            return html

        b_attrs = body_m.group(1)
        b_inner = body_m.group(2)
        h_open  = head_m.group(1) if head_m else "<head>"
        h_inner = head_m.group(2) if head_m else ""
        h_close = head_m.group(3) if head_m else "</head>"

        # ── Double XOR encryption ──────────────────────────────
        enc      = self._double_xor(b_inner, self._key1, self._key2)
        k1_b64   = self._b64(self._key1)
        k2_b64   = self._b64(self._key2)

        vE  = self._rvar()   # encrypted blob
        vK1 = self._rvar()   # key 1
        vK2 = self._rvar()   # key 2
        vFn = self._rvar()   # decode function
        vB  = self._rvar()   # bytes var
        vP1 = self._rvar()   # pass-1 intermediate
        vR  = self._rvar()   # result
        vI  = self._rvar()   # loop index
        vD  = self._rvar()   # decoded HTML
        vSc = self._rvar()   # scripts list
        vNs = self._rvar()   # new script el
        vSi = self._rvar()   # script loop index

        # JS decoder — reverses double XOR: key2 first, then key1
        decoder = (
            f"(function(){{"
            # encrypted blob + two keys (stored as base64)
            f"var {vE}='{enc}';"
            f"var {vK1}=atob('{k1_b64}');"
            f"var {vK2}=atob('{k2_b64}');"
            # double XOR decode function
            f"function {vFn}(d,k1,k2){{"
            f"var {vB}=atob(d),{vP1}='',{vR}='';"
            # reverse pass-2: XOR with key2
            f"for(var {vI}=0;{vI}<{vB}.length;{vI}++)"
            f"{{{vP1}+=String.fromCharCode({vB}.charCodeAt({vI})^k2.charCodeAt({vI}%k2.length));}}"
            # reverse pass-1: XOR with key1
            f"for(var {vI}=0;{vI}<{vP1}.length;{vI}++)"
            f"{{{vR}+=String.fromCharCode({vP1}.charCodeAt({vI})^k1.charCodeAt({vI}%k1.length));}}"
            f"return {vR};}}"
            # decode + inject via eval
            f"var {vD}={vFn}({vE},{vK1},{vK2});"
            f"eval('document.body.innerHTML='+JSON.stringify({vD}));"
            # re-execute any <script> tags in decoded body
            f"var {vSc}=document.body.querySelectorAll('script');"
            f"for(var {vSi}=0;{vSi}<{vSc}.length;{vSi}++){{"
            f"var {vNs}=document.createElement('script');"
            f"if({vSc}[{vSi}].src){{{vNs}.src={vSc}[{vSi}].src;}}"
            f"else{{{vNs}.textContent={vSc}[{vSi}].textContent;}}"
            f"document.body.appendChild({vNs});}}"
            f"}})();"
        )

        return (
            f"{doctype}<html>"
            f"{h_open}{h_inner}{h_close}"
            f"<body{b_attrs}>"
            f"<script>{self._obf(decoder)}</script>"
            f"</body></html>"
        )

    # ═══════════════════════════════════════════
    #  LAYER 3 — DEVTOOLS DETECTION
    # ═══════════════════════════════════════════

    def _layer3(self, html: str) -> str:
        dt_obf = self._obf(self._devtools_js())
        inject = f"<script>{dt_obf}</script>"
        m = re.search(r"<head[^>]*>", html, re.IGNORECASE)
        if m:
            html = html[:m.end()] + inject + html[m.end():]
        else:
            html = inject + html
        return html

    def _devtools_js(self) -> str:
        return r"""
!function(){
'use strict';
var _gone=false;
var _ua=(navigator.userAgent||'').toLowerCase();
var _th=(_ua.indexOf('kiwi')!==-1)?80:100;

function _blank(){
if(_gone)return;_gone=true;
try{document.body.innerHTML='';}catch(e){}
try{document.head.innerHTML='';}catch(e){}
try{document.documentElement.innerHTML='<html><head></head><body></body></html>';}catch(e){}
try{window.location.replace('about:blank');}catch(e){}
try{window.location.href='about:blank';}catch(e){}
setInterval(function(){try{window.location.replace('about:blank');}catch(e){}},30);
try{(new BroadcastChannel('__shield__')).postMessage('nuke');}catch(e){}
}

try{var _bch=new BroadcastChannel('__shield__');_bch.onmessage=function(ev){if(ev.data==='nuke')_blank();};}catch(e){}
document.addEventListener('visibilitychange',function(){if(document.hidden)_blank();});

/* ── Window blur detection ──────────────────────────────────────────
   সমস্যা: CSS blur animation / page load focus change → false blank
   Fix:
   1. প্রথম 2 সেকেন্ড blur ignore (CSS animation / page init)
   2. 800ms grace period (focus ফিরে আসলে cancel)
   3. শুধু document.hidden চেক (animation false positive বাদ দিতে)
──────────────────────────────────────────────────────────────────── */
var _startMs  = Date.now();
var _blurTimer = null;

window.addEventListener('blur', function() {
    /* Skip: first 2s after page load (CSS animations / document.write init) */
    if (Date.now() - _startMs < 2000) return;
    clearTimeout(_blurTimer);
    /* Only blank if page stays hidden for 800ms (real tab switch) */
    _blurTimer = setTimeout(function() {
        if (document.hidden) _blank();
    }, 800);
});

window.addEventListener('focus', function() {
    /* Focus returned → cancel pending blank */
    clearTimeout(_blurTimer);
    _blurTimer = null;
});

try{
new ResizeObserver(function(){
if(_gone)return;
if(window.outerWidth-window.innerWidth>_th||window.outerHeight-window.innerHeight>_th)_blank();
}).observe(document.documentElement);
}catch(e){}

document.addEventListener('contextmenu',function(e){e.preventDefault();e.stopPropagation();return false;},{capture:true,passive:false});
document.addEventListener('keydown',function(e){
var k=e.keyCode||e.which;
if(k===123){e.preventDefault();_blank();return false;}
if((e.ctrlKey||e.metaKey)&&e.shiftKey&&[73,74,67,75].indexOf(k)!==-1){e.preventDefault();_blank();return false;}
if((e.ctrlKey||e.metaKey)&&[85,83,80].indexOf(k)!==-1){e.preventDefault();_blank();return false;}
},{capture:true});

var _origLog=typeof console!=='undefined'?console.log.bind(console):function(){};
var _origClear=typeof console!=='undefined'?console.clear.bind(console):function(){};

function _chkSz(){if(window.outerWidth-window.innerWidth>_th||window.outerHeight-window.innerHeight>_th)_blank();}
var _el=document.createElement('div');
Object.defineProperty(_el,'id',{get:function(){if(!_gone){_gone=true;_blank();}}});
function _chkCon(){try{_origLog(_el);_origClear();}catch(e){}}
function _chkMob(){
if(typeof eruda!=='undefined'||typeof VConsole!=='undefined'||typeof vconsole!=='undefined'||
document.getElementById('eruda')||document.querySelector('.eruda-container')||
document.querySelector('#__vconsole')||document.querySelector('[class*="eruda"]')||
document.querySelector('[id*="vconsole"]'))_blank();
}
function _chkDbg(){var t=performance.now();(function(){debugger;})();if(performance.now()-t>100)_blank();}

window.addEventListener('beforeprint',function(e){e.preventDefault();_blank();},{passive:false});
window.onbeforeprint=function(){_blank();};
try{if(window!==window.top)window.top.location.replace(window.location.href);}catch(e){_blank();}

try{
var _nop=function(){};
['log','warn','error','info','debug','dir','table','trace','assert','group','groupEnd','time','timeEnd'].forEach(function(m){try{console[m]=_nop;}catch(ex){}});
}catch(e){}

var _tick=0;
setInterval(function(){
if(_gone)return;
_chkSz();_chkCon();_chkMob();
_tick++;if(_tick%100===0)_chkDbg();
},60);

function _init(){_chkSz();_chkMob();}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',_init);
else _init();
}();
""".strip()

    # ═══════════════════════════════════════════
    #  LAYER 4 — SINGLE <script> WRAPPER
    #  CSS + full HTML hidden inside base64 blob
    #  Output: ONLY <script>...</script>
    # ═══════════════════════════════════════════

    def _layer4(self, html: str) -> str:
        """
        Converts the entire protected HTML into a single <script> tag.
        - Encodes full HTML as base64 (CSS, structure — everything hidden)
        - document.write() reconstructs the page at runtime
        - Wraps the writer in javascript-obfuscator obfuscation
        Final output: <script>[obfuscated code]</script>
        """
        # Encode entire HTML
        b64_full = self._b64(html)

        # Split into chunks so the string array is manageable
        chunk_size = 8000
        chunks = [b64_full[i: i + chunk_size]
                  for i in range(0, len(b64_full), chunk_size)]

        vc = self._rvar()   # chunks array var
        vb = self._rvar()   # joined base64 var

        js_writer = (
            f"(function(){{"
            f"var {vc}={json.dumps(chunks)};"
            f"var {vb}={vc}.join('');"
            f"document.open('text/html','replace');"
            f"document.write(atob({vb}));"
            f"document.close();"
            f"}})();"
        )

        # Use lighter obfuscation — base64 is already opaque
        obfuscated = self._obf_l4(js_writer)

        # Return ONLY the script tag — no other HTML tags
        return f"<script>{obfuscated}</script>"
