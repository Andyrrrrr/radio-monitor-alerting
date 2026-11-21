# Plan: a Marine 1 web page (radio feed, tides, marine weather, webcams)

Status: version 1 built on branch `marine1-web-dashboard` 2026-11-21 (tests pass, previewed locally against a copy of the station database with live NOAA and NWS data). Not deployed. Decision B (webcams) still open; the shortlist is in the config defaults.

## 1. What we're building and why

One page, readable on a phone, that answers "what is happening on the water right now?":

- **Channel 16 feed:** the recent transmissions, each with its recording to play and its transcript underneath (the same evidence-first rule as the incident pages), with urgent and watch items marked.
- **Tides:** today's highs and lows and a "now" marker for Bellingham, plus a station picker for the rest of the zone.
- **Marine weather:** the National Weather Service marine forecast for the zone and any active warnings (small craft advisory, gale warning).
- **Webcams:** a small set of views of the bay and the ferry terminals.

Why: today the radio traffic reaches people only through the Telegram group, and the people in it want the water conditions beside it. A dispatcher or crew reading a transcript wants to know the tide and the wind at the same moment.

## 2. What it touches

- `src/vhfwatch/web/` only: the existing FastAPI app (a separate process on purpose, D8). New routes and one new template, plus a small `conditions.py` that fetches and caches tides and weather. **The pipeline is not touched**, so nothing here can delay detection or alerts.
- `config.py` and `config.example.toml`: a `[web.dashboard]` section (tide stations, forecast zone, webcam list, how long to cache).
- `docs/`: a decision entry for the access model, and `deploy/README.md` for turning it on.
- No new dependency: `httpx` is already used by the Telegram channel.

## 3. The data sources (checked 2026-11-21, free, no keys)

| Piece | Source | Checked |
|---|---|---|
| Tide predictions and observed water level | NOAA CO-OPS API (`api.tidesandcurrents.noaa.gov`). Bellingham is station `9449211`. About 35 other stations sit inside the zone (Friday Harbor `9449880`, Anacortes `9448794`, Cherry Point `9449424`, Blaine `9449679`, Roche Harbor `9449834`, ...) | A real request returned today's highs and lows |
| Marine forecast | NWS API (`api.weather.gov`). Zone `PZZ133`, "Northern Inland Waters Including The San Juan Islands", has a forecast with periods. Warnings come from `alerts/active?zone=PZZ133` | Both returned data; zero alerts at the time |
| Webcams | See the table below | Search only; nothing embedded or tested |

NWS asks for an identifying `User-Agent`. We will send a generic client name, not anyone's email.

Webcam candidates found so far (all need a decision before use; none is verified to allow embedding):

- Port of Bellingham: six live cameras, mostly weather views at Fairhaven, Zuanich Point and Marine Park, published on its YouTube channel and website. One is an indoor tank camera, not useful here.
- WSDOT VesselWatch: ferry terminal cameras including Anacortes and Friday Harbor. Lummi Island is a county ferry and I found no camera for it.
- Bellingham Bay Boating Center: a bay webcam page.
- No official Squalicum Harbor live view turned up.

## 4. Approach

**Server-side fetch with a cache.** The web process fetches tides and forecasts, caches them (tides for hours, forecasts and alerts for 10 to 15 minutes), and renders the last good copy with an "updated at" stamp if the network is down. The radio feed never depends on the internet (constraint 5), so with the network unplugged the page still shows transmissions, and the weather tiles say how old they are.

**Webcams as links, then images.** Third-party streams often forbid embedding or hot-linking. Version 1 shows each camera as a card that opens its own page. Embedding only where the owner clearly allows it.

**One page, three blocks**, server-rendered like the rest of the app, with the existing live-update stream (`/live`) reused for new transmissions. Times in the station's timezone.

**Access is the hard part** (decision A in section 6). The current app trusts loopback and requires a per-incident share token for everyone else, and the deploy notes say it is never exposed to the internet because these are recordings of real people. A dashboard for the whole group does not fit per-incident tokens.

Alternatives considered:

| Option | Why it lost, or what it costs |
|---|---|
| Public page with a shared password | Puts recordings on the open internet behind one password people forward. Against the standing rule and D26's scope. |
| Telegram Mini App | Larger build, ties the page to Telegram, and needs a public web address anyway. |
| Hosting the dashboard in the cloud | Out of scope for the project (cloud hosting). |
| A static public "conditions" page (tides, forecast, webcam links) plus the private radio tab | Possible if the group wants the conditions off-station; it carries no recordings, so it can be public. More to maintain. |

## 5. How we'll know it works

- **Tests:** feed rendering from a seeded database; tides and forecast parsing from saved real responses; the page rendering with the network failing and the cache empty and warm; the access rule for trusted and untrusted callers.
- **On the host, by hand:** open it on a phone over the chosen access path, check the tide against NOAA's own site for the same station and day, check the forecast text against weather.gov, play a recent recording, and unplug the network from the host to confirm the radio block still renders.
- **Done means:** a firefighter with a phone, following a one-page instruction, can open the page and see all four blocks.

## 6. Decisions

- **A. Access: decided 2026-11-21, station network plus Tailscale.** The page is reachable from the station network and over Tailscale, never from the open internet. Consequences to build and document: a way to mark those networks as trusted (a configured list of source ranges, so the existing per-incident token rule stays for everyone else), the web service bound to an address those networks can reach, and a one-page setup sheet for anyone joining the tailnet. **To check before relying on it:** Tailscale's current limit on free users against the number of people who will log in.
- **B. Which webcams: open.** Start with the shortlist in section 3, and replace or add the team's favorites when sent (page links). Each is a link card; none is embedded until its owner's terms are checked.
- **C. Version 1 scope: decided 2026-11-21.** Radio feed, tides, weather and webcam link cards in the same release. Anything fancier later.

## 7. What's explicitly not in this plan

- Anything that changes detection, severity, the gate or alerting.
- User accounts and roles (out of scope for the project).
- Exposing recordings to the open internet.
- A native app.
- Writing or logging from the page (the incident pages already have acknowledgments and notes).
- Tide and weather forecasts of our own: we show NOAA's and NWS's, labeled as theirs.
