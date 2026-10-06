# Branding and fonts

The app and README use the supplied outlined logo at
[`public/brand/redacted-logo.svg`](../public/brand/redacted-logo.svg).
It includes the Tide mark and renders without installing or downloading a font.

The owner-supplied `public/brand/redacted-icon.png` is the app favicon.
The optional Tide enclave uses `public/brand/redacted-wallpaper.jpg` as its realm
background and `public/brand/redacted-logo_stacked.jpg` as its logo. SVG source
variants are retained alongside these assets. Enclave setup uploads the JPEGs
and applies signed branding through TideCloak; the app does not generate replacements.

The interface serves these free fonts locally from `public/fonts/`:

- **Inter**, under SIL Open Font License 1.1. [Bundled licence](../public/fonts/Inter-OFL.txt) · [Project](https://github.com/rsms/inter).
  The existing WOFF2 was originally obtained from Tide’s website.
- **Cousine Regular**, under SIL Open Font License 1.1. [Bundled licence](../public/fonts/Cousine-OFL.txt) · [Source](https://github.com/google/fonts/tree/main/ofl/cousine).
  Used for the typewriter-style interface text.

No external font service or paid subscription is required. Retain the font
licence files when redistributing the fonts. The application’s software licence
does not grant rights to Tide’s name or marks.
