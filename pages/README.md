# Browser demo

The GitHub Pages demo lets visitors try Escalane’s worklist, alarm detail,
responder acknowledgement, and simulation feed without running the service.
It uses fictional data and sends no Escalane API or provider requests. Each
page keeps its own copy of the sample data, which resets when you reload. The
browser remembers only your light or dark theme preference.

The build copies the application’s styles, script, and logo from
`src/escalane/web/assets/`. Edit the demo HTML and behavior in this directory.
Shared styles belong in the application’s asset directory. The build writes
its output to `build/pages/`, so edits made there will be overwritten.

## Preview locally

Run from the repository root:

```bash
make pages-build pages-check
node --check pages/assets/demo.js
python3 -m http.server 8000 --bind 127.0.0.1 --directory build/pages
```

Open `http://127.0.0.1:8000/`. The routes are `/`, `/alarm.html`,
`/acknowledge.html`, `/simulation.html`, and `/tour.html`. Serve `build/pages/` rather than this source directory; the built copy
includes the application assets.

## Screenshot tour

`tour.html` and the repository README use the same four PNG files in
`assets/screenshots/`. Capture them from this static demo and keep the captions clear about
what they show. Do not substitute screenshots from a live deployment.

To refresh the images, build and serve the demo, then capture each workflow
page in a fresh browser context at 1440 × 1000 pixels with device scale 1,
a light theme, and reduced motion. Wait for the page and fonts to load. Save viewport
captures as `worklist.png`, `alarm.png`, `acknowledge.png`, and `simulation.png`.
Use only the built-in fictional data and capture before changing any state.
Rebuild afterwards to copy the new images into the built site.

Check the tour at desktop and phone widths. Open each full-size image and
follow each “Try this screen” link. Try filtering the worklist, searching for
an alarm that does not exist, using the drawer actions, and closing the drawer
with Escape. Submit the responder form and clear the simulation feed.
Inspect screenshots for clipping, overlap, and accidental personal data.

The Pages workflow builds and deploys changes pushed to `main`; local builds
and screenshots do not publish the site.
