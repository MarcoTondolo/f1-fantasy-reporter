"""The drill-down "predicted vs. actual fantasy points" visual for a round
page: a self-contained interactive HTML page (own inline SVG + JS, no
build step or dependency), reading whichever
``data/pace/round_comparison/{season}_{round}.json`` files ``f1-fantasy
round-comparison`` has already cached.

Same assembly-only discipline as the rest of ``f1_fantasy.site``: this
module never computes a prediction or fetches anything itself, it only
renders whatever's already on disk.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

COMPONENT_ORDER = ("total", "position", "positions_gained", "overtakes_and_dotd", "fastest_lap", "dnf", "qualifying")
COMPONENT_LABELS = {
    "total": "Total points",
    "position": "Race position",
    "positions_gained": "Positions gained",
    "overtakes_and_dotd": "Overtakes + DOTD",
    "fastest_lap": "Fastest lap",
    "dnf": "DNF",
    "qualifying": "Qualifying",
}

PREDICTED_COLOR = "#3987e5"
ACTUAL_COLOR = "#e10600"


def load_round_comparison(season: int, round_number: int, comparison_dir: Path) -> dict | None:
    path = comparison_dir / f"{season}_{round_number}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def render_comparison_page(payload: dict, *, event_name: str, round_number: int, season: int) -> str:
    drivers = payload.get("drivers", [])
    data_json = json.dumps(
        {
            "drivers": drivers,
            "componentLabels": COMPONENT_LABELS,
            "componentOrder": list(COMPONENT_ORDER),
        }
    )
    title = f"{html.escape(event_name)} — model vs. actuals"
    body = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="Driver-by-driver predicted vs. actual fantasy points for the {html.escape(event_name)}, broken down by scoring component.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
{_CSS}
</style>
</head>
<body>
<div class="page">
  <a class="nav-back" href="../round-{round_number}.html">&larr; Round {round_number}</a>
  <header class="site-header">
    <div class="eyebrow">Round {round_number} &middot; {season} &middot; Model vs. actuals</div>
    <h1>{html.escape(event_name)}</h1>
    <div class="subtitle">Every driver's predicted fantasy points against what they actually scored, broken down by the same components on both sides. Drill into one component below, or compare totals.</div>
  </header>

  <div class="controls">
    <div class="control-group" id="componentPills"></div>
    <div class="control-group">
      <span class="control-label">Sort</span>
      <button type="button" class="sort-btn active" data-sort="actual">Actual points</button>
      <button type="button" class="sort-btn" data-sort="surprise">Biggest surprise</button>
    </div>
    <button type="button" class="table-toggle" id="tableToggle">View as table</button>
  </div>

  <div class="legend">
    <span class="legend-item"><span class="swatch" style="background:{PREDICTED_COLOR}"></span>Predicted (mean)</span>
    <span class="legend-item"><span class="swatch" style="background:{ACTUAL_COLOR}"></span>Actual</span>
  </div>

  <div class="chart-wrap">
    <svg id="chart" viewBox="0 0 100 100" preserveAspectRatio="none"></svg>
  </div>
  <div id="tooltip" class="tooltip" hidden></div>

  <div class="table-wrap" id="tableWrap" hidden></div>

  <div class="note">
    <b>Overtakes + DOTD</b> is shown combined on both sides, not split: neither public
    results (no lap-by-lap positions) nor the public fantasy feed (its overtake/DOTD
    totals are season-cumulative and confirmed stale between rounds) can isolate the
    two per round. A driver whose bar is greyed out here had an implausible residual
    (not a small non-negative number) -- shown as zero rather than a guess.
    <br><br>
    <b>Predicted</b> is the model's mean across many simulated scenarios, conditioned
    on the real qualifying grid (not simulating its own). The dotted range on the
    Total view is the 10th-90th percentile of the full predicted-points distribution.
  </div>

  <footer class="site-footer">Generated from this repository's own cached model output and public race results.</footer>
</div>
<script>
const DATA = {data_json};
{_JS}
</script>
</body>
</html>
"""
    return body


_CSS = """
:root {
  --page: #0d0d0d; --surface: #1a1a19; --surface-raised: #232322;
  --ink: #ffffff; --ink-secondary: #c3c2b7; --ink-muted: #898781;
  --grid: #2c2c2a; --baseline: #383835; --hairline: rgba(255,255,255,0.10);
  --accent: #e10600; --predicted: #3987e5;
  --font-display: 'Barlow Condensed', 'Liberation Sans', system-ui, sans-serif;
  --font-body: 'Inter', 'Liberation Sans', system-ui, sans-serif;
  color-scheme: dark;
}
* { box-sizing: border-box; }
body { background: var(--page); color: var(--ink); font-family: var(--font-body); -webkit-font-smoothing: antialiased; margin: 0; padding: 20px 16px 48px; display: flex; justify-content: center; }
.page { width: 100%; max-width: 1100px; display: flex; flex-direction: column; gap: 16px; }
a { color: inherit; }
.eyebrow { font-family: var(--font-display); font-weight: 700; font-size: 13px; letter-spacing: 0.16em; text-transform: uppercase; color: var(--accent); }
header.site-header { border-bottom: 2px solid var(--accent); padding-bottom: 16px; display: flex; flex-direction: column; gap: 8px; }
h1 { font-family: var(--font-display); font-weight: 700; font-size: clamp(26px, 5vw, 38px); line-height: 0.98; letter-spacing: -0.01em; text-transform: uppercase; margin: 0; text-wrap: balance; }
.subtitle { color: var(--ink-secondary); font-size: 14px; max-width: 760px; }
.nav-back { font-size: 13px; color: var(--ink-secondary); text-decoration: none; display: inline-flex; align-items: center; gap: 6px; }
.nav-back:hover { color: var(--ink); }

.controls { display: flex; flex-wrap: wrap; align-items: center; gap: 14px; padding: 10px 0; border-bottom: 1px solid var(--hairline); }
.control-group { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.control-label { font-size: 11px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--ink-muted); margin-right: 2px; }
.pill, .sort-btn, .table-toggle {
  font-family: var(--font-body); font-weight: 600; font-size: 12px; padding: 6px 12px; border-radius: 999px;
  border: 1px solid var(--hairline); background: var(--surface); color: var(--ink-secondary); cursor: pointer;
}
.pill.active, .sort-btn.active { border-color: var(--accent); color: var(--ink); background: rgba(225,6,0,0.14); }
.table-toggle { margin-left: auto; }
.table-toggle:hover, .pill:hover, .sort-btn:hover { color: var(--ink); }

.legend { display: flex; gap: 18px; font-size: 12px; color: var(--ink-secondary); }
.legend-item { display: inline-flex; align-items: center; gap: 6px; }
.swatch { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }

.chart-wrap { overflow-x: auto; background: var(--surface); border-radius: 4px; padding: 16px 8px; }
#chart { display: block; height: 460px; min-width: 640px; width: 100%; }
.bar { cursor: pointer; }
.bar.dimmed { opacity: 0.35; }
.zero-line { stroke: var(--baseline); stroke-width: 1; }
.grid-line { stroke: var(--grid); stroke-width: 1; }
.axis-label { fill: var(--ink-muted); font-family: var(--font-body); font-size: 9px; }
.driver-label { fill: var(--ink-secondary); font-family: var(--font-display); font-weight: 700; font-size: 10px; text-transform: uppercase; }
.driver-label.hovered { fill: var(--ink); }
.range-marker { stroke: var(--ink-muted); stroke-width: 1.5; stroke-dasharray: 2 2; }

.tooltip {
  position: fixed; background: var(--surface-raised); border: 1px solid var(--hairline); border-radius: 4px;
  padding: 10px 12px; font-size: 12px; pointer-events: none; z-index: 20; max-width: 220px;
  box-shadow: 0 6px 18px rgba(0,0,0,0.5);
}
.tooltip .t-driver { font-family: var(--font-display); font-weight: 700; font-size: 14px; text-transform: uppercase; margin-bottom: 6px; }
.tooltip .t-row { display: flex; justify-content: space-between; gap: 14px; }
.tooltip .t-row .t-key { display: flex; align-items: center; gap: 6px; color: var(--ink-secondary); }
.tooltip .t-row .t-key .k { width: 9px; height: 2px; display: inline-block; }
.tooltip .t-row .t-val { font-variant-numeric: tabular-nums; font-weight: 600; }
.tooltip .t-note { color: var(--ink-muted); font-size: 10px; margin-top: 6px; }

.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th, td { text-align: right; padding: 7px 10px; border-bottom: 1px solid var(--hairline); font-variant-numeric: tabular-nums; }
th:first-child, td:first-child { text-align: left; font-variant-numeric: normal; }
th { color: var(--ink-muted); font-weight: 600; text-transform: uppercase; font-size: 10px; letter-spacing: 0.04em; }
td.driver-cell { font-family: var(--font-display); font-weight: 700; text-transform: uppercase; }

.note { font-size: 12px; line-height: 1.6; color: var(--ink-muted); background: var(--surface); border-radius: 4px; padding: 14px 16px; }
footer.site-footer { font-size: 11px; color: var(--ink-muted); line-height: 1.6; padding-top: 8px; border-top: 1px solid var(--hairline); }

@media (max-width: 560px) { .table-toggle { margin-left: 0; } }
"""

_JS = r"""
(function () {
  var svg = document.getElementById('chart');
  var pillsEl = document.getElementById('componentPills');
  var tooltip = document.getElementById('tooltip');
  var tableWrap = document.getElementById('tableWrap');
  var tableToggle = document.getElementById('tableToggle');
  var sortBtns = Array.prototype.slice.call(document.querySelectorAll('.sort-btn'));

  var state = { component: 'total', sort: 'actual', showTable: false };

  function componentValue(driver, side, component) {
    if (component === 'total') return side === 'predicted' ? driver.predicted_total : driver.actual_total;
    var bucket = side === 'predicted' ? driver.predicted_components : driver.actual_components;
    return (bucket && bucket[component]) || 0;
  }

  function sortedDrivers() {
    var list = DATA.drivers.slice();
    if (state.sort === 'actual') {
      list.sort(function (a, b) { return componentValue(b, 'actual', state.component) - componentValue(a, 'actual', state.component); });
    } else {
      list.sort(function (a, b) {
        var da = Math.abs(componentValue(a, 'actual', state.component) - componentValue(a, 'predicted', state.component));
        var db = Math.abs(componentValue(b, 'actual', state.component) - componentValue(b, 'predicted', state.component));
        return db - da;
      });
    }
    return list;
  }

  function buildPills() {
    DATA.componentOrder.forEach(function (key) {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'pill' + (key === state.component ? ' active' : '');
      btn.textContent = DATA.componentLabels[key];
      btn.setAttribute('data-component', key);
      btn.addEventListener('click', function () {
        state.component = key;
        pillsEl.querySelectorAll('.pill').forEach(function (p) { p.classList.toggle('active', p === btn); });
        render();
      });
      pillsEl.appendChild(btn);
    });
  }

  function barPath(x, yZero, yTip, width, radius) {
    var top = Math.min(yZero, yTip), bottom = Math.max(yZero, yTip);
    var r = Math.min(radius, (bottom - top) / 2, width / 2);
    if (r < 0) r = 0;
    var positive = yTip < yZero;
    if (positive) {
      return 'M ' + x + ' ' + bottom +
        ' L ' + x + ' ' + (top + r) +
        ' Q ' + x + ' ' + top + ' ' + (x + r) + ' ' + top +
        ' L ' + (x + width - r) + ' ' + top +
        ' Q ' + (x + width) + ' ' + top + ' ' + (x + width) + ' ' + (top + r) +
        ' L ' + (x + width) + ' ' + bottom + ' Z';
    }
    return 'M ' + x + ' ' + top +
      ' L ' + (x + width) + ' ' + top +
      ' L ' + (x + width) + ' ' + (bottom - r) +
      ' Q ' + (x + width) + ' ' + bottom + ' ' + (x + width - r) + ' ' + bottom +
      ' L ' + (x + r) + ' ' + bottom +
      ' Q ' + x + ' ' + bottom + ' ' + x + ' ' + (bottom - r) + ' Z';
  }

  function ns(tag) { return document.createElementNS('http://www.w3.org/2000/svg', tag); }

  function showTooltip(evt, driver) {
    var comp = state.component;
    var predicted = componentValue(driver, 'predicted', comp);
    var actual = componentValue(driver, 'actual', comp);
    var delta = actual - predicted;
    var rows = [
      ['predicted', DATA.componentLabels[comp] + ' (predicted)', predicted.toFixed(1), '#3987e5'],
      ['actual', DATA.componentLabels[comp] + ' (actual)', actual.toFixed(1), '#e10600'],
    ];
    tooltip.innerHTML = '';
    var head = document.createElement('div');
    head.className = 't-driver';
    head.textContent = driver.driver + ' · ' + driver.constructor;
    tooltip.appendChild(head);
    rows.forEach(function (r) {
      var row = document.createElement('div');
      row.className = 't-row';
      var key = document.createElement('span');
      key.className = 't-key';
      var swatch = document.createElement('span');
      swatch.className = 'k';
      swatch.style.background = r[3];
      key.appendChild(swatch);
      key.appendChild(document.createTextNode(r[1]));
      var val = document.createElement('span');
      val.className = 't-val';
      val.textContent = r[2];
      row.appendChild(key);
      row.appendChild(val);
      tooltip.appendChild(row);
    });
    var deltaRow = document.createElement('div');
    deltaRow.className = 't-row';
    deltaRow.innerHTML = '<span class="t-key">Delta (actual − predicted)</span>';
    var deltaVal = document.createElement('span');
    deltaVal.className = 't-val';
    deltaVal.textContent = (delta >= 0 ? '+' : '') + delta.toFixed(1);
    deltaRow.appendChild(deltaVal);
    tooltip.appendChild(deltaRow);
    if (comp === 'total') {
      var range = document.createElement('div');
      range.className = 't-note';
      range.textContent = 'Predicted range (p10–p90): ' + driver.predicted_p10.toFixed(0) + '–' + driver.predicted_p90.toFixed(0);
      tooltip.appendChild(range);
    }
    if (!driver.residual_confident && (comp === 'overtakes_and_dotd' || comp === 'total')) {
      var warn = document.createElement('div');
      warn.className = 't-note';
      warn.textContent = 'Overtakes + DOTD residual was not a clean number for this driver — shown as 0, not trusted.';
      tooltip.appendChild(warn);
    }
    tooltip.hidden = false;
    positionTooltip(evt);
  }

  function positionTooltip(evt) {
    var pad = 14;
    var x = evt.clientX + pad, y = evt.clientY + pad;
    var maxX = window.innerWidth - 240, maxY = window.innerHeight - 140;
    if (x > maxX) x = evt.clientX - 240 - pad;
    if (y > maxY) y = evt.clientY - 140 - pad;
    tooltip.style.left = x + 'px';
    tooltip.style.top = y + 'px';
  }

  function hideTooltip() { tooltip.hidden = true; }

  function render() {
    var drivers = sortedDrivers();
    var comp = state.component;
    var values = [];
    drivers.forEach(function (d) {
      values.push(componentValue(d, 'predicted', comp), componentValue(d, 'actual', comp));
      if (comp === 'total') values.push(d.predicted_p10, d.predicted_p90);
    });
    var maxVal = Math.max(0, Math.max.apply(null, values));
    var minVal = Math.min(0, Math.min.apply(null, values));
    if (maxVal === minVal) { maxVal += 1; }

    var n = drivers.length;
    var groupWidth = 34;
    var groupGap = 12;
    var chartWidth = Math.max(640, n * (groupWidth + groupGap) + 40);
    var chartHeight = 420;
    var topPad = 16, bottomPad = 34;
    var plotHeight = chartHeight - topPad - bottomPad;
    var range = maxVal - minVal;
    var yZero = topPad + (maxVal / range) * plotHeight;

    function yFor(v) { return topPad + ((maxVal - v) / range) * plotHeight; }

    svg.setAttribute('viewBox', '0 0 ' + chartWidth + ' ' + (chartHeight + 20));
    svg.style.minWidth = chartWidth + 'px';
    svg.innerHTML = '';

    // gridlines at 0 and nice steps
    var step = niceStep(range);
    for (var g = Math.ceil(minVal / step) * step; g <= maxVal; g += step) {
      var gy = yFor(g);
      var line = ns('line');
      line.setAttribute('x1', 0); line.setAttribute('x2', chartWidth);
      line.setAttribute('y1', gy); line.setAttribute('y2', gy);
      line.setAttribute('class', Math.abs(g) < 0.01 ? 'zero-line' : 'grid-line');
      svg.appendChild(line);
      var label = ns('text');
      label.setAttribute('x', 2); label.setAttribute('y', gy - 3);
      label.setAttribute('class', 'axis-label');
      label.textContent = Math.round(g);
      svg.appendChild(label);
    }

    drivers.forEach(function (d, i) {
      var gx = 20 + i * (groupWidth + groupGap);
      var barW = 14;
      var predVal = componentValue(d, 'predicted', comp);
      var actVal = componentValue(d, 'actual', comp);
      var predGroup = ns('g');
      var predPath = ns('path');
      predPath.setAttribute('d', barPath(gx, yZero, yFor(predVal), barW, 4));
      predPath.setAttribute('fill', '#3987e5');
      predPath.setAttribute('class', 'bar');
      predGroup.appendChild(predPath);

      var actPath = ns('path');
      actPath.setAttribute('d', barPath(gx + barW + 2, yZero, yFor(actVal), barW, 4));
      actPath.setAttribute('fill', '#e10600');
      var actUntrusted = !d.residual_confident && comp === 'overtakes_and_dotd';
      actPath.setAttribute('class', actUntrusted ? 'bar dimmed' : 'bar');
      predGroup.appendChild(actPath);

      if (comp === 'total') {
        var rx = gx + barW + 1;
        var marker = ns('line');
        marker.setAttribute('x1', rx); marker.setAttribute('x2', rx);
        marker.setAttribute('y1', yFor(d.predicted_p10)); marker.setAttribute('y2', yFor(d.predicted_p90));
        marker.setAttribute('class', 'range-marker');
        predGroup.appendChild(marker);
      }

      var hit = ns('rect');
      hit.setAttribute('x', gx - 4); hit.setAttribute('y', topPad);
      hit.setAttribute('width', barW * 2 + 10); hit.setAttribute('height', plotHeight);
      hit.setAttribute('fill', 'transparent');
      hit.addEventListener('pointerenter', function (evt) { predGroup.classList.add('bar-hover'); showTooltip(evt, d); dlabel.classList.add('hovered'); });
      hit.addEventListener('pointermove', positionTooltip);
      hit.addEventListener('pointerleave', function () { hideTooltip(); dlabel.classList.remove('hovered'); });
      predGroup.appendChild(hit);

      var dlabel = ns('text');
      dlabel.setAttribute('x', gx + barW + 1);
      dlabel.setAttribute('y', chartHeight + 14);
      dlabel.setAttribute('text-anchor', 'middle');
      dlabel.setAttribute('class', 'driver-label');
      dlabel.textContent = d.driver;
      predGroup.appendChild(dlabel);

      svg.appendChild(predGroup);
    });
  }

  function niceStep(range) {
    var raw = range / 6;
    var pow = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10));
    var n = raw / pow;
    var step = n < 1.5 ? 1 : n < 3 ? 2 : n < 7 ? 5 : 10;
    return step * pow;
  }

  function renderTable() {
    var drivers = sortedDrivers();
    var comp = state.component;
    var table = document.createElement('table');
    var thead = document.createElement('thead');
    thead.innerHTML = '<tr><th>Driver</th><th>Predicted</th><th>Actual</th><th>Delta</th></tr>';
    table.appendChild(thead);
    var tbody = document.createElement('tbody');
    drivers.forEach(function (d) {
      var predVal = componentValue(d, 'predicted', comp);
      var actVal = componentValue(d, 'actual', comp);
      var delta = actVal - predVal;
      var tr = document.createElement('tr');
      var driverCell = document.createElement('td');
      driverCell.className = 'driver-cell';
      driverCell.textContent = d.driver;
      tr.appendChild(driverCell);
      [predVal, actVal].forEach(function (v) {
        var td = document.createElement('td');
        td.textContent = v.toFixed(1);
        tr.appendChild(td);
      });
      var deltaCell = document.createElement('td');
      deltaCell.textContent = (delta >= 0 ? '+' : '') + delta.toFixed(1);
      tr.appendChild(deltaCell);
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    tableWrap.innerHTML = '';
    tableWrap.appendChild(table);
  }

  sortBtns.forEach(function (btn) {
    btn.addEventListener('click', function () {
      state.sort = btn.getAttribute('data-sort');
      sortBtns.forEach(function (b) { b.classList.toggle('active', b === btn); });
      renderAll();
    });
  });

  tableToggle.addEventListener('click', function () {
    state.showTable = !state.showTable;
    tableToggle.textContent = state.showTable ? 'View as chart' : 'View as table';
    document.querySelector('.chart-wrap').hidden = state.showTable;
    document.getElementById('tooltip').hidden = true;
    tableWrap.hidden = !state.showTable;
    if (state.showTable) renderTable();
  });

  function renderAll() {
    render();
    if (state.showTable) renderTable();
  }

  buildPills();
  renderAll();
})();
"""
