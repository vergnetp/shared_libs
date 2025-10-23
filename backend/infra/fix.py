"""
Fix all imports in backend/infra files to work both locally and in containers.

Changes imports from:
    from module_name import Something
    
To:
    try:
        from .module_name import Something  # Container (relative)
    except ImportError:
        from module_name import Something  # Local (absolute)
"""

import os
import re
from pathlib import Path

# List of infra modules that need dual-import pattern
INFRA_MODULES = [
    'execute_cmd',
    'execute_docker', 
    'deployment_naming',
    'deployment_port_resolver',
    'deployment_state_manager',
    'deployment_config',
    'server_inventory',
    'do_manager',
    'logger',
    'env_loader',
    'path_resolver',
    'resource_resolver',
    'constants',
    'encryption',
    'backup_manager',
    'certificate_manager',
    'cron_manager',
    'git_manager',
    'health_agent',
    'health_agent_installer',
    'health_monitor',
    'health_monitor_installer',
    'metrics_collector',
    'nginx_config_generator',
    'nginx_config_parser',
    'live_deployment_query',
    'secrets_rotator',
    'rollback_manager',
    'scheduler_manager',
    'auto_scaler',
    'auto_scaling_coordinator',
    'agent_deployer',
    'deployment_syncer',
    'checks',
    'do_cost_tracker',
]

def fix_imports_in_file(filepath):
    """Fix imports in a single file"""
    print(f"Processing: {filepath}")
    
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    original_content = content
    changes_made = []
    
    # Pattern: from module_name import Something
    # But NOT: from .module_name (already relative)
    # But NOT: from typing import (standard library)
    # But NOT: from datetime import (standard library)
    
    for module in INFRA_MODULES:
        # Pattern 1: from module_name import ClassName
        pattern1 = rf'^(\s*)from {module} import (.+)$'
        
        def replace1(match):
            indent = match.group(1)
            imports = match.group(2)
            
            # Check if already has try/except
            if 'try:' in content[max(0, match.start()-100):match.start()]:
                return match.group(0)  # Already fixed
            
            changes_made.append(f"  - from {module} import {imports}")
            
            return f"""{indent}try:
{indent}    from .{module} import {imports}
{indent}except ImportError:
{indent}    from {module} import {imports}"""
        
        content = re.sub(pattern1, replace1, content, flags=re.MULTILINE)
        
        # Pattern 2: from module_name import (
        # Multi-line imports
        pattern2 = rf'^(\s*)from {module} import \($'
        
        def replace2(match):
            indent = match.group(1)
            
            # Find the closing parenthesis
            start_pos = match.end()
            paren_count = 1
            end_pos = start_pos
            
            while end_pos < len(content) and paren_count > 0:
                if content[end_pos] == '(':
                    paren_count += 1
                elif content[end_pos] == ')':
                    paren_count -= 1
                end_pos += 1
            
            # Extract the full import statement
            full_import = content[match.start():end_pos]
            
            # Check if already has try/except
            if 'try:' in content[max(0, match.start()-100):match.start()]:
                return full_import  # Already fixed
            
            # Get just the imported items part
            import_items = content[match.end():end_pos]
            
            changes_made.append(f"  - from {module} import (...)")
            
            return f"""{indent}try:
{indent}    from .{module} import ({import_items}
{indent}except ImportError:
{indent}    from {module} import ({import_items}"""
        
        content = re.sub(pattern2, replace2, content, flags=re.MULTILINE)
    
    # Only write if changes were made
    if content != original_content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        
        print(f"  ✓ Fixed {len(changes_made)} imports")
        for change in changes_made[:5]:  # Show first 5
            print(change)
        if len(changes_made) > 5:
            print(f"  ... and {len(changes_made) - 5} more")
        return True
    else:
        print(f"  - No changes needed")
        return False

def main():
    """Fix all Python files in backend/infra"""
    
    # Get the infra directory
    script_dir = Path(__file__).parent
    infra_dir = script_dir / "backend" / "infra"
    
    if not infra_dir.exists():
        infra_dir = Path.cwd() / "backend" / "infra"
    
    if not infra_dir.exists():
        print(f"Error: Could not find backend/infra directory")
        print(f"Tried: {infra_dir}")
        return
    
    print(f"Fixing imports in: {infra_dir}")
    print("=" * 60)
    
    # Get all Python files
    python_files = list(infra_dir.glob("*.py"))
    
    # Exclude some files
    exclude_files = ['__init__.py', 'test.py', 'conftest.py']
    python_files = [f for f in python_files if f.name not in exclude_files]
    
    print(f"Found {len(python_files)} Python files to process\n")
    
    fixed_count = 0
    for filepath in sorted(python_files):
        if fix_imports_in_file(filepath):
            fixed_count += 1
        print()
    
    print("=" * 60)
    print(f"Summary: Fixed imports in {fixed_count}/{len(python_files)} files")
    print("\nNext steps:")
    print("1. Review the changes: git diff backend/infra/")
    print("2. Test locally: python -m pytest")
    print("3. Commit: git add backend/infra/ && git commit -m 'Fix imports for container compatibility'")
    print("4. Push and redeploy")

if __name__ == "__main__":
    main()