@echo off
cd /d "%~dp0"
where python >nul 2>nul || (echo Python 3.10+ is not installed. Install it from https://python.org and tick "Add to PATH". & pause & exit /b 1)
where node >nul 2>nul || (echo Node.js 20+ is not installed. Install it from https://nodejs.org & pause & exit /b 1)
if not exist .env copy .env.example .env >nul
powershell -ExecutionPolicy Bypass -File .\run.ps1
pause
