# Run this to see exactly what's being generated
from container_generator import ContainerGenerator
from services_config import CommonServiceConfigs
from enums import ServiceTypes, Envs
import secrets_manager as sm

project_name = "testlocal"
env = Envs.DEV

# Create the backup config
backup_config = CommonServiceConfigs.backup_worker(project_name, env.value)

print("=== DEBUG INFO ===")
print(f"backup_config.user: '{backup_config.user}'")
print(f"backup_config.get_user_command(): '{backup_config.get_user_command()}'")
print()

# Generate the Dockerfile
cg = ContainerGenerator(sm.SecretsManager())
dockerfile = cg.generate_container_file_content(
    ServiceTypes.WORKER, project_name, env, "backup_worker", 
    service_config=backup_config
)

print("=== GENERATED DOCKERFILE ===")
print(dockerfile)
print()

# Look for specific patterns
lines = dockerfile.split('\n')
user_lines = [line for line in lines if 'USER' in line]
print("=== USER COMMANDS IN DOCKERFILE ===")
for line in user_lines:
    print(f"'{line}'")