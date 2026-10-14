@echo off
rem shape-param-lab start (Windows). First run installs libraries and builds the UI.
cd /d "%~dp0"
where uv >nul 2>nul || (echo [ERROR] uv not found. See README. & pause & exit /b 1)
if not exist frontend\dist (
  where npm >nul 2>nul || (echo [ERROR] Node.js 22.12+ not found. & pause & exit /b 1)
  pushd frontend
  call npm install || (popd & pause & exit /b 1)
  call npm run build || (popd & pause & exit /b 1)
  popd
)
cd backend
uv sync || (pause & exit /b 1)
start "" http://127.0.0.1:8200
uv run uvicorn server:app --host 127.0.0.1 --port 8200
pause
