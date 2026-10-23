# PN532 NFC Studio

PN532 NFC Studio is a Windows desktop application for reading, writing, and
clearing NDEF data on NFC tags with a PN532 USB reader. It combines a graphical
interface with the project's Python NFC backend.

## Features

- **Read Tag** reads and displays tag information and NDEF records from
  supported NFC-A tags: MIFARE Classic, NFC Type 2 (such as Ultralight/NTAG),
  and NFC Type 4.
- NDEF URL/URI records show a button to open valid HTTP or HTTPS links in the
  default browser.
- **Write Tag** writes a URL or plain text to a prepared MIFARE Classic tag.
  The app detects the tag and asks for confirmation before writing.
- **Delete Tag** clears NDEF data from an already formatted MIFARE Classic
  tag, or prepares an eligible factory-blank MIFARE Classic tag for NDEF.
  The app checks the tag and asks for confirmation before changing it.
- Press **F11** to switch between borderless fullscreen and windowed mode.

## Hardware and tag compatibility

- A PN532 NFC reader connected over USB. The app uses the serial connection
  provided by the reader's USB adapter.
- Reading supports supported NFC-A tags with readable NDEF data, including
  MIFARE Classic, Type 2, and Type 4 tags.
- Writing is limited to MIFARE Classic tags that have been made NDEF-ready
  with **Delete Tag**. The maximum NDEF data capacity for writing is 96 bytes.
- Formatting is limited to eligible factory-blank MIFARE Classic tags with a
  four-byte UID. The app does not format arbitrary tags or erase unrelated
  tag types.
- Classic access is performed only with the known standard keys used by the
  app; it does not guess keys.

## Download and run

Download the Windows installer from the project's GitHub **Releases** page and
run the setup program. The installer includes the Electron application and a
standalone Python NFC backend, so Python does not need to be installed on the
computer running the installed app.

Connect the PN532 reader over USB, start **PN532 NFC Studio**, select an action,
and follow the on-screen instructions. For writing and formatting, review the
detected tag details and confirm only when you intend to make the change.

## Build from source

The installer is built for Windows and includes the Python backend. Run these
commands from the project root (the folder containing `requirements.txt`):

```powershell
py -m pip install -r .\requirements.txt
py -m pip install -r .\electron\requirements-build.txt
Set-Location .\electron
npm install
npm run dist
```

If PowerShell is already in the `electron` folder, use the relative paths below
without running `Set-Location`:

```powershell
py -m pip install -r ..\requirements.txt
py -m pip install -r .\requirements-build.txt
npm install
npm run dist
```

The installer is written to `electron\release`.

## Python interface

The original Python desktop interface is also available for development. Install
the Python dependencies from the project root, then run `PN532_App.pyw`.

## Changelog

See [CHANGELOG.md](./CHANGELOG.md) for recent changes.
