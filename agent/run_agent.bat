@echo off
REM ── BTP ERP Agent — รันรอบเดียว (local mode) ──
REM ดับเบิลคลิกไฟล์นี้เพื่อตรวจคลังหนึ่งครั้ง แล้วดูผลที่ agent\reports\latest.md
cd /d "%~dp0\.."
python -m agent.loop --once
echo.
echo เสร็จแล้ว — เปิดดูรายงานที่ agent\reports\latest.md
pause
