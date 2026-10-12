# Photo Organizer

A Windows desktop app that scans your PC for photos, filters out app/system
junk, lets you browse quickly by folder or date, and helps you clean up
duplicates — including whole-folder duplicate detection with survivorship
recommendations.

## 1. Setup (one-time)

You'll need Python 3.10+ installed on Windows ([python.org](https://www.python.org/downloads/) —
during install, check **"Add Python to PATH"**).

Open a terminal (PowerShell or Command Prompt) in this folder and run:

```
pip install -r requirements.txt
```

## 2. Run the app

```
python main.py
```

## Don't want to install Python? Build a standalone .exe instead

The `.exe` runs on its own with no Python required, and you can copy it to
another PC that doesn't have Python at all. There are two ways to get it:

**Option A - let GitHub build it for you (no Python on your PC at all).**
Put this project in a GitHub repository. The file
`.github/workflows/build-windows.yml` tells GitHub to build the exe on one
of its Windows machines. Open the repo's **Actions** tab, run
**Build Windows exe** (it also runs automatically on every push to `main`),
then download the **PhotoOrganizer-windows** artifact from the finished run.

**Option B - build it yourself on a PC that has Python.**
Double-click **`build_windows.bat`** in this folder (or run it from a
terminal). It installs the one extra tool needed (`PyInstaller`) and builds
the app. When it finishes, your app is at **`dist\PhotoOrganizer.exe`**.

A few things worth knowing about the built `.exe`:
- It's large (100–200 MB) because it bundles the Python interpreter and
  all libraries inside it — that's normal for this kind of packaging.
- The first launch can take a few seconds longer than later launches.
- Windows SmartScreen may warn about an unrecognized app the first time
  (the exe isn't code-signed). Click **More info**, then **Run anyway**.
- Its database file (`photo_organizer.db`, where your scan results are
  saved) is created right next to the `.exe` itself, so keep the `.exe`
  in a folder you're happy to also hold that file.
- If you change any of the Python code afterward, rebuild the `.exe` to
  pick up your changes.

## How it works

The app has four tabs, meant to be used roughly in order:

1. **Scan** — pick one or more folders/drives. The scan automatically skips
   `Program Files`, `Windows`, `AppData`, `ProgramData`, and similar
   system/app locations without even walking into them (fast). Results are
   saved to a local SQLite file (`photo_organizer.db`, created next to
   `main.py`) so you don't have to rescan every time you reopen the app.

2. **Browse** — filter your indexed photos by folder, date range, or a
   text search on filename/path. Every thumbnail shows its source folder
   path underneath it, and the full path of whatever you point at appears
   at the bottom. The whole collection is one scrollable list (tens of
   thousands of photos are fine); thumbnails are loaded in the background
   only for what's on screen.

   **Ignoring junk:** the red-when-on buttons **Ignore FILE from future
   scans** and **Ignore FOLDER from future scans** work as modes. Turn one
   on, then every photo you click is ignored immediately, with no
   confirmation, and the mode stays on until you turn it off. Ignoring a
   folder also covers its sub-folders. Ignored photos disappear from every
   tab and are skipped by future scans. **Undo last ignore** reverses the
   most recent one; **Manage ignored…** lists everything you've ignored
   and lets you bring items back.

3. **Duplicate Review** — the *only* place duplicate decisions happen, kept
   deliberately separate from Browse so you're not scrolling past your
   whole collection to deal with duplicates. Two sections:
   - **Whole-folder duplicates**: if every photo in a folder already exists
     elsewhere, it's shown as one row and **not** recommended for deletion
     by default (it might be an intentional backup — you decide). If a
     folder is *mostly* duplicated (more than half), the duplicated photos
     are pre-checked for deletion and any genuinely unique photos in that
     folder are called out separately so they're never accidentally lost.
   - **Individual duplicate photos**: exact filename+size matches that
     aren't part of a whole-folder match. The recommended keeper (based on
     having camera metadata, living in an "organized" folder, size, and
     age) is left unchecked; the others are pre-checked for deletion.

   Nothing is ever deleted until you click **Apply**, and even then files
   go to the **Recycle Bin**, not permanent deletion — so a bad call is
   always recoverable.

4. **Visual Duplicates (Experimental)** — the stretch goal: finds photos
   that look the same even when the filename/size don't match (a resized
   copy, a re-saved copy, a screenshot of the same photo). Each group is
   shown as photo cards side by side (real thumbnails, with name, folder,
   dimensions and size) so you can compare them by eye; click a photo to
   open it full size. This is a
   separate, optional, slower pass using perceptual image hashing.
   Nothing here is pre-checked for deletion — visual similarity is a
   judgment call, not a certainty.

## Customizing the rules

Everything that decides what counts as "junk" vs. "photo," what folders
count as "already organized," and how duplicate scoring works lives in
one place: **`core/config.py`**. It's heavily commented — tweak the sets
and thresholds there without touching any other file. A few examples:

- `EXCLUDED_PATH_SEGMENTS` — folder names skipped entirely during scanning.
- `MIN_PHOTO_DIMENSION_PX` — images smaller than this (in either dimension)
  are treated as icons/sprites.
- `MOSTLY_DUPLICATE_THRESHOLD` — how duplicated a folder needs to be
  (default 50%) before it gets the whole-folder treatment.
- `ORGANIZED_FOLDER_PATTERNS` — regex patterns for folder names that count
  as "already organized" (e.g. `2023-06`, `2023 Summer Trip`).

## Known limitations (honest list)

- **HEIC/HEIF support** depends on the `pillow-heif` package installing
  cleanly on your machine. If it doesn't, HEIC files are still indexed by
  extension but may show a blank thumbnail and no EXIF.
- **Browse thumbnails are cached in memory only** (the most recent ~4,000),
  so scrolling back over a very large collection re-decodes thumbnails
  that were evicted. A thumbnail cache on disk would fix that if it ever
  feels slow.
- **Visual duplicate matching is O(n²)** (compares every hashed photo to
  every other) and runs on the main window thread. Fine for a few thousand
  photos; measured at roughly 1.8 hours for 65,000 photos, during which the
  window is unresponsive. A smarter nearest-neighbor search (see
  `core/perceptual.py`) is the fix and has not been done yet.
- **"Already organized" folder detection** is pattern-based (year/date-ish
  folder names). It's a reasonable first pass, not a guarantee — you can
  extend `ORGANIZED_FOLDER_PATTERNS` for your own naming habits.

## Project structure

```
main.py                        entry point
build_windows.bat              one-click standalone .exe builder
requirements.txt
.github/workflows/build-windows.yml   builds the exe on GitHub (Actions)
core/
  config.py                    all tunable rules/thresholds (start here)
  database.py                  SQLite storage layer
  scanner.py                   file system walk + indexing
  metadata.py                  EXIF extraction
  filters.py                   junk-file / organized-folder logic
  duplicates.py                exact-ish duplicate + folder-level grouping
  perceptual.py                Phase 5: visual similarity (stretch goal)
  actions.py                   the only place files are deleted (Recycle Bin)
gui/
  main_window.py                tab container
  scan_tab.py / browse_tab.py / duplicate_review_tab.py / visual_duplicates_tab.py
  thumbnails.py                 Pillow-based thumbnail generation
  workers.py                    background threads (scan, hashing)
```
