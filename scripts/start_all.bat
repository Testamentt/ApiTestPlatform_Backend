@echo off
rem One-click launch: uvicorn + celery worker, one tab per window (Ctrl+C stops each).
rem Why Windows Terminal: cmd (conhost) does not render ANSI color codes, so uvicorn's colored logs show as '[' chars.
rem Why two separate wt calls: wt's `; subcommand separator leaks a backtick into the previous tab's command (uvicorn then
rem errors with "Got unexpected extra argument (`)"). Two independent calls avoid the separator entirely.
rem Why cd inline: wt new tabs start in the user's home dir, not the launching dir; cd inside cmd /k is deterministic.
cd /d "%~dp0.."
wt new-tab --title "testplatform-api" cmd /k "cd /d %cd% && .venv\Scripts\uvicorn.exe app.main:app --port 8000"
wt new-tab --title "testplatform-worker" cmd /k "cd /d %cd% && .venv\Scripts\celery.exe -A app.celery_app:celery_app worker --pool=solo --loglevel=INFO"
