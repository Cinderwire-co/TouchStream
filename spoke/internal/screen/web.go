package screen

import (
	"image/png"
	"net/http"
	"strconv"
	"time"
)

// Routes serves the screen for the emulator: /screen to see it (click to tap), /screen.png
// for the picture itself, POST /screen/tap?x=&y= for a touch.
func Routes(mux *http.ServeMux, s *Screen) {
	mux.HandleFunc("GET /screen", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		_, _ = w.Write([]byte(page))
	})
	mux.HandleFunc("GET /screen.png", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "image/png")
		w.Header().Set("Cache-Control", "no-store")
		_ = png.Encode(w, s.Render(time.Now()))
	})
	// A touch on the screen: kind is down, move or up
	mux.HandleFunc("POST /screen/touch", func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()
		x, errX := strconv.Atoi(q.Get("x"))
		y, errY := strconv.Atoi(q.Get("y"))
		if errX != nil || errY != nil {
			http.Error(w, "x and y on the 240×240 screen", http.StatusBadRequest)
			return
		}
		x, y = min(max(x, 0), Size-1), min(max(y, 0), Size-1)
		switch q.Get("kind") {
		case "down":
			s.Down(x, y)
		case "move":
			s.Move(x, y)
		case "up":
			s.Up(x, y)
		default:
			http.Error(w, "kind is down, move or up", http.StatusBadRequest)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	})
	mux.HandleFunc("POST /screen/wheel", func(w http.ResponseWriter, r *http.Request) {
		dy, _ := strconv.Atoi(r.URL.Query().Get("dy"))
		s.Wheel(dy)
		w.WriteHeader(http.StatusNoContent)
	})
}

// The emulator's view of the box: its screen at twice the size, refreshed a few times a second,
// and (in the emulator only) a switch for the HDMI cable.
const page = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>PicoKVM emulator</title>
<style>
  :root { color-scheme: dark; --ink: #0E0E0E; --char: #1A1A1A; --line: #272727; --ash: #F2F2F3; --smoke: #8E8E93;
          --ember: #FE5C09; }
  * { box-sizing: border-box; }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px 16px; background: var(--ink);
         font: 15px/1.45 system-ui, -apple-system, sans-serif; color: var(--smoke); -webkit-tap-highlight-color: transparent; }
  main { width: min(100%, 536px); }
  h1 { margin: 0 0 14px; font-size: 13px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; color: var(--smoke); }
  h1 b { color: var(--ember); font-weight: 600; }
  .box { padding: 28px; border-radius: 36px; background: var(--char); box-shadow: inset 0 0 0 1px var(--line); }
  img { display: block; width: 100%; aspect-ratio: 1; border-radius: 10px; image-rendering: pixelated; cursor: pointer;
        touch-action: manipulation; user-select: none; -webkit-user-select: none; }
  .row { display: flex; align-items: center; gap: 12px; margin-top: 18px; flex-wrap: wrap; }
  .row span { flex: 1; min-width: 160px; }
  .seg { display: inline-flex; padding: 3px; border-radius: 999px; background: var(--char); box-shadow: inset 0 0 0 1px var(--line); }
  .seg button { border: 0; padding: 8px 16px; border-radius: 999px; background: none; color: var(--smoke); font: inherit;
                font-weight: 600; cursor: pointer; }
  .seg button[aria-pressed="true"] { background: var(--ash); color: var(--ink); }
  a { color: var(--ash); }
  p { margin: 14px 0 0; }
  [hidden] { display: none !important; }
</style></head>
<body><main>
  <h1><b>TouchStream</b> · PicoKVM screen</h1>
  <div class="box"><img id="s" src="/screen.png" alt="The PicoKVM's 1.54-inch screen" draggable="false"></div>
  <div id="emu" hidden>
    <div class="row"><span>HDMI cable</span>
      <div class="seg" role="group" aria-label="HDMI cable">
        <button id="in" type="button">Plugged in</button><button id="out" type="button">Pulled out</button></div></div>
    <p id="link" hidden>The QR code opens <a target="_blank" rel="noopener"></a></p>
    <p>Tap the middle of the home screen for the recordings, drag to scroll, tap one for its QR code. Go live, preview or rename it from the
      <a id="hub" href="#" target="_blank" rel="noopener">hub dashboard</a>, and watch the screen follow.</p>
  </div>
</main>
<script>
  const img = document.getElementById('s');
  const refresh = () => { img.src = '/screen.png?t=' + Date.now(); };
  img.onload = img.onerror = () => setTimeout(refresh, 120);   // often enough to show its animations
  const at = (e) => {
    const r = img.getBoundingClientRect();
    return [Math.floor((e.clientX - r.left) * 240 / r.width), Math.floor((e.clientY - r.top) * 240 / r.height)];
  };
  const send = (kind, e) => { const [x, y] = at(e); return fetch('/screen/touch?kind=' + kind + '&x=' + x + '&y=' + y, { method: 'POST' }); };
  // A finger (or the mouse) on the screen: drag to scroll the list, tap to pick
  let down = false, moving = null;
  img.addEventListener('pointerdown', (e) => { down = true; img.setPointerCapture(e.pointerId); send('down', e); });
  img.addEventListener('pointermove', (e) => {
    if (!down || moving) return;
    moving = send('move', e).finally(() => { moving = null; refresh(); });
  });
  img.addEventListener('pointerup', (e) => { down = false; Promise.resolve(moving).then(() => send('up', e)).then(refresh); });
  img.addEventListener('wheel', (e) => { e.preventDefault(); fetch('/screen/wheel?dy=' + Math.round(e.deltaY), { method: 'POST' }).then(refresh); }, { passive: false });
  const show = (hdmi) => {
    document.getElementById('in').setAttribute('aria-pressed', hdmi);
    document.getElementById('out').setAttribute('aria-pressed', !hdmi);
  };
  const plug = (on) => fetch('/emulator/hdmi?in=' + (on ? 1 : 0), { method: 'POST' }).then(() => { show(on); refresh(); });
  const poll = () => fetch('/emulator').then((r) => r.ok ? r.json() : null).then((emu) => {
    if (!emu) return;
    document.getElementById('emu').hidden = false;
    show(emu.hdmi);
    const hub = document.getElementById('hub');
    if (emu.hub) hub.href = emu.hub; else hub.removeAttribute('href');
    const link = document.getElementById('link');
    link.hidden = !emu.link;
    if (emu.link) { link.querySelector('a').href = emu.link; link.querySelector('a').textContent = emu.link; }
    setTimeout(poll, 1000);
  }).catch(() => {});
  document.getElementById('in').onclick = () => plug(true);
  document.getElementById('out').onclick = () => plug(false);
  poll();
</script></body></html>`
