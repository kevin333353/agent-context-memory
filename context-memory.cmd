@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%USERPROFILE%\.agent-context-memory\context-memory.ps1" %*
