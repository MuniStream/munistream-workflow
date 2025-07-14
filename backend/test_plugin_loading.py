#!/usr/bin/env python3
"""
Test script to verify plugin loading functionality.
"""

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))

from app.workflows.plugin_loader import WorkflowPluginManager
from app.workflows.registry import step_registry

def test_plugin_loading():
    """Test loading Aquabilidad workflows via plugin system"""
    
    print("🔌 Testing Aquabilidad Plugin Loading System")
    print("=" * 50)
    
    # Initialize plugin manager
    plugin_manager = WorkflowPluginManager(config_file="plugins.yaml")
    
    # Load plugin configuration
    print("📋 Loading plugin configuration...")
    plugin_manager.load_config()
    
    print(f"📦 Found {len(plugin_manager.plugins)} plugins:")
    for plugin in plugin_manager.plugins:
        print(f"   - {plugin.name} ({plugin.version})")
    
    # Clear existing workflows from registry
    print("\n🧹 Clearing existing workflow registry...")
    step_registry.workflows.clear()
    
    # Discover and load workflows
    print("\n🔍 Discovering and loading workflows from plugins...")
    try:
        workflows_loaded = plugin_manager.discover_and_load_workflows()
        print(f"✅ Successfully loaded {workflows_loaded} workflows")
    except Exception as e:
        print(f"❌ Error loading workflows: {e}")
        return False
    
    # List loaded workflows
    print("\n📝 Loaded workflows:")
    for workflow_id, workflow_info in step_registry.workflows.items():
        workflow = step_registry.get_workflow(workflow_id)
        if workflow:
            print(f"   - {workflow_id}: {workflow.name}")
            print(f"     Description: {workflow.description}")
            print(f"     Steps: {len(workflow.steps)}")
    
    # Test workflow creation
    print("\n🧪 Testing workflow instantiation...")
    for workflow_id in step_registry.workflows.keys():
        try:
            workflow = step_registry.get_workflow(workflow_id)
            if workflow:
                workflow.validate()
                print(f"   ✅ {workflow_id}: Valid")
            else:
                print(f"   ❌ {workflow_id}: Could not instantiate")
        except Exception as e:
            print(f"   ❌ {workflow_id}: Validation failed - {e}")
    
    print(f"\n🎉 Plugin loading test completed!")
    print(f"   Plugins loaded: {len(plugin_manager.plugins)}")
    print(f"   Workflows loaded: {len(step_registry.workflows)}")
    
    return True

if __name__ == "__main__":
    test_plugin_loading()