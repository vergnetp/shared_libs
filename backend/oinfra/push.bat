@echo off
echo Smart Push - Automatically finds servers and pushes config
echo ============================================
set /p project_name="Enter the project name: "

echo.
echo Pushing to all servers with tag "Infra"...
python -c "from deployer import Deployer; from server_inventory import ServerInventory; servers = ServerInventory.list_all_servers(); ips = [s['ip'] for s in servers]; print(f'Found {len(ips)} servers: {ips}'); d = Deployer('%project_name%'); d.push_config(targets=ips if ips else ['localhost'])"

echo.
echo Push complete!
pause