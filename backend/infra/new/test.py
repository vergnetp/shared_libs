import subprocess
from container_generator import ContainerGenerator
from services_config import ServiceConfig, CommonServiceConfigs
from container_manager import ContainerManager
from secrets_manager import SecretsManager
from enums import Envs, ServiceTypes
from datetime import datetime
import time
import os


def test_backup_demo():
    """Focused test for backup functionality with quick demonstration"""
    
    project_name = "testlocal"
    env = Envs.DEV 
    tag = datetime.now().strftime("%Y%m%d-%H%M%S")    
   
    secrets_json = SecretsManager.create_secrets_json(project_name, env)
    
    db_service_name = "maindb"
    services = [
        (db_service_name, ServiceTypes.POSTGRES, ServiceConfig()),  # Database to backup
        ("backup_worker", ServiceTypes.WORKER, CommonServiceConfigs.backup_worker(project_name, env.value, db_service_name)),  
    ]
    
    for service_name, service_type, service_config in services:       
       
        dockerfile = ContainerGenerator.generate_container_file_content(
            service_type, project_name, env, service_name, service_config=service_config
        )        
        print(f"✓ Dockerfile content generated: {service_name}")       
        
        image_name = ContainerGenerator.generate_image_name(project_name, env, service_name) 
        build_success = ContainerManager.build_image(dockerfile, image_name, tag=tag)
        
        if build_success:
            print(f"✅ Build successful: {image_name}:{tag}")
        else:
            print(f"❌ Build failed: {image_name}:{tag}")
            return       
  
    network_cmd = ContainerGenerator.generate_container_network_command(project_name, env)
    network_result = subprocess.run(network_cmd, shell=True, capture_output=True, text=True)

    if network_result.returncode == 0:
        print("✅ Docker network created successfully")
    else:        
        if "already exists" in network_result.stderr:
            print("✅ Docker network already exists")
        else:
            print(f"❌ Network creation failed: {network_result.stderr}")
            return    

    postgres_run_cmd = ContainerGenerator.generate_container_run_command(ServiceTypes.POSTGRES, project_name, env, db_service_name, tag)   
    postgres_result = subprocess.run(postgres_run_cmd, shell=True, capture_output=True, text=True)

    if postgres_result.returncode == 0:
        print("✅ PostgreSQL container started successfully")
    else:
        print(f"❌ PostgreSQL failed to start: {postgres_result.stderr}")
        return    
    
    print("⏳ Waiting for PostgreSQL to be ready...")
    time.sleep(10)
    create_sample_data_in_db(project_name, env, db_service_name)
    
    
    backup_run_cmd = ContainerGenerator.generate_container_run_command(ServiceTypes.WORKER, project_name, env, "backup_worker", tag)    
    # Modify the generated command to add backups volume mount (Windows-compatible)
    current_dir = os.getcwd().replace("\\", "/")  # Windows path fix
    backup_run_cmd = backup_run_cmd.replace(
        f"testlocal-dev-backup_worker:{tag}",
        f'-v "{current_dir}/backups:/app/backups" testlocal-dev-backup_worker:{tag}'
    ) 
    backup_result = subprocess.run(backup_run_cmd, shell=True, capture_output=True, text=True)

    if backup_result.returncode == 0:
        print("✅ Backup Worker container started successfully")
    else:
        print(f"❌ Backup Worker failed to start: {backup_result.stderr}")
        return


def test_centralized_scheduler():
    """Test the centralized scheduler system"""
    
    project_name = "testlocal"
    env = Envs.DEV 
    tag = datetime.now().strftime("%Y%m%d-%H%M%S")    
   
    # Create secrets
    secrets_json = SecretsManager.create_secrets_json(project_name, env)
    
    # Services to create
    db_service_name = "maindb"
    services = [
        (db_service_name, ServiceTypes.POSTGRES, ServiceConfig()),  # Database
        ("cache", ServiceTypes.REDIS, ServiceConfig()),  # Redis cache
        ("scheduler", ServiceTypes.WORKER, CommonServiceConfigs.centralized_scheduler()),  # Scheduler
    ]
    
    print("🏗️ Building services for centralized scheduler demo...")
    
    # Build all services
    for service_name, service_type, service_config in services:       
        dockerfile = ContainerGenerator.generate_container_file_content(
            service_type, project_name, env, service_name, service_config=service_config
        )        
        print(f"✓ Dockerfile content generated: {service_name}")       
        
        image_name = ContainerGenerator.generate_image_name(project_name, env, service_name) 
        build_success = ContainerManager.build_image(dockerfile, image_name, tag=tag)
        
        if build_success:
            print(f"✅ Build successful: {image_name}:{tag}")
        else:
            print(f"❌ Build failed: {image_name}:{tag}")
            return       
  
    # Create network
    network_cmd = ContainerGenerator.generate_container_network_command(project_name, env)
    network_result = subprocess.run(network_cmd, shell=True, capture_output=True, text=True)

    if network_result.returncode == 0:
        print("✅ Docker network created successfully")
    else:        
        if "already exists" in network_result.stderr:
            print("✅ Docker network already exists")
        else:
            print(f"❌ Network creation failed: {network_result.stderr}")
            return    

    print("\n🚀 Starting services...")
    
    # Start PostgreSQL
    postgres_run_cmd = ContainerGenerator.generate_container_run_command(ServiceTypes.POSTGRES, project_name, env, db_service_name, tag)   
    postgres_result = subprocess.run(postgres_run_cmd, shell=True, capture_output=True, text=True)

    if postgres_result.returncode == 0:
        print("✅ PostgreSQL container started successfully")
    else:
        print(f"❌ PostgreSQL failed to start: {postgres_result.stderr}")
        return    
    
    # Start Redis
    redis_run_cmd = ContainerGenerator.generate_container_run_command(ServiceTypes.REDIS, project_name, env, "cache", tag)   
    redis_result = subprocess.run(redis_run_cmd, shell=True, capture_output=True, text=True)

    if redis_result.returncode == 0:
        print("✅ Redis container started successfully")
    else:
        print(f"❌ Redis failed to start: {redis_result.stderr}")
        return
    
    print("⏳ Waiting for services to be ready...")
    time.sleep(15)
    
    # Create sample data
    create_sample_data_in_db(project_name, env, db_service_name)
    
    # Start scheduler with proper volume mounts
    scheduler_run_cmd = ContainerGenerator.generate_container_run_command(ServiceTypes.WORKER, project_name, env, "scheduler", tag)    
    
    # Add volume mounts for scheduler
    current_dir = os.getcwd().replace("\\", "/")  # Windows path fix
    
    # Add mounts for job scripts, configs, and logs
    volume_mounts = [
        f'-v "{current_dir}/jobs:/app/jobs"',
        f'-v "{current_dir}/config:/app/config"', 
        f'-v "{current_dir}/backups:/app/backups"',
        f'-v "{current_dir}/logs:/var/log/jobs"'
    ]
    
    # Insert volume mounts before the image name
    scheduler_run_cmd = scheduler_run_cmd.replace(
        f"testlocal-dev-scheduler:{tag}",
        f'{" ".join(volume_mounts)} testlocal-dev-scheduler:{tag}'
    )
    
    print("🕐 Starting centralized scheduler...")
    print(f"📋 Run command: {scheduler_run_cmd}")
    
    scheduler_result = subprocess.run(scheduler_run_cmd, shell=True, capture_output=True, text=True)

    if scheduler_result.returncode == 0:
        print("✅ Scheduler container started successfully")
        print("\n📋 Scheduler Management Commands:")
        print("   # List all jobs")
        print("   docker exec testlocal_dev_scheduler python scheduler.py list")
        print()
        print("   # Add a demo backup job (runs every minute)")
        print('   docker exec testlocal_dev_scheduler python scheduler.py add demo_backup "*/1 * * * *" jobs/backup_job.py testlocal dev maindb demo')
        print()
        print("   # Run a job manually")
        print("   docker exec testlocal_dev_scheduler python scheduler.py run demo_backup")
        print()
        print("   # Check scheduler status")
        print("   docker exec testlocal_dev_scheduler python scheduler.py status")
        print()
        print("   # View logs")
        print("   docker exec testlocal_dev_scheduler tail -f /var/log/cron.log")
        print("   docker exec testlocal_dev_scheduler tail -f /var/log/jobs/demo_backup.log")
        
    else:
        print(f"❌ Scheduler failed to start: {scheduler_result.stderr}")
        return


def create_scheduler_demo_jobs():
    """Create example job configuration files"""
    
    # Create directories
    os.makedirs("jobs", exist_ok=True)
    os.makedirs("config", exist_ok=True)
    os.makedirs("backups", exist_ok=True)
    os.makedirs("logs", exist_ok=True)
    
    # Create job scripts directory with __init__.py
    with open("jobs/__init__.py", "w") as f:
        f.write("# Scheduler job scripts\n")
    
    # Create sample job configuration
    jobs_config = {
        "jobs": [
            {
                "name": "demo_backup",
                "schedule": "*/2 * * * *",  # Every 2 minutes for demo
                "script_path": "jobs/backup_job.py",
                "args": ["testlocal", "dev", "maindb", "demo"],
                "description": "Demo backup job",
                "enabled": False  # Disabled by default
            },
            {
                "name": "health_check",
                "schedule": "*/5 * * * *",  # Every 5 minutes
                "script_path": "jobs/health_check_job.py", 
                "args": ["testlocal", "dev", "maindb", "cache"],
                "description": "Health check for core services",
                "enabled": False
            },
            {
                "name": "secrets_check",
                "schedule": "0 */6 * * *",  # Every 6 hours
                "script_path": "jobs/secrets_check_job.py",
                "args": ["testlocal", "dev"],
                "description": "Verify secrets are accessible",
                "enabled": False
            }
        ]
    }
    
    import json
    with open("config/demo_jobs.json", "w") as f:
        json.dump(jobs_config, f, indent=2)
    
    print("📁 Created job configuration files:")
    print("   jobs/ - Job scripts directory")
    print("   config/demo_jobs.json - Example job configuration") 
    print("   backups/ - Backup storage directory")
    print("   logs/ - Job logs directory")


def test_scheduler_management():
    """Test scheduler management commands"""
    
    print("🕐 Testing Scheduler Management Commands")
    print("="*60)
    
    container_name = "testlocal_dev_scheduler"
    
    # Test basic commands
    commands = [
        ("List jobs", f"docker exec {container_name} python scheduler.py list"),
        ("Check status", f"docker exec {container_name} python scheduler.py status"),
        ("Add demo job", f'docker exec {container_name} python scheduler.py add test_job "*/3 * * * *" jobs/backup_job.py testlocal dev maindb test'),
        ("List jobs again", f"docker exec {container_name} python scheduler.py list"),
        ("Run job manually", f"docker exec {container_name} python scheduler.py run test_job"),
        ("Disable job", f"docker exec {container_name} python scheduler.py disable test_job"),
        ("Remove job", f"docker exec {container_name} python scheduler.py remove test_job"),
    ]
    
    for description, command in commands:
        print(f"\n📋 {description}:")
        print(f"   Command: {command}")
        
        try:
            result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                print(f"   ✅ Success")
                if result.stdout.strip():
                    print("   Output:")
                    for line in result.stdout.strip().split('\n')[:5]:  # Show first 5 lines
                        print(f"     {line}")
            else:
                print(f"   ❌ Failed: {result.stderr}")
        except subprocess.TimeoutExpired:
            print(f"   ⏰ Command timed out")
        except Exception as e:
            print(f"   ❌ Error: {e}")


def test_scheduler_demo():
    """Main scheduler demo test"""
    print("🕐 CENTRALIZED SCHEDULER DEMO")
    print("="*60)
    
    # Create demo files and directories
    create_scheduler_demo_jobs()
    
    # Build and start services
    test_centralized_scheduler()
    
    print("\n" + "="*60)
    print("✅ Scheduler demo setup complete!")
    print()
    print("🎯 Next steps:")
    print("1. Wait for scheduler to start (check logs)")
    print("2. Add jobs using the management commands shown above")
    print("3. Monitor job execution in the logs")
    print()
    print("📊 Monitoring commands:")
    print("   docker logs testlocal_dev_scheduler")
    print("   docker exec testlocal_dev_scheduler tail -f /var/log/cron.log")
    print("   ls -la backups/  # Check if backups are created")


def create_sample_data_in_db(project_name: str, env: Envs, service_name: str):
    """Create sample data directly in the running database"""
    try:       
        
        # SQL commands to create sample data
        sql_commands = [
            "CREATE TABLE IF NOT EXISTS users (id SERIAL PRIMARY KEY, username VARCHAR(50) UNIQUE NOT NULL, email VARCHAR(100) UNIQUE NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);",
            "CREATE TABLE IF NOT EXISTS posts (id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id), title VARCHAR(200) NOT NULL, content TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);",
            "INSERT INTO users (username, email) VALUES ('alice', 'alice@example.com'), ('bob', 'bob@example.com'), ('charlie', 'charlie@example.com') ON CONFLICT (username) DO NOTHING;",
            "INSERT INTO posts (user_id, title, content) VALUES (1, 'First Post', 'This is Alice first post for backup testing.'), (1, 'Second Post', 'Another post from Alice.'), (2, 'Bob Introduction', 'Hello from Bob.'), (3, 'Charlie Thoughts', 'Random thoughts from Charlie.') ON CONFLICT DO NOTHING;"
        ]
        
        container_name = ContainerGenerator.generate_container_name(project_name, env, service_name)
        
        # Generate database identifiers using static method
        db_name = ContainerGenerator.generate_identifier(project_name, env, "database")
        db_user = ContainerGenerator.generate_identifier(project_name, env, "user")
        
        for sql in sql_commands:
            cmd = f'docker exec {container_name} psql -U {db_user} -d {db_name} -c "{sql}"'
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"⚠️ SQL command failed: {result.stderr}")
        
        # Verify data was created
        verify_cmd = f'docker exec {container_name} psql -U {db_user} -d {db_name} -c "SELECT COUNT(*) as user_count FROM users; SELECT COUNT(*) as post_count FROM posts;"'
        result = subprocess.run(verify_cmd, shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            print("✅ Sample data created successfully")
            print(f"📊 Data summary:\n{result.stdout}")
        else:
            print(f"⚠️ Failed to verify data: {result.stderr}")
            
    except Exception as e:
        print(f"⚠️ Error creating sample data: {e}")


def test_database_with_sample_data():
    """Create sample data file (not needed anymore - data created directly)"""
    print("\n📊 Sample data is now created automatically in the running database!")
    print("🔍 To manually verify data:")
    
    # Generate database identifiers using static method
    db_name = ContainerGenerator.generate_identifier("testlocal", Envs.DEV, "database")
    db_user = ContainerGenerator.generate_identifier("testlocal", Envs.DEV, "user")
    
    print(f"   docker exec testlocal_dev_maindb psql -U {db_user} -d {db_name} -c 'SELECT * FROM users;'")
    print(f"   docker exec testlocal_dev_maindb psql -U {db_user} -d {db_name} -c 'SELECT * FROM posts;'")


def test_all_services():
    """Original comprehensive test (renamed for clarity)"""
    project_name = "testlocal"
    env = Envs.DEV
    
    # Use timestamp tag for this test
    tag = datetime.now().strftime("%Y%m%d-%H%M%S")
    
    # Create secrets JSON file using static method
    secrets_json = SecretsManager.create_secrets_json(project_name, env)

    # Define services with custom configurations
    services = [
        ("api", ServiceTypes.WEB, ServiceConfig()),  # Standard API
        ("image_api", ServiceTypes.WEB, CommonServiceConfigs.image_processing()),  # Image processing API
        ("email_worker", ServiceTypes.WORKER, CommonServiceConfigs.email_worker()),  # Email worker
        ("backup_worker", ServiceTypes.WORKER, CommonServiceConfigs.backup_worker(project_name, env.value)),  # Standard backup worker
        ("maindb", ServiceTypes.POSTGRES, ServiceConfig()),  # Standard PostgreSQL
        ("cache", ServiceTypes.REDIS, ServiceConfig()),  # Standard Redis
        ("search", ServiceTypes.OPENSEARCH, ServiceConfig()),  # Standard OpenSearch
        ("proxy", ServiceTypes.NGINX, ServiceConfig()),  # Standard Nginx
    ]
    
    print("🔨 Building all service containers with ServiceConfig...")
    print("="*80)
    print(f"📋 Using tag: {tag}")
    print(f"🏷️  Naming convention:")
    print(f"   Image: {project_name}-{env.value}-<service>:{tag}")
    print(f"   Container: {project_name}_{env.value}_<service>")
    print(f"   Volume: {project_name}_{env.value}_<service>_data")
    print("="*80)
    
    for service_name, service_type, service_config in services:
        print(f"\n📦 Building {service_type.value}: {service_name}")
        
        # Show configuration details
        if service_config.has_customizations():
            print(f"🔧 Custom configuration:")
            if service_config.packages:
                print(f"   📦 Packages: {', '.join(service_config.packages)}")
            if service_config.setup_commands:
                print(f"   ⚙️  Setup commands: {len(service_config.setup_commands)} command(s)")
            if service_config.environment_vars:
                print(f"   🌍 Environment vars: {', '.join(service_config.environment_vars.keys())}")
            if service_config.start_command:
                print(f"   🚀 Custom start: {service_config.start_command[:50]}...")
        else:
            print(f"🔧 Standard configuration")
        
        # Generate Dockerfile using static method
        dockerfile = ContainerGenerator.generate_container_file_content(
            service_type, project_name, env, service_name, service_config=service_config
        ) 
        
        print(f"✓ Dockerfile content generated: {service_name}")
       
        # Use static naming methods
        image_name = ContainerGenerator.generate_image_name(project_name, env, service_name)
        container_name = ContainerGenerator.generate_container_name(project_name, env, service_name)
        
        print(f"🏷️  Image name: {image_name}:{tag}")
        print(f"📦 Container name: {container_name}")
        
        # Build image using static method
        build_success = ContainerManager.build_image(dockerfile, image_name, tag=tag)
        
        if build_success:
            print(f"✅ Build successful: {image_name}:{tag}")
            
            # Generate run command using static methods
            run_cmd = ContainerGenerator.generate_container_run_command(service_type, project_name, env, service_name, tag)        
            print(f"📋 Run command:")
            print(f"{run_cmd}")
            print()
        else:
            print(f"❌ Build failed: {image_name}:{tag}")

    print("\n" + "="*80)
    print("✅ All services processed!")
    print(f"💡 To run all containers, execute the generated run commands above")
    print(f"🐳 Images built with tag: {tag}")


def test_registry_push_demo():
    """Demo showing hybrid workflow: static build + instance push"""
    project_name = "testlocal"
    env = Envs.DEV
    tag = datetime.now().strftime("%Y%m%d-%H%M%S")
    
    print("🚀 HYBRID WORKFLOW DEMO - Static Build + Instance Push")
    print("="*80)
    
    # Create secrets
    SecretsManager.create_secrets_json(project_name, env)
    
    # Generate a simple web service
    dockerfile = ContainerGenerator.generate_container_file_content(
        ServiceTypes.WEB, project_name, env, "demo_api"
    )
    
    image_name = ContainerGenerator.generate_image_name(project_name, env, "demo_api")
    
    print(f"📦 Building image: {image_name}:{tag}")
    
    # Step 1: Static build (no authentication needed)
    if ContainerManager.build_image(dockerfile, image_name, tag=tag):
        print(f"✅ Static build successful: {image_name}:{tag}")
        
        # Step 2: Instance registry operations (authentication required)
        print(f"\n🔐 Setting up registry operations...")
        cm = ContainerManager()
        
        # Uncomment and modify for actual registry testing:
        print("💡 To test registry push:")
        print("   1. cm.authenticate('localhost:5000')  # or your registry")
        print("   2. cm.push_image('testlocal-dev-demo_api', 'your_tag')")
        print("   3. Image will be auto-tagged for registry and pushed")
        
        # Example (commented out):
        # if cm.authenticate("localhost:5000"):
        #     print("✓ Registry authentication successful")
        #     if cm.push_image(image_name, tag):
        #         print("✓ Push successful - hybrid workflow complete!")
    else:
        print(f"❌ Static build failed")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "demo":
            print("🚀 Running BACKUP DEMO (original)...")
            test_backup_demo()
            test_database_with_sample_data()
        elif sys.argv[1] == "scheduler":
            print("🚀 Running CENTRALIZED SCHEDULER DEMO...")
            test_scheduler_demo()
        elif sys.argv[1] == "scheduler-test":
            print("🚀 Testing SCHEDULER MANAGEMENT...")
            test_scheduler_management()
        elif sys.argv[1] == "registry":
            print("🚀 Running REGISTRY DEMO...")
            test_registry_push_demo()
        else:
            print("Usage:")
            print("  python test.py                 # Full build test")
            print("  python test.py demo            # Original backup demo")
            print("  python test.py scheduler       # Centralized scheduler demo")
            print("  python test.py scheduler-test  # Test scheduler management")
            print("  python test.py registry        # Registry push demo")
    else:
        print("🏗️ Running FULL BUILD...")
        print("💡 For backup demo, run: python test.py demo")
        print("💡 For scheduler demo, run: python test.py scheduler")
        print("💡 For registry demo, run: python test.py registry")
        test_all_services()