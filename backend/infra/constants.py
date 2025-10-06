from pathlib import Path
from typing import List

ROOT_PATH = Path(__file__).resolve().parent  # Folder containing constants.py
DEPLOYMENTS_FOLDER = Path("deployments")
TOOLS_FOLDER = Path("tools")


def get_root_path() -> Path:
    """Return the absolute path to the root folder (currently the folder containing constants.py)."""
    ROOT_PATH.mkdir(exist_ok=True, parents=True)
    return ROOT_PATH


def get_deployment_files_path(deployment_id: str) -> Path:
    """
    Return the absolute path to the folder holding the files used for a deployment
    (docker files, nginx config, etc.). Creates the folder if it does not exist.

    Currently: ROOT_PATH / 'deployments' / deployment_id

    Args:
        deployment_id (str): The ID of the deployment (e.g., deployment_586978988)

    Returns:
        Path: Path to the deployment folder.
    """
    folder = get_root_path() / DEPLOYMENTS_FOLDER / deployment_id
    folder.mkdir(exist_ok=True, parents=True)
    return folder


def get_tools_path() -> Path:
    """
    Return the absolute path to the folder holding tools such as openssl.
    Creates the folder if it does not exist.

    Currently: ROOT_PATH / 'tools'

    Returns:
        Path: Path to the tools folder.
    """
    folder = get_root_path() / TOOLS_FOLDER
    folder.mkdir(exist_ok=True, parents=True)
    return folder

def get_deployment_config_path() -> Path:
    """
    Return the absolute path to the folder holding the deployment config file.
    Currently: Path(__file__).resolve().parent / 'config'
    """
    return Path(__file__).resolve().parent / Path('config')

def get_dockerfiles_path() -> Path:
    """
    Return the absolute path to the folder holding the docker files.
    Currently: Path(__file__).resolve().parent / 'config'
    """
    return Path(__file__).resolve().parent / Path('config')  

def get_projects_path() -> Path:
    """
    Return the absolute path to the projects config folder.
    Currently: ROOT_PATH / 'config' / 'projects'
    """
    folder = get_root_path() / Path('config') / Path('projects')
    folder.mkdir(exist_ok=True, parents=True)
    return folder

def get_project_config_path(project_name: str) -> Path:
    """
    Return the absolute path to a specific project's config file.
    """
    return get_projects_path() / f"{project_name}.json"

def list_projects() -> List[str]:
    """
    List all available projects by scanning the projects folder.
    """
    projects_path = get_projects_path()
    if not projects_path.exists():
        return []
    
    project_files = projects_path.glob("*.json")
    return [f.stem for f in project_files]