@echo off
rem 单启 Celery Worker（Windows 必须 --pool=solo；不支持 prefork）
cd /d "%~dp0.."
.venv\Scripts\celery.exe -A app.celery_app:celery_app worker --pool=solo --loglevel=INFO
