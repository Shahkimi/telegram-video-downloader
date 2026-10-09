# 🚀 Telegram Downloader (Videos & Documents)

> **Developed by:** PARVEJ  
> **License:** MIT Open Source  
> **Language:** Python 3.10+  
> **Also available:** an [Android app](apk/README.md) built on the same engine

An advanced, beginner-friendly interactive CLI tool to download videos, documents, images, PDFs, archives, and files from Telegram private channels, groups, and public channels with live progress tracking.

---

## ✨ Features

- 🟢 **Interactive ASCII CLI Menu**: Modern Terminal interface powered by `rich`.
- 🔐 **Built-in Account Management**:
  - Interactive **Telegram Login** (Phone number, OTP, and 2FA password support).
  - **Logout Telegram** feature to clear sessions securely.
- 📦 **Categorized Batch Download Mode**:
  - **Private Channels**, **Private Groups**, **Public Channels**, **Public Groups**, and **Manual ID Input**.
  - Automatic **A-Z Alphabetical Sorting** of all channels.
  - Safe size budget calculation to prevent disk storage overflow.
- 🔗 **Direct Link Download Modes**:
  - **Option [3] Video Download by Link**: Download specific videos using message links.
  - **Option [4] General File/Document Download by Link**: Download non-video files (PDF, PNG, JPG, DOCX, PPTX, TXT, ZIP, RAR, MP3, etc.).
  - **Range Link Support**: Download ranges like `t.me/c/1234567890/10-25` or space-separated multiple links.
- 📊 **Real-time Live Progress Bar**: Shows current queue index, filename, download speed (MB/s), completion percentage, and estimated time remaining (ETA).
- 🌐 **Videos from other websites**: YouTube, TikTok, Instagram, X and hundreds more through [yt-dlp](https://github.com/yt-dlp/yt-dlp), right from the same link prompt.
- ➕ **Add your own supported links**: define link formats (mirrors, wrapper sites, bots) as rules and the downloader understands them. See [Link Rules](#5️⃣-option-5-link-rules).
- ⚡ **Fast and fault-tolerant**: parallel connections per file, several files at once, `.part` files that are only renamed when the size matches, retries.
- 📱 **Android app**: the same downloader on your phone, with share-to-download. See [`apk/README.md`](apk/README.md).
- 📂 **Automatic Download Folder**: Automatically saves all files to `C:\Users\USER\Downloads\Telegram Downloads` (change it in Settings).

---

## 🛠️ Prerequisites (Prerequisite Setup)

Before running the tool, make sure you have:

1. **Python 3.10 or higher** installed on your system.  
   - Download Python: [https://www.python.org/downloads/](https://www.python.org/downloads/)
   - **Important during installation**: Check the box **"Add Python to PATH"**.

---

## 🔑 How to Get Your Telegram API Credentials

To use Telegram API services, you need an `API_ID` and `API_HASH`:

1. Open your web browser and go to [https://my.telegram.org](https://my.telegram.org).
2. Enter your phone number with your country code (e.g., `+88017XXXXXXXX`) and click **Next**.
3. Enter the confirmation code sent to your Telegram app.
4. Click on **API Development Tools**.
5. Fill in the **App title** and **Short name** (e.g., `MyDownloaderApp`).
6. Click **Create Application**.
7. Copy your **App api_id** (number) and **App api_hash** (string).  
   *(Note: You will only need to enter these once in the tool or save them in a `.env` file!)*

---

## 📥 Beginner-Friendly Quick Start Guide

### Step 1: Clone or Download the Project
Open Terminal (Command Prompt / PowerShell) and run:
```bash
git clone https://github.com/xyzbuddy/telegram-video-downloader.git
cd telegram-video-downloader
```

### Step 2: Install Required Dependencies
Run the following command to install the required Python packages:
```bash
pip install -r requirements.txt
```

### Step 3: Run the Downloader
You can launch the tool in **one click**:

- **On Windows**: Simply double-click **`start.bat`** (or run `.\start` in PowerShell/CMD).
- **On Linux / Mac**: Run `python downloader.py` in your terminal.

Optional: install [`ffmpeg`](https://ffmpeg.org/download.html) (and Node.js) if you want the best quality from YouTube and similar sites; without them the downloader falls back to ready-made single files.

---

## 🎮 How to Use the Interactive Menu

When you start the tool, you will see the Main Menu:

```text
==================================================
 Telegram Private Downloader
==================================================
Select Download Mode:
  [1] My Account
  [2] Download ALL videos from channel (Batch Mode)
  [3] Download SPECIFIC video(s) by Link (Telegram or other sites)
  [4] Download SPECIFIC file(s)/document(s) by Link
  [5] Link Rules (add your own supported links)
  [6] Settings & Diagnostics
  [7] Exit
==================================================
Enter choice (1-7): 
```

### 1️⃣ Option [1]: My Account
- **Telegram Login**: If you haven't logged in yet, select this option. It will ask for your `API_ID` & `API_HASH` (saved automatically to `.env`), followed by your Phone Number, OTP Code, and 2FA Password.
- **Logout Telegram**: Safely terminates your active Telegram session after asking for confirmation (Y/N).

### 2️⃣ Option [2]: Download ALL Videos from Channel (Batch Mode)
- Choose target category: **Private Channel**, **Private Group**, **Public Channel**, **Public Group**, or **Manual ID Input**.
- Select a channel from the alphabetically sorted A-Z list.
- Scans messages, displays total detected videos and total size budget, and asks `Continue download? (Y/N)`.

### 3️⃣ Option [3]: Download SPECIFIC Video(s) by Link
- Paste single video post links, range links (`https://t.me/c/12345/10-20`), or multiple space-separated links.
- Also understood: `t.me/username/123`, `/s/` links, forum-topic links, `web.telegram.org` links, `tg://` links, and bare message numbers when a default channel is set.
- Paste a link from another website (YouTube, TikTok, X, ...) and it is downloaded with yt-dlp.
- Confirms detected videos and downloads them with a live progress bar.

### 4️⃣ Option [4]: Download SPECIFIC File(s)/Document(s) by Link
- Paste links for non-video files (PDF, PNG, JPG, DOCX, ZIP, PPT, TXT, etc.).
- Detects documents and downloads them to your download folder.

### 5️⃣ Option [5]: Link Rules

Teach the downloader about link formats it does not know. A rule says "links that look like *this* mean *that*":

| Action | Example pattern | What it does |
|---|---|---|
| Download Telegram messages | `mysite.com/{channel}/{msg}` | `https://mysite.com/durov/12-14` downloads messages 12 to 14 of `durov` |
| Open as a whole channel | `mysite.com/channel/{channel}` | offers the channel for a batch scan |
| Rewrite into another link | `go.example/{*}/{msg}` rewritten to `https://t.me/mychannel/{msg}` | converts the link, then reads it again |
| Download with yt-dlp | `videos.example/{**}` | hands the page to yt-dlp |

Placeholders: `{channel}` `{username}` `{cid}` `{peer}` `{msg}` `{topic}` `{*}` (one path segment) `{**}` (anything). Advanced users can pick the regex style with named groups.
The menu lets you **add** (with a guided wizard that tests your example link), **test** any link, **turn on/off**, **delete**, **export** and **import** rules. Rules are stored in `link_rules.json` next to `downloader.py` (not committed), and the Android app can import the same file.

Quick check without logging in: `python downloader.py --parse "https://t.me/c/123/10-12"`.

### 6️⃣ Option [6]: Settings & Diagnostics
Download folder, size budget, files at once, connections per file, default channel, yt-dlp switch and maximum video height, plus a diagnostics screen (`python downloader.py --diag`) that shows library versions, the AES backend and a quick speed test.

---

## 📱 Android App

The `apk/` folder contains the same downloader as an Android app with a Tachiyomi-style interface: a Library of followed channels, Updates, History, a page per channel to pick videos, a download manager (pause, resume, reorder), a choice of save folder (globally or per channel) and a `.nomedia` switch that hides downloads from the gallery. Download the APK from the Releases page or the GitHub Actions artifacts, or build it yourself; see [`apk/README.md`](apk/README.md) for installation, usage and build instructions.

---

## 🔒 Security & Privacy Notice

- **No Secrets Tracked**: `.env` and `*.session` files are listed in `.gitignore` to prevent sensitive credentials from ever being uploaded.
- **Local Execution**: All session data and credentials stay on your local computer (or in the Android app's private storage).
- **Credentials are never logged**: codes, passwords and the API hash are kept out of the log files.

---

## 🤝 Open Source & Contributions

This project is **Open Source** under the **MIT License**. Contributions, bug fixes, and feature upgrades are welcome!

1. Fork the Project.
2. Create your Feature Branch (`git checkout -b feature/AmazingFeature`).
3. Commit your Changes (`git commit -m 'Add some AmazingFeature'`).
4. Push to the Branch (`git push origin feature/AmazingFeature`).
5. Open a Pull Request.

---

## 📜 License

Distributed under the MIT License. See [`LICENSE`](LICENSE) for details.

Developed with ❤️ by **PARVEJ**
