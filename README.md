# Goods Receiving: scan QR, assign to PO/GRN, export

## Run it
    pip install -r requirements.txt
    python manage.py makemigrations receiving
    python manage.py migrate
    python manage.py runserver 0.0.0.0:8000
Open http://localhost:8000

## Scanner
Any USB or Bluetooth barcode scanner in keyboard mode works. It types the QR content
and presses Enter; the page reads that as "scan finished". Click the scan box first.

## Pages that build content with JavaScript
If a site shows little in plain HTML, install the optional renderer once:
    pip install playwright
    playwright install chromium
The app switches to it automatically when fewer than 4 fields are found.
