# Bundled font licenses

All TTFs in this folder are Open Font License (OFL 1.1) fonts pulled from the
[google/fonts](https://github.com/google/fonts) repository. Full license text
for each family lives in the `OFL.txt` of the matching folder in that repo.

| File | Family | Style | Source path (google/fonts) |
|---|---|---|---|
| BebasNeue-Regular.ttf | Bebas Neue | Regular | `ofl/bebasneue/BebasNeue-Regular.ttf` |
| Poppins-Bold.ttf | Poppins | Bold | `ofl/poppins/Poppins-Bold.ttf` |
| Anton-Regular.ttf | Anton | Regular | `ofl/anton/Anton-Regular.ttf` |
| ArchivoBlack-Regular.ttf | Archivo Black | Regular | `ofl/archivoblack/ArchivoBlack-Regular.ttf` |
| Bangers-Regular.ttf | Bangers | Regular | `ofl/bangers/Bangers-Regular.ttf` |
| BarlowCondensed-Bold.ttf | Barlow Condensed | Bold | `ofl/barlowcondensed/BarlowCondensed-Bold.ttf` |
| SairaCondensed-Bold.ttf | Saira Condensed | Bold | `ofl/sairacondensed/SairaCondensed-Bold.ttf` |
| Kanit-Bold.ttf | Kanit | Bold | `ofl/kanit/Kanit-Bold.ttf` |
| Lato-Bold.ttf | Lato | Bold | `ofl/lato/Lato-Bold.ttf` |

Notes:
- Variable-font-only families in google/fonts (Montserrat, Oswald, Inter)
  were skipped — libass renders static TTF instances most reliably.
- `apply_template` copies every `*.ttf` here next to the ASS file and passes
  `fontsdir=.` to ffmpeg, so every family above is embeddable at render time.
