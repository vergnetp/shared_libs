@echo off
echo ============================================================
echo Secret Rotation Utility
echo ============================================================
echo.
echo This will rotate database passwords and restart all services.
echo.
echo What happens:
echo   1. Scan secrets directory for database passwords
echo   2. Generate new passwords for databases only
echo   3. Push ALL secrets to all production servers
echo   4. Restart ALL services in correct startup order
echo.
echo External secrets (API keys, etc.) are NOT auto-rotated.
echo.
echo ============================================================
echo.

set /p project_name="Enter the project name: "
set /p env_name="Enter the environment (prod/staging/dev): "

echo.
echo Starting secret rotation for %project_name%/%env_name%...
echo.

python -c "from secrets_rotator import SecretsRotator; rotator = SecretsRotator('%project_name%', '%env_name%'); results = rotator.rotate_all_secrets(restart_all_services=True); print(f'\nRotation complete! Rotated: {len(results[\"rotated\"])} services, Restarted: {len(results[\"restarted\"])} services')"

echo.
echo ============================================================
echo Rotation complete!
echo ============================================================
echo.
pause