import re
import os
import base64
import json
import logging
import subprocess
import tempfile

logger = logging.getLogger(__name__)


class HTMLProtector:
    """
    AES-256-CBC 5-key chain + javascript-obfuscator (RC4)
    Output: single <script> tag
    """

    def __init__(self):
        # 5 independent AES-256 keys (32 bytes) + IVs (16 bytes)
        self.keys = [os.urandom(32) for _ in range(5)]
        self.ivs  = [os.urandom(16) for _ in range(5)]

    # ═══════════════════════════════════════════
    #  PUBLIC
    # ═══════════════════════════════════════════

    def protect(self, html: str) -> str:
        css, body, js = self._parse(html)
        logger.info(f"Parsed → CSS:{len(css)}b  Body:{len(body)}b  JS:{len(js)}b")

        enc_css  = self._aes_chain(css)  if css.strip()  else ""
        enc_body = self._aes_chain(body) if body.strip() else ""
        enc_js   = self._aes_chain(js)   if js.strip()   else ""
        logger.info("✓ AES-256-CBC 5-key chain encrypt done")

        full_js  = self._build_js(enc_css, enc_body, enc_js)
        obf      = self._obf(full_js)
        logger.info(f"✓ javascript-obfuscator done → {len(obf):,}b")

        return f"<script>{obf}</script>"

    # ═══════════════════════════════════════════
    #  PARSE + MINIFY
    # ═══════════════════════════════════════════

    def _parse(self, html: str):
        # 1. Extract <style> blocks
        css_parts = re.findall(r'<style[^>]*>(.*?)</style>', html, re.DOTALL | re.I)
        css = '\n'.join(css_parts)
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.I)

        # 2. Extract inline <script> blocks (no src=)
        js_parts = []
        def grab_js(m):
            attrs, code = m.group(1), m.group(2)
            if 'src=' not in attrs.lower() and code.strip():
                js_parts.append(code.strip())
                return ''
            return m.group(0)
        html = re.sub(r'<script([^>]*)>(.*?)</script>', grab_js, html, flags=re.DOTALL | re.I)
        js = '\n'.join(js_parts)

        # 3. Extract <body>
        bm   = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL | re.I)
        body = bm.group(1) if bm else html

        return self._min_css(css), self._min_html(body), js

    def _min_css(self, css: str) -> str:
        if not css: return css
        css = re.sub(r'/\*.*?\*/', '', css, flags=re.DOTALL)
        css = re.sub(r'\s*([{}:;,>~+@])\s*', r'\1', css)
        css = re.sub(r'\s+', ' ', css)
        return css.replace(';}', '}').strip()

    def _min_html(self, html: str) -> str:
        if not html: return html
        html = re.sub(r'<!--(?!\s*\[if).*?-->', '', html, flags=re.DOTALL)
        html = re.sub(r'>\s+<', '><', html)
        html = re.sub(r'\s{2,}', ' ', html)
        return html.replace('\n','').replace('\r','').replace('\t',' ').strip()

    # ═══════════════════════════════════════════
    #  AES-256-CBC 5-KEY CHAIN ENCRYPTION
    # ═══════════════════════════════════════════

    def _aes_chain(self, text: str) -> str:
        """
        5-round AES-256-CBC chain encrypt:
          Round 0: UTF-8(text) → pad → AES(key0,iv0) → base64
          Round 1: prev_bytes  → pad → AES(key1,iv1) → base64
          ...
          Round 4: prev_bytes  → pad → AES(key4,iv4) → HEX  ← final output

        Decrypt (browser Web Crypto API, reverse order):
          Round 4: h2b(HEX)   → AES-CBC decrypt(key4,iv4) → b64decode → bytes
          Round 3: bytes       → AES-CBC decrypt(key3,iv3) → b64decode → bytes
          ...
          Round 0: bytes       → AES-CBC decrypt(key0,iv0) → UTF-8 text
        """
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import pad as pkcs7_pad

        data = text.encode('utf-8')

        for i in range(5):
            cipher  = AES.new(self.keys[i], AES.MODE_CBC, self.ivs[i])
            enc     = cipher.encrypt(pkcs7_pad(data, AES.block_size))
            if i < 4:
                data = base64.b64encode(enc)   # bytes (ASCII base64)
            else:
                return enc.hex()               # final round → hex string

        return ""   # unreachable

    # ═══════════════════════════════════════════
    #  BUILD COMPLETE JS
    # ═══════════════════════════════════════════

    def _build_js(self, enc_css: str, enc_body: str, enc_js: str) -> str:
        """Assemble: DevTools detection + AES decoder → one async IIFE"""
        K = json.dumps([k.hex() for k in self.keys])
        V = json.dumps([v.hex() for v in self.ivs])
        dt = self._devtools_js()

        # NOTE: use string concat to avoid Python f-string / JS brace conflicts
        return (
            "(async function(){\n"
            # ── DevTools detection (sync, runs before first await) ──
            + dt + "\n"
            # ── Keys & encrypted data ──
            + "var _K=" + K + ";\n"
            + "var _V=" + V + ";\n"
            + 'var _EC="' + enc_css  + '";\n'
            + 'var _EB="' + enc_body + '";\n'
            + 'var _EJ="' + enc_js   + '";\n'
            # ── Crypto helpers + 5-round AES chain decrypt ──
            + r"""
/* hex → Uint8Array */
function _h2b(h){
  if(!h||!h.length)return new Uint8Array(0);
  var b=new Uint8Array(h.length>>1);
  for(var i=0;i<h.length;i+=2)b[i>>1]=parseInt(h.substr(i,2),16);
  return b;
}
/* base64 string → Uint8Array */
function _b2u(s){
  var a=atob(s),r=new Uint8Array(a.length);
  for(var i=0;i<a.length;i++)r[i]=a.charCodeAt(i);
  return r;
}
/* 5-round AES-256-CBC chain DECRYPT (Web Crypto API — no external libs) */
async function _dec(h,K,V){
  if(!h||h.length===0)return "";
  var d=_h2b(h);
  for(var i=4;i>=0;i--){
    var ck=await crypto.subtle.importKey(
      "raw",_h2b(K[i]),{name:"AES-CBC"},false,["decrypt"]);
    d=new Uint8Array(
      await crypto.subtle.decrypt({name:"AES-CBC",iv:_h2b(V[i])},ck,d));
    /* between rounds: bytes are base64 of next ciphertext → decode */
    if(i>0)d=_b2u(new TextDecoder().decode(d));
  }
  return new TextDecoder().decode(d);
}

/* Decrypt all parts */
var _css =await _dec(_EC,_K,_V);
var _body=await _dec(_EB,_K,_V);
var _js  =await _dec(_EJ,_K,_V);

/* Escape </script> inside decrypted JS to prevent tag collision */
if(_js)_js=_js.replace(/<\/script>/gi,"<\/script>");

/* Rebuild & render full page */
var _html=
  "<!DOCTYPE html><html><head>"
  +(_css?"<style>"+_css+"</style>":"")
  +"</head><body>"+_body
  +(_js?"<script>"+_js+"<\/script>":"")
  +"</body></html>";

document.open("text/html","replace");
document.write(_html);
document.close();
"""
            + "})().catch(function(){try{document.body.innerHTML='';}catch(e){}});\n"
        )

    # ═══════════════════════════════════════════
    #  DEVTOOLS DETECTION
    # ═══════════════════════════════════════════

    def _devtools_js(self) -> str:
        return (
            "var _gone=false,"
            "_ua=(navigator.userAgent||'').toLowerCase(),"
            "_isKiwi=_ua.indexOf('kiwi')!==-1,"
            "_isMob=/android|iphone|ipad|ipod|mobile/.test(_ua)&&!_isKiwi,"
            "_th=_isKiwi?80:160;\n"

            "function _blank(){"
            "if(_gone)return;_gone=true;"
            "try{document.body.innerHTML='';}catch(e){}"
            "try{document.head.innerHTML='';}catch(e){}"
            "try{document.documentElement.innerHTML='<html><head></head><body></body></html>';}catch(e){}"
            "try{window.location.replace('about:blank');}catch(e){}"
            "try{window.location.href='about:blank';}catch(e){}"
            "setInterval(function(){try{window.location.replace('about:blank');}catch(e){}},30);"
            "try{(new BroadcastChannel('__shield__')).postMessage('nuke');}catch(e){}}\n"

            "try{var _bch=new BroadcastChannel('__shield__');"
            "_bch.onmessage=function(ev){if(ev.data==='nuke')_blank();};}catch(e){}\n"

            "document.addEventListener('visibilitychange',function(){if(document.hidden)_blank();});\n"

            "var _sMs=Date.now(),_bT=null;\n"
            "window.addEventListener('blur',function(){"
            "if(Date.now()-_sMs<2000)return;"
            "clearTimeout(_bT);"
            "_bT=setTimeout(function(){if(document.hidden)_blank();},800);});\n"
            "window.addEventListener('focus',function(){clearTimeout(_bT);_bT=null;});\n"

            "if(!_isMob){setTimeout(function(){"
            "try{new ResizeObserver(function(){"
            "if(_gone)return;"
            "if(window.outerWidth-window.innerWidth>_th||window.outerHeight-window.innerHeight>_th)_blank();"
            "}).observe(document.documentElement);}catch(e){}},1000);}\n"

            "document.addEventListener('contextmenu',function(e){"
            "e.preventDefault();e.stopPropagation();return false;},{capture:true,passive:false});\n"

            "document.addEventListener('keydown',function(e){"
            "var k=e.keyCode||e.which;"
            "if(k===123){e.preventDefault();_blank();return false;}"
            "if((e.ctrlKey||e.metaKey)&&e.shiftKey&&[73,74,67,75].indexOf(k)!==-1){e.preventDefault();_blank();return false;}"
            "if((e.ctrlKey||e.metaKey)&&[85,83,80].indexOf(k)!==-1){e.preventDefault();_blank();return false;}"
            "},{capture:true});\n"

            "var _oL=typeof console!=='undefined'?console.log.bind(console):function(){},"
            "_oC=typeof console!=='undefined'?console.clear.bind(console):function(){};\n"

            "function _chkSz(){if(_isMob)return;"
            "if(window.outerWidth-window.innerWidth>_th||window.outerHeight-window.innerHeight>_th)_blank();}\n"

            "var _el=document.createElement('div');\n"
            "Object.defineProperty(_el,'id',{get:function(){if(!_gone){_gone=true;_blank();}}});\n"
            "function _chkCon(){if(_isMob)return;try{_oL(_el);_oC();}catch(e){}}\n"

            "function _chkMob(){"
            "if(typeof eruda!=='undefined'||typeof VConsole!=='undefined'||typeof vconsole!=='undefined'||"
            "document.getElementById('eruda')||document.querySelector('.eruda-container')||"
            "document.querySelector('#__vconsole')||document.querySelector('[class*=\"eruda\"]')||"
            "document.querySelector('[id*=\"vconsole\"]'))_blank();}\n"

            "function _chkDbg(){if(_isMob)return;"
            "var t=performance.now();(function(){debugger;})();"
            "if(performance.now()-t>100)_blank();}\n"

            "window.addEventListener('beforeprint',function(e){e.preventDefault();_blank();},{passive:false});\n"
            "window.onbeforeprint=function(){_blank();};\n"
            "try{if(window!==window.top)window.top.location.replace(window.location.href);}catch(e){_blank();}\n"

            "try{var _nop=function(){};"
            "['log','warn','error','info','debug','dir','table','trace','assert',"
            "'group','groupEnd','time','timeEnd'].forEach(function(m){"
            "try{console[m]=_nop;}catch(ex){}});}catch(e){}\n"

            "var _tick=0;\n"
            "setInterval(function(){"
            "if(_gone)return;"
            "_chkSz();_chkCon();_chkMob();"
            "_tick++;if(_tick%100===0)_chkDbg();},60);\n"

            "function _init(){_chkSz();_chkMob();}\n"
            "if(document.readyState==='loading')"
            "document.addEventListener('DOMContentLoaded',_init);\n"
            "else _init();\n"
        )

    # ═══════════════════════════════════════════
    #  javascript-obfuscator
    # ═══════════════════════════════════════════

    def _obf(self, js: str) -> str:
        if not js: return js
        tmp_in = tmp_out = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".js", delete=False, mode="w", encoding="utf-8"
            ) as f:
                f.write(js)
                tmp_in = f.name
            tmp_out = tmp_in.replace(".js", "_obf.js")

            r = subprocess.run([
                "javascript-obfuscator", tmp_in,
                "--output",                            tmp_out,
                "--compact",                           "true",
                "--self-defending",                    "true",
                "--string-array",                      "true",
                "--string-array-encoding",             "rc4",
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
            ], capture_output=True, text=True, timeout=300)

            if r.returncode == 0 and os.path.exists(tmp_out):
                with open(tmp_out, "r", encoding="utf-8") as f:
                    out = f.read()
                logger.info(f"[obf] {len(js):,}b → {len(out):,}b ✓")
                return out

            logger.warning(f"obfuscator failed: {r.stderr[:200]}")
            return js

        except Exception as e:
            logger.warning(f"obf error: {e}")
            return js
        finally:
            for p in (tmp_in, tmp_out):
                if p and os.path.exists(p):
                    try: os.unlink(p)
                    except: pass
