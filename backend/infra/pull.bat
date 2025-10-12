@echo off
echo Pushing config, secrets, and files for all environments...
python -c "from deployer import Deployer; Deployer('new_project').pull_data()"
echo Push complete!
pause