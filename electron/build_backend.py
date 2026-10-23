import subprocess
import sys
from pathlib import Path


electron_dir = Path(__file__).resolve().parent
project_dir = electron_dir.parent

command = [
    sys.executable,
    "-m",
    "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onedir",
    "--name",
    "PN532_Backend",
    "--distpath",
    str(electron_dir / "backend-build"),
    "--workpath",
    str(electron_dir / "build" / "pyinstaller"),
    "--specpath",
    str(electron_dir / "build"),
    "--paths",
    str(project_dir),
    str(project_dir / "PN532_Backend.py"),
]

subprocess.run(command, cwd=project_dir, check=True)
