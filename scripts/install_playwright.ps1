# Установка браузера Playwright с увеличенным таймаутом и зеркалом.
# Запуск из корня проекта с активированным venv:
#   .\scripts\install_playwright.ps1

$ErrorActionPreference = "Stop"

Write-Host "Playwright: установка Chromium (таймаут 10 мин)..." -ForegroundColor Cyan

$env:PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT = "600000"

# Основной CDN
playwright install chromium
if ($LASTEXITCODE -eq 0) {
    Write-Host "OK: Chromium установлен." -ForegroundColor Green
    exit 0
}

Write-Host "Повтор через зеркало azureedge..." -ForegroundColor Yellow
$env:PLAYWRIGHT_DOWNLOAD_HOST = "https://playwright.azureedge.net"
playwright install chromium
if ($LASTEXITCODE -eq 0) {
    Write-Host "OK: Chromium установлен через зеркало." -ForegroundColor Green
    exit 0
}

Write-Host @"

Не удалось скачать Chromium (таймаут / блокировка CDN).

Обход без playwright install:
  1. Установите Google Chrome: https://www.google.com/chrome/
  2. В .env добавьте: BROWSER_CHANNEL=chrome
  3. Запускайте: python main.py login

"@ -ForegroundColor Yellow
exit 1
