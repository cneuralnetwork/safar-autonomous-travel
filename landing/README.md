# Safar landing page

This is a dependency-free static landing page.

## Preview locally

```bash
cd landing
python -m http.server 4173
```

Then open `http://localhost:4173`.

## Add the Android build

Place the production APK at:

```text
landing/downloads/safar.apk
```

Then remove `data-apk-placeholder="true"` from the three APK links in
`index.html`. The links already point to the correct file and include the
`download` attribute where appropriate.

All major visual assets in `assets/images/` were generated specifically for
Safar. The `.webp` files are optimized for the page; source PNGs are retained
beside them.
