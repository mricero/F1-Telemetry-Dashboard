// Browser replay player (IMPROVEMENTS.md REPLAY-05; layout.md section 9).
//
// Everything is precomputed in Python (processing/replay_payload.py): this
// module only looks values up - a binary search per tower field, a linear
// blend between two position frames - and draws them. Racing rules (who is
// P3, whether a lap was purple) never live here.
//
// Streamlit re-invokes the default export whenever `data` changes and calls
// the returned cleanup only on unmount, so one Player per parent element is
// kept and rebuilt only when the session changes. The cursor is reported to
// Python only on pause, on a seek while paused and at the end: every state
// change reruns the script. `data.focus` is read at mount only (UI-11).
//
// Car positions and the interval trend arrive packed (REPLAY-29) and are
// unpacked once, asynchronously, with the browser's DecompressionStream; the
// root gets data-ready="1" when that is done and drawn.

const SVG_NS = "http://www.w3.org/2000/svg";
const INSTANCES = new WeakMap();
const SPEEDS = [0.5, 1, 2, 4, 8, 16, 32, 64];
const ROW = 30;
const HEADING = 18;
const DASH = String.fromCharCode(0x2013);
const DOT = String.fromCharCode(0xb7);
const DEGREE = String.fromCharCode(0xb0);
const STATUS_CHIPS = { "IN PIT": "PIT", OUT: "OUT", FIN: "FIN", KO: "KO" };
const SHADED = { "SAFETY CAR": true, VSC: true, RED: true };
const MAP_CHIPS = { "SAFETY CAR": "SC", VSC: "VSC", RED: "RED" };
const CARDINALS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function svgEl(tag, attrs) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attrs || {})) node.setAttribute(name, value);
  return node;
}

// Index of the last entry <= t, or -1.
function bisect(times, t) {
  let lo = 0;
  let hi = times.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (times[mid] <= t) lo = mid + 1;
    else hi = mid;
  }
  return lo - 1;
}

function valueAt(series, t, fallback) {
  if (!series) return fallback;
  const index = bisect(series[0], t);
  return index >= 0 ? series[1][index] : fallback;
}

function clockText(seconds) {
  const total = Math.max(0, Math.round(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function setText(node, text) {
  const value = text === null || text === undefined ? DASH : String(text);
  if (node.textContent !== value) node.textContent = value;
}

function setClass(node, name, on) {
  if (node.classList.contains(name) !== on) node.classList.toggle(name, on);
}

// Attribute and property writes only on a change: every write is a DOM
// mutation, and draw() runs every animation frame.
function setAttr(node, name, value) {
  const text = String(value);
  if (node.getAttribute(name) !== text) node.setAttribute(name, text);
}

function setHidden(node, hidden) {
  if (node.hidden !== hidden) node.hidden = hidden;
}

function setDisabled(node, disabled) {
  if (node.disabled !== disabled) node.disabled = disabled;
}

function base64Bytes(text) {
  const binary = atob(text);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

// Raw DEFLATE (Python's zlib with wbits -15), via the browser's own decoder.
async function inflate(text) {
  const stream = new DecompressionStream("deflate-raw");
  const writer = stream.writable.getWriter();
  // Errors surface through the reader below.
  writer.write(base64Bytes(text)).catch(() => {});
  writer.close().catch(() => {});
  const reader = stream.readable.getReader();
  const chunks = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    total += value.length;
  }
  const out = new Uint8Array(total);
  let at = 0;
  for (const chunk of chunks) {
    out.set(chunk, at);
    at += chunk.length;
  }
  return out;
}

// processing/replay_payload._pack_positions reversed: absent flags, then the
// low and high bytes of zigzag second differences (driver, x/y, frame), into
// frame-major Int16 frames holding pos.absent where a car has no sample.
function unpackPositions(pos, raw) {
  const frames = pos.frames;
  const drivers = pos.drivers;
  const count = frames * drivers;
  const flagBytes = (count + 7) >> 3;
  if (raw.length !== flagBytes + 4 * count) throw new Error("unexpected position data length");
  const low = flagBytes;
  const high = flagBytes + 2 * count;
  const xy = new Int16Array(count * 2);
  for (let d = 0; d < drivers; d += 1) {
    for (let c = 0; c < 2; c += 1) {
      const base = (d * 2 + c) * frames;
      let value = 0;
      let step = 0;
      for (let f = 0; f < frames; f += 1) {
        const code = raw[low + base + f] | (raw[high + base + f] << 8);
        step += (code >>> 1) ^ -(code & 1);
        value += step;
        xy[(f * drivers + d) * 2 + c] = value;
      }
    }
    for (let f = 0; f < frames; f += 1) {
      const bit = d * frames + f;
      if (raw[bit >> 3] & (0x80 >> (bit & 7))) {
        xy[(f * drivers + d) * 2] = pos.absent;
        xy[(f * drivers + d) * 2 + 1] = pos.absent;
      }
    }
  }
  return xy;
}

// The interval trend: little-endian UInt16 hundredths, one row per driver,
// trend.missing for "no interval" (NaN here).
function unpackTrend(trend, raw) {
  const samples = trend.samples;
  if (raw.length !== trend.codes.length * samples * 2) throw new Error("unexpected trend data length");
  const values = {};
  trend.codes.forEach((code, row) => {
    const out = new Float64Array(samples);
    for (let i = 0; i < samples; i += 1) {
      const at = (row * samples + i) * 2;
      const value = raw[at] | (raw[at + 1] << 8);
      out[i] = value === trend.missing ? NaN : value / trend.scale;
    }
    values[code] = out;
  });
  return values;
}

class Player {
  constructor(root, data, setStateValue) {
    this.root = root;
    this.data = data;
    this.key = data.session_key;
    this.setStateValue = setStateValue;
    this.clock = data.clock;
    this.style = data.style || { teams: {}, flags: {}, compounds: {} };
    this.cursor = this.clamp(data.cursor ?? this.clock.lights_out);
    this.seekSeen = data.seek ?? 0;
    // The last cursor sent to Python; nothing is sent until it changes.
    this.reported = this.cursor;
    this.keyTimer = null;
    this.playing = false;
    this.speed = 1;
    this.focus = data.focus || null;
    this.follow = false;
    this.labels = false;
    this.mode = "gap";
    this.frame = null;
    this.lastTick = null;
    this.lastDrawn = null;
    this.listeners = [];
    this.destroyed = false;
    this.order = [];
    this.cardKey = null;
    this.flagTimes = data.flags.map((row) => row[0]);
    this.rcTimes = data.rcm.map((row) => row[0]);
    this.weatherTimes = data.weather.map((row) => row[0]);
    this.lapTimes = {};
    for (const [code, laps] of Object.entries(data.laps || {})) this.lapTimes[code] = laps.map((lap) => lap[0]);
    this.xy = null;
    this.trend = null;
    this.carIndex = {};
    (data.pos ? data.pos.codes : []).forEach((code, index) => {
      this.carIndex[code] = index;
    });
    this.root.dataset.ready = "0";
    this.build();
    this.draw(true);
    this.ready = this.unpack()
      .catch((error) => {
        console.warn("replay player: could not unpack the payload", error);
        this.mapNote("Car positions could not be unpacked, so the cars are not drawn.");
      })
      .then(() => {
        if (this.destroyed) return;
        this.draw(true);
        this.root.dataset.ready = "1";
      });
  }

  // ---------------------------------------------------------------- data
  clamp(t) {
    return Math.min(Math.max(t, this.clock.start), this.clock.end);
  }

  async unpack() {
    const { pos, trend } = this.data;
    const packed = Boolean(pos) || Boolean(trend && trend.z);
    if (!packed) return;
    if (typeof DecompressionStream === "undefined") {
      this.mapNote("This browser cannot unpack the car positions; a current Chrome, Edge, Firefox or Safari can.");
      return;
    }
    const [xy, values] = await Promise.all([
      pos ? inflate(pos.xy_z).then((raw) => unpackPositions(pos, raw)) : null,
      trend && trend.z ? inflate(trend.z).then((raw) => unpackTrend(trend, raw)) : null,
    ]);
    if (this.destroyed) return;
    this.xy = xy;
    this.trend = values;
  }

  mapNote(text) {
    if (this.mapNode && !this.destroyed) this.mapNode.append(el("div", "rp-map-note", text));
  }

  carAt(code, t) {
    const pos = this.data.pos;
    const d = this.carIndex[code];
    if (!pos || !this.xy || d === undefined) return null;
    let i = (t - pos.t0) / pos.step;
    // Within one frame of either end, hold the nearest frame.
    if (i < -1 || i > pos.frames) return null;
    i = Math.min(Math.max(i, 0), pos.frames - 1);
    const lo = Math.floor(i);
    const hi = Math.min(lo + 1, pos.frames - 1);
    const w = i - lo;
    const a = (lo * pos.drivers + d) * 2;
    const b = (hi * pos.drivers + d) * 2;
    const xy = this.xy;
    const absentA = xy[a] === pos.absent;
    const absentB = xy[b] === pos.absent;
    if (absentA && absentB) return null;
    if (absentA || absentB) {
      // At the edge of a car's window, use the nearer real sample.
      const nearer = w < 0.5 ? a : b;
      if (xy[nearer] === pos.absent) return null;
      return [xy[nearer] / pos.scale, xy[nearer + 1] / pos.scale];
    }
    return [
      (xy[a] + (xy[b] - xy[a]) * w) / pos.scale,
      (xy[a + 1] + (xy[b + 1] - xy[a + 1]) * w) / pos.scale,
    ];
  }

  towerAt(t) {
    const defaults = this.data.defaults;
    const rows = this.data.drivers.map((driver, hint) => {
      const fields = this.data.tower[driver.code] || {};
      const row = { code: driver.code, hint, driver };
      for (const name of Object.keys(defaults)) row[name] = valueAt(fields[name], t, defaults[name]);
      return row;
    });
    rows.sort((x, y) => {
      if ((x.pos === null) !== (y.pos === null)) return x.pos === null ? 1 : -1;
      if (x.pos !== y.pos) return (x.pos || 0) - (y.pos || 0);
      return x.hint - y.hint;
    });
    return rows;
  }

  flagAt(t) {
    const index = bisect(this.flagTimes, t);
    return index >= 0 ? this.data.flags[index][1] : "GREEN";
  }

  // --------------------------------------------------------------- build
  build() {
    const body = this.root.querySelector(".rp-body");
    body.textContent = "";
    const timed = this.data.session.kind !== "race";
    this.timed = timed;

    // Header
    const header = el("div", "rp-header");
    const year = this.data.session.year ? ` ${this.data.session.year}` : "";
    header.append(
      el("span", "rp-event", `${this.data.session.event}${year}`),
      el("span", "rp-session", this.data.session.name),
    );
    this.lapNode = el("span", "rp-lap");
    this.clockLabel = el("span", "rp-label");
    this.clockNode = el("span", "rp-clock");
    this.flagNode = el("span", "rp-chip");
    this.weatherNode = el("span", "rp-weather");
    header.append(this.lapNode, this.clockLabel, this.clockNode, this.flagNode, this.weatherNode);
    body.append(header);
    if (this.data.estimated) {
      body.append(el("div", "rp-note", "Gaps estimated at the timing lines"));
    }

    // Tower and map
    const main = el("div", "rp-main");
    const tower = el("section", "rp-tower");
    tower.setAttribute("aria-label", "Timing tower");
    const cols = timed ? "rp-cols-timed" : "rp-cols-race";
    const head = el("div", `rp-thead rp-label ${cols}`);
    const columns = timed
      ? [
          ["Pos", "rp-pos"],
          ["", ""],
          ["Driver", ""],
          ["Best", "rp-right"],
          ["Gap", "rp-right"],
          ["Last", "rp-right rp-col-last-timed"],
          ["S1", "rp-right rp-col-sector"],
          ["S2", "rp-right rp-col-sector"],
          ["S3", "rp-right rp-col-sector"],
          ["Tyre", ""],
        ]
      : [
          ["Pos", "rp-pos"],
          ["", ""],
          ["Driver", ""],
          ["Gap", "rp-right"],
          ["Last", "rp-right rp-col-last"],
          ["Tyre", ""],
          ["Pit", "rp-right rp-col-pit"],
          ["", ""],
        ];
    for (const [text, className] of columns) head.append(el("span", className, text));
    this.gapHeading = head.children[timed ? 4 : 3];
    this.rowsNode = el("div", "rp-rows");
    tower.append(head, this.rowsNode);
    if (!timed) {
      const toggle = el("div", "rp-toggle");
      this.gapButton = el("button", "on", "Gap");
      this.intervalButton = el("button", "", "Interval");
      this.gapButton.setAttribute("aria-pressed", "true");
      this.intervalButton.setAttribute("aria-pressed", "false");
      this.on(this.gapButton, "click", () => this.setMode("gap"));
      this.on(this.intervalButton, "click", () => this.setMode("int"));
      toggle.setAttribute("aria-label", "Gap column shows");
      toggle.append(this.gapButton, this.intervalButton);
      tower.append(toggle);
    }
    this.buildRows(cols);

    const side = el("section", "rp-side");
    this.mapNode = el("div", "rp-map");
    side.append(this.mapNode);
    this.buildMap();
    this.card = el("div", "rp-card");
    this.card.hidden = true;
    this.card.setAttribute("aria-label", "Focused driver");
    this.cardTitle = el("div", "rp-card-title");
    this.cardFacts = el("div", "rp-card-facts rp-num");
    this.cardLaps = el("div", "rp-card-laps rp-num");
    this.cardTrend = el("div", "rp-card-trend");
    this.cardTrend.title = "Gap to the car ahead, last five minutes";
    this.analyseButton = el("button", "", "Analyse this lap");
    this.analyseButton.disabled = true;
    this.on(this.analyseButton, "click", () => {
      this.reportCursor();
      this.report("analyse", this.cardLap);
    });
    this.card.append(this.cardTitle, this.cardFacts, this.cardLaps, this.cardTrend, this.analyseButton);
    side.append(this.card);
    this.rcNode = el("div", "rp-rc");
    this.rcNode.id = "rp-rc";
    this.rcNode.setAttribute("aria-label", "Race control");
    this.rcLines = [0, 1, 2].map(() => el("div", "rp-rc-line"));
    this.rcNode.append(el("div", "rp-label", "Race control"), ...this.rcLines);
    side.append(this.rcNode);
    main.append(tower, side);
    body.append(main);

    // Controls
    const controls = el("div", "rp-controls");
    this.playButton = el("button", "rp-play");
    this.playButton.setAttribute("aria-label", "Play");
    this.playButton.title = "Play (Space)";
    const icon = svgEl("svg", { viewBox: "0 0 16 16", "aria-hidden": "true" });
    this.playUse = svgEl("use", { href: "#rp-icon-play" });
    icon.append(this.playUse);
    this.playButton.append(icon);
    this.on(this.playButton, "click", () => this.toggle());
    controls.append(this.playButton);
    for (const [label, delta] of [
      ["-30s", -30],
      ["-5s", -5],
      ["+5s", 5],
      ["+30s", 30],
    ]) {
      const button = el("button", "", label);
      this.on(button, "click", () => this.seek(this.cursor + delta));
      controls.append(button);
    }
    const previous = el("button", "", "Previous lap");
    const next = el("button", "", "Next lap");
    this.on(previous, "click", () => this.lap(-1));
    this.on(next, "click", () => this.lap(1));
    controls.append(previous, next);
    this.speedSelect = el("select");
    this.speedSelect.setAttribute("aria-label", "Playback speed");
    for (const speed of SPEEDS) {
      const option = el("option", "", `${speed}x`);
      option.value = String(speed);
      option.selected = speed === this.speed;
      this.speedSelect.append(option);
    }
    this.on(this.speedSelect, "change", () => {
      this.speed = Number(this.speedSelect.value);
    });
    controls.append(this.speedSelect);
    this.labelButton = el("button", "", "Labels");
    this.labelButton.title = "Driver labels on the map (L)";
    this.labelButton.setAttribute("aria-pressed", "false");
    this.on(this.labelButton, "click", () => this.toggleLabels());
    controls.append(this.labelButton);
    this.followButton = el("button", "", "Follow");
    this.followButton.title = "Keep the map on the focused driver (F)";
    this.followButton.setAttribute("aria-pressed", "false");
    this.on(this.followButton, "click", () => this.toggleFollow());
    controls.append(this.followButton);
    const rcToggle = el("button", "rp-rc-toggle", "Race control");
    rcToggle.setAttribute("aria-controls", this.rcNode.id);
    rcToggle.setAttribute("aria-expanded", "false");
    this.on(rcToggle, "click", () => {
      this.rcNode.classList.toggle("open");
      rcToggle.setAttribute("aria-expanded", String(this.rcNode.classList.contains("open")));
    });
    controls.append(rcToggle);
    this.readout = el("span", "rp-readout");
    controls.append(this.readout);
    body.append(controls);

    // Timeline
    this.timelineNode = el("div", "rp-timeline");
    // A slider for keyboards and screen readers: arrow keys seek (onKey),
    // Home and End go to either end.
    this.timelineNode.setAttribute("role", "slider");
    this.timelineNode.tabIndex = 0;
    this.timelineNode.setAttribute("aria-label", "Session time");
    this.timelineNode.setAttribute("aria-valuemin", Math.round(this.clock.start - this.clock.lights_out));
    this.timelineNode.setAttribute("aria-valuemax", Math.round(this.clock.end - this.clock.lights_out));
    body.append(this.timelineNode);
    this.drawTimeline();
    if (typeof ResizeObserver !== "undefined") {
      this.resize = new ResizeObserver(() => this.drawTimeline());
      this.resize.observe(this.timelineNode);
    }
    let dragging = false;
    const momentAt = (event) => {
      const box = this.timelineNode.getBoundingClientRect();
      const share = Math.min(Math.max((event.clientX - box.left) / box.width, 0), 1);
      return this.clock.start + share * (this.clock.end - this.clock.start);
    };
    this.on(this.timelineNode, "pointerdown", (event) => {
      dragging = true;
      this.seek(momentAt(event), { report: false });
    });
    this.on(this.timelineNode, "pointermove", (event) => {
      const t = momentAt(event);
      const lap = valueAt(this.data.leader_laps, t, null);
      const lapText = lap ? `Lap ${lap} ${DOT} ` : "";
      this.timelineNode.title = `${lapText}${clockText(t - this.clock.lights_out)}`;
      // While dragging only redraw; every report reruns the Python script.
      if (dragging) this.seek(t, { report: false });
    });
    this.on(window, "pointerup", () => {
      if (dragging && !this.playing) this.report("cursor", this.cursor);
      dragging = false;
    });
    // Leaving the page (switching pages, closing the tab) keeps the moment.
    this.on(document, "visibilitychange", () => {
      if (document.visibilityState === "hidden") this.reportCursor();
    });

    this.on(this.root, "keydown", (event) => this.onKey(event));
    this.on(this.root, "pointerdown", () => this.root.focus({ preventScroll: true }));
  }

  buildRows(cols) {
    this.rows = {};
    for (const driver of this.data.drivers) {
      const row = el("div", `rp-row ${cols}`);
      const team = this.style.teams[driver.code] || {};
      const cells = {
        pos: el("span", "rp-pos"),
        bar: el("span", "rp-bar"),
        code: el("span", "rp-code"),
      };
      cells.bar.style.background = team.fill || "var(--line)";
      cells.code.append(document.createTextNode(driver.code));
      if (driver.number) cells.code.append(el("small", "", driver.number));
      row.append(cells.pos, cells.bar, cells.code);
      cells.gap = el("span", "rp-right rp-gap");
      cells.tyre = el("span", "rp-tyre");
      if (this.timed) {
        cells.best = el("span", "rp-right");
        cells.last = el("span", "rp-right rp-last rp-col-last-timed");
        cells.s1 = el("span", "rp-right rp-col-sector");
        cells.s2 = el("span", "rp-right rp-col-sector");
        cells.s3 = el("span", "rp-right rp-col-sector");
        row.append(cells.best, cells.gap, cells.last, cells.s1, cells.s2, cells.s3, cells.tyre);
      } else {
        cells.last = el("span", "rp-right rp-last rp-col-last");
        cells.pits = el("span", "rp-right rp-col-pit");
        cells.status = el("span", "rp-status");
        row.append(cells.gap, cells.last, cells.tyre, cells.pits, cells.status);
      }
      cells.tyreBadge = el("i");
      cells.tyreAge = el("span", "rp-num");
      cells.tyre.append(cells.tyreBadge, cells.tyreAge);
      row.title = driver.team ? `${driver.name} ${DOT} ${driver.team}` : driver.name;
      // A button for keyboards and screen readers (UI-15): Enter or Space
      // focuses the driver, the up and down arrows move between rows.
      row.setAttribute("role", "button");
      row.tabIndex = 0;
      row.setAttribute("aria-pressed", "false");
      this.on(row, "click", () => this.setFocus(driver.code));
      this.on(row, "keydown", (event) => this.onRowKey(event, driver.code));
      const heading = el("div", "rp-heading rp-label");
      heading.hidden = true;
      this.rowsNode.append(heading, row);
      this.rows[driver.code] = { row, cells, heading, last: null };
    }
  }

  buildMap() {
    const track = this.data.track;
    this.cars = {};
    this.mapSvg = null;
    if (!track) {
      this.mapNode.append(
        el("div", "rp-map-note", "No GPS data for this session, so the track cannot be drawn."),
      );
      return;
    }
    const [w, h] = track.view;
    const svg = svgEl("svg", { viewBox: `0 0 ${w} ${h}`, role: "img" });
    this.mapTitle = svgEl("title");
    svg.append(this.mapTitle);
    const ribbon = { d: track.path, fill: "none", "stroke-linejoin": "round" };
    svg.append(svgEl("path", { ...ribbon, stroke: "var(--edge)", "stroke-width": 16 }));
    svg.append(svgEl("path", { ...ribbon, stroke: "var(--surface-2)", "stroke-width": 14 }));
    this.tint = svgEl("path", { ...ribbon, stroke: "transparent", "stroke-width": 14, opacity: 0.3 });
    svg.append(this.tint);
    const [x1, y1, x2, y2] = track.sf;
    svg.append(svgEl("line", { x1, y1, x2, y2, stroke: "var(--white)", "stroke-width": 3 }));
    for (const corner of track.corners) {
      const label = svgEl("text", {
        x: corner.x,
        y: corner.y + 5,
        "text-anchor": "middle",
        fill: "var(--text-dim)",
        "font-size": 16,
        "font-family": "var(--font-label)",
      });
      label.textContent = corner.label;
      svg.append(label);
    }
    const layer = svgEl("g");
    for (const driver of this.data.drivers) {
      const team = this.style.teams[driver.code] || {};
      const group = svgEl("g", { visibility: "hidden" });
      group.style.cursor = "pointer";
      const ring = svgEl("circle", {
        r: 20,
        fill: "none",
        stroke: "var(--accent)",
        "stroke-width": 3,
        visibility: "hidden",
      });
      const dot = svgEl("circle", {
        r: 11,
        fill: team.fill || "var(--text-dim)",
        stroke: "var(--bg)",
        "stroke-width": 2,
      });
      const text = svgEl("text", {
        y: -18,
        "text-anchor": "middle",
        fill: "var(--text)",
        "font-size": 18,
        "font-weight": 600,
        "font-family": "var(--font-label)",
      });
      text.textContent = driver.code;
      const tip = svgEl("title");
      tip.textContent = driver.name;
      group.append(tip, ring, dot, text);
      this.on(group, "click", () => this.setFocus(driver.code));
      layer.append(group);
      this.cars[driver.code] = { group, ring, dot, text };
    }
    svg.append(layer);
    this.mapChip = el("span", "rp-chip rp-track-chip");
    this.mapChip.hidden = true;
    this.mapNode.append(svg, this.mapChip);
    this.mapSvg = svg;
    this.view = [w, h];
    if (!this.data.pos) {
      this.mapNode.append(el("div", "rp-map-note", "No car positions for this session."));
    }
  }

  drawTimeline() {
    const node = this.timelineNode;
    const width = Math.max(node.clientWidth || 800, 100);
    const height = 28;
    const { start, end } = this.clock;
    const x = (t) => ((t - start) / Math.max(end - start, 1)) * width;
    const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, width, height });
    // Safety car, VSC and red flag periods, shaded in the flag's colour.
    const flags = this.data.flags;
    flags.forEach(([t, state], index) => {
      if (!SHADED[state]) return;
      const until = index + 1 < flags.length ? flags[index + 1][0] : end;
      const colour = (this.style.flags[state] || [])[0] || "var(--text-dim)";
      const rect = svgEl("rect", {
        x: x(t),
        y: 0,
        width: Math.max(x(until) - x(t), 1),
        height,
        fill: colour,
        opacity: 0.3,
      });
      const title = svgEl("title");
      title.textContent = (this.style.flags[state] || [])[2] || state;
      rect.append(title);
      svg.append(rect);
    });
    // Lap ticks, labelled every five laps.
    const [lapTimes, laps] = this.data.leader_laps;
    lapTimes.forEach((t, index) => {
      const lap = laps[index];
      if (t < start || lap % 5 !== 0) return;
      svg.append(svgEl("line", { x1: x(t), x2: x(t), y1: 0, y2: 6, stroke: "var(--text-dim)" }));
      const label = svgEl("text", { x: x(t) + 2, y: 16, fill: "var(--text-dim)", "font-size": 10 });
      label.textContent = String(lap);
      svg.append(label);
    });
    // Events: distinct shapes, each with a title - never colour alone.
    for (const [t, kind, text] of this.data.events) {
      const cx = x(t);
      let mark = null;
      if (kind === "pit") {
        mark = svgEl("rect", { x: cx - 3, y: 19, width: 6, height: 6, fill: "var(--text-dim)" });
      } else if (kind === "out") {
        mark = svgEl("g", { stroke: "var(--text)", "stroke-width": 1.5 });
        mark.append(
          svgEl("line", { x1: cx - 3, y1: 19, x2: cx + 3, y2: 25 }),
          svgEl("line", { x1: cx + 3, y1: 19, x2: cx - 3, y2: 25 }),
        );
      } else if (kind === "fastest") {
        mark = svgEl("path", {
          d: `M ${cx} 18 L ${cx + 3.5} 22 L ${cx} 26 L ${cx - 3.5} 22 Z`,
          fill: "var(--best)",
        });
      } else if (kind === "flag") {
        mark = svgEl("line", { x1: cx, x2: cx, y1: 0, y2: height, stroke: "var(--text)" });
      }
      if (!mark) continue;
      const title = svgEl("title");
      title.textContent = `${clockText(t - this.clock.lights_out)} ${text}`;
      mark.append(title);
      svg.append(mark);
    }
    this.playhead = svgEl("line", { y1: 0, y2: height, stroke: "var(--accent)", "stroke-width": 2 });
    svg.append(this.playhead);
    node.textContent = "";
    node.append(svg);
    this.timelineX = x;
    this.drawPlayhead();
  }

  drawPlayhead() {
    if (!this.playhead) return;
    const px = this.timelineX(this.cursor);
    this.playhead.setAttribute("x1", px);
    this.playhead.setAttribute("x2", px);
  }

  // ---------------------------------------------------------------- draw
  draw(force) {
    const t = this.cursor;
    this.drawHeader(t);
    this.order = [];
    this.drawTower(t, force);
    this.drawMap(t);
    this.drawRaceControl(t);
    this.drawCard(t);
    this.drawPlayhead();
    setText(this.readout, clockText(t - this.clock.lights_out));
    // The timeline is a slider (UI-15): its value is the readout.
    setAttr(this.timelineNode, "aria-valuenow", Math.round(t - this.clock.lights_out));
    const lap = valueAt(this.data.leader_laps, t, null);
    const lapText = lap ? `Lap ${lap}, ` : "";
    setAttr(this.timelineNode, "aria-valuetext", `${lapText}${clockText(t - this.clock.lights_out)}`);
    this.lastDrawn = t;
  }

  drawHeader(t) {
    const lap = valueAt(this.data.leader_laps, t, null);
    setText(this.lapNode, lap && this.clock.total_laps ? `LAP ${lap}/${this.clock.total_laps}` : "");
    const segments = this.data.segments;
    const index = bisect(segments.map((row) => row[0]), t);
    if (index >= 0) {
      setText(this.clockLabel, `${segments[index][1]} time`);
      setText(this.clockNode, clockText(t - segments[index][0]));
    } else {
      setText(this.clockLabel, this.data.session.kind === "race" ? "Race time" : "Session time");
      setText(this.clockNode, clockText(t - this.clock.lights_out));
    }
    const state = this.flagAt(t);
    const [bg, fg, label] = this.style.flags[state] || ["var(--line)", "var(--text)", state];
    this.flagNode.style.background = bg;
    this.flagNode.style.color = fg;
    setText(this.flagNode, label);
    const reading = bisect(this.weatherTimes, t);
    if (reading < 0) {
      setText(this.weatherNode, "");
      return;
    }
    const [, air, track, , rain, wind, direction] = this.data.weather[reading];
    const parts = [];
    if (air !== null) parts.push(`AIR ${Math.round(air)}${DEGREE}`);
    if (track !== null) parts.push(`TRACK ${Math.round(track)}${DEGREE}`);
    parts.push(rain ? "WET" : "DRY");
    if (wind !== null) {
      const bearing = direction === null ? "" : ` ${CARDINALS[Math.round((direction % 360) / 45) % 8]}`;
      parts.push(`WIND ${Math.round(wind)} km/h${bearing}`);
    }
    setText(this.weatherNode, parts.join("  "));
  }

  drawTower(t, force) {
    const ranked = this.towerAt(t);
    const stepped = this.lastDrawn === null ? Infinity : t - this.lastDrawn;
    let y = 0;
    ranked.forEach((data, rank) => {
      const entry = this.rows[data.code];
      if (!entry) return;
      this.order.push(data.code);
      const { row, cells, heading } = entry;
      if (data.partition) {
        heading.hidden = false;
        setText(heading, data.partition);
        heading.style.transform = `translateY(${y}px)`;
        y += HEADING;
      } else if (!heading.hidden) {
        heading.hidden = true;
      }
      row.style.transform = `translateY(${y}px)`;
      y += ROW;
      setClass(row, "odd", rank % 2 === 1);
      setClass(row, "focused", this.focus === data.code);
      setAttr(row, "aria-pressed", this.focus === data.code);
      setClass(row, "out", data.status === "OUT" || data.status === "KO");
      setClass(cells.code, "flying", Boolean(data.flying));
      cells.code.title = data.flying ? "On a flying lap" : "";
      // Numbered by rank, as the server tower is: the stream can briefly
      // give two cars the same position.
      setText(cells.pos, rank + 1);
      setClass(cells.pos, "top", rank < 3);
      const interval = !this.timed && this.mode === "int";
      setText(cells.gap, interval ? data.int : data.gap);
      setClass(cells.gap, "close", interval && Boolean(data.close));
      setText(cells.last, data.last);
      setClass(cells.last, "sb", data.last_flag === "sb");
      setClass(cells.last, "pb", data.last_flag === "pb");
      // A lap that has just completed as a best flashes once; a seek across
      // the session must not flash every row.
      const changed = entry.last !== null && entry.last !== data.last;
      if (!force && changed && data.last_flag && stepped > 0 && stepped < 5) {
        cells.last.classList.remove("rp-flash-sb", "rp-flash-pb");
        void cells.last.offsetWidth;
        cells.last.classList.add(data.last_flag === "sb" ? "rp-flash-sb" : "rp-flash-pb");
      }
      entry.last = data.last;
      const compound = this.style.compounds[data.tyre] || {};
      setText(cells.tyreBadge, data.tyre ? compound.letter || "?" : DASH);
      cells.tyreBadge.style.borderColor = compound.colour || "var(--text-dim)";
      cells.tyreBadge.style.color = compound.colour || "var(--text-dim)";
      setClass(cells.tyreBadge, "used", data.new === false);
      cells.tyreBadge.title = data.tyre
        ? `${data.tyre.toLowerCase()}${data.new === false ? ", used" : ""}`
        : "";
      setText(cells.tyreAge, data.age === null ? "" : data.age);
      if (this.timed) {
        setText(cells.best, data.best);
        for (const name of ["s1", "s2", "s3"]) {
          setText(cells[name], data[name] === null ? null : data[name].toFixed(3));
        }
      } else {
        setText(cells.pits, data.pits);
        const chip = STATUS_CHIPS[data.status] || "";
        if (cells.status.textContent !== chip) {
          cells.status.textContent = "";
          if (chip) cells.status.append(el("span", chip === "PIT" ? "rp-chip pit" : "rp-chip", chip));
        }
      }
    });
    this.rowsNode.style.height = `${y}px`;
  }

  drawMap(t) {
    if (!this.mapSvg) return;
    const state = this.flagAt(t);
    const colour = (this.style.flags[state] || [])[0];
    this.tint.setAttribute("stroke", SHADED[state] && colour ? colour : "transparent");
    this.mapChip.hidden = !MAP_CHIPS[state];
    if (MAP_CHIPS[state]) {
      const [bg, fg] = this.style.flags[state] || ["var(--line)", "var(--text)"];
      this.mapChip.style.background = bg;
      this.mapChip.style.color = fg;
      setText(this.mapChip, MAP_CHIPS[state]);
    }
    let focusedAt = null;
    for (const driver of this.data.drivers) {
      const car = this.cars[driver.code];
      const at = this.carAt(driver.code, t);
      if (!at) {
        car.group.setAttribute("visibility", "hidden");
        continue;
      }
      const focused = this.focus === driver.code;
      car.group.setAttribute("visibility", "visible");
      car.group.setAttribute("transform", `translate(${at[0].toFixed(1)} ${at[1].toFixed(1)})`);
      car.ring.setAttribute("visibility", focused ? "visible" : "hidden");
      car.dot.setAttribute("r", focused ? 15 : 11);
      car.text.setAttribute("visibility", this.labels || focused ? "visible" : "hidden");
      car.group.setAttribute("opacity", this.focus && !focused ? 0.45 : 1);
      if (focused) focusedAt = at;
    }
    const [w, h] = this.view;
    if (this.follow && focusedAt) {
      const zw = w * 0.4;
      const zh = h * 0.4;
      this.mapSvg.setAttribute("viewBox", `${focusedAt[0] - zw / 2} ${focusedAt[1] - zh / 2} ${zw} ${zh}`);
    } else {
      this.mapSvg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    }
    const lap = valueAt(this.data.leader_laps, t, null);
    const lapText = lap ? `lap ${lap}, ` : "";
    this.mapTitle.textContent = `${this.data.session.event} ${this.data.session.name}, ${lapText}${clockText(t - this.clock.lights_out)}`;
  }

  drawCard(t) {
    const code = this.focus;
    setHidden(this.card, !code);
    if (!code) {
      this.cardKey = null;
      return;
    }
    const driver = this.data.drivers.find((entry) => entry.code === code) || { code, name: code };
    setText(this.cardTitle, driver.team ? `${driver.code} ${DOT} ${driver.name} ${DOT} ${driver.team}` : driver.code);
    const fields = this.data.tower[code] || {};
    const defaults = this.data.defaults;
    const tyre = valueAt(fields.tyre, t, null);
    const age = valueAt(fields.age, t, null);
    const fresh = valueAt(fields.new, t, null);
    const pits = valueAt(fields.pits, t, defaults.pits);
    const tyreText = tyre ? `${tyre.toLowerCase()}${age === null ? "" : `, ${age} laps`}${fresh === false ? ", used" : ""}` : DASH;
    setText(this.cardFacts, `Tyre ${tyreText}   Pits ${pits ?? DASH}`);
    const done = this.data.laps[code] || [];
    const count = bisect(this.lapTimes[code] || [], t) + 1;
    const laps = done.slice(Math.max(0, count - 5), count);
    // Sparkline of the interval to the car ahead over the last five minutes.
    const trend = this.data.trend;
    const values = (this.trend || {})[code] || [];
    const last = Math.floor((t - trend.t0) / trend.step);
    // The laps list and the sparkline change only with a new lap or a new
    // trend sample; rebuilding them every frame was ~60 mutations a second
    // (UI-23, layout.md 9.2).
    const cardKey = `${code}|${count}|${last}|${values.length}`;
    if (cardKey === this.cardKey) return;
    this.cardKey = cardKey;
    this.cardLaps.textContent = "";
    for (const [, number, text, flag] of laps) {
      const line = el("div", flag === "sb" ? "rp-last sb" : flag === "pb" ? "rp-last pb" : "");
      line.textContent = `L${number ?? DASH}  ${text}`;
      this.cardLaps.append(line);
    }
    this.cardLap = laps.length ? laps[laps.length - 1][1] : null;
    setDisabled(this.analyseButton, this.cardLap === null);
    const first = Math.max(0, last - Math.round(300 / trend.step));
    const points = [];
    for (let i = first; i <= last && i < values.length; i += 1) {
      // NaN marks no interval: no timing yet, or leading (REPLAY-21).
      if (Number.isFinite(values[i])) points.push([i - first, values[i]]);
    }
    this.cardTrend.textContent = "";
    if (points.length > 1) {
      const width = 240;
      const height = 32;
      const span = Math.max(last - first, 1);
      const top = Math.max(...points.map((p) => p[1]), 1);
      const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, width, height, role: "img" });
      const title = svgEl("title");
      title.textContent = `Interval to the car ahead: ${points[points.length - 1][1].toFixed(3)} s`;
      svg.append(title);
      const path = points.map(([x, y], index) => `${index ? "L" : "M"} ${(x / span) * width} ${height - 2 - (y / top) * (height - 4)}`).join(" ");
      svg.append(svgEl("path", { d: path, fill: "none", stroke: "var(--text-dim)", "stroke-width": 1.5 }));
      this.cardTrend.append(svg);
    }
  }

  drawRaceControl(t) {
    const messages = this.data.rcm;
    const index = bisect(this.rcTimes, t);
    this.rcLines.forEach((line, offset) => {
      const message = messages[index - offset];
      if (!message || index - offset < 0) {
        setText(line, "");
        return;
      }
      const [at, lap, , , text] = message;
      const lapText = lap === null ? "" : `L${lap} `;
      setText(line, `${lapText}${clockText(at - this.clock.lights_out)}  ${text}`);
    });
  }

  // ------------------------------------------------------------ controls
  on(target, type, handler) {
    target.addEventListener(type, handler);
    this.listeners.push([target, type, handler]);
  }

  toggleLabels() {
    this.labels = !this.labels;
    setClass(this.labelButton, "on", this.labels);
    setAttr(this.labelButton, "aria-pressed", this.labels);
    this.draw(true);
  }

  toggleFollow() {
    this.follow = !this.follow;
    setClass(this.followButton, "on", this.follow);
    setAttr(this.followButton, "aria-pressed", this.follow);
    this.draw(true);
  }

  setMode(mode) {
    this.mode = mode;
    setClass(this.gapButton, "on", mode === "gap");
    setClass(this.intervalButton, "on", mode === "int");
    setAttr(this.gapButton, "aria-pressed", mode === "gap");
    setAttr(this.intervalButton, "aria-pressed", mode === "int");
    setText(this.gapHeading, mode === "gap" ? "Gap" : "Interval");
    this.draw(true);
  }

  setFocus(code) {
    this.focus = this.focus === code ? null : code;
    this.reportCursor();
    this.report("focus", this.focus);
    this.draw(true);
  }

  reportCursor() {
    if (this.reported !== this.cursor) this.report("cursor", this.cursor);
  }

  report(name, value) {
    if (name === "cursor") this.reported = value;
    try {
      this.setStateValue(name, value);
    } catch (error) {
      console.warn("replay player: could not report", name, error);
    }
  }

  // Keyboard seeks arrive in bursts (a held arrow key); report once they stop.
  seekSoon(t) {
    this.seek(t, { report: false });
    if (this.keyTimer) clearTimeout(this.keyTimer);
    this.keyTimer = setTimeout(() => {
      this.keyTimer = null;
      if (!this.playing) this.reportCursor();
    }, 400);
  }

  seek(t, options = {}) {
    this.cursor = this.clamp(t);
    this.draw(true);
    if (options.report !== false && !this.playing) this.reportCursor();
  }

  lap(direction) {
    // Lap completions: the leader's in a race, anyone's in qualifying and
    // practice (which have no leader laps; stepping those jumped to the end).
    const marks = this.data.lap_marks || this.data.leader_laps[0];
    const times = marks.filter((t) => t > this.clock.start);
    const landing = 1;
    if (direction > 0) {
      const next = times.find((t) => t + landing > this.cursor + 1e-6);
      this.seek(next === undefined ? this.clock.end : next + landing);
    } else {
      const earlier = times.filter((t) => t + landing < this.cursor - 1e-6);
      this.seek(earlier.length ? earlier[earlier.length - 1] + landing : this.clock.lights_out);
    }
  }

  toggle() {
    if (this.playing) this.pause();
    else this.play();
  }

  play() {
    if (this.cursor >= this.clock.end) this.cursor = this.clock.lights_out;
    this.playing = true;
    this.playUse.setAttribute("href", "#rp-icon-pause");
    this.playButton.setAttribute("aria-label", "Pause");
    this.playButton.title = "Pause (Space)";
    this.lastTick = null;
    const tick = (now) => {
      if (!this.playing) return;
      if (this.lastTick !== null) {
        const advance = ((now - this.lastTick) / 1000) * this.speed;
        this.cursor = Math.min(this.cursor + advance, this.clock.end);
      }
      this.lastTick = now;
      this.draw(false);
      if (this.cursor >= this.clock.end) {
        this.pause();
        return;
      }
      this.frame = requestAnimationFrame(tick);
    };
    this.frame = requestAnimationFrame(tick);
  }

  pause() {
    this.playing = false;
    if (this.frame !== null) cancelAnimationFrame(this.frame);
    this.frame = null;
    this.playUse.setAttribute("href", "#rp-icon-play");
    this.playButton.setAttribute("aria-label", "Play");
    this.playButton.title = "Play (Space)";
    this.report("cursor", this.cursor);
  }

  onRowKey(event, code) {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === "Enter" || event.key === " " || event.code === "Space") {
      // A row is a button: Enter and Space focus the driver, not play.
      event.preventDefault();
      event.stopPropagation();
      this.setFocus(code);
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      // Moves the keyboard focus only; nothing is reported (each report
      // reruns the Python script).
      event.preventDefault();
      event.stopPropagation();
      const at = this.order.indexOf(code);
      const next = this.order[at + (event.key === "ArrowDown" ? 1 : -1)];
      if (next && this.rows[next]) this.rows[next].row.focus({ preventScroll: true });
    }
  }

  onKey(event) {
    // Ctrl/Cmd/Alt combinations belong to the browser: find in page,
    // switch tab, history (UI-16).
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    const target = event.target;
    if (target && target.tagName === "SELECT") return;
    const onButton = target && target.tagName === "BUTTON";
    if (onButton && (event.key === "Enter" || event.key === " " || event.code === "Space")) return;
    if (target === this.timelineNode && (event.key === "Home" || event.key === "End")) {
      event.preventDefault();
      this.seekSoon(event.key === "Home" ? this.clock.lights_out : this.clock.end);
      return;
    }
    if (event.code === "Space") {
      event.preventDefault();
      this.toggle();
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      this.seekSoon(this.cursor + (event.shiftKey ? 30 : 5));
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      this.seekSoon(this.cursor - (event.shiftKey ? 30 : 5));
    } else if (event.key === "]") {
      this.lap(1);
    } else if (event.key === "[") {
      this.lap(-1);
    } else if (/^[1-8]$/.test(event.key)) {
      this.speed = SPEEDS[Number(event.key) - 1];
      this.speedSelect.value = String(this.speed);
    } else if (event.key === "l" || event.key === "L") {
      this.toggleLabels();
    } else if (event.key === "f" || event.key === "F") {
      this.toggleFollow();
    }
  }

  update(data, setStateValue) {
    this.setStateValue = setStateValue;
    // Python moved the cursor (a jump from outside the player).
    if ((data.seek ?? 0) !== this.seekSeen) {
      this.seekSeen = data.seek ?? 0;
      if (this.playing) {
        this.playing = false;
        if (this.frame !== null) cancelAnimationFrame(this.frame);
        this.frame = null;
        this.playUse.setAttribute("href", "#rp-icon-play");
        this.playButton.setAttribute("aria-label", "Play");
        this.playButton.title = "Play (Space)";
      }
      this.cursor = this.clamp(data.cursor ?? this.cursor);
      this.reported = this.cursor;
      this.draw(true);
    }
  }

  destroy() {
    this.reportCursor();
    if (this.keyTimer) clearTimeout(this.keyTimer);
    this.playing = false;
    if (this.frame !== null) cancelAnimationFrame(this.frame);
    if (this.resize) this.resize.disconnect();
    for (const [target, type, handler] of this.listeners) target.removeEventListener(type, handler);
    this.listeners = [];
  }
}

export default function (component) {
  const { data, parentElement, setStateValue } = component;
  const root = parentElement.querySelector(".rp");
  if (!root || !data || !data.clock) return undefined;
  let player = INSTANCES.get(parentElement);
  if (player && player.key !== data.session_key) {
    player.destroy();
    player = null;
  }
  if (!player) {
    player = new Player(root, data, setStateValue);
    INSTANCES.set(parentElement, player);
  } else {
    player.update(data, setStateValue);
  }
  return () => {
    const current = INSTANCES.get(parentElement);
    if (current) {
      current.destroy();
      INSTANCES.delete(parentElement);
    }
  };
}
