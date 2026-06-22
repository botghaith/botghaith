# تشغيل محلي للتجربة فقط — لا تستخدمه مع Render
Write-Host ""
Write-Host "========================================" -ForegroundColor Yellow
Write-Host "  تحذير: للتجربة المحلية فقط" -ForegroundColor Yellow
Write-Host "  اذهب Render -> Suspend قبل التشغيل" -ForegroundColor Yellow
Write-Host "  وإلا يصير 409 Conflict" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow
Write-Host ""

Set-Location (Split-Path $PSScriptRoot -Parent)
& .\venv\Scripts\python.exe bot.py
