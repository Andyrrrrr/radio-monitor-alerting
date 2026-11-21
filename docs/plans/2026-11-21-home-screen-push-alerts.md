# Plan: urgent alerts on the iPhone home screen (Marine 1 as a web app)

Status: built on branch `home-screen-push` 2026-11-21 (tests pass; manifest, service worker and endpoints checked locally). Not deployed; the end-to-end test on the operator's iPhone is next.

## 1. What we're building and why

The Marine 1 page saved to an iPhone's home screen, behaving like an app: its own icon, and
**a notification that rings when an urgent alert goes out**, the same alerts that ring in the
Telegram group today. Tapping the notification opens that incident's record.

Why: people want one place (the Marine 1 icon) instead of Telegram plus a web page. Since
iOS 16.4, Safari supports standard Web Push for pages added to the home screen, so this needs
no App Store, no Swift, and no $100/year fee.

It is a **second path, not a replacement**: Telegram keeps ringing too. Two independent
paths mean one quiet failure doesn't silence alerts.

## 2. What it touches

- **Web app:** an app manifest and icons, a small service worker (`/sw.js`, the script iOS runs
  to show a notification), and an "Enable alerts" control on the Marine 1 page plus a
  "Send test alert" button. Routes: `/manifest.webmanifest`, `/sw.js`, `/push/key`,
  `/push/subscribe`, `/push/test`. Same access rule as the rest of the app (D36).
- **Store:** a `push_subscription` table, one row per phone that enabled alerts: endpoint, keys,
  a label, and when it was added. Deleted when Apple reports it is gone (410), and logged.
- **Alerting:** a new `webpush` channel beside Telegram, listed in `channels_critical` and
  `channels_urgent` only. It uses the same router, dedupe and outbox retries, so a failed
  push can never hold up detection (D8 and D28 unchanged).
- **One new dependency: `pywebpush`.** This is the standard Python implementation of the Web
  Push protocol (VAPID signing plus payload encryption). Writing that encryption ourselves would
  be the wrong kind of clever. It brings in `cryptography`, `http-ece`, `py-vapid` and `requests`.
- **One new secret:** a VAPID private key in `/etc/vhfwatch/vhfwatch.env` as
  `VHFWATCH_VAPID_PRIVATE_KEY` (secrets are env-only). The public key is derived from it.
- **Host:** HTTPS. iPhones only allow notifications from secure (`https`) pages. Tailscale can
  serve the existing app over HTTPS at `https://vhfwatch.tail89b577.ts.net` (`tailscale serve`),
  with a real certificate, tailnet-only (not Funnel: nothing on the public internet).

## 3. Approach

**Sending:** when the router fires `webpush` for an urgent or critical incident, the channel
sends one notification to every stored subscription through Apple's push service. Title: the
severity, channel and time. Body: the opening of the transcript, the same text Telegram shows.
Tap target: the incident record. The send is outbound HTTPS only; it opens no port.

**Receiving:** Apple delivers the notification whether or not the phone is on Tailscale.
Tapping it opens the record, and that needs Tailscale (same as the alert link today).

**Health notices are not sent this way.** Only incidents ring. "Capture died" and the daily
check stay in Telegram, so the push sound always means a possible emergency (D33's reasoning).

Alternatives considered:

| Option | Why not (now) |
|---|---|
| Native iPhone app ($100/yr) | Out of scope for the project; Swift upkeep through every iOS release. Its one real advantage is Apple's Critical Alerts, which ring through Do Not Disturb, but Apple grants that only on request. Revisit if push from the web app proves the idea. |
| Telegram only | Works today and stays. This adds the Marine 1 icon as a second path. |
| Pushover (already in the code) | Can break through Do Not Disturb, but every person needs a Pushover license and account; a separate app again. |

## 4. How we'll know it works

- **Tests:** subscribe/store/delete; the channel sends to every subscription and drops one that
  returns 410; a failed send is retryable and never raises; health notices don't go to push;
  the routes respect the access rule; the manifest and service worker are served correctly.
- **On the operator's iPhone (iOS 16.4 or newer), by hand:** add the page to the home screen
  from the `https://...ts.net` address, open it from the icon, tap Enable alerts and allow,
  tap Send test alert and see it arrive with the phone locked; tap it and land on the page.
  Then the real thing: the next urgent alert should ring both Telegram and the icon.
- **Done means:** the test alert arrives on a locked iPhone, and a real urgent alert does too.

## 5. What's explicitly not in this plan

- Critical Alerts / breaking through Do Not Disturb (not possible for web apps).
- Health notices, watch-level detections or every transmission as pushes.
- Per-person preferences, accounts or quiet hours.
- Android-specific work (standard Web Push should work there too, but it is untested).
- Any public (internet) access: HTTPS is tailnet-only.

## 6. What the operator has to do (only he can)

1. In the Tailscale admin console, **DNS** page: MagicDNS is already on; turn on **HTTPS
   Certificates**. Tailscale notes the machine name (`vhfwatch.tail89b577.ts.net`) then appears in
   public certificate-transparency logs.
2. Make sure the iPhone is on iOS 16.4 or newer and is signed in to this tailnet in the Tailscale app.
