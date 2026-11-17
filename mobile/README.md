# GlossX for iPhone, iPad and Android

One [Capacitor](https://capacitorjs.com) app for all three platforms. It does not
run Claude Code or Codex itself. Choose **Local API workspace** to chat using
your own OpenAI-compatible API on this device, or connect to GlossX on your
computer over the local network or a Cloudflare tunnel. The connected mode
uses the computer's agents, papers and CLIs; local API settings and chats stay
separate on the device. Both modes use the shared React frontend.

The local API interface opens without a computer or network, but model calls
need Internet access. Browser builds need an API endpoint that allows CORS;
native builds use Capacitor HTTP. Returning to the connection page lets you
switch modes. The app never automatically skips this choice on launch.

## Pairing

No app install is needed on the phone: the browser works too.

1. On the computer, open GlossX → Settings → **Mobile access** and turn on
   **Allow**, then restart GlossX (it then listens on port 8010; `npm start`
   already listens on all interfaces). For use away from home, turn on
   **Internet access** instead or as well: a free Cloudflare quick tunnel
   (`cloudflared`, downloaded once) gives an `https://….trycloudflare.com`
   address. No open ports, no account; the address changes on every restart.
2. Scan the QR code with the phone's camera. It is a one-time link (10
   minutes) that signs the phone in. Then Share → **Add to Home Screen** to use
   it full screen.
3. In this app, type the address and pairing code instead.

Requests from the computer itself never need pairing; everything else does,
including requests arriving through the tunnel. Paired devices are listed in
Settings and can be removed one by one; **New pairing code** signs every device
out.

## Building

```bash
cd frontend
npm ci
cd ../mobile
npm ci
npm run sync          # builds shared frontend with relative assets, then cap sync
npx cap open ios       # Xcode: pick your team under Signing, then Run (iPhone or iPad)
npx cap open android   # Android Studio: Run, or ./gradlew assembleDebug in android/
```

See [docs/glossx-mobile.md](../docs/glossx-mobile.md) for the plan and how the code is split across platforms.

`www/` is the app's own mode selection and connect page. `www/local/` is a
generated, ignored bundle of `frontend/`; `npm run build:web` builds it without
overwriting `frontend/dist`. Local API mode changes require an app rebuild;
connected mode frontend changes reach the phone from the computer. The
**Mobile build** workflow builds a debug APK (a workflow artifact) and checks
that the iOS project compiles.

App icons and splash screens are generated from `assets/` with
`npx @capacitor/assets generate`.
