#!/bin/bash
set -e

echo "=== Preparing Build ==="
cd /home/wa/VTTAI2

source playtest_env/bin/activate
pip install pyinstaller

echo "=== Running PyInstaller ==="
# We use --onedir to avoid unpacking 27000+ files on every startup.
# We include all specific data directories.
# Since the project imports from google.genai and others dynamically,
# we might need some hidden imports.

pyinstaller --noconfirm --onedir --windowed --name "VTTAI2" \
    --add-data "adventure:adventure" \
    --add-data "bestiary:bestiary" \
    --add-data "book:book" \
    --add-data "campagne:campagne" \
    --add-data "class:class" \
    --add-data "images:images" \
    --add-data "music:music" \
    --add-data "piper_models:piper_models" \
    --add-data "race:race" \
    --add-data "spells:spells" \
    --add-data "otherkeys:." \
    --add-data ".env:." \
    --hidden-import "google.genai" \
    --hidden-import "PIL._tkinter_finder" \
    --hidden-import "pygame" \
    --hidden-import "pydub" \
    main.py

echo "=== Build Complete ==="
echo "The executable is located in dist/VTTAI2/VTTAI2"
