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
// change reruns the script.

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
    this.flagTimes = data.flags.map((row) => row[0]);
    this.rcTimes = data.rcm.map((row) => row[0]);
    this.weatherTimes = data.weather.map((row) => row[0]);
    this.decodePositions();
    this.build();
    this.draw(true);
  }

  // ---------------------------------------------------------------- data
  clamp(t) {
    return Math.min(Math.max(t, this.clock.start), this.clock.end);
  }

  decodePositions() {
    const pos = this.data.pos;
    this.xy = null;
    this.carIndex = {};
    if (!pos) return;
    const binary = atob(pos.xy_b64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    this.xy = new Int16Array(bytes.buffer);
    pos.codes.forEach((code, index) => {
      this.carIndex[code] = index;
    });
  }

  carAt(code, t) {
    const pos = this.data.pos;
    const d = this.carIndex[code];
    if (!pos || d === undefined) return null;
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
      this.on(this.gapButton, "click", () => this.setMode("gap"));
      this.on(this.intervalButton, "click", () => this.setMode("int"));
      toggle.append(this.gapButton, this.intervalButton);
      tower.append(toggle);
    }
    this.buildRows(cols);

    const side = el("section", "rp-side");
    this.mapNode = el("div", "rp-map");
    side.append(this.mapNode);
    this.buildMap();
    this.rcNode = el("div", "rp-rc");
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
    this.on(this.labelButton, "click", () => this.toggleLabels());
    controls.append(this.labelButton);
    const rcToggle = el("button", "rp-rc-toggle", "Race control");
    this.on(rcToggle, "click", () => this.rcNode.classList.toggle("open"));
    controls.append(rcToggle);
    this.readout = el("span", "rp-readout");
    controls.append(this.readout);
    body.append(controls);

    // Timeline
    this.timelineNode = el("div", "rp-timeline");
    this.timelineNode.setAttribute("aria-label", "Timeline: click or drag to seek");
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
      this.seek(momentAt(event));
    });
    this.on(this.timelineNode, "pointermove", (event) => {
      const t = momentAt(event);
      const lap = valueAt(this.data.leader_laps, t, null);
      const lapText = lap ? `Lap ${lap} ${DOT} ` : "";
      this.timelineNode.title = `${lapText}${clockText(t - this.clock.lights_out)}`;
      if (dragging) this.seek(t);
    });
    this.on(window, "pointerup", () => {
      dragging = false;
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
      this.on(row, "click", () => this.setFocus(driver.code));
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
    this.drawTower(t, force);
    this.drawMap(t);
    this.drawRaceControl(t);
    this.drawPlayhead();
    setText(this.readout, clockText(t - this.clock.lights_out));
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
    this.draw(true);
  }

  setMode(mode) {
    this.mode = mode;
    setClass(this.gapButton, "on", mode === "gap");
    setClass(this.intervalButton, "on", mode === "int");
    setText(this.gapHeading, mode === "gap" ? "Gap" : "Interval");
    this.draw(true);
  }

  setFocus(code) {
    this.focus = this.focus === code ? null : code;
    this.report("focus", this.focus);
    this.draw(true);
  }

  report(name, value) {
    try {
      this.setStateValue(name, value);
    } catch (error) {
      console.warn("replay player: could not report", name, error);
    }
  }

  seek(t) {
    this.cursor = this.clamp(t);
    this.draw(true);
    if (!this.playing) this.report("cursor", this.cursor);
  }

  lap(direction) {
    const times = this.data.leader_laps[0].filter((t) => t > this.clock.start);
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

  onKey(event) {
    const target = event.target;
    if (target && target.tagName === "SELECT") return;
    if (event.code === "Space") {
      event.preventDefault();
      this.toggle();
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      this.seek(this.cursor + (event.shiftKey ? 30 : 5));
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      this.seek(this.cursor - (event.shiftKey ? 30 : 5));
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
      this.follow = !this.follow;
      this.draw(true);
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
      }
      this.cursor = this.clamp(data.cursor ?? this.cursor);
      this.draw(true);
    }
  }

  destroy() {
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
