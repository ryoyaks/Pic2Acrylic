"""Minimal local web UI for the Pic2Acrylic pipeline.

Drag part PNGs into the browser, pick a thickness, hit Build:
  -> saves the files, runs stage 1 (prep_masks), then launches Blender (stage 2)
     with the standee assembled, ready for you to arrange the pieces.

  python web_ui.py            # serves http://127.0.0.1:5000
"""

import glob
import os
import pathlib
import shutil
import subprocess
import sys

from flask import (Flask, request, render_template_string, send_from_directory,
                   jsonify, Response, stream_with_context)

HERE = pathlib.Path(__file__).resolve().parent
WORK = HERE / "_web_work"          # uploads + prep output live here (gitignored)

app = Flask(__name__)


def _steam_libraries():
    """All Steam library roots, discovered via the registry + libraryfolders.vdf."""
    import re
    roots = []
    try:
        import winreg
        for hive, key, val in (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        ):
            try:
                with winreg.OpenKey(hive, key) as k:
                    roots.append(winreg.QueryValueEx(k, val)[0])
            except OSError:
                pass
    except ImportError:
        pass
    roots += [r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"]

    libs = []
    for root in roots:
        if root and root not in libs:
            libs.append(root)
        vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
        if os.path.exists(vdf):
            with open(vdf, encoding="utf-8", errors="ignore") as f:
                for m in re.finditer(r'"path"\s*"([^"]+)"', f.read()):
                    p = m.group(1).replace("\\\\", "\\")
                    if p not in libs:
                        libs.append(p)
    return libs


def find_blender():
    """BLENDER_PATH -> PATH -> Program Files -> any Steam library."""
    env = os.environ.get("BLENDER_PATH")
    if env and os.path.exists(env):
        return env
    on_path = shutil.which("blender")
    if on_path:
        return on_path
    for pat in glob.glob(r"C:\Program Files\Blender Foundation\*\blender.exe"):
        return pat
    for lib in _steam_libraries():
        cand = os.path.join(lib, "steamapps", "common", "Blender", "blender.exe")
        if os.path.exists(cand):
            return cand
    return None


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pic2Acrylic</title>
<style>
  :root{
    color-scheme:dark;
    --bg0:#0e1014; --bg1:#15181f; --card:#1b1f29; --line:#2a3040;
    --ink:#eef1f7; --muted:#9aa3b6; --accent:#6ea8ff; --accent2:#9d7bff;
    --ok:#7ee2a8; --err:#ff8b8b;
  }
  *{box-sizing:border-box}
  body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif; color:var(--ink);
       margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
       padding:28px; background:radial-gradient(1200px 600px at 20% -10%,#1d2333 0%,var(--bg0) 55%);}
  .card{width:min(620px,94vw); background:linear-gradient(180deg,var(--card),#171b24);
        border:1px solid var(--line); border-radius:18px; overflow:hidden;
        box-shadow:0 24px 70px rgba(0,0,0,.5);}

  /* header / brand */
  header{display:flex; align-items:center; gap:14px; padding:20px 24px;
         border-bottom:1px solid var(--line); background:rgba(255,255,255,.015);}
  .logo{width:46px; height:46px; flex:0 0 auto; display:grid; place-items:center;}
  .logo img{width:100%; height:100%; object-fit:contain;}
  .brand h1{margin:0; font-size:18px; letter-spacing:.2px;}
  .brand p{margin:2px 0 0; font-size:12px; color:var(--muted);}

  /* hero */
  .hero{display:flex; align-items:center; gap:18px; padding:18px 24px;
        background:linear-gradient(90deg,rgba(110,168,255,.08),rgba(157,123,255,.05));
        border-bottom:1px solid var(--line);}
  .hero svg{flex:0 0 auto}
  .steps{display:flex; gap:8px; flex-wrap:wrap; font-size:12px; color:var(--muted);}
  .steps b{display:inline-grid; place-items:center; width:18px; height:18px; border-radius:50%;
           background:var(--accent); color:#0b1020; font-size:11px; margin-right:6px;}
  .steps span{display:flex; align-items:center; background:#11141c; border:1px solid var(--line);
              padding:5px 10px 5px 6px; border-radius:999px;}

  .body{padding:22px 24px 24px;}

  #drop{border:2px dashed #39435a; border-radius:14px; padding:30px 16px; text-align:center;
        cursor:pointer; transition:.15s; color:var(--muted); background:#13161d;}
  #drop.hover{border-color:var(--accent); background:#1a2233; color:#cfd9ee;
              box-shadow:inset 0 0 40px rgba(110,168,255,.08);}
  #drop .ic{display:block; margin:0 auto 10px;}
  #drop b{color:var(--ink)}

  #files{list-style:none; padding:0; margin:14px 0 0; display:grid; gap:8px;
         max-height:212px; overflow:auto;}
  #files li{display:flex; align-items:center; gap:11px; background:#12151d;
            border:1px solid var(--line); border-radius:10px; padding:7px 10px; font-size:13px;}
  #files img{width:38px; height:38px; border-radius:7px; object-fit:cover; background:#0c0e13;
             border:1px solid var(--line);}
  #files .nm{flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;}
  #files .sz{color:var(--muted); font-size:12px;}
  #files .rm{cursor:pointer; color:var(--muted); border:0; background:none; font-size:16px; padding:0 4px;}
  #files .rm:hover{color:var(--err);}
  .tag{font-size:10px; padding:1px 7px; border-radius:999px; border:1px solid var(--line); color:var(--muted);}
  .tag.mask{color:var(--accent2); border-color:rgba(157,123,255,.4);}

  .controls{display:flex; flex-direction:row; align-items:center; gap:18px; margin-top:20px;}
  .ctrl-fields{flex:1; min-width:0; display:flex; flex-direction:column; gap:14px;}
  .field{display:flex; align-items:center; gap:12px;}
  .field label{font-size:13px; color:#c3cad8; white-space:nowrap; width:88px;}
  .diagram{flex:0 0 auto; width:148px;}
  .diagram svg{width:148px; height:auto; display:block;}
  input[type=range]{flex:1; accent-color:var(--accent); min-width:80px;}
  input[type=number]{width:68px; background:#0e1117; border:1px solid #333b48; color:var(--ink);
        border-radius:8px; padding:8px 8px; font-size:14px;}
  .unit{color:var(--muted); font-size:13px; margin-left:-6px;}

  .build-row{display:flex; justify-content:flex-end; margin-top:18px;}
  button#build{display:flex; align-items:center; gap:8px;
        background:linear-gradient(180deg,var(--accent),#5b86ff); color:#0a1020; border:0;
        font-weight:700; padding:12px 20px; border-radius:11px; font-size:14px; cursor:pointer;
        box-shadow:0 6px 18px rgba(91,134,255,.35);}
  button#build:disabled{opacity:.45; cursor:default; box-shadow:none;}

  .prog{height:8px; background:#0e1117; border:1px solid var(--line); border-radius:6px;
        overflow:hidden; margin-top:18px;}
  .prog span{display:block; height:100%; width:0; transition:width .3s;
        background:linear-gradient(90deg,var(--accent),var(--accent2));}
  .log{margin-top:10px; font-size:12px; font-family:ui-monospace,Consolas,monospace;
        color:var(--muted); max-height:120px; overflow:auto;}
  .log div{padding:1px 2px;}
  .log div.err{color:var(--err);}

  #status{margin-top:14px; font-size:13px; white-space:pre-wrap; border-radius:10px;}
  #status.show{padding:12px 14px; background:#0e1117; border:1px solid var(--line);}
  #status.ok{color:var(--ok); border-color:rgba(126,226,168,.35);}
  #status.err{color:var(--err); border-color:rgba(255,139,139,.35);}
</style>
</head>
<body>
  <div class="card">
    <header>
      <!-- STUDIO LOGO -->
      <div class="logo" aria-label="studio logo">
        <img src="/logo.png" alt="studio logo">
      </div>
      <!-- /STUDIO LOGO -->
      <div class="brand">
        <h1>Pic2Acrylic</h1>
        <p>Turn layered art into printed transparent-acrylic standee pieces.</p>
      </div>
    </header>

    <div class="hero">
      <svg width="92" height="64" viewBox="0 0 92 64" xmlns="http://www.w3.org/2000/svg">
        <ellipse cx="46" cy="58" rx="30" ry="4" fill="#000" opacity=".35"/>
        <g>
          <rect x="14" y="10" width="34" height="44" rx="5" fill="#6ea8ff" opacity=".22" stroke="#6ea8ff" stroke-opacity=".5"/>
          <rect x="26" y="7" width="34" height="44" rx="5" fill="#9d7bff" opacity=".22" stroke="#9d7bff" stroke-opacity=".5"/>
          <rect x="38" y="4" width="34" height="44" rx="5" fill="#fff" opacity=".10" stroke="#cfd9ee" stroke-opacity=".55"/>
          <circle cx="55" cy="18" r="6" fill="#cfd9ee" opacity=".85"/>
          <rect x="48" y="26" width="14" height="18" rx="3" fill="#cfd9ee" opacity=".75"/>
        </g>
      </svg>
      <div class="steps">
        <span><b>1</b>Drop art</span>
        <span><b>2</b>Set thickness</span>
        <span><b>3</b>Build in Blender</span>
      </div>
    </div>

    <div class="body">
      <div id="drop">
        <svg class="ic" width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="#6ea8ff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 16V4M12 4l-4 4M12 4l4 4"/><path d="M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>
        </svg>
        <div><b>Drag your parts folder here</b>, or click to choose a folder</div>
        <div style="font-size:12px;margin-top:5px;opacity:.8">&lt;part&gt;.png + optional &lt;part&gt;_mask.png &middot; build saved to <code>&lt;folder&gt;/_prep/</code></div>
      </div>
      <input id="picker" type="file" accept=".png,image/png" multiple hidden>
      <ul id="files"></ul>

      <div class="controls">
        <div class="ctrl-fields">
          <div class="field thick" title="Real-world height of the TALLEST piece; everything else scales to match.">
            <label for="height">Max Height</label>
            <input id="hrange" type="range" min="5" max="60" step="0.5" value="15">
            <input id="height" type="number" min="5" step="0.5" value="15">
            <span class="unit">cm</span>
          </div>
          <div class="field thick">
            <label for="thickness">Thickness</label>
            <input id="trange" type="range" min="0.1" max="1" step="0.05" value="0.3">
            <input id="thickness" type="number" min="0.1" step="0.05" value="0.3">
            <span class="unit">cm</span>
          </div>
        </div>

        <div class="diagram" aria-hidden="true">
          <svg id="dim" viewBox="0 0 148 172" xmlns="http://www.w3.org/2000/svg">
            <polygon id="d-top"   fill="#9d7bff" fill-opacity=".45" stroke="#cfd9ee" stroke-opacity=".5" stroke-width="1"/>
            <polygon id="d-side"  fill="#6ea8ff" fill-opacity=".5"  stroke="#cfd9ee" stroke-opacity=".5" stroke-width="1"/>
            <polygon id="d-front" fill="#6ea8ff" fill-opacity=".16" stroke="#cfd9ee" stroke-opacity=".75" stroke-width="1.2"/>
            <line id="d-haxis" stroke="#6ea8ff" stroke-width="1.2"/>
            <line id="d-htop"  stroke="#6ea8ff" stroke-width="1.2"/>
            <line id="d-hbot"  stroke="#6ea8ff" stroke-width="1.2"/>
            <text id="d-hlabel" fill="#cfd9ee" font-size="11" text-anchor="middle"></text>
            <line id="d-taxis" stroke="#9d7bff" stroke-width="1.2"/>
            <text id="d-tlabel" fill="#cfd9ee" font-size="10" text-anchor="middle"></text>
          </svg>
        </div>
      </div>

      <div class="build-row">
        <button id="build" disabled>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 16V8a2 2 0 0 0-1-1.7l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.7l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>
            <path d="M3.3 7L12 12l8.7-5M12 22V12"/>
          </svg>
          Build &amp; open Blender
        </button>
      </div>
      <div id="prog" class="prog" hidden><span id="bar"></span></div>
      <div id="log" class="log"></div>
      <div id="status"></div>
    </div>
  </div>

<script>
  const drop=document.getElementById('drop'), picker=document.getElementById('picker'),
        list=document.getElementById('files'), buildBtn=document.getElementById('build'),
        statusEl=document.getElementById('status'), heightEl=document.getElementById('height'),
        hrange=document.getElementById('hrange'),
        range=document.getElementById('trange'), num=document.getElementById('thickness'),
        prog=document.getElementById('prog'), bar=document.getElementById('bar'),
        logEl=document.getElementById('log');
  let chosen=[], dirHandle=null;

  function setStatus(m,c){ statusEl.textContent=m; statusEl.className=(m?'show ':'')+(c||''); }
  function setProg(pct){ prog.hidden=false; bar.style.width=Math.max(0,Math.min(100,pct))+'%'; }
  function logLine(m,cls){ const d=document.createElement('div'); if(cls)d.className=cls;
    d.textContent=m; logEl.appendChild(d); logEl.scrollTop=logEl.scrollHeight; }

  function render(){
    list.innerHTML='';
    chosen.forEach((f,i)=>{
      const li=document.createElement('li');
      const img=document.createElement('img'); img.src=URL.createObjectURL(f);
      img.onload=()=>URL.revokeObjectURL(img.src);
      const nm=document.createElement('span'); nm.className='nm'; nm.textContent=f.name;
      const isMask=/_mask\\.png$/i.test(f.name), isBleed=/_bleed\\.png$/i.test(f.name);
      if(isMask||isBleed){ const t=document.createElement('span'); t.className='tag mask';
        t.textContent=isMask?'mask':'bleed'; nm.appendChild(document.createTextNode(' ')); nm.appendChild(t); }
      const sz=document.createElement('span'); sz.className='sz'; sz.textContent=(f.size/1048576).toFixed(1)+' MB';
      const rm=document.createElement('button'); rm.className='rm'; rm.textContent='\\u00d7';
      rm.onclick=()=>{ chosen.splice(i,1); render(); };
      li.append(img,nm,sz,rm); list.appendChild(li);
    });
    buildBtn.disabled = chosen.length===0;
  }

  function addFiles(fl){
    for(const f of fl)
      if(f.name.toLowerCase().endsWith('.png') && !chosen.some(c=>c.name===f.name)) chosen.push(f);
    render();
  }

  async function useDirectory(handle){
    dirHandle=handle;
    try{ if(handle.requestPermission) await handle.requestPermission({mode:'readwrite'}); }catch(_){}
    chosen=[];
    for await (const [name,h] of handle.entries())
      if(h.kind==='file' && name.toLowerCase().endsWith('.png')) chosen.push(await h.getFile());
    render();
    setStatus(chosen.length
      ? 'Folder \"'+handle.name+'\" - '+chosen.length+' PNG(s). Build saves to '+handle.name+'/_prep/.'
      : 'No PNGs found in that folder.', chosen.length?'':'err');
  }

  drop.onclick=async()=>{
    if(window.showDirectoryPicker){
      try{ await useDirectory(await window.showDirectoryPicker({mode:'readwrite'})); }catch(_){}
    } else { picker.click(); }
  };
  picker.onchange=e=>{ dirHandle=null; addFiles(e.target.files); };

  ['dragenter','dragover'].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();drop.classList.add('hover');}));
  drop.addEventListener('dragleave',e=>{e.preventDefault();drop.classList.remove('hover');});
  drop.addEventListener('drop',async e=>{
    e.preventDefault(); drop.classList.remove('hover');
    const loose=e.dataTransfer.files;
    let promises=[];
    if(window.DataTransferItem && DataTransferItem.prototype.getAsFileSystemHandle)
      promises=[...e.dataTransfer.items].filter(it=>it.kind==='file').map(it=>it.getAsFileSystemHandle());
    for(const p of promises){
      try{ const h=await p; if(h && h.kind==='directory'){ await useDirectory(h); return; } }catch(_){}
    }
    dirHandle=null; addFiles(loose);
  });

  // live 3D-ish diagram so users see what Max Height / Thickness mean
  const SVGNS='http://www.w3.org/2000/svg';
  function setAttrs(id,a){ const el=document.getElementById(id); for(const k in a) el.setAttribute(k,a[k]); }
  function pts(arr){ return arr.map(p=>p[0].toFixed(1)+','+p[1].toFixed(1)).join(' '); }
  function updateDiagram(){
    const H=parseFloat(heightEl.value)||0, T=parseFloat(num.value)||0;
    const x0=52, w=52, cy=86;                          // cy = vertical centre of the viewBox
    const hpx=Math.min(120, Math.max(24, H*3.8));   // taller value -> taller sheet
    const dpx=Math.min(30, Math.max(4, T*34));        // thicker value -> deeper side
    const dx=dpx*0.86, dy=dpx*0.5;
    const baseY=cy + (hpx+dy)/2 - 4;                   // centre the whole drawing vertically
    const topY=baseY-hpx;
    setAttrs('d-front',{points:pts([[x0,topY],[x0+w,topY],[x0+w,baseY],[x0,baseY]])});
    setAttrs('d-top',{points:pts([[x0,topY],[x0+w,topY],[x0+w+dx,topY-dy],[x0+dx,topY-dy]])});
    setAttrs('d-side',{points:pts([[x0+w,topY],[x0+w+dx,topY-dy],[x0+w+dx,baseY-dy],[x0+w,baseY]])});
    const ax=34;
    setAttrs('d-haxis',{x1:ax,y1:baseY,x2:ax,y2:topY});
    setAttrs('d-htop',{x1:ax-4,y1:topY,x2:ax+4,y2:topY});
    setAttrs('d-hbot',{x1:ax-4,y1:baseY,x2:ax+4,y2:baseY});
    const my=(topY+baseY)/2;
    setAttrs('d-hlabel',{x:ax-7,y:my,transform:'rotate(-90 '+(ax-7)+' '+my+')'});
    document.getElementById('d-hlabel').textContent=H+' cm';
    setAttrs('d-taxis',{x1:x0+w,y1:baseY+8,x2:x0+w+dx,y2:baseY+8-dy});
    setAttrs('d-tlabel',{x:x0+w+dx+11,y:baseY+8-dy+3});
    document.getElementById('d-tlabel').textContent=T+' cm';
  }

  range.oninput=()=>{ num.value=range.value; updateDiagram(); };
  num.oninput=()=>{ const v=parseFloat(num.value); if(!isNaN(v)) range.value=Math.min(1,Math.max(0.1,v)); updateDiagram(); };
  hrange.oninput=()=>{ heightEl.value=hrange.value; updateDiagram(); };
  heightEl.oninput=()=>{ const v=parseFloat(heightEl.value); if(!isNaN(v)) hrange.value=Math.min(60,Math.max(5,v)); updateDiagram(); };
  updateDiagram();

  async function writeBlendBack(){
    const buf=await (await fetch('/result.blend')).arrayBuffer();
    const prep=await dirHandle.getDirectoryHandle('_prep',{create:true});
    const fh=await prep.getFileHandle('acrylic.blend',{create:true});
    const w=await fh.createWritable(); await w.write(buf); await w.close();
  }

  buildBtn.onclick=async()=>{
    buildBtn.disabled=true;
    setStatus('',''); logEl.innerHTML=''; setProg(0);
    logLine('Uploading '+chosen.length+' file(s)...');
    const fd=new FormData();
    chosen.forEach(f=>fd.append('files',f,f.name));
    fd.append('thickness',num.value); fd.append('height',heightEl.value);
    try{
      const r=await fetch('/build',{method:'POST',body:fd});
      const reader=r.body.getReader(), dec=new TextDecoder(); let buf='', okMsg=null, failed=false;
      while(true){
        const {value,done}=await reader.read(); if(done) break;
        buf+=dec.decode(value,{stream:true});
        const lines=buf.split('\\n'); buf=lines.pop();
        for(const line of lines){
          if(!line) continue;
          const sep=line.indexOf('|');
          const tag=line.slice(0,sep), rest=line.slice(sep+1);
          if(tag==='P'){ const j=rest.indexOf('|'); setProg(parseInt(rest.slice(0,j))); logLine(rest.slice(j+1)); }
          else if(tag==='OK'){ okMsg=rest; setProg(100); }
          else if(tag==='ERR'){ failed=true; logLine(rest,'err'); setStatus(rest,'err'); }
          else logLine(line);
        }
      }
      if(failed){ buildBtn.disabled=chosen.length===0; return; }
      let msg=okMsg||'Done.';
      if(dirHandle){
        try{ logLine('Saving .blend into '+dirHandle.name+'/_prep/...'); await writeBlendBack();
             msg+='\\nSaved to '+dirHandle.name+'/_prep/acrylic.blend'; }
        catch(err){ msg+='\\nCould not write into the folder ('+err+'); open in Blender, save manually.'; }
      } else {
        msg+='\\nTip: drag a FOLDER (not loose files) so the .blend saves into it.';
      }
      setProg(100); setStatus(msg,'ok');
    }catch(err){ setStatus('Request failed: '+err,'err'); }
    buildBtn.disabled=chosen.length===0;
  };
</script>
</body>
</html>"""


@app.route("/")
def index():
    return render_template_string(PAGE)


@app.route("/logo.png")
def logo():
    return send_from_directory(HERE / "assets", "logo.png")


@app.route("/result.blend")
def result_blend():
    """The last built .blend, so the browser can copy it into the user's folder."""
    return send_from_directory(WORK / "prep", "acrylic.blend")


def _is_part(fn):
    s = fn.lower()
    return s.endswith(".png") and not (s.endswith("_mask.png") or s.endswith("_bleed.png"))


@app.route("/build", methods=["POST"])
def build():
    thickness = request.form.get("thickness", "0.3")
    height = request.form.get("height", "15")
    try:
        float(thickness)
        float(height)
    except ValueError:
        return Response("ERR|Height and thickness must be numbers.\n", mimetype="text/plain")

    uploaded = request.files.getlist("files")
    pngs = [f for f in uploaded if f.filename.lower().endswith(".png")]
    if not pngs:
        return Response("ERR|No PNG files received.\n", mimetype="text/plain")

    blender = find_blender()
    if not blender:
        return Response("ERR|Blender not found. Set BLENDER_PATH and restart the server.\n",
                        mimetype="text/plain")

    # save uploads synchronously (request data isn't available once streaming starts)
    if WORK.exists():
        shutil.rmtree(WORK)
    parts = WORK / "parts"
    prep = WORK / "prep"
    parts.mkdir(parents=True)
    for f in pngs:
        f.save(str(parts / pathlib.Path(f.filename).name))
    nparts = max(1, sum(1 for f in pngs if _is_part(f.filename)))

    def gen():
        def p(pct, msg):
            return f"P|{pct}|{msg}\n"

        yield p(10, f"Saved {len(pngs)} file(s).")
        yield p(20, "Tracing masks (stage 1)...")
        r = subprocess.run(
            [sys.executable, str(HERE / "prep_masks.py"), str(parts), "-o", str(prep)],
            capture_output=True, text=True)
        if r.returncode != 0:
            yield f"ERR|Stage 1 failed:\n{r.stderr or r.stdout}\n"
            return
        yield p(35, "Masks traced. Building in Blender (stage 2)...")

        blend = prep / "acrylic.blend"
        env = dict(os.environ, ACRYLIC_THICKNESS_CM=str(thickness),
                   ACRYLIC_HEIGHT_CM=str(height), ACRYLIC_PACK="1")
        proc = subprocess.Popen(
            [blender, "--background", "--factory-startup", "--python",
             str(HERE / "build_acrylic.py"), "--", str(prep / "manifest.json"), str(blend)],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        built = 0
        for line in proc.stdout:
            line = line.rstrip()
            if line.startswith("  built "):
                built += 1
                yield p(35 + int(45 * built / nparts), line.strip())
            elif "[packed]" in line:
                yield p(84, "Packed textures into the .blend.")
        proc.wait()
        if proc.returncode != 0 or not blend.exists():
            yield f"ERR|Stage 2 (Blender) failed. See the server console.\n"
            return

        subprocess.Popen([blender, str(blend)])
        yield p(95, "Opening in Blender...")
        yield (f"OK|Built {nparts} piece(s): {height} cm tall, {thickness} cm thick. "
               f"Opened in Blender.\n")

    return Response(stream_with_context(gen()), mimetype="text/plain",
                    headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


if __name__ == "__main__":
    print("Pic2Acrylic UI -> http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
