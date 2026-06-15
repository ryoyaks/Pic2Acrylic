"""Minimal local web UI for the acrylic-standee pipeline.

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

from flask import Flask, request, render_template_string

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
<title>Acrylic Standee</title>
<style>
  :root { color-scheme: dark; }
  body { font-family: system-ui, sans-serif; background:#15171c; color:#e7e9ee;
         margin:0; display:flex; min-height:100vh; align-items:center; justify-content:center; }
  .card { width:min(560px,92vw); background:#1d2027; border:1px solid #2c313c;
          border-radius:14px; padding:28px; box-shadow:0 12px 40px rgba(0,0,0,.45); }
  h1 { font-size:20px; margin:0 0 4px; }
  p.sub { margin:0 0 20px; color:#9aa3b2; font-size:13px; }
  #drop { border:2px dashed #3a4250; border-radius:12px; padding:34px 16px; text-align:center;
          cursor:pointer; transition:.15s; color:#9aa3b2; }
  #drop.hover { border-color:#5b8cff; background:#222838; color:#cfd6e4; }
  #files { list-style:none; padding:0; margin:14px 0 0; font-size:13px; max-height:160px; overflow:auto; }
  #files li { display:flex; justify-content:space-between; padding:4px 8px; border-radius:6px; }
  #files li:nth-child(odd) { background:#22262f; }
  .row { display:flex; align-items:center; gap:12px; margin-top:20px; }
  label { font-size:13px; color:#c3cad8; }
  input[type=number] { width:84px; background:#12141a; border:1px solid #333b48; color:#e7e9ee;
          border-radius:8px; padding:8px 10px; font-size:14px; }
  button { margin-left:auto; background:#5b8cff; color:#0b1020; border:0; font-weight:600;
          padding:11px 22px; border-radius:9px; font-size:14px; cursor:pointer; }
  button:disabled { opacity:.5; cursor:default; }
  #status { margin-top:18px; font-size:13px; white-space:pre-wrap; border-radius:8px; padding:0; }
  #status.show { padding:12px 14px; background:#12141a; border:1px solid #2c313c; }
  .ok { color:#7ee2a8; } .err { color:#ff8b8b; }
</style>
</head>
<body>
  <div class="card">
    <h1>Acrylic Standee</h1>
    <p class="sub">Drop your part PNGs (<code>&lt;part&gt;.png</code> + optional
       <code>&lt;part&gt;_mask.png</code>), set the thickness, and build.</p>

    <div id="drop">Drag PNG files here, or click to choose</div>
    <input id="picker" type="file" accept=".png,image/png" multiple hidden>
    <ul id="files"></ul>

    <div class="row">
      <label for="thickness">Thickness (mm)</label>
      <input id="thickness" type="number" min="0.5" step="0.5" value="3.0">
      <button id="build" disabled>Build &amp; open Blender</button>
    </div>
    <div id="status"></div>
  </div>

<script>
  const drop = document.getElementById('drop');
  const picker = document.getElementById('picker');
  const list = document.getElementById('files');
  const buildBtn = document.getElementById('build');
  const statusEl = document.getElementById('status');
  let chosen = [];

  function setStatus(msg, cls) {
    statusEl.textContent = msg;
    statusEl.className = (msg ? 'show ' : '') + (cls || '');
  }
  function addFiles(fileList) {
    for (const f of fileList) {
      if (f.name.toLowerCase().endsWith('.png') && !chosen.some(c => c.name === f.name))
        chosen.push(f);
    }
    render();
  }
  function render() {
    list.innerHTML = '';
    chosen.forEach((f, i) => {
      const li = document.createElement('li');
      li.innerHTML = `<span>${f.name}</span><span>${(f.size/1048576).toFixed(1)} MB</span>`;
      list.appendChild(li);
    });
    buildBtn.disabled = chosen.length === 0;
  }

  drop.onclick = () => picker.click();
  picker.onchange = e => addFiles(e.target.files);
  ['dragenter','dragover'].forEach(ev => drop.addEventListener(ev, e => {
    e.preventDefault(); drop.classList.add('hover'); }));
  ['dragleave','drop'].forEach(ev => drop.addEventListener(ev, e => {
    e.preventDefault(); drop.classList.remove('hover'); }));
  drop.addEventListener('drop', e => addFiles(e.dataTransfer.files));

  buildBtn.onclick = async () => {
    buildBtn.disabled = true;
    setStatus('Tracing masks and launching Blender...', '');
    const fd = new FormData();
    chosen.forEach(f => fd.append('files', f, f.name));
    fd.append('thickness', document.getElementById('thickness').value);
    try {
      const r = await fetch('/build', { method: 'POST', body: fd });
      const t = await r.text();
      setStatus(t, r.ok ? 'ok' : 'err');
    } catch (err) {
      setStatus('Request failed: ' + err, 'err');
    }
    buildBtn.disabled = chosen.length === 0;
  };
</script>
</body>
</html>"""


@app.route("/")
def index():
    return render_template_string(PAGE)


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
    print("Acrylic Standee UI -> http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
