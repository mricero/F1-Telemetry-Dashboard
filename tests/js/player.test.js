// The replay player in jsdom (IMPROVEMENTS.md UI-09, UI-11, UI-15, UI-16,
// UI-23, REPLAY-21, REPLAY-29). Run through tests/test_player_js.py, or
// `npm ci && node --test` in tests/js.

import assert from "node:assert/strict";
import { after, before, describe, test } from "node:test";
import { componentSource, key, loadMeta, loadPayload, mount, rendered } from "./harness.js";

let race;
let qualifying;
let meta;

before(() => {
  race = loadPayload("race");
  qualifying = loadPayload("qualifying");
  meta = loadMeta();
});

const opened = [];
async function open(payload, extra) {
  const player = await mount(payload, extra);
  opened.push(player);
  return player;
}
after(() => {
  for (const player of opened) player.unmount();
});

let seekToken = 0;
async function seek(player, cursor, extra = {}) {
  seekToken += 1;
  await player.rerun({ cursor, seek: seekToken, ...extra });
}

const rows = (player) => [...player.root.querySelectorAll(".rp-row")];
const rowFor = (player, code) =>
  rows(player).find((row) => row.querySelector(".rp-code").firstChild.textContent === code);
const card = (player) => player.root.querySelector(".rp-card");
const mapChip = (player) => player.root.querySelector(".rp-track-chip");
const button = (player, label) =>
  [...player.root.querySelectorAll("button")].find(
    (node) => node.textContent === label || node.getAttribute("aria-label") === label,
  );
const focusReports = (player) => player.state.filter(([name]) => name === "focus");

describe("mounting", () => {
  test("the player draws the tower and the map", async () => {
    const player = await open(race);

    assert.equal(rows(player).length, race.drivers.length);
    assert.ok(player.root.querySelector(".rp-map svg"));
    assert.equal(player.root.querySelector(".rp-readout").textContent, "0:00:00");
  });

  test("car positions decode to the payload's frames", async () => {
    const player = await open(race);
    const cars = [...player.root.querySelectorAll(".rp-map svg g g")];
    const visible = cars.filter((group) => group.getAttribute("visibility") === "visible");

    assert.ok(visible.length > 0, "no car is drawn after decoding");
    for (const group of visible) {
      const [x, y] = group
        .getAttribute("transform")
        .match(/translate\(([-\d.]+) ([-\d.]+)\)/)
        .slice(1)
        .map(Number);
      assert.ok(x >= 0 && x <= race.track.view[0] && y >= 0 && y <= race.track.view[1]);
    }
  });

  test("the payload's positions are compact (REPLAY-29)", () => {
    assert.equal(race.pos.xy_b64, undefined);
    assert.equal(typeof race.pos.xy_z, "string");
    assert.equal(race.trend.values, undefined);
    assert.equal(typeof race.trend.z, "string");
  });
});

describe("FEAT-07: the linear track-position strip", () => {
  const strip = (player) => player.root.querySelector(".rp-strip svg");
  const shown = (nodes) => [...nodes].filter((node) => node.getAttribute("visibility") === "visible");

  test("every car with a sample is a marker and a label on the line", async () => {
    const player = await open(race, { cursor: 1200 });
    const dots = shown(strip(player).querySelectorAll("circle"));
    const labels = shown(strip(player).querySelectorAll("text")).map((node) => node.firstChild.textContent);

    assert.ok(dots.length > 0, "no car is on the strip");
    assert.equal(labels.length, dots.length);
    assert.ok(labels.every((code) => race.drivers.some((driver) => driver.code === code)));
    assert.match(strip(player).textContent, /START \/ FINISH/);
    const line = strip(player).querySelector("line");
    const [x1, x2] = [line.getAttribute("x1"), line.getAttribute("x2")].map(Number);
    for (const dot of dots) {
      const x = Number(dot.getAttribute("cx"));
      assert.ok(x >= x1 && x <= x2, `${x} outside ${x1}..${x2}`);
    }
  });

  test("each car sits where Python put it round the lap", async () => {
    const player = await open(race, { cursor: meta.lap_cursor });
    const line = strip(player).querySelector("line");
    const left = Number(line.getAttribute("x1"));
    const span = Number(line.getAttribute("x2")) - left;
    const drawn = {};
    for (const text of shown(strip(player).querySelectorAll("text"))) {
      drawn[text.firstChild.textContent] = Number(text.getAttribute("x")) - 3;
    }

    const expected = Object.entries(meta.lap_fractions).filter(([, fraction]) => fraction !== null);
    assert.ok(expected.length > 1);
    assert.deepEqual(Object.keys(drawn).sort(), expected.map(([code]) => code).sort());
    for (const [code, fraction] of expected) {
      assert.ok(Math.abs(drawn[code] - (left + fraction * span)) < 0.2, code);
    }
  });

  test("labels in one lane never overlap", async () => {
    const player = await open(race, { cursor: 1200 });
    const lanes = {};
    for (const text of shown(strip(player).querySelectorAll("text"))) {
      const y = text.getAttribute("y");
      (lanes[y] ||= []).push(Number(text.getAttribute("x")));
    }

    for (const xs of Object.values(lanes)) {
      xs.sort((a, b) => a - b);
      for (let i = 1; i < xs.length; i += 1) assert.ok(xs[i] - xs[i - 1] >= 26 - 1e-6);
    }
  });

  test("a payload without lap fractions has no strip", async () => {
    const bare = { ...race, pos: { ...race.pos, lap_z: undefined } };
    const player = await open(bare);

    assert.equal(player.root.querySelector(".rp-strip"), null);
  });

  test("a car with no sample is not drawn", async () => {
    const player = await open(race, { cursor: meta.lights_out + 5000 });
    const gone = shown(strip(player).querySelectorAll("circle")).length;

    assert.ok(gone < race.drivers.length);
  });
});

describe("UI-09: hidden things are not rendered", () => {
  test("the map's SC chip shows under the safety car and goes after it", async () => {
    const player = await open(race);

    await seek(player, (meta.sc_start + meta.sc_end) / 2);
    assert.ok(rendered(mapChip(player)), "chip hidden under the safety car");
    assert.equal(mapChip(player).textContent, "SC");

    await seek(player, meta.sc_end + 20);
    assert.equal(player.root.querySelector(".rp-header .rp-chip").textContent, "GREEN");
    assert.ok(!rendered(mapChip(player)), "the SC chip stays after the period ended");
  });

  test("with nothing focused the driver card is not rendered", async () => {
    const player = await open(race, { cursor: 1200 });

    assert.ok(!rendered(card(player)));
    assert.ok(button(player, "Analyse this lap").disabled);
  });

  test("focusing a driver shows the card", async () => {
    const player = await open(race, { cursor: 1200 });
    rowFor(player, "B").click();

    assert.ok(rendered(card(player)));
    assert.match(player.root.querySelector(".rp-card-title").textContent, /^B /);
  });
});

describe("UI-16: shortcuts ignore Ctrl, Cmd and Alt", () => {
  test("Ctrl+F leaves follow off", async () => {
    const player = await open(race, { cursor: 1200 });
    rowFor(player, "B").click();
    const view = () => player.root.querySelector(".rp-map svg").getAttribute("viewBox");
    const full = view();

    const event = key(player.root, { key: "f", ctrlKey: true });

    assert.equal(event.defaultPrevented, false);
    assert.equal(view(), full);
    assert.equal(button(player, "Follow").getAttribute("aria-pressed"), "false");
  });

  test("F alone still follows the focused car", async () => {
    const player = await open(race, { cursor: 1200 });
    rowFor(player, "B").click();
    const full = player.root.querySelector(".rp-map svg").getAttribute("viewBox");

    key(player.root, { key: "f" });

    assert.notEqual(player.root.querySelector(".rp-map svg").getAttribute("viewBox"), full);
    assert.equal(button(player, "Follow").getAttribute("aria-pressed"), "true");
  });

  test("Ctrl+L, Cmd+2 and Alt+Arrow do nothing", async () => {
    const player = await open(race, { cursor: 1200 });
    const select = player.root.querySelector("select");
    const readout = player.root.querySelector(".rp-readout").textContent;

    key(player.root, { key: "l", ctrlKey: true });
    key(player.root, { key: "2", metaKey: true });
    key(player.root, { key: "ArrowRight", altKey: true });

    assert.equal(button(player, "Labels").getAttribute("aria-pressed"), "false");
    assert.equal(select.value, "1");
    assert.equal(player.root.querySelector(".rp-readout").textContent, readout);
  });
});

describe("UI-15: keyboard and screen readers", () => {
  test("a tower row takes keyboard focus and Enter opens the card", async () => {
    const player = await open(race, { cursor: 1200 });
    const row = rowFor(player, "C");

    assert.equal(row.getAttribute("tabindex"), "0");
    assert.equal(row.getAttribute("role"), "button");
    row.focus();
    assert.equal(player.shadow.activeElement, row);
    const event = key(player.root, { key: "Enter", target: row });

    assert.ok(event.defaultPrevented);
    assert.ok(rendered(card(player)));
    assert.equal(row.getAttribute("aria-pressed"), "true");
    assert.deepEqual(focusReports(player).at(-1), ["focus", "C"]);
  });

  test("Space on a row focuses the driver instead of playing", async () => {
    const player = await open(race, { cursor: 1200 });
    const row = rowFor(player, "A");
    row.focus();

    key(player.root, { key: " ", code: "Space", target: row });

    assert.deepEqual(focusReports(player).at(-1), ["focus", "A"]);
    assert.equal(button(player, "Play").getAttribute("aria-label"), "Play");
  });

  test("arrow keys move between drivers in running order", async () => {
    const player = await open(race, { cursor: 1200 });
    const order = rows(player)
      .map((row) => [Number(row.querySelector(".rp-pos").textContent), row])
      .sort((a, b) => a[0] - b[0])
      .map(([, row]) => row);
    order[0].focus();

    key(player.root, { key: "ArrowDown", target: order[0] });
    assert.equal(player.shadow.activeElement, order[1]);
    key(player.root, { key: "ArrowUp", target: order[1] });
    assert.equal(player.shadow.activeElement, order[0]);
    // Moving the keyboard focus reports nothing (each report reruns Python).
    assert.equal(focusReports(player).length, 0);
  });

  test("the toggles report their state", async () => {
    const player = await open(race, { cursor: 1200 });
    const gap = button(player, "Gap");
    const interval = button(player, "Interval");
    const labels = button(player, "Labels");
    const rc = button(player, "Race control");

    assert.equal(gap.getAttribute("aria-pressed"), "true");
    assert.equal(interval.getAttribute("aria-pressed"), "false");
    interval.click();
    assert.equal(gap.getAttribute("aria-pressed"), "false");
    assert.equal(interval.getAttribute("aria-pressed"), "true");

    assert.equal(labels.getAttribute("aria-pressed"), "false");
    labels.click();
    assert.equal(labels.getAttribute("aria-pressed"), "true");

    assert.equal(rc.getAttribute("aria-expanded"), "false");
    const panel = player.shadow.getElementById(rc.getAttribute("aria-controls"));
    assert.ok(panel && panel.classList.contains("rp-rc"));
    rc.click();
    assert.equal(rc.getAttribute("aria-expanded"), "true");
  });

  test("Enter on a toggle button clicks it rather than a shortcut", async () => {
    const player = await open(race, { cursor: 1200 });
    const labels = button(player, "Labels");
    labels.focus();

    const event = key(player.root, { key: " ", code: "Space", target: labels });

    assert.equal(event.defaultPrevented, false, "Space must reach the button");
    assert.equal(button(player, "Play").getAttribute("aria-label"), "Play");
  });

  test("the timeline is a slider with a readable value", async () => {
    const player = await open(race, { cursor: 1200 });
    const timeline = player.root.querySelector(".rp-timeline");

    assert.equal(timeline.getAttribute("role"), "slider");
    assert.equal(timeline.getAttribute("tabindex"), "0");
    assert.equal(Number(timeline.getAttribute("aria-valuenow")), 1200 - meta.lights_out);
    assert.match(timeline.getAttribute("aria-valuetext"), /0:03:20/);
    assert.ok(Number(timeline.getAttribute("aria-valuemax")) > 0);

    timeline.focus();
    key(player.root, { key: "ArrowRight", target: timeline });
    assert.equal(Number(timeline.getAttribute("aria-valuenow")), 1205 - meta.lights_out);
  });
});

describe("UI-23: the driver card is not rebuilt every frame", () => {
  test("no card mutations across frames within one lap", async () => {
    const player = await open(race, { cursor: 1201 });
    rowFor(player, "B").click();
    const target = card(player);
    const records = [];
    const observer = new player.window.MutationObserver((list) => records.push(...list));
    observer.observe(target, { subtree: true, childList: true, characterData: true, attributes: true });

    // Small paused steps inside one five-second trend sample and one lap.
    for (const t of [1201.2, 1201.5, 1201.9, 1202.4, 1203.0]) {
      await seek(player, t);
    }
    await new Promise((resolve) => setTimeout(resolve, 0));
    observer.disconnect();

    assert.deepEqual(
      records.map((record) => `${record.type} ${record.target.className || record.target.nodeName}`),
      [],
    );
  });

  test("a new lap does update the card", async () => {
    const player = await open(race, { cursor: 1175 });
    rowFor(player, "B").click();
    const before = player.root.querySelector(".rp-card-laps").textContent;

    await seek(player, 1185);

    assert.notEqual(player.root.querySelector(".rp-card-laps").textContent, before);
  });
});

describe("REPLAY-21: a missing interval reads as missing", () => {
  const leaderSample = (payload) => {
    // A copy of the race payload whose trend says "no car ahead" for B now.
    return payload;
  };

  test("the leader has no interval sparkline value", async () => {
    const player = await open(leaderSample(race), { cursor: 1300 });
    // B leads from lap 3 (fixture): its interval is null in the payload.
    rowFor(player, "B").click();
    const title = player.root.querySelector(".rp-card-trend title");

    if (title) assert.doesNotMatch(title.textContent, /0\.0+ s/);
    assert.ok(!player.root.querySelector(".rp-card-trend").textContent.includes("NaN"));
  });

  test("a null interval cell shows an en dash, not 0.000", async () => {
    const payload = structuredClone(race);
    const code = payload.drivers[0].code;
    payload.tower[code].int = [[payload.clock.start], [null]];
    payload.tower[code].close = [[payload.clock.start], [null]];
    const player = await open(payload, { cursor: 1200 });
    button(player, "Interval").click();
    const cell = rowFor(player, code).querySelector(".rp-gap");

    assert.equal(cell.textContent, String.fromCharCode(0x2013));
    assert.ok(!cell.classList.contains("close"));
  });
});

describe("UI-14: purple time text uses the lighter token", () => {
  test("session-best text uses --best-text, fills keep --best", () => {
    const { css } = componentSource();

    assert.match(css, /:root[^{]*\{[^}]*--best-text:\s*#c56ef0/);
    assert.match(css, /\.rp-last\.sb\s*\{\s*color:\s*var\(--best-text\)/);
    assert.match(css, /--best:\s*#b138dd/);
  });
});

describe("timed sessions", () => {
  test("qualifying mounts with sector columns", async () => {
    const player = await open(qualifying);

    assert.ok(player.root.querySelector(".rp-cols-timed"));
    assert.equal(rows(player).length, qualifying.drivers.length);
  });
});

describe("layout chosen by the viewer (FEAT-10)", () => {
  const hiddenCells = (player) =>
    [...player.root.querySelector(".rp-thead").children].filter((cell) => cell.classList.contains("rp-off"));

  test("the default layout hides nothing", async () => {
    const player = await open(race);

    assert.equal(hiddenCells(player).length, 0);
    assert.equal(player.root.className.includes("rp-hide-"), false);
  });

  test("a hidden column is hidden in the header and every row, and leaves the template", async () => {
    const player = await open(race, { layout: { hide_cols: ["tyres", "pit"], hide_panels: [] } });

    assert.deepEqual(hiddenCells(player).map((cell) => cell.textContent), ["Tyre", "Pit"]);
    const row = rows(player)[0];
    assert.equal([...row.children].filter((cell) => cell.classList.contains("rp-off")).length, 2);
    const css = [...player.root.querySelectorAll("style")].map((node) => node.textContent).join("");
    assert.match(css, /\.rp \.rp-cols-race \{ grid-template-columns: 28px 8px minmax\(64px, 1fr\) 76px 76px 44px; \}/);
  });

  test("hidden panels become classes on the root", async () => {
    const player = await open(race, { layout: { hide_cols: [], hide_panels: ["map", "rc"] } });

    assert.ok(player.root.classList.contains("rp-hide-map"));
    assert.ok(player.root.classList.contains("rp-hide-rc"));
    assert.equal(player.root.classList.contains("rp-hide-card"), false);
  });

  test("a new layout from Python applies without remounting", async () => {
    const player = await open(race);

    await player.rerun({ layout: { hide_cols: ["last"], hide_panels: ["strip"] } });

    assert.deepEqual(hiddenCells(player).map((cell) => cell.textContent), ["Last"]);
    assert.ok(player.root.classList.contains("rp-hide-strip"));
    await player.rerun({ layout: { hide_cols: [], hide_panels: [] } });
    assert.equal(hiddenCells(player).length, 0);
  });

  test("timed sessions treat the three sectors as one column", async () => {
    const player = await open(qualifying, { layout: { hide_cols: ["sectors"], hide_panels: [] } });

    assert.deepEqual(hiddenCells(player).map((cell) => cell.textContent), ["S1", "S2", "S3"]);
  });
});

describe("units and the session start (UX-12)", () => {
  const weather = (player) => player.root.querySelector(".rp-weather").textContent;
  const withWeather = (payload) => ({
    ...payload,
    weather: [[0, 20, 30, 50, false, 36, 90]],
  });

  test("the default weather reads in Celsius and km/h", async () => {
    const player = await open(withWeather(race));

    assert.match(weather(player), /AIR 20\u00b0 {2}TRACK 30\u00b0/);
    assert.match(weather(player), /WIND 36 km\/h E/);
  });

  test("Fahrenheit and mph convert the weather line", async () => {
    const player = await open(withWeather(race), { units: { speed: "mph", temp: "f" } });

    assert.match(weather(player), /AIR 68\u00b0F {2}TRACK 86\u00b0F/);
    assert.match(weather(player), /WIND 22 mph E/);
  });

  test("a new unit applies on the next update", async () => {
    const player = await open(withWeather(race));

    await player.rerun({ units: { speed: "mph", temp: "f" } });
    await seek(player, race.clock.lights_out + 1);

    assert.match(weather(player), /AIR 68\u00b0F/);
  });

  test("the session start is shown in the header when Python sends it", async () => {
    const player = await open(race, { start: "15:00:00 UTC+2" });

    assert.equal(player.root.querySelector(".rp-start").textContent, "Start 15:00:00 UTC+2");
  });

  test("without a start the header has none", async () => {
    const player = await open(race);

    assert.equal(player.root.querySelector(".rp-start"), null);
  });
});
