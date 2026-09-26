# 🚀 Telegram Downloader (Videos & Documents)

> **Developed by:** PARVEJ  
> **License:** MIT Open Source  
> **Language:** Python 3.8+

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
- 📂 **Automatic Download Folder**: Automatically saves all files to `C:\Users\USER\Downloads\Telegram Downloads`.

---

## 🛠️ Prerequisites (Prerequisite Setup)

Before running the tool, make sure you have:

1. **Python 3.8 or higher** installed on your system.  
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
  [3] Download SPECIFIC video(s) by Post/Message Link
  [4] Download SPECIFIC file(s)/document(s) by Link
  [5] Exit
==================================================
Enter choice (1-5): 
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
- Confirms detected videos and downloads them with a live progress bar.

### 4️⃣ Option [4]: Download SPECIFIC File(s)/Document(s) by Link
- Paste links for non-video files (PDF, PNG, JPG, DOCX, ZIP, PPT, TXT, etc.).
- Detects documents and downloads them to your download folder.

---

## 🔒 Security & Privacy Notice

- **No Secrets Tracked**: `.env` and `*.session` files are listed in `.gitignore` to prevent sensitive credentials from ever being uploaded.
- **Local Execution**: All session data and credentials stay on your local computer.

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
