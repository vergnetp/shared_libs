from project_deployer import ProjectDeployer
import sys, os
import ctypes
from logger import Logger

def ensure_admin():
    """Ensure script runs with administrator privileges on Windows"""
    if os.name == 'nt':  # Windows only
        try:
            is_admin = ctypes.windll.shell32.IsUserAnAdmin()
        except:
            is_admin = False
        
        if not is_admin:
            print("Administrator privileges required for Windows deployment.")
            print("Restarting with elevated privileges...")
            
            # Get current working directory to maintain context
            cwd = os.getcwd()
            
            # Build command with working directory
            script_path = os.path.abspath(sys.argv[0])
            args = sys.argv[1:] if len(sys.argv) > 1 else []
            
            try:
                # Restart with elevation, maintaining working directory
                ctypes.windll.shell32.ShellExecuteW(
                    None,
                    "runas",
                    sys.executable,
                    f'"{script_path}" {" ".join(args)}',
                    cwd,  # Set working directory
                    1
                )
                print("Elevated process started. Original process exiting...")
                sys.exit(0)  # Exit original process cleanly
                
            except Exception as e:
                print(f"Failed to elevate privileges: {e}")
                print("\nPlease run this script manually as Administrator:")
                print("1. Right-click Command Prompt/PowerShell")
                print("2. Select 'Run as administrator'") 
                print("3. Navigate to your script directory")
                print("4. Run the script again")
                sys.exit(1)

def main():
    try:
        #ensure_admin()
        userA = 'User_A'
        userB = 'User_B'
        project = "Project1"
        deployerA = ProjectDeployer(userA, project)
        deployerB = ProjectDeployer(userB, project)
        deployerA.deploy(env="uat")
        deployerB.deploy(env='uat')
    except Exception as e:
        Logger.log(f"MAIN ERROR: {e}")

main()