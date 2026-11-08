// The Luckfox PicoKVM, drawn: a model of the box seen from above and in front (isometric), as SVG.
// It lies screen up, with the side holding the microSD slot, RESET and BOOT toward you; its cables
// (HDMI IN, Ethernet, USB-C power) go in at the back, at the top of the screen.
'use strict';

// boxArt({ screen: 'ready' | 'off', card: true }): with card, a microSD card slides into the slot, again and again.
function boxArt({ screen = 'ready', card = false } = {}) {
  const W = 56, D = 56, H = 30, R = 7;   // the box in mm: 56 across, 30 tall, rounded corners
  const s = 2.2;                          // pixels per mm
  const cx = Math.cos(Math.PI / 6) * s, cy = 0.5 * s;
  const ox = 170, oy = 84;                // where the model's (0, 0, 0) lands
  const at = (x, y, z) => [ox + (x - y) * cx, oy + (x + y) * cy - z * s];
  const pts = (list) => list.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const plane = (z) => `matrix(${cx} ${cy} ${-cx} ${cy} ${ox} ${oy - z * s})`;   // flat at height z, in mm
  const front = `matrix(${cx} ${cy} 0 ${s} ${ox - D * cx} ${oy + D * cy - H * s})`;  // the slot's side: mm across, mm down

  // Around the top's edge, each point with its outward normal
  const edge = [];
  for (const [x, y, from] of [[W - R, D - R, 0], [R, D - R, 90], [R, R, 180], [W - R, R, 270]]) {
    for (let a = from; a <= from + 90; a += 7.5) {
      const r = (a * Math.PI) / 180;
      edge.push([x + R * Math.cos(r), y + R * Math.sin(r), Math.cos(r), Math.sin(r)]);
    }
  }
  // The sides you can see, each sliver shaded by which way it faces (the slot's side is lit)
  const sides = [];
  for (let i = 0; i < edge.length; i++) {
    const [x1, y1, nx1, ny1] = edge[i], [x2, y2, nx2, ny2] = edge[(i + 1) % edge.length];
    const nx = (nx1 + nx2) / 2, ny = (ny1 + ny2) / 2;
    if (nx + ny <= 0.01) continue;
    const light = Math.max(0, Math.min(1, 0.5 + 0.55 * ny - 0.45 * nx));
    const shade = Math.round(16 + light * 26);
    const fill = `rgb(${shade},${shade},${shade + 2})`;
    sides.push(`<polygon points="${pts([at(x1, y1, H), at(x2, y2, H), at(x2, y2, 0), at(x1, y1, 0)])}" fill="${fill}" stroke="${fill}" stroke-width=".6"/>`);
  }
  const top = pts(edge.map(([x, y]) => at(x, y, H)));
  const floor = pts(edge.map(([x, y]) => at(x + 1.5, y + 2.5, 0)));

  const on = screen === 'ready';
  const ui = on ? `
    <path d="M14 11.4 l2 1.15 -2 1.15z" fill="#fe5c09"/>
    <text x="17.2" y="13.4" font-size="2.5" font-weight="600" fill="#f2f2f3">Home</text>
    <rect x="19" y="16" width="18" height="6.4" rx="3.2" fill="#3b404b"/>
    <text x="28" y="20.6" font-size="3.6" font-weight="800" fill="#f2f2f3" text-anchor="middle">READY</text>
    <text x="28" y="25.6" font-size="1.9" fill="#e0e0e4" text-anchor="middle" font-family="JetBrains, monospace">1080p60 · HDMI</text>
    <text x="28" y="28.2" font-size="1.55" fill="#a3a3aa" text-anchor="middle" font-family="JetBrains, monospace">Hub connected · 192.168.1.20</text>
    <rect x="13.2" y="32" width="12.4" height="6" rx="3" fill="none" stroke="#f2f2f3" stroke-width=".45"/>
    <text x="19.4" y="35.6" font-size="1.75" font-weight="600" fill="#f2f2f3" text-anchor="middle">Recordings</text>
    <circle cx="29.6" cy="35" r="2.8" fill="none" stroke="#f2f2f3" stroke-width=".45"/>
    <circle cx="29.6" cy="35" r=".7" fill="#fe5c09"/>
    <path d="M28.2 33.6 a2 2 0 0 0 0 2.8 M31 33.6 a2 2 0 0 1 0 2.8" fill="none" stroke="#fe5c09" stroke-width=".4"/>
    <circle cx="37.6" cy="35" r="2.8" fill="none" stroke="#f2f2f3" stroke-width=".45"/>
    <circle cx="37.6" cy="35" r="2.1" fill="#f47a7a"/>` : `
    <path d="M12 41 L30 10 H36 L18 41z" fill="#ffffff" opacity=".035"/>`;

  // The microSD card: 11 × 15 mm, contacts first, its notch at the back
  const sd = (fill, stroke) => `<path d="M13 0.5 Q13 0 13.5 0 H23.5 Q24 0 24 .5 V8.6 L23.2 9.4 V14.5 Q23.2 15 22.7 15 H13.5 Q13 15 13 14.5z" fill="${fill}" stroke="${stroke}" stroke-width=".3"/>`;
  const cardArt = card ? `
  <g transform="${plane(10)}" clip-path="url(#outside)">
    <g class="card-in">
      <g transform="translate(0 ${D - 13.5})">
        <g transform="translate(0 .25)">${sd('#2a2a2d', '#2a2a2d')}</g>
        ${sd('#e4e4e8', '#9a9aa0')}
        <rect x="14.6" y="9.5" width="7" height="3.6" rx=".6" fill="#fe5c09"/>
        <path d="M18.5 26 V18.6 M16 21 L18.5 18.4 21 21" fill="none" stroke="#fe5c09" stroke-width="1" stroke-linecap="round" stroke-linejoin="round"/>
      </g>
    </g>
  </g>` : '';

  const label = on ? 'The Luckfox PicoKVM: its screen on top, its microSD slot beside RESET and BOOT'
    : 'Putting the microSD card into the PicoKVM’s slot, beside RESET and BOOT';
  return `<svg class="boxart" viewBox="${card ? '22 10 266 206' : '56 10 232 206'}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="${label}"
    font-family="Bricolage, sans-serif">
  <defs>
    <filter id="soft" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="3"/></filter>
    <clipPath id="outside" clipPathUnits="userSpaceOnUse"><rect x="-40" y="${D}" width="140" height="120"/></clipPath>
  </defs>
  <polygon points="${floor}" fill="#000" opacity=".55" filter="url(#soft)"/>
  ${sides.join('')}
  <polygon points="${top}" fill="#232325" stroke="rgba(255,255,255,.13)" stroke-width=".8"/>
  <g transform="${plane(H)}">
    <rect x="5" y="5" width="46" height="46" rx="5" fill="#101011" stroke="#2f2f32" stroke-width=".5"/>
    <rect x="11" y="8.5" width="34" height="34" rx="1.2" fill="${on ? '#0b0d12' : '#060607'}"/>${ui}
    <circle cx="46.5" cy="46.5" r=".8" fill="${on ? '#2fe0c2' : '#1e2a28'}"/>
  </g>
  <g transform="${front}">
    <path d="M14.6 11.4 h2.4 l.9 .9 v3.4 h-3.3z" fill="none" stroke="#9a9aa0" stroke-width=".35"/>
    <rect x="12.5" y="19.2" width="13" height="1.7" rx=".85" fill="#050505" stroke="#3d3d41" stroke-width=".3"/>
    ${card ? '<rect class="hint" x="10.8" y="17.6" width="16.4" height="4.9" rx="2.4" fill="none" stroke="#fe5c09" stroke-width=".55"/>' : ''}
    <text x="31" y="8.6" font-size="2.3" font-weight="700" fill="#e8e8ea" text-anchor="middle">RESET</text>
    <circle cx="31" cy="12.4" r="2.1" fill="#0c0c0d" stroke="#3d3d41" stroke-width=".4"/>
    <text x="39.6" y="8.6" font-size="2.3" font-weight="700" fill="#e8e8ea" text-anchor="middle">BOOT</text>
    <circle cx="39.6" cy="12.4" r="2.1" fill="#0c0c0d" stroke="#3d3d41" stroke-width=".4"/>
  </g>${cardArt}
</svg>`;
}
