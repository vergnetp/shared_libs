import os
import hashlib
from pathlib import Path
from deployment_port_resolver import DeploymentPortResolver
from path_resolver import PathResolver
from deployment_syncer import DeploymentSyncer

class ResourceResolver:

    @staticmethod
    def get_db_password() -> str:
        '''
        Get the password from within the container/host/bastion/env
        '''
        PathResolver.get_volume_container_path('postgres', 'secrets')  #/run/secrets
        PathResolver.get_volume_host_path(project, env, 'postgres', 'secrets', host_ip) #/local/{project}/{env}/secrets/postgres
        PathResolver.get_volume_host_path(project, env, 'postgres', 'secrets', bastion_ip) #C:\local\{project}\{env}\secrets\postgres
        
        password_file = os.getenv("DB_PASSWORD_FILE", "/app/secrets/db_password")
        if os.path.exists(password_file):
            return Path(password_file).read_text().strip()
        return os.getenv("DB_PASSWORD", "")

    @staticmethod
    def get_db_name(project: str, env: str) -> str:
        hash_input = f"{project}_{env}_postgres"
        db_suffix = hashlib.md5(hash_input.encode()).hexdigest()[:8]
        return f"{project}_{db_suffix}"

    @staticmethod
    def get_db_user(project: str) -> str:
        return f"{project}_user"

    @staticmethod
    def get_db_port(project: str, env: str) -> str:
        return DeploymentPortResolver.get_internal_port(project, env, "postgres")


