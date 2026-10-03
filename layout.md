# Telemetry & Live Timing Dashboard Specification

> **Which sections bind.**
>
> - **Section 9 (Replay screen) is binding.** It is the contract the browser
>   replay player (`ui/components/replay_player/`) implements; a change to the
>   player changes this section in the same commit.
> - **Sections 2-5 are reference.** They describe what `ui/dashboard.py`,
>   `ui/track_map.py` and `processing/timing.py` draw. Where they disagree with
>   section 9 or with the UI guideline (`IMPROVEMENTS.md` section 5), those win.
> - **Sections 1, 6, 7 and 8 are historical** and not binding: the colour
>   tokens live in `ui/theme.py` (guideline 5.4), and the app has no WebSocket
>   push to the browser, React/Vue list or canvas renderer - the replay
>   player's payload is built by `processing/replay_payload.py`.

## 1. System Architecture & Layout Grid

The telemetry dashboard follows a two-column responsive grid system optimized for ultra-wide continuous data streaming displays (16:9 or 21:9 aspect ratios).

```
+---------------------------------------------------------------------------------------------------+
|                                  GLOBAL SESSION HEADER BAR                                        |
+---------------------------------------------------------------+-----------------------------------+
|                                                               |  SECTOR TOP 3 LEADERBOARD WIDGETS |
|                                                               |  [Sec 1]   [Sec 2]   [Sec 3]      |
|                                                               +-----------------------------------+
|                                                               |                                   |
|               LIVE DRIVER LEADERBOARD MATRIX                  |                                   |
|                     (LEFT PANEL - 60%)                        |     INTERACTIVE 2D TRACK MAP      |
|                                                               |       CANVAS ENGINE & TELEMETRY   |
|                                                               |         (RIGHT PANEL - 40%)       |
|  - Q3 Active Driver Rows (Positions 1 to 10)                  |                                   |
|  - Eliminated Driver Rows (Positions 11 to 22)                |                                   |
|                                                               |                                   |
|                                                               |                                   |
+---------------------------------------------------------------+-----------------------------------+

```

---

## 2. Global Environmental & Session Header Bar

The top header bar provides session state, clock controls, and environmental telemetry indicators.

### Component Structure

* **Event & Session Badge**: Event Name (`Dutch GP`) + host country (`Netherlands`) + Session Identifier (`Qualifying 3`).
  *Implemented as the country name rather than a flag icon: mapping events to flags needs a hand-maintained table, which goes stale as the calendar changes (Madrid 2026).*
* **Session Status Banner**:
* **Session Clock**: `H:MM:SS`. Historical sessions show their duration; live sessions count down `ExtrapolatedClock`. (Was `MM:SS`, which overflows on any session longer than an hour.)
* **Flag Status Indicator**: Background-highlighted badge (e.g., `Yellow Flag` in yellow `#FFD700` with black text, `Green Flag`, `Red Flag`, or `Safety Car`).


* **Telemetry Data Points**:
* **Wind Vector**: Speed in km/h and directional cardinal orientation (`6.5 km/h E`).
* **Track Temperature**: Numeric display in Celsius (`29.9 °C`).
* **Air Temperature**: Ambient temperature display (`17.5 °C`).
* **Humidity Percentage**: Relative atmospheric humidity (`59.7%`).
* **Barometric Pressure**: Atmospheric pressure in millibars (`1021.2 mb`).
* **Precipitation State**: Binary/Boolean state (`Rain: No` or `Rain: Yes`).



### Data Fields Specification Table

| Metric Field | Data Type | Refresh Rate | Visual Styling / State Rules |
| --- | --- | --- | --- |
| `session_name` | String | Static | Font-weight: Bold, Uppercase, White text |
| `session_clock` | String (H:MM:SS) | 1 Hz | Monospace font (`Roboto Mono`), digital readout; labelled Duration (historical) / Remaining (live) |
| `flag_status` | Enum | Event-driven | Dynamic fill: `GREEN` (#00E676), `YELLOW` (#FFD700), `RED` (#FF1744) |
| `wind_speed_dir` | String | 0.1 Hz | Inline compass arrow + value in km/h (the feed reports m/s) |
| `track_temp` | Float (°C) | 0.1 Hz | High contrast text display |
| `air_temp` | Float (°C) | 0.1 Hz | High contrast text display |
| `humidity` | Float (%) | 0.05 Hz | Standard telemetry font |
| `baro_pressure` | Float (mb) | 0.05 Hz | Standard telemetry font |
| `rain_status` | Boolean | Event-driven | Red text indicator if true, standard grey/white if false |

---

## 3. Live Timing & Driver Leaderboard Matrix (Left Panel)

The left panel is the telemetry core. It renders a real-time table of 22 drivers separated into active session participants and eliminated/knocked-out participants.

### Column Definitions

1. **Position Badge (`Pos`)**: Numeric position (1 to 22) styled with individual team color blocks on the left edge.
2. **Driver Identification (`Driver`)**:
* Team accent bar indicator.
* Driver 3-letter shorthand tag (e.g., `NOR`, `RUS`, `ANT`, `PIA`, `HAM`, `LEC`, `VER`, `LAW`, `BOR`, `LIN`).


3. **Driver Status Tag (`Status`)**:
* State Badges: `IN PIT` (Vivid Red fill `#D32F2F`), `ON TRACK` (Green fill), `KO` (Yellow outline/fill pill for eliminated drivers).


4. **Last Lap Time (`Last lap`)**: Time string (`1:11.163`). Highlighted in pink/purple (`#E040FB`) for absolute session best.
5. **Best Lap Time (`Best lap`)**: Driver's personal best session lap time.
6. **Interval (`Interval`)**: Time delta relative to the car directly ahead (`+0.102`).
7. **Gap (`Gap`)**: Time delta relative to the overall session leader (`+0.102`).
8. **Sector 1 Time & Micro-Sector Strip (`Sector 1`)**:
* Numeric time string (`31.442`).
* **Mini-Sector Progress Bar**: Sub-segmented line under the numerical value consisting of 5 micro-segments. Each segment is color-coded:
* Purple (`#D000FF`): Session fastest micro-sector.
* Green (`#00E676`): Personal best micro-sector.
* Yellow (`#FFEA00`): Slower micro-sector.




9. **Sector 2 Time & Micro-Sector Strip (`Sector 2`)**: Numerical sector time + 5 micro-segment heatmap bar.
10. **Sector 3 Time & Micro-Sector Strip (`Sector 3`)**: Numerical sector time + 5 micro-segment heatmap bar.
11. **Tyre History Matrix (`Tyre history`)**:
* Dynamic array of compound badges depicting driver stint history.
* Compound Badge Visuals:
* Circular icon with tire compound identifier (`S` = Soft / Red ring, `M` = Medium / Yellow ring, `H` = Hard / White ring, `I` = Intermediate / Green ring, `W` = Wet / Blue ring).
* Lap age integer overlay (e.g., `3`, `4`, `7`, `8`).




12. **Diff (`Diff`)**: Differential value compared to theoretical best sector sum.
13. **Speed Trap (`Speed`)**: Speed readout in km/h, `0 km/h` when in the pits. Historical sessions show the driver's best speed-trap reading (there is no "current" speed once a session has ended); live sessions show the latest reading.

---

### Row Partitioning Logic

#### Active Drivers Section (Q3 runners)

The knock-out split follows the session's own segments, not a fixed top ten: with 22 cars (2026) six are eliminated after Q1 and six after Q2; with 20 cars, five and five. Races and practice partition nobody.

* **Background**: Dark charcoal theme (`#121212`) with alternate row zebra striping (`#1E1E1E`).
* ~~**Interactive Elements**: Real-time position swap animations on telemetry updates.~~
  *Struck: Streamlit re-renders the whole table through `st.html` on every update, so there is no stable DOM for a row to animate between. Revisit only if the tower becomes a custom component.*
* **Full Data Transparency**: Displays complete Sector 1, 2, 3 micro-sectors, Tyre history, and gap deltas.

```
+----+--------+--------+----------+----------+---------+---------+----------+----------+----------+---------------+------+--------+
| POS| DRIVER | STATUS | LAST LAP | BEST LAP | INTRVL  |   GAP   | SECTOR 1 | SECTOR 2 | SECTOR 3 | TYRE HISTORY  | DIFF | SPEED  |
+----+--------+--------+----------+----------+---------+---------+----------+----------+----------+---------------+------+--------+
| 1  |  NOR   | IN PIT | 1:11.163 | 1:11.163 |  ----   |  ----   | 31.442   | 32.475   | 35.812   | [3S][4S][3S]  | N/A  | 0 km/h |
|    |        |        |          |          |         |         | [|||||]  | [|||||]  | [|||||]  |               |      |        |
| 2  |  RUS   | IN PIT | 1:11.265 | 1:11.265 | +0.102  | +0.102  | 32.633   | 34.287   | 34.490   | [3S][4S][3S]  | N/A  | 0 km/h |
|    |        |        |          |          |         |         | [|||||]  | [|||||]  | [|||||]  |               |      |        |
+----+--------+--------+----------+----------+---------+---------+----------+----------+----------+---------------+------+--------+

```

#### Knocked Out / Eliminated Section (Positions 11 - 22)

* **Visual Styling**: Dimmed opacity (50% background alpha), dark red/maroon section tinting (`#2A0808`).
* **Status Badge**: Solid yellow pill badge (`KO`).
* **Deltas**: Gap and Interval fields marked as empty (`---`).
* **Sector Times**: Rendered inactive or displays final session sector state in low contrast grey.

---

## 4. Interactive 2D Track Map Canvas Engine (Right Panel)

The main canvas renders a vector geometry map of the circuit path (Circuit Zandvoort in this configuration) with real-time positional overlay nodes.

```
                     (Turn 3)
                      /-----\
                     /       \   (Turn 5)
                    /         \-----\
          (Turn 2) /                 \ (Turn 6)
                  /                   \
                 /                     \-----\
  [Start/Finish]|                             \ (Turn 7)
    (Turn 1)    |                              |
                \                              |
                 \                     (Turn 10)
                  \ (Turn 11)          /-------/
                   \----\             /  (Turn 9)
                         \           /
                     (Turn 12)---(Turn 8)

```

### Map Layer Breakdown

1. **Base Track Vector Path**:
* Continuous 2D spline matching geographic GPS coordinates of the track layout.
* Rendered using SVG path components or HTML5 Canvas 2D Context.


2. **Turn Marker Overlay Nodes**:
* Circular node indicators placed along track vertices indicating turn numbers (`1` through `14`).
* Style: Solid grey/white circular badges with high-contrast inner text.


3. **Sector & Mini-Sector Status Layer**:
* Vector path segmentation: Track polyline split into sector/mini-sector segments.
* Color Encoding:
* **Yellow**: Sector under yellow flag condition or local slow zone.
* **Green**: Personal best segment active.
* **Purple**: Overall session best segment path.
* **Cyan/Blue**: DRS Activation Zone or standard sector tracking bounds.




4. **Live Car Position Markers**:
* Dynamic SVG elements animated via real-time telemetry coordinates $(x, y)$.
* Colored circular node representing each driver's team color, labeled with driver position or three-letter abbreviation.


5. **Track Sector Benchmark Widget (Top-Left Canvas Overlay)**:
* Floating overlay box.
* **Target Benchmark Time**: `1:11.56`.
* **Benchmark Target Name**: `Sprint Pole` / Session Benchmark Target.



---

## 5. Sector Top 3 Leaderboard Widgets (Top-Right Canvas Overlay)

Positioned directly above or overlaid on the top-right corner of the track map canvas is a mini-leaderboard showing performance leaders per sector.

```
+--------------------------+--------------------------+--------------------------+
|         SECTOR 1         |         SECTOR 2         |         SECTOR 3         |
+---+-----+----------------+---+-----+----------------+---+-----+----------------+
| 1 | RUS | 24.300         | 1 | NOR | 24.898         | 1 | ANT | 21.643         |
| 2 | ANT | 24.373         | 2 | PIA | 24.988         | 2 | RUS | 21.741         |
| 3 | PIA | 24.432         | 3 | HAM | 25.048         | 3 | VER | 21.765         |
+---+-----+----------------+---+-----+----------------+---+-----+----------------+

```

### Component Breakdown

* **Three Side-by-Side Column Cards**: Representing Sector 1, Sector 2, and Sector 3.
* **Header Bar**: Dark teal fill (`#006064`) with clear white text indicating sector index.
* **Row Formatting**:
* **Position**: Rank (1, 2, 3).
* **Driver Pill Badge**: Driver code (`RUS`, `NOR`, `ANT`, `PIA`, `HAM`, `VER`) rendered over respective official F1 team background accent colors:
* Mercedes: Teal (`#00D2BE`)
* McLaren: Papaya Orange (`#FF8000`)
* Ferrari: Red (`#E80020`)
* Red Bull: Dark Blue (`#3671C6`)


* **Sector Delta Time**: Recorded sector time in seconds accurate to 3 decimal places (e.g., `24.300`, `24.898`, `21.643`).



---

## 6. CSS Grid & Design Token Specifications

### CSS Root Theme Variables

```css
:root {
  /* Surface & Background Colors */
  --bg-primary: #0a0a0a;
  --bg-surface-dark: #121212;
  --bg-surface-row-alt: #1a1a1a;
  --bg-ko-row: rgba(42, 8, 8, 0.6);
  --border-color: #2a2a2a;

  /* Telemetry Status Colors */
  --color-purple-best: #d000ff;
  --color-green-pb: #00e676;
  --color-yellow-slow: #ffea00;
  --color-pit-red: #d32f2f;
  --color-ko-yellow: #cddc39;

  /* Team Branding Colors */
  --team-mclaren: #ff8000;
  --team-mercedes: #00d2be;
  --team-ferrari: #e80020;
  --team-redbull: #3671c6;
  --team-williams: #64c4ff;
  --team-astonmartin: #229971;
  --team-alpine: #0093cc;

  /* Typography */
  --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  --font-mono: 'JetBrains Mono', 'Roboto Mono', monospace;
}

```

### Main Layout Container CSS Grid Architecture

```css
.dashboard-container {
  display: grid;
  grid-template-rows: 48px 1fr;
  grid-template-columns: 60% 40%;
  grid-template-areas:
    "header  header"
    "leaderboard map-canvas";
  width: 100vw;
  height: 100vh;
  background-color: var(--bg-primary);
  color: #ffffff;
  font-family: var(--font-sans);
  overflow: hidden;
}

.header-bar {
  grid-area: header;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  background-color: var(--bg-surface-dark);
  border-bottom: 1px solid var(--border-color);
}

.leaderboard-panel {
  grid-area: leaderboard;
  overflow-y: auto;
  border-right: 1px solid var(--border-color);
  background-color: var(--bg-primary);
}

.map-canvas-panel {
  grid-area: map-canvas;
  position: relative;
  display: flex;
  flex-direction: column;
  background-color: var(--bg-surface-dark);
}

```

---

## 7. JSON Telemetry Data Contracts (WebSocket Integration)

To drive this dashboard, incoming real-time socket frames must conform to standardized JSON payloads.

### Global Session Data Schema

```json
{
  "event": "TELEMETRY_SESSION_UPDATE",
  "timestamp": 1756306370000,
  "payload": {
    "grand_prix": "Dutch GP",
    "session_type": "Qualifying 3",
    "session_clock": "00:00",
    "flag_state": "YELLOW_FLAG",
    "environment": {
      "wind_speed_kmh": 6.5,
      "wind_direction": "E",
      "track_temp_celsius": 29.9,
      "air_temp_celsius": 17.5,
      "humidity_percentage": 59.7,
      "baro_pressure_mb": 1021.2,
      "rain": false
    }
  }
}

```

### Driver Leaderboard Array Data Schema

```json
{
  "event": "LEADERBOARD_UPDATE",
  "timestamp": 1756306370100,
  "payload": {
    "drivers": [
      {
        "position": 1,
        "code": "NOR",
        "team_id": "mclaren",
        "status": "IN_PIT",
        "last_lap": "1:11.163",
        "best_lap": "1:11.163",
        "is_overall_best": true,
        "interval": "----",
        "gap": "----",
        "sectors": {
          "s1": {
            "time": 31.442,
            "segments": ["PURPLE", "PURPLE", "GREEN", "GREEN", "PURPLE"]
          },
          "s2": {
            "time": 32.475,
            "segments": ["GREEN", "GREEN", "YELLOW", "GREEN", "GREEN"]
          },
          "s3": {
            "time": 35.812,
            "segments": ["PURPLE", "GREEN", "PURPLE", "PURPLE", "GREEN"]
          }
        },
        "tyre_history": [
          { "compound": "S", "laps_used": 3 },
          { "compound": "S", "laps_used": 4 },
          { "compound": "S", "laps_used": 3 }
        ],
        "diff": null,
        "speed_kmh": 0
      },
      {
        "position": 11,
        "code": "GAS",
        "team_id": "alpine",
        "status": "KO",
        "last_lap": "1:12.616",
        "best_lap": "1:12.616",
        "is_overall_best": false,
        "interval": "----",
        "gap": "----",
        "sectors": {
          "s1": { "time": null, "segments": [] },
          "s2": { "time": null, "segments": [] },
          "s3": { "time": null, "segments": [] }
        },
        "tyre_history": [
          { "compound": "S", "laps_used": 3 },
          { "compound": "S", "laps_used": 3 },
          { "compound": "S", "laps_used": 3 }
        ],
        "diff": null,
        "speed_kmh": 0
      }
    ]
  }
}

```

### Track Vector & Micro-Sector Best Schema

```json
{
  "event": "TRACK_SECTOR_LEADERS",
  "timestamp": 1756306370200,
  "payload": {
    "benchmark_target": {
      "time": "1:11.56",
      "label": "Sprint Pole"
    },
    "sector_leaders": {
      "sector_1": [
        { "rank": 1, "driver": "RUS", "team_id": "mercedes", "time": "24.300" },
        { "rank": 2, "driver": "ANT", "team_id": "mercedes", "time": "24.373" },
        { "rank": 3, "driver": "PIA", "team_id": "mclaren", "time": "24.432" }
      ],
      "sector_2": [
        { "rank": 1, "driver": "NOR", "team_id": "mclaren", "time": "24.898" },
        { "rank": 2, "driver": "PIA", "team_id": "mclaren", "time": "24.988" },
        { "rank": 3, "driver": "HAM", "team_id": "ferrari", "time": "25.048" }
      ],
      "sector_3": [
        { "rank": 1, "driver": "ANT", "team_id": "mercedes", "time": "21.643" },
        { "rank": 2, "driver": "RUS", "team_id": "mercedes", "time": "21.741" },
        { "rank": 3, "driver": "VER", "team_id": "redbull", "time": "21.765" }
      ]
    }
  }
}

```

---

## 8. Rendering & Execution Flow

1. **Header Component**: Listens for high-frequency environment updates, updating the digital clock via local browser requestAnimationFrame intervals synced to server timestamp.
2. **Leaderboard Engine**:
* Uses React virtualized list or optimized DOM reconciliation (Vue/Svelte) to handle rapid sorting without layout thrashing.
* ~~Row state transitions (Position switches) execute CSS flex/grid animations.~~ *Struck, same reason as section 3.*
* Tyre badges render as SVG or CSS circular elements containing lap age counts.
* Micro-sector heatmaps render as dynamic HTML flex spans within each sector cell.


3. **2D Map Layer**:
* Renders static circuit SVG paths once at initialization.
* Dynamic car marker points stream over WebSockets at 30–60 FPS and update coordinate attributes directly using HTML5 Canvas or SVG transform matrices.


4. **Sector Top 3 Widget Overlay**: Positioned absolute over map canvas top-right, updated on sector transition triggers.

---

## 9. Replay screen

The contract the browser replay player (`ui/components/replay_player/`, IMPROVEMENTS.md REPLAY-05) implements. Sizes are CSS px; colours are the guideline 5.4 tokens (`ui/theme.py`, mirrored once in the player's `:root`). Desktop is 1200 px wide or more.

```
+------------------------------------------------------------------------------------------+
| BAHRAIN GRAND PRIX 2023 · RACE    LAP 23/57    0:41:07    [GREEN]    AIR 26° TRACK 31° DRY |  header 48
+--------------------------------+---------------------------------------------------------+
| POS  DRIVER        GAP     TYRE  PIT |                                                   |
|  1 | VER   1     LEADER    M 14   1  |              track map (SVG, 60 %)                |
|  2 | PER  11     +4.211    M 14   1  |    cars: 9 px circles in team colour,             |
|  3 | ALO  14    +21.030    H  3   2  |    optional 3-letter labels, focused car 12 px    |
|  … 22 rows x 30 px (40 %)            |    with accent ring                               |
|                                      +---------------------------------------------------+
| [Gap | Interval]                     | RACE CONTROL   L22 14:02  TRACK LIMITS CAR 16 T4  |  3 lines
+--------------------------------------+---------------------------------------------------+
| [play] [-30s] [-5s] [+5s] [+30s]  Previous lap  Next lap   Speed 1x v   ----o------------ |  controls 36
| lap ticks every 5 · SC/VSC shaded (flag colour 30 %) · red flag shaded · pit, out, fastest |  timeline 28
+------------------------------------------------------------------------------------------+
```

### 9.1 Regions

| Region | Size | Content |
|---|---|---|
| Header | 48 high, full width | Event name (Titillium 700, 22, uppercase) · session · `LAP n/N` · race time `H:MM:SS` from lights out (segment time `Q2 12:04` in qualifying) · flag chip · weather as text (`AIR 26° TRACK 31° DRY`, wind `12 km/h NE`) |
| Tower | 40 % of the width, 22 rows × 30 | See 9.2 |
| Map | 60 % of the width | Track ribbon 14 wide in `--surface-2` with a 1 px darker edge; start/finish a short white line; corner numbers 10 in `--text-dim`, no circles; cars 9 px team-colour circles with a 1 px `--bg` stroke and optional 3-letter labels; the focused car 12 px with an `--accent` ring; no shadows, no glow; `role="img"` and a `<title>` naming the session and moment |
| Position strip | Under the map, full width of the map column, 85 high | The linear track-position strip (FEAT-07): a 2 px line from the start/finish line (left, 0, a white tick) to one lap (right, 1, a white tick), a 4 px team-colour marker per car on it, 3-letter label (Titillium 600, 10) with a 1 px team-colour stem; cars within 26 px of the previous label in a lane go to the next of six lanes (above, below, above further out ...), so a train of cars stays legible at 375 px. The focused car is a 6 px marker with an `--accent` label, others dimmed to 45 %. Reads `pos.lap_z` (see 9.8); `role="img"`, a `title` per label |
| Race control | 3 lines under the map | The last messages at or before the cursor: `L22 14:02 TRACK LIMITS CAR 16 T4` |
| Controls | 36 high, full width | See 9.4 |
| Timeline | 28 high, full width | See 9.3 |

### 9.2 Tower

- Race columns: `POS` (28, right-aligned) · 4 px team bar · driver code (Titillium 600, 44) + racing number (`--text-dim`, 24) · `GAP` or `INTERVAL` (a `[Gap | Interval]` toggle under the tower; 80, right) · `LAST` (80; `--best` text for a session best, `--pb` for a personal best) · tyre badge + age · `PIT` count (28) · status chip (`PIT`, `OUT`, `FIN`; empty on track).
- Qualifying and practice columns: `POS`, driver, `BEST`, `GAP`, `LAST`, S1/S2/S3 of the lap in progress, tyre.
- Rows 30 high, zebra `--surface` / `--surface-2`, no row dividers. The focused row has a 2 px `--accent` left border and a `--surface-2` background. `OUT` rows use `--text-dim` text **and** the `OUT` chip. An interval under 1.000 s is `--text` (overtaking range); others `--text-dim`.
- Rows are absolutely positioned and move with `transform: translateY(rank × 30)` and a 350 ms ease-out transition, so overtakes animate; text is rewritten only when a value changes.
- Values are formatted in Python (guideline 5.7): `LEADER`, `+1.234`, `+1:02.3`, `+1 LAP`, `+2 LAPS`, `1:32.456`, `–` for missing.

### 9.3 Timeline

- Full width, 28 high. Lap ticks labelled every 5 laps in `--text-dim`; hover shows `Lap n · 0:41:07`; click or drag seeks.
- Safety car and VSC periods shaded in the amber flag colour at 30 %; red flags in the red flag colour at 30 %.
- Event markers are 6 px shapes with distinct forms - pit stop a square, retirement a cross drawn with SVG lines, fastest lap a diamond - each with a `title`, so no marker depends on colour alone.

### 9.4 Transport

- Only play/pause is an icon (inline SVG `<symbol>` from guideline 5.8, with `aria-label` and `title`); every other control is text: `-30s`, `-5s`, `+5s`, `+30s`, `Previous lap`, `Next lap`, and a speed select `0.5x 1x 2x 4x 8x 16x 32x 64x`.
- Keyboard: Space play/pause, ←/→ ±5 s, Shift+←/→ ±30 s, `[`/`]` previous/next lap, `1`–`8` speed, `F` follow the focused driver. Clicking a tower row or a car focuses that driver (others dimmed, the focused car larger with a halo). The focus ring is `--accent`, 2 px.
- Layout (FEAT-10): the component's data carries `layout = {hide_cols, hide_panels}` beside the payload. `hide_cols` names tower columns (`status last best gap sectors tyres pit`), `hide_panels` names `map strip card rc`; the player hides those cells and panels and rebuilds the grid template per breakpoint, so the remaining columns keep their widths. A new `layout` applies without remounting. The viewer's units (UX-12) travel the same way: `units = {speed: kmh|mph, temp: c|f}` converts the weather line, and `start` is the session start as a time of day with its zone, formatted in Python (the player never converts zones).

### 9.5 Flag chip words

`GREEN`, `YELLOW`, `SC`, `VSC`, `RED`, `CHEQUERED` (`ui.theme.FLAG_STATES` labels). The keys stay `GREEN`, `YELLOW`, `SAFETY CAR`, `VSC`, `RED`, `CHEQUERED`; the player's `flags` payload carries the keys.

### 9.6 Responsive

| Width | Layout |
|---|---|
| ≥ 1200 | As drawn: tower 40 %, map 60 % |
| 900–1199 | Map 55 %; the tower drops `LAST` and `PIT` |
| < 900 | Map on top (50 vh), tower below at full width, race control behind a "Race control" text toggle |

No horizontal page scroll at any width from 360 px.

### 9.7 Motion and accessibility

- Motion only when data moves: rows sliding to a new position (350 ms ease-out), cars moving continuously (interpolated), the playhead, and a 600 ms flash (`--best` / `--pb` at 25 %) on a lap-time cell when a session-best or personal-best lap completes. `prefers-reduced-motion: reduce` turns the row transitions and flashes off; positions still update.
- Contrast: body text at least 4.5:1, large text and UI boundaries at least 3:1. Every coloured state has a text equivalent (chip text, compound letter, `title`). No information only on hover.

### 9.8 Payload: lap fractions (FEAT-07)

`pos.lap_z` (payload `v` 3) carries each car's distance round the lap beside `pos.xy_z`, in whole `pos.lap_scale` (1000) ths of a lap, `[0, 1)`, 0 at the start/finish line (`outline[0]`). Python computes it per frame from that frame's own projected position (`TrackGeometry.lap_fraction`: the nearest point on the closed outline, arc length over lap length), so it never reads another frame. Packed as the step from the previous frame wrapped into `[-500, 500)` (crossing the line is a small step), zigzag 16-bit, low bytes then high bytes, driver-major (driver, frame), raw DEFLATE, base64. A frame where `xy_z` flags the car absent has no fraction (the player reads the xy absent marker). `processing.replay_payload.decode_lap_fractions` is the Python reader; the player only looks the value up and lays the labels out.
