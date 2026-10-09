# TG Downloader for Android

An Android app that downloads videos and files from Telegram (private channels, groups and public channels, using your
own account) and videos from other websites. It is the phone version of the command-line downloader in this repository
and shares the same download engine.

- **Laid out like Tachiyomi**: a Library of the channels you follow (cover grid with downloaded and new counts), Updates,
  History, Browse and More along the bottom, and a page per channel that lists its videos like chapters.
- **Telegram**: log in with your own API ID, paste or share links, pick single videos from a channel, or download all of it.
- **Download manager**: pause, resume, reorder, cancel and retry; choose where files are saved, globally or per channel.
- **Watch first**: stream a video from Telegram inside the app before deciding to download it.
- **Hide from the gallery**: one switch puts a `.nomedia` file in the download folder (or in one channel's folder) so
  Gallery and Photos leave those videos alone.
- **Add your own link formats**: teach the app about links it does not know yet (see [Link rules](#link-rules)).
- **Share to download**: use Android's Share button, the text-selection menu, or just copy a link in Telegram.
- **Other websites**: YouTube, TikTok, Instagram, X and hundreds more through [yt-dlp](https://github.com/yt-dlp/yt-dlp).
- **Fast and sturdy**: parallel connections per file, several files at once, retry and cancel, downloads keep running
  with the screen off.

> **Status.** The app is built and tested on a computer (unit tests, the full interface in a browser, and a real APK
> build). The Android-only pieces (share sheet, background service, saving into `Download/`) compile and follow
> Android's documentation, but they have not been tried on a physical phone yet. See [Known limitations](#known-limitations).
> Please open an issue if something misbehaves and attach the log from *More > Diagnostics > Share*.

## Install

1. Get `tg-downloader-<version>.apk` from the project's **Releases** page (or build it yourself, see below).
2. Open the file on your phone. Android asks to allow installing apps from this source; allow it once.
3. Open **TG Downloader** and follow the login steps.

The APK is for 64-bit ARM phones (practically every phone since 2017). Android 7.0 or newer is required.

> Updating only works when the new APK is signed with the same key as the installed one. APKs from GitHub releases are
> signed with the project's key when the maintainer configured one; a build you make yourself without a key gets a
> debug signature and has to be uninstalled before a differently signed APK can be installed.

## First start: logging in

Telegram requires every app to use its own API credentials, and they are free:

1. On any browser open <https://my.telegram.org> and sign in with your phone number.
2. Open **API development tools** and create an app (any name).
3. Copy the **api_id** (a number) and the **api_hash** (32 characters).
4. In the app, enter them, then your phone number, the code Telegram sends you and, if you use one, your 2-step
   verification password.

The login is stored only inside the app's private storage on your phone. Android backup is switched off for the app, so
it is not copied to Google Drive. *Settings > Log out* removes it.

## The screens

| Tab | What it does |
|---|---|
| **Library** | The chats you follow, as a grid of covers. The left badge counts downloaded files, the right one new posts. Long-press a cover for its folder, *Hide from gallery*, *Mark as seen* or *Remove*. The toolbar searches, sorts, switches grid/list and checks for new posts. |
| **Updates** | New media in Library chats since you last opened them, grouped by day. Tap the arrow to download one, or the download button at the top for all of them. Checked when you open the tab (at most every 15 minutes) or with the refresh button. |
| **History** | Every finished download, newest first. Play it, share it, open its chat, delete the file, or forget missing files. |
| **Browse** | *Chats*: all your channels and groups with search and category chips; the heart adds one to the Library, a tap opens it. *Links*: paste links (see below). |
| **More** | The *Hide downloads from gallery* switch, the **Download queue**, Settings, Link rules, Diagnostics and About. |

### A channel page

Opening a chat shows its cover, name and download folder, four buttons (*Add to library*, *Folder*, *Hide*, *Get all*) and
its media newest first, 40 at a time (*Load older* fetches more). Choose *Videos*, *Files* or *Everything* with the chips.

- The button on each row shows its state: download arrow, waiting clock, a progress ring while downloading (tap to pause),
  a pause icon (tap to resume), a green check when it is on the phone, or a red error (tap to retry).
- Tap a row for details (caption, size, where it was saved) with Download, Open with, Share and Delete.
- **Watch before downloading**: tap a video's picture (it has a play icon) or *Watch* in its details. The video streams
  straight from Telegram and nothing is saved; *Download* on the player queues it if you want to keep it. Videos that are
  already downloaded play from the phone instead. Seeking works, though a jump may take a moment while Telegram sends
  that part.
- Long-press a row to select several, then use the floating *Download* button. The toolbar has *Select all* and *Invert*.
- **Download to a folder of your own**: select media, then tap *To folder...* (or the folder icon in the toolbar; a single
  item has *To folder...* in its details). Pick the chat's main folder, one of the folders you made before (with its file
  count), or type a new name. The files go to `Chat name/your folder/`, for example `Dev/auth`, `Dev/session`,
  `Dev/localstorage`. Plain *Download* still saves straight into the chat's folder (`Dev`). Finished rows show the folder
  name, the queue shows `Dev / auth`, and the folder names are remembered per Library chat and included in the backup.
  A chat hidden with `.nomedia` hides its custom folders too. *Get all* saves into the main folder.
- *Get all* (or the download menu) scans the whole chat and queues everything that is not downloaded or queued yet.

### Download queue

*More > Download queue* lists running, waiting, paused and finished downloads. The pause button in the toolbar pauses
the whole queue; each row has its own menu with *Pause/Resume*, *Move to top*, *Cancel*, *Try again*, *Share* and
*Open channel*. Telegram cannot continue a half-finished file, so a paused download starts that file over when resumed.

Finished rows add *Play* (videos, in the app's own player) and *Open with...*, which shows Android's app list every time
so you pick the app: VLC, MX Player, a gallery, a PDF reader. The button on the row plays a video, or opens any other
file the same way. The other app gets read-only access to that one file, and it works for files hidden with `.nomedia`
too. History and the player page have *Open with...* as well.

### Backing up your settings

*More > Settings > Backup > Export* saves one file with your API ID and hash, all settings, link rules and the Library
(chats with their folder and *Hide* choices). Pick what to include and, if the API hash is in it, set a password: the file
is then encrypted (AES-256 with a key derived from the password) and a wrong password or a changed file is refused.
*Import* on another phone or after reinstalling reads it back: settings and rules are replaced, Library chats are added,
and folders that do not exist on the new phone are skipped. The Telegram login is never exported, so you log in once
with your phone number after importing.

## Downloading

### From links (Browse > Links)

Paste one or many links, one per line or separated by spaces, and tap **Add to queue**. **Check links** shows how each
one is understood without downloading anything. The filter chooses whether Telegram posts are saved as everything,
only videos or only non-video files.

Understood out of the box:

| Link | Meaning |
|---|---|
| `https://t.me/c/1234567890/456` | message in a private channel or supergroup |
| `https://t.me/c/1234567890/456-460` | a range of messages (capped at 1000, see Settings) |
| `https://t.me/username/456` | message in a public channel or group (also `telegram.me`, `telegram.dog`, `/s/username/456`) |
| `https://t.me/c/1234567890/12/456` | message 456 inside forum topic 12 |
| `https://web.telegram.org/k/#-1001234567890/456` | Telegram Web links |
| `tg://privatepost?channel=1234567890&post=456`, `tg://resolve?domain=username&post=456` | deep links |
| `https://t.me/username` | a whole channel: offered as *Open*, which shows its page |
| `456` or `456-460` | bare message numbers, when a *Default channel* is set in Settings |
| any other `http(s)` link | handed to yt-dlp when *Other websites* is switched on |

### From Telegram: copy the link

In Telegram, long-press a message and choose **Copy link**, then open TG Downloader. If the clipboard holds a link the app
understands, a bar offers **Download**. (You can switch this off in Settings.) Telegram's own *Share* button opens its
internal chat picker, so *Copy link* is the way to go for Telegram posts.

### From other apps: Share and "Open with"

- **Share sheet**: in Chrome, YouTube and most other apps, tap *Share* and pick **Download with TG Downloader**.
- **Text selection**: select a link, open the menu and choose **Download with TG Downloader**.
- **Open with**: to make `t.me` links open in this app, go to *Android Settings > Apps > TG Downloader > Open by default >
  Add link* and tick the `t.me` entries. (Android 12 and newer will not do this automatically for apps that are not
  from a store.)

By default a shared link starts downloading straight away. *Settings > Start right away when I share a link* turns that
into a confirmation step.

### Whole channels

Open the chat from *Browse* (or type its id or `@username` there) and tap *Get all*. The app scans the messages, tells
you how many files it found and how big they are, and asks before it queues them. The *Max size per channel scan*
setting protects your storage. Files with the same name and size as an existing file are skipped, and files with the
same name but different content get the message number added to their name.

## Where files go

By default `Download/Telegram Downloads/<channel name>/` in your phone's storage, so they show up in the Files app.
If Android refuses to let the app write there, the app falls back to its own folder (`Android/data/<app>/files/`) and
tells you.

- **Another folder**: *Settings > Storage* has a folder picker. Folders outside `Download/` (or on an SD card) need
  **All files access**, which Android 11+ grants in its own settings screen; the app opens it for you. That permission is
  fine for an APK installed from GitHub; Google Play would ask for a justification.
- **One folder per channel** can be switched off in *Settings > Storage* to put everything in one folder.
- **A folder for one channel**: on its page tap *Folder* (or long-press its Library cover). New downloads go there;
  files already saved are not moved.

### Hiding downloads from the gallery (.nomedia)

- *More > Hide downloads from gallery* (also in *Settings > Storage*) writes an empty `.nomedia` file into the main
  download folder. Android's media scanner then skips that folder and everything inside it.
- *Hide* on a channel page does the same for that channel's folder only. The channel joins the Library so the choice is
  remembered, and new downloads into that folder get the marker too.
- After a change the app asks Android to re-scan the affected files, so they disappear from (or come back to) Gallery and
  Photos. Some gallery apps keep their own cache for a while; clearing that app's cache helps if a video still shows.
  Nothing is deleted or moved, and file managers still show the files.
- When the main folder is hidden, a single channel cannot be shown again on its own (`.nomedia` covers subfolders).

## Link rules

A rule says "links that look like *this* mean *that*". Use it for mirrors, bots or sites that wrap Telegram links.
Create one in *More > Link rules > Add rule*, or share the same file with the command-line tool
(`link_rules.json`, menu 5 in `downloader.py`).

Write the pattern like the link, with placeholders where parts change:

| Placeholder | Matches |
|---|---|
| `{channel}` | username, `-100…` id or a plain number (read as a private channel id) |
| `{username}` | public username |
| `{cid}` | private channel number (becomes `-100<cid>`) |
| `{peer}` | username or id, used as written |
| `{msg}` | message id; ranges such as `12-20` work |
| `{topic}` | forum topic number (ignored) |
| `{*}` | any text inside one path segment |
| `{**}` | anything |

What a rule can do:

| Action | Example |
|---|---|
| Download Telegram messages | pattern `mysite.com/{channel}/{msg}` matches `https://mysite.com/durov/12-14` |
| Open as a whole channel | pattern `mysite.com/channel/{channel}` |
| Rewrite into another link | pattern `go.example/{*}/{msg}` rewrites to `https://t.me/mychannel/{msg}` |
| Download page with yt-dlp | pattern `videos.example/{**}` |

The scheme (`https://`) and `www.` are optional and matching ignores upper/lower case. Rules with a higher priority are
tried first. Choose **Regex** in the editor if you need full control; use named groups with the same names
(`channel`, `username`, `cid`, `peer`, `msg`, `msg_end`).

The editor checks a rule before saving it, runs its example link, and refuses patterns that cannot work. The **Test a link**
box on the same screen shows exactly how any text would be understood. *Export* shares all rules as JSON and *Import*
loads such a file.

## Other websites (yt-dlp)

Anything that is not a Telegram link is passed to yt-dlp. Because Android has no ffmpeg, the app asks for ready-made
single files (video and audio together) instead of merging separate streams. That works for most sites but limits the
quality on some, notably YouTube. Instagram and X sometimes need you to be logged in: export `cookies.txt` from your
browser (a "cookies.txt" extension does it) and pick it in *Settings > Other websites*.

## Background downloads

While something downloads, the app shows a notification with progress and keeps the connection and CPU awake, so downloads
continue with the screen off. Android 13+ asks once for permission to show that notification. Some phone makers kill
background apps aggressively; *Settings > Android > Allow unrestricted battery use* asks Android to leave the app alone.
Android 15 limits this kind of service to about six hours a day.

## Speed

Telegram encrypts downloads, so decryption speed matters. *More > Diagnostics* shows the AES backend: `openssl-ctypes`
(or `cryptg`) is fast; `cryptography` or `pyaes` means downloads will be slow. *Run speed test* measures it. If
files fail often, lower *Connections per file* or *Files at the same time* in Settings.

## Known limitations

- Not yet verified on a physical Android device; see the status note at the top.
- Telegram's own *Share* button cannot target this app; use *Copy link*.
- YouTube works with limited quality on Android (no ffmpeg or JavaScript runtime). The desktop tool does better when
  `ffmpeg` and `node` are installed.
- Android 15 limits download services to about six hours a day.
- Pausing a Telegram download restarts that file from the beginning when it resumes.
- Only 64-bit ARM is built by default (see below for other architectures).

## Building the APK yourself

You need Windows (or Linux/macOS with the same commands), Python 3.13 and a few GB of disk. The first build downloads
Flutter, a JDK and parts of the Android SDK (20 to 40 minutes); later builds take a few minutes.

```powershell
cd apk
py -3.13 -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
scripts\build_apk.ps1            # tests, secrets check, build, check again
scripts\build_apk.ps1 -Install   # ...and adb install -r onto a connected phone
```

The script needs PowerShell 7. The APK ends up in `build\apk\tg-downloader.apk`.

What the script takes care of for you, in case you build by hand with `flet build apk --python-version 3.13`:

- **`pyaes` wheel.** Telethon depends on `pyaes`, which PyPI only ships as source, and the build accepts wheels only.
  `scripts/prepare_wheels.py` builds it and the script points `PIP_FIND_LINKS` at the result.
- **Symlinks on Windows.** Flutter creates symlinks for its desktop targets. Turn on *Developer Mode* in Windows settings,
  or let the script disable the Windows/Linux desktop targets for the duration of the build.
- **Networks that block a download host.** Some corporate DNS servers sinkhole `s3-accelerate.amazonaws.com`, which pip
  needs for Flet's Android wheels. `scripts\build_apk.ps1 -DnsFix` starts `scripts/dns_fix_proxy.py`, a small local proxy that
  falls back to public DNS for hosts that do not resolve.
- **Encoding.** `PYTHONUTF8=1` and `FLET_CLI_NO_RICH_OUTPUT=1` keep Flet's console output from crashing on Windows code pages.
- **`jni_flutter`.** Flet's build template pins `jni 1.0.0`, but the newest `jni_flutter` needs `jni >= 1.1`. `pyproject.toml`
  pins `jni_flutter 1.0.1` under `[tool.flet.flutter.pubspec.dependency_overrides]`. Remove that once Flet updates its template.
- **Secrets.** `scripts/check_no_secrets.py` refuses to continue when the sources contain `.env`, a Telegram session, credentials, a
  keystore or media files, and checks the finished APK again. Only `apk/src` is packaged.

Other CPU architectures: `scripts\build_apk.ps1 -Arch armeabi-v7a` (32-bit phones) or `-Arch x86_64` (emulators).

### Signing

Without a key the APK gets a debug signature. To sign with your own key (keep this file safe and backed up; losing it
means users must uninstall to update):

```powershell
keytool -genkeypair -v -keystore $env:USERPROFILE\.android\tgdl-upload.jks -alias tgdl -keyalg RSA -keysize 2048 -validity 10000
[Environment]::SetEnvironmentVariable("FLET_ANDROID_SIGNING_KEY_STORE", "$env:USERPROFILE\.android\tgdl-upload.jks", "User")
[Environment]::SetEnvironmentVariable("FLET_ANDROID_SIGNING_KEY_STORE_PASSWORD", "<store password>", "User")
[Environment]::SetEnvironmentVariable("FLET_ANDROID_SIGNING_KEY_ALIAS", "tgdl", "User")
[Environment]::SetEnvironmentVariable("FLET_ANDROID_SIGNING_KEY_PASSWORD", "<key password>", "User")
```

### Releases with GitHub Actions

`.github/workflows/android.yml` runs the tests, builds the APK on a clean machine and attaches it to a GitHub Release when
you push a tag:

```bash
git tag v3.1.0
git push <your-fork> v3.1.0
```

Every push to the `android-app` branch (touching `apk/`) also builds a test APK; download it from the run's
*Artifacts* section in the *Actions* tab. You can also run the workflow by hand there once the workflow file is on the
default branch. To sign
release builds, add these repository secrets: `ANDROID_KEYSTORE_BASE64` (the `.jks` file encoded with base64),
`ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_PASSWORD`, `ANDROID_KEY_ALIAS`. Without them the APK is debug-signed and the
workflow prints a warning.

## For developers

```
apk/
  src/main.py            Flet entry point
  src/tgdl/              download engine, link router, rules, Telegram session, library/history stores,
                         storage helpers (.nomedia) and media browsing (no UI code; also used by downloader.py)
  src/tgdl_ui/           the Flet interface: app shell and tabs (library, updates, history, browse, more),
                         channel page, download queue, settings, native bridge
  extensions/flet-tgdl-native/   Kotlin plugin: share target, foreground service, media scan, permissions
  tests/                 pytest
  scripts/               build_apk.ps1, check_no_secrets.py, prepare_wheels.py, dns_fix_proxy.py
```

```powershell
.venv\Scripts\python -m pytest                      # unit tests, including interface smoke tests
$env:TGDL_DATA_DIR = "$env:TEMP\tgdl-dev"           # keep a dev run away from your real data
.venv\Scripts\flet run src\main.py                  # run the interface on the desktop
```

The command-line tool in the repository root (`downloader.py`) imports `tgdl` from `apk/src`, so a fix to the engine or the
link router helps both.
