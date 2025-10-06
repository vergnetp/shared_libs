@echo off
echo Pushing config, secrets, and files for all environments...
python -c "from deployer import Deployer; Deployer().push_config()"
echo Push complete!
pause