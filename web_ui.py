"""Minimal local web UI for the Acrylic-Standee-Maker pipeline.

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

from flask import Flask, request, render_template_string, send_from_directory

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
<title>Acrylic Standee Maker</title>
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

  .controls{display:flex; align-items:center; gap:14px; margin-top:20px; flex-wrap:wrap;}
  .thick{display:flex; align-items:center; gap:12px; flex:1; min-width:220px;}
  .thick label{font-size:13px; color:#c3cad8; white-space:nowrap;}
  input[type=range]{flex:1; accent-color:var(--accent);}
  input[type=number]{width:74px; background:#0e1117; border:1px solid #333b48; color:var(--ink);
        border-radius:8px; padding:8px 8px; font-size:14px;}
  .unit{color:var(--muted); font-size:13px; margin-left:-6px;}

  button#build{margin-left:auto; display:flex; align-items:center; gap:8px;
        background:linear-gradient(180deg,var(--accent),#5b86ff); color:#0a1020; border:0;
        font-weight:700; padding:12px 20px; border-radius:11px; font-size:14px; cursor:pointer;
        box-shadow:0 6px 18px rgba(91,134,255,.35);}
  button#build:disabled{opacity:.45; cursor:default; box-shadow:none;}

  #status{margin-top:16px; font-size:13px; white-space:pre-wrap; border-radius:10px;}
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
        <h1>Acrylic Standee Maker</h1>
        <p>Preview your layered art as printed transparent-acrylic pieces.</p>
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
        <div><b>Drag part PNGs here</b>, or click to choose</div>
        <div style="font-size:12px;margin-top:5px;opacity:.8">&lt;part&gt;.png + optional &lt;part&gt;_mask.png</div>
      </div>
      <input id="picker" type="file" accept=".png,image/png" multiple hidden>
      <ul id="files"></ul>

      <div class="controls">
        <div class="thick">
          <label for="thickness">Thickness</label>
          <input id="trange" type="range" min="1" max="10" step="0.5" value="3">
          <input id="thickness" type="number" min="0.5" step="0.5" value="3.0">
          <span class="unit">mm</span>
        </div>
        <button id="build" disabled>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 16V8a2 2 0 0 0-1-1.7l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.7l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>
            <path d="M3.3 7L12 12l8.7-5M12 22V12"/>
          </svg>
          Build &amp; open Blender
        </button>
      </div>
      <div id="status"></div>
    </div>
  </div>

<script>
  const drop=document.getElementById('drop'), picker=document.getElementById('picker'),
        list=document.getElementById('files'), buildBtn=document.getElementById('build'),
        statusEl=document.getElementById('status'),
        range=document.getElementById('trange'), num=document.getElementById('thickness');
  let chosen=[];

  function setStatus(m,c){ statusEl.textContent=m; statusEl.className=(m?'show ':'')+(c||''); }
  function addFiles(fl){
    for(const f of fl)
      if(f.name.toLowerCase().endsWith('.png') && !chosen.some(c=>c.name===f.name)) chosen.push(f);
    render();
  }
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

  drop.onclick=()=>picker.click();
  picker.onchange=e=>addFiles(e.target.files);
  ['dragenter','dragover'].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();drop.classList.add('hover');}));
  ['dragleave','drop'].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();drop.classList.remove('hover');}));
  drop.addEventListener('drop',e=>addFiles(e.dataTransfer.files));

  range.oninput=()=>{ num.value=range.value; };
  num.oninput=()=>{ const v=parseFloat(num.value); if(!isNaN(v)) range.value=Math.min(10,Math.max(1,v)); };

  buildBtn.onclick=async()=>{
    buildBtn.disabled=true; setStatus('Tracing masks and launching Blender...','');
    const fd=new FormData();
    chosen.forEach(f=>fd.append('files',f,f.name));
    fd.append('thickness',num.value);
    try{ const r=await fetch('/build',{method:'POST',body:fd}); const t=await r.text();
         setStatus(t, r.ok?'ok':'err'); }
    catch(err){ setStatus('Request failed: '+err,'err'); }
    buildBtn.disabled = chosen.length===0;
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


@app.route("/build", methods=["POST"])
def build():
    thickness = request.form.get("thickness", "3.0")
    try:
        float(thickness)
    except ValueError:
        return ("Thickness must be a number.", 400)

    uploaded = request.files.getlist("files")
    pngs = [f for f in uploaded if f.filename.lower().endswith(".png")]
    if not pngs:
        return ("No PNG files received.", 400)

    blender = find_blender()
    if not blender:
        return ("Blender not found. Set the BLENDER_PATH environment variable to "
                "your blender.exe and restart this server.", 500)

    # fresh work dir each build
    if WORK.exists():
        shutil.rmtree(WORK)
    parts = WORK / "parts"
    prep = WORK / "prep"
    parts.mkdir(parents=True)
    for f in pngs:
        f.save(str(parts / pathlib.Path(f.filename).name))

    # stage 1
    r = subprocess.run(
        [sys.executable, str(HERE / "prep_masks.py"), str(parts), "-o", str(prep)],
        capture_output=True, text=True)
    if r.returncode != 0:
        return (f"Stage 1 (prep_masks) failed:\n{r.stderr or r.stdout}", 500)

    # stage 2: launch Blender GUI (non-blocking) with thickness via env
    env = dict(os.environ, ACRYLIC_THICKNESS_MM=str(thickness))
    subprocess.Popen(
        [blender, "--python", str(HERE / "build_acrylic.py"), "--",
         str(prep / "manifest.json"), str(prep / "acrylic.blend")],
        env=env)

    return (f"Prepared {len(pngs)} file(s) at {thickness} mm thickness.\n"
            f"{r.stdout.strip()}\nLaunching Blender...")


if __name__ == "__main__":
    print("Acrylic Standee Maker UI -> http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
