@echo off
rem 面试前一键启动：uvicorn + celery worker 各开一个独立窗口（各自 Ctrl+C 可分别停止）
cd /d "%~dp0.."
start "testplatform-api" cmd /k .venv\Scripts\uvicorn.exe app.main:app --port 8000
start "testplatform-worker" cmd /k .venv\Scripts\celery.exe -A app.celery_app:celery_app worker --pool=solo --loglevel=INFO
