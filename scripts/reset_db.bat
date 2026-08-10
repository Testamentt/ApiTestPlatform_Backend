@echo off
rem Reset the local dev SQLite DB. Why: Base.metadata.create_all only creates missing tables and never ALTERs
rem existing ones, so after adding model columns an existing dev DB drifts (2026-08-10: trust_score missing ->
rem GET /cases 500). Deleting the file lets create_all rebuild the full schema on next startup.
rem Dev data is disposable (data/ is gitignored); for a real backup use `sqlite3 <db> ".backup <dest>"`.
set "DB=%~dp0..\data\platform.db"
if exist "%DB%" (
    echo Deleting %DB% and its -wal / -shm sidecars...
    del /q "%DB%" "%DB%-wal" "%DB%-shm"
    echo Done. Restart via scripts\start_all.bat (or uvicorn) to recreate the schema on startup.
) else (
    echo No existing DB at %DB% - nothing to delete. First startup will create it.
)
pause
