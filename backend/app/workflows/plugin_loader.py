"""
Plugin system for loading external workflow definitions from user repositories.

This allows organizations to maintain their own workflow repositories and have
CivicStream dynamically discover and load them.
"""

import os
import sys
import importlib
import importlib.util
from pathlib import Path
from typing import List, Dict, Any, Optional
from git import Repo
import yaml
import json

from .workflow import Workflow
from .registry import step_registry


class WorkflowPlugin:
    """Represents an external workflow plugin"""
    
    def __init__(self, repo_url: str, config: Dict[str, Any]):
        self.repo_url = repo_url
        self.config = config
        self.name = config.get("name", "unnamed_plugin")
        self.version = config.get("version", "1.0.0")
        self.workflows = config.get("workflows", [])
        self.local_path = None
        
    def clone_or_update(self, base_path: str = "/tmp/civicstream_plugins") -> str:
        """Clone or update the plugin repository"""
        # Check if repo_url is a local path
        if os.path.isabs(self.repo_url) and os.path.exists(self.repo_url):
            # It's a local path, use it directly
            self.local_path = self.repo_url
            print(f"Using local plugin path: {self.local_path}")
            return self.local_path
        
        # Create plugin directory for remote repos
        plugin_dir = Path(base_path) / self.name
        plugin_dir.mkdir(parents=True, exist_ok=True)
        
        repo_path = plugin_dir / "repo"
        
        if repo_path.exists():
            # Update existing repo
            repo = Repo(repo_path)
            origin = repo.remotes.origin
            origin.pull()
        else:
            # Clone new repo
            repo = Repo.clone_from(self.repo_url, repo_path)
        
        self.local_path = str(repo_path)
        return self.local_path
    
    def load_workflows(self) -> List[Workflow]:
        """Load all workflows from the plugin"""
        if not self.local_path:
            raise ValueError("Plugin not cloned yet. Call clone_or_update first.")
        
        loaded_workflows = []
        
        # Add plugin path to Python path
        if self.local_path not in sys.path:
            sys.path.insert(0, self.local_path)
        
        try:
            for workflow_info in self.workflows:
                module_path = workflow_info.get("module")
                function_name = workflow_info.get("function")
                
                if not module_path or not function_name:
                    continue
                
                # Import the module
                module = importlib.import_module(module_path)
                
                # Get the workflow creation function
                if hasattr(module, function_name):
                    workflow_func = getattr(module, function_name)
                    workflow = workflow_func()
                    
                    if isinstance(workflow, Workflow):
                        loaded_workflows.append(workflow)
                        print(f"Loaded workflow: {workflow.name} from {self.name}")
                    
        finally:
            # Clean up sys.path
            if self.local_path in sys.path:
                sys.path.remove(self.local_path)
        
        return loaded_workflows


class WorkflowPluginManager:
    """Manages external workflow plugins"""
    
    def __init__(self, config_file: Optional[str] = None):
        self.plugins: List[WorkflowPlugin] = []
        self.config_file = config_file or os.getenv("CIVICSTREAM_PLUGINS_CONFIG", "plugins.yaml")
        self.base_path = os.getenv("CIVICSTREAM_PLUGINS_PATH", "/tmp/civicstream_plugins")
        
    def load_config(self):
        """Load plugin configuration from file"""
        if not os.path.exists(self.config_file):
            return
        
        with open(self.config_file, 'r') as f:
            if self.config_file.endswith('.yaml') or self.config_file.endswith('.yml'):
                config = yaml.safe_load(f)
            else:
                config = json.load(f)
        
        for plugin_config in config.get("plugins", []):
            if plugin_config.get("enabled", True):
                plugin = WorkflowPlugin(
                    repo_url=plugin_config.get("repo_url"),
                    config=plugin_config
                )
                self.plugins.append(plugin)
    
    def discover_and_load_workflows(self) -> int:
        """Discover and load all workflows from configured plugins"""
        total_loaded = 0
        
        for plugin in self.plugins:
            try:
                # Clone or update the plugin repository
                plugin.clone_or_update(self.base_path)
                
                # Load workflows from the plugin
                workflows = plugin.load_workflows()
                
                # Register workflows with the system
                for workflow in workflows:
                    step_registry.register_workflow(workflow)
                    total_loaded += 1
                    
            except Exception as e:
                print(f"Error loading plugin {plugin.name}: {e}")
                continue
        
        return total_loaded
    
    def add_plugin_from_url(self, repo_url: str, name: str) -> bool:
        """Add a new plugin dynamically"""
        plugin = WorkflowPlugin(
            repo_url=repo_url,
            config={
                "name": name,
                "repo_url": repo_url,
                "enabled": True,
                "workflows": []  # Will be auto-discovered
            }
        )
        
        try:
            # Clone the repository
            plugin.clone_or_update(self.base_path)
            
            # Auto-discover workflows
            plugin_config_file = Path(plugin.local_path) / "civicstream.yaml"
            if plugin_config_file.exists():
                with open(plugin_config_file, 'r') as f:
                    config = yaml.safe_load(f)
                    plugin.workflows = config.get("workflows", [])
            
            self.plugins.append(plugin)
            return True
            
        except Exception as e:
            print(f"Error adding plugin: {e}")
            return False
    
    def list_plugins(self) -> List[Dict[str, Any]]:
        """List all configured plugins"""
        return [
            {
                "name": plugin.name,
                "version": plugin.version,
                "repo_url": plugin.repo_url,
                "workflow_count": len(plugin.workflows),
                "enabled": True
            }
            for plugin in self.plugins
        ]


# Example plugin configuration file (plugins.yaml):
"""
plugins:
  - name: aquabilidad-workflows
    repo_url: https://github.com/aquabilidad/civicstream-workflows
    enabled: true
    version: 1.0.0
    workflows:
      - module: aquabilidad.fishing_workflows
        function: create_catch_reporting_workflow
      - module: aquabilidad.fishing_workflows
        function: create_traceability_workflow
        
  - name: municipal-workflows
    repo_url: https://github.com/example/municipal-workflows
    enabled: true
    version: 1.0.0
    workflows:
      - module: municipal.permits
        function: create_event_permit_workflow
"""

# Example external workflow repository structure:
"""
aquabilidad-workflows/
├── civicstream.yaml          # Plugin configuration
├── requirements.txt          # Python dependencies
├── aquabilidad/
│   ├── __init__.py
│   └── fishing_workflows.py  # Workflow definitions
└── tests/
    └── test_workflows.py
"""