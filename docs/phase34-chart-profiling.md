# Phase 3.4 — chart and state payload profiling

## Findings and change

Dashboard built six figures and Sales Performance eight figures on every
single-store load, although only half were visible. Their dark/light payloads
duplicated chart data. Sales additionally serialized an unused `sp_chart`
intermediate list. Both pages share the KPI pace calculation; the optimization
does not introduce a cross-page cache or lazy-load visible charts.

Only the active theme is now constructed. Inactive figure fields are `None`,
and a computed active figure supplies the existing Plotly components. Theme
changes restyle from plain private source inputs on the same server-side
state instance, without another operational-data read. Those inputs are not
serialized and are cleared/replaced on every selection and authorized load.
There is no cross-user or cross-record cache to invalidate.

## Measurement method

A disposable database held 36 complete synthetic months (2024–2026) with
four target levels, daily sales, basket data and contribution shares. The
February 2024 leap month and October 2026 representative month were profiled.
Serialized state uses Reflex `state.dict()` and its registered serializers;
chart bytes use Plotly JSON. Construction is the median of 10 warmed runs of
the complete page data-loading function, not just the Plotly builder.
Browser tests use production Reflex builds, five page loads and three theme
switches per page/width, measuring until the expected Plotly charts are ready
or the first chart DOM changes. Numbers are local measurements, not absolute
production SLAs. Profiling scripts and synthetic databases stay outside the
repository and production state.

| Page / month | State before | State after | Chart bytes before | Chart bytes after | Construction before | Construction after |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Dashboard 2024-02 | 60,464 B | 33,621 B | 51,504 B | 25,755 B | 132.4 ms | 121.0 ms |
| Dashboard 2026-10 | 60,850 B | 34,007 B | 51,834 B | 25,920 B | 114.0 ms | 109.8 ms |
| Sales 2024-02 | 116,359 B | 64,976 B | 79,580 B | 39,791 B | 541.5 ms | 394.3 ms |
| Sales 2026-10 | 119,103 B | 66,087 B | 80,466 B | 40,234 B | 739.8 ms | 474.2 ms |

After-state figures subtract the active-figure computed value (equal to the
active stored figure), avoiding double-counting the same data in Reflex's
state dictionary. Actual chart bytes are measured directly.

### Browser results

| Page / width | Load before | Load after | Theme after |
| --- | ---: | ---: | ---: |
| Dashboard 1440 px | 1475.7 ms | 1213.3 ms | 347.7 ms |
| Sales 1440 px | 2304.6 ms | 2281.2 ms | 1183.3 ms |
| Dashboard 390 px | 1332.2 ms | 1181.6 ms | 375.0 ms |
| Sales 390 px | 2087.4 ms | 1472.5 ms | 1009.5 ms |

Each load median is five runs; each theme median is three runs. Baseline
alternating theme timings were unavailable: its event repeatedly requested
dark mode. The optimized event toggles in both directions. All before/after
runs rendered the expected charts with no horizontal overflow or console/page
errors; screenshots were inspected at both widths.

## Verification

- Phase 3.4 tests cover per-session source reuse, active-theme generation,
  dark/light data parity, empty months, missing payroll, incomplete records,
  operational edits, store/month/user changes and theme switching.
- Production export uses a disposable database.
- Browser regression covers Dashboard and Sales Performance at desktop and
  narrow widths, including repeated theme switching.
