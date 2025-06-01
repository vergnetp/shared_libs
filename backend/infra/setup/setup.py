#!/usr/bin/env python3
"""
Setup Script for Personal Cloud Orchestration System

Initializes all configuration files, templates, and validates the system setup.
Run this first before using the orchestrator.
"""

import os
import sys
import json
from pathlib import Path

# Add current directory to path so we can import our modules
sys.path.insert(0, str(Path(__file__).parent))

from config_loader import ConfigManager
from orchestrator import InfrastructureOrchestrator


def print_banner():
    """Print setup banner"""
    banner = """
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║   🚀 Personal Cloud Orchestration System Setup 🚀          ║
║                                                              ║
║   A comprehensive infrastructure management system for       ║
║   hosting multiple projects with automatic scaling,         ║
║   deployment, and recovery capabilities.                    ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
"""
    print(banner)


def check_prerequisites():
    """Check system prerequisites"""
    print("🔍 Checking prerequisites...")
    
    issues = []
    
    # Check Python version
    if sys.version_info < (3, 8):
        issues.append("Python 3.8+ required")
    
    # Check required environment variables
    required_env_vars = ['DO_TOKEN']
    for var in required_env_vars:
        if not os.getenv(var):
            issues.append(f"Environment variable {var} not set")
    
    # Check optional environment variables
    optional_env_vars = ['ADMIN_IP', 'OFFICE_IP']
    warnings = []
    for var in optional_env_vars:
        if not os.getenv(var):
            warnings.append(f"Optional environment variable {var} not set")
    
    # Check required packages
    required_packages = [
        'digitalocean', 'paramiko', 'aiohttp', 'jinja2'
    ]
    
    missing_packages = []
    for package in required_packages:
        try:
            __import__(package)
        except ImportError:
            missing_packages.append(package)
    
    if missing_packages:
        issues.append(f"Missing Python packages: {', '.join(missing_packages)}")
    
    # Print results
    if issues:
        print("❌ Prerequisites check failed:")
        for issue in issues:
            print(f"   - {issue}")
        return False
    else:
        print("✅ Prerequisites check passed")
        if warnings:
            print("⚠️  Warnings:")
            for warning in warnings:
                print(f"   - {warning}")
        return True


def setup_directories():
    """Create necessary directories"""
    print("📁 Setting up directories...")
    
    directories = [
        'config',
        'templates',
        'templates/email-templates',
        'logs',
        'backups'
    ]
    
    for directory in directories:
        Path(directory).mkdir(parents=True, exist_ok=True)
        print(f"   ✓ Created {directory}")
    
    return True


def setup_configurations():
    """Initialize all configuration files"""
    print("⚙️  Setting up configuration files...")
    
    config_manager = ConfigManager()
    results = config_manager.initialize_all_configs()
    
    for config_type, result in results.items():
        if isinstance(result, dict):
            if result.get('status') == 'created':
                print(f"   ✓ Created {config_type}")
            elif result.get('status') == 'exists':
                print(f"   ✓ {config_type} already exists")
        else:
            # Handle nested results (like templates)
            for sub_type, sub_result in result.items():
                if sub_result.get('status') == 'created':
                    print(f"   ✓ Created {config_type}/{sub_type}")
                elif sub_result.get('status') == 'exists':
                    print(f"   ✓ {config_type}/{sub_type} already exists")
    
    return True


def validate_setup():
    """Validate the complete setup"""
    print("🔍 Validating setup...")
    
    config_manager = ConfigManager()
    validation_result = config_manager.validate_all_configs()
    
    if validation_result['valid']:
        print("   ✅ Configuration validation passed")
    else:
        print("   ❌ Configuration validation failed:")
        for issue in validation_result['issues']:
            print(f"      - {issue}")
        return False
    
    if validation_result['warnings']:
        print("   ⚠️  Configuration warnings:")
        for warning in validation_result['warnings']:
            print(f"      - {warning}")
    
    # Test orchestrator initialization
    try:
        orchestrator = InfrastructureOrchestrator()
        init_result = orchestrator.initialize_system()
        
        if init_result['system_ready']:
            print("   ✅ Orchestrator initialization successful")
        else:
            print("   ❌ Orchestrator initialization failed:")
            for component, result in init_result.items():
                if isinstance(result, dict) and not result.get('success', True):
                    print(f"      - {component}: {result.get('error', 'unknown error')}")
            return False
            
    except Exception as e:
        print(f"   ❌ Orchestrator initialization error: {str(e)}")
        return False
    
    return True


def print_next_steps():
    """Print next steps for the user"""
    next_steps = """
🎉 Setup completed successfully!

📋 Next Steps:

1. Configure your projects:
   ├─ Edit config/projects.csv with your project details
   └─ Update config/deployment_config.json with Git repositories

2. Set up secrets (choose one):
   ├─ OS Environment Variables:
   │  export HOSTOMATIC_PROD_DB_PASSWORD="your_password"
   │  export HOSTOMATIC_PROD_STRIPE_KEY="sk_live_..."
   └─ Or use Vault (deployed automatically)

3. Configure notifications:
   ├─ Edit config/email_config.json for email alerts
   └─ Edit config/sms_config.json for SMS notifications

4. Create your infrastructure:
   └─ python orchestrator.py --orchestrate

5. Deploy your first project:
   ├─ python orchestrator.py --deploy-uat hostomatic
   └─ python orchestrator.py --deploy-prod hostomatic

6. Start health monitoring:
   ├─ python orchestrator.py --monitor master &
   └─ python orchestrator.py --monitor web1 &

📖 Documentation:
   └─ See plan.md for complete system documentation

🆘 Get Help:
   └─ python orchestrator.py --help

🎯 Example Commands:
   ├─ python orchestrator.py --status
   ├─ python orchestrator.py --scale hostomatic 4
   └─ python orchestrator.py --cleanup

Happy orchestrating! 🚀
"""
    print(next_steps)


def print_configuration_summary():
    """Print configuration summary"""
    print("\n📊 Configuration Summary:")
    
    config_manager = ConfigManager()
    summary = config_manager.get_config_summary()
    
    print("\n   Configuration Files:")
    for name, info in summary['config_files'].items():
        status = "✅" if info['exists'] else "❌"
        print(f"   {status} {name}: {info['path']}")
    
    print("\n   Template Files:")
    for name, info in summary['template_files'].items():
        status = "✅" if info['exists'] else "❌"
        print(f"   {status} {name}: {info['path']}")


def create_env_file_example():
    """Create example .env file"""
    env_file = Path('.env.example')
    
    if env_file.exists():
        return
    
    env_content = """# Personal Cloud Orchestration System Environment Variables
# Copy this file to .env and fill in your values

# Required: DigitalOcean API Token
DO_TOKEN=your_digitalocean_api_token_here

# Required: Administrator IP (for SSH access)
ADMIN_IP=203.0.113.100/32

# Optional: Office IP (additional SSH access)
# OFFICE_IP=203.0.113.200/32

# Example Project Secrets (Hostomatic Production)
HOSTOMATIC_PROD_DB_PASSWORD=secure_database_password
HOSTOMATIC_PROD_REDIS_PASSWORD=secure_redis_password
HOSTOMATIC_PROD_STRIPE_KEY=sk_live_your_stripe_secret_key
HOSTOMATIC_PROD_STRIPE_WEBHOOK_SECRET=whsec_your_webhook_secret
HOSTOMATIC_PROD_OPENAI_API_KEY=sk-your_openai_api_key
HOSTOMATIC_PROD_JWT_SECRET=your_jwt_secret_key

# Example Project Secrets (Hostomatic UAT)
HOSTOMATIC_UAT_DB_PASSWORD=uat_database_password
HOSTOMATIC_UAT_STRIPE_KEY=sk_test_your_test_stripe_key
HOSTOMATIC_UAT_OPENAI_API_KEY=sk-your_test_openai_api_key

# Example Project Secrets (DigitalPixo Production)
DIGITALPIXO_PROD_DB_PASSWORD=digitalpixo_db_password
DIGITALPIXO_PROD_OPENAI_API_KEY=sk-digitalpixo_openai_key
DIGITALPIXO_PROD_SENDGRID_API_KEY=SG.your_sendgrid_api_key

# Global Secrets (used by all projects if project-specific not found)
STRIPE_PUBLISHABLE_KEY=pk_live_your_publishable_key
GOOGLE_OAUTH_CLIENT_ID=your_google_oauth_client_id
SENDGRID_API_KEY=SG.your_global_sendgrid_key

# Infrastructure Secrets
OPENSEARCH_ADMIN_PASSWORD=opensearch_admin_password
VAULT_ROOT_TOKEN=vault_root_token_here

# Email Configuration (for notifications)
GMAIL_APP_PASSWORD=your_gmail_app_password

# SMS Configuration (for critical alerts)
TWILIO_ACCOUNT_SID=your_twilio_account_sid
TWILIO_AUTH_TOKEN=your_twilio_auth_token
"""
    
    with open(env_file, 'w') as f:
        f.write(env_content)
    
    print(f"   ✓ Created {env_file} (example environment variables)")


def main():
    """Main setup function"""
    print_banner()
    
    # Step 1: Check prerequisites
    if not check_prerequisites():
        print("\n❌ Setup failed due to missing prerequisites.")
        print("Please install required packages and set environment variables.")
        sys.exit(1)
    
    # Step 2: Setup directories
    if not setup_directories():
        print("\n❌ Failed to setup directories.")
        sys.exit(1)
    
    # Step 3: Create configurations
    if not setup_configurations():
        print("\n❌ Failed to setup configurations.")
        sys.exit(1)
    
    # Step 4: Create example environment file
    create_env_file_example()
    
    # Step 5: Validate setup
    if not validate_setup():
        print("\n❌ Setup validation failed.")
        print("Please check the errors above and run setup again.")
        sys.exit(1)
    
    # Step 6: Print summary
    print_configuration_summary()
    
    # Step 7: Print next steps
    print_next_steps()


if __name__ == '__main__':
    main()