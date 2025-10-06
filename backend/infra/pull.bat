@echo off
echo Pushing config, secrets, and files for all environments...
python -c "from deployer import Deployer; Deployer().pull_data()"
echo Push complete!
pause