@rem 本项目编码格式：UTF-8（无 BOM）。
@echo off
rem 单启 Celery Beat（周期调度 scan_stale_tasks 僵尸回收）。
rem why 单独一个进程：Beat 只负责"什么时候发任务"，不执行任务；与 Worker 分开才能各自重启互不影响。
rem 注意：Beat 用文件锁（celerybeat-schedule）保证同机只跑一个实例，重复启动会直接报错退出。
cd /d "%~dp0.."
.venv\Scripts\celery.exe -A app.celery_app:celery_app beat --loglevel=INFO
