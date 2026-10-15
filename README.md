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

The app has five tabs, meant to be used roughly in order:

1. **Scan** — pick one or more folders/drives. The scan automatically skips
   `Program Files`, `Windows`, `AppData`, `ProgramData`, and similar
   system/app locations without even walking into them (fast). Results are
   saved to a local SQLite file (`photo_organizer.db`, created next to the
   app) so you don't have to rescan every time you reopen it.
   **After upgrading from an older version, scan again once**: the
   date-taken of camera photos is now read correctly, which duplicate
   matching and album-date suggestions depend on.

2. **Browse** — filter your indexed photos by folder, date range, or a
   text search on filename/path. The whole collection is one scrollable list
   (tens of thousands of photos are fine); thumbnails load in the background
   only for what's on screen.
   - **Click a photo** to enlarge it in the side panel, with its full file
     path, size, created date, modified date, dimensions, date taken and
     camera. Double-click opens it in your normal photo viewer.
   - **Star ratings (1–5):** hover over any photo and five stars pop up on it —
     click one to rate it (click the same star again to clear). Or just
     type **1–5** while the mouse is over a photo (**0** clears); with the mouse
     off the grid, the keys rate the selected photos. Rated photos keep a small
     star badge, and the **Rating** filter at the top narrows the list to
     unrated photos or "★★★ and up". Typing in the search box never rates anything.
   - **Albums:** the album list is always visible on the left, as a tree, so
     sub-albums show up while you sort. File photos by **dragging them onto an
     album**, by selecting photos and **double-clicking an album**, or with
     **Add selected to album…**, a single window where you either pick an
     existing album or create a new one (a date prefix like `23_06` is
     *recommended* from the photos' date taken, and the name is suggested from
     their folder — but both are optional and editable). To make a
     **sub-album**, pick the parent in that window and tick "Inside the
     selected album", or type a name with `/` in it (e.g. `23_06 Trip/Day 1`).
     Albums are **virtual** — nothing on disk is moved or copied — and photos
     in an album disappear from Browse.
   - **Ignoring junk:** the red-when-on buttons **Ignore FILE from future
     scans** and **Ignore FOLDER from future scans** work as modes. Turn one
     on, then every photo you click is ignored immediately, with no
     confirmation (clicking does not open the preview while a mode is on).
     Ignoring a folder also covers its sub-folders. **Undo last ignore**
     reverses the most recent one; **Manage ignored…** lists everything
     you've ignored and lets you bring items back.

3. **Albums** — your albums as a tree (counts include sub-albums), the photos
   in the selected album (optionally including its sub-albums), and the same
   enlarge-and-details panel and star ratings. Create albums/sub-albums,
   rename or move an album (its sub-albums follow), delete an album and its
   sub-albums (photos go back to Browse), or remove selected photos from an
   album.

4. **Duplicate Review** — the *only* place duplicate decisions happen, kept
   deliberately separate from Browse. Duplicates are found from **metadata
   only**: same **date taken + camera + file size** (so a renamed copy still
   matches); photos with no camera data fall back to **file name + size**.
   - **Click any file** to see all its copies side by side — thumbnails,
     folder, name, size, dimensions, date taken, camera, created, modified —
     with the differences highlighted.
   - **Confirm** a group as real duplicates, or **Not duplicates — unflag**
     it (it's taken off the list and never flagged again; *Reset my
     confirm/unflag choices…* undoes that).
   - The folder list shows, per folder: **Photos** in it, its **Sub-folders**
     that hold photos (and how many photos they hold — these move along if you
     quarantine the folder), how many photos are **Duplicated elsewhere**
     (count and %), and how many are **Unique**. Each duplicated file says
     which other folder(s) it also lives in.
   - **Whole-folder duplicates**: if every photo in a folder also exists in
     another folder, it's one row, **not** recommended for deletion by
     default (it might be an intentional backup). If a folder is *mostly*
     duplicated (50% or more), its duplicated photos are pre-checked and the
     unique ones are listed separately and never touched here. (Two copies in
     the *same* folder are listed under *Individual duplicate photos*.)
   - **Individual duplicate photos**: the recommended keeper (a copy already
     in an album, a higher star rating, camera metadata, an "organized" folder, size, age) is left
     unchecked.
   - **Quarantine selected folder…** moves a whole folder to a quarantine
     location you pick the first time (nothing is deleted). **Quarantined
     folders…** lists them with a **Restore** button. Pick a location on the
     same drive as your photos so moving is instant. Quarantining moves
     *everything* in that folder, including photos that aren't duplicates.
   - **Safety:** the app never pre-checks, and refuses to apply, a selection
     that would remove *every* copy of a photo (this matters when two folders
     are each mostly duplicates of the other).
   - Nothing is deleted until you click **Apply**, and even then files go to
     the **Recycle Bin**, not permanent deletion.

5. **Visual Duplicates (Experimental)** — the stretch goal: finds photos
   that look the same even when the metadata doesn't match (a resized copy, a
   re-saved copy, a screenshot of the same photo). Each group is shown as photo
   cards side by side so you can compare them by eye; click a photo to open it
   full size. This is a separate, optional, slower pass using perceptual image
   hashing. Nothing here is pre-checked for deletion.

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
- **Metadata matching needs metadata.** Edited or re-saved copies (different
  file size) won't match; that's what the Visual Duplicates tab is for. Photos
  with no camera data only match when the file name is also the same.
- **Created date** in the preview is the file system's creation time (accurate
  on Windows). Copying a file usually resets it, which is why the album date
  suggestion prefers the photo's own date taken when it has one.
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
  duplicates.py                metadata duplicate matching + folder-level grouping
  albums.py                    album naming rules + name/date suggestions
  quarantine.py                move a folder aside / restore it
  fileinfo.py                  file size/date helpers
  perceptual.py                Phase 5: visual similarity (stretch goal)
  actions.py                   the only place files are deleted (Recycle Bin)
gui/
  main_window.py                tab container
  scan_tab.py / browse_tab.py / albums_tab.py / duplicate_review_tab.py / visual_duplicates_tab.py
  photo_grid.py / preview_panel.py / compare_panel.py / album_dialogs.py / quarantine_dialog.py
  thumbnails.py                 Pillow-based thumbnail generation
  workers.py                    background threads (scan, hashing)
```
