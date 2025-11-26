"""
Visualizer Factory

Factory class for creating entity visualizers based on type.
"""
from typing import Dict, Any, Optional, List
import logging

from .base import EntityVisualizer
from .pdf_visualizer import PDFVisualizer
from .signed_pdf_visualizer import SignedPDFVisualizer

logger = logging.getLogger(__name__)


class VisualizerFactory:
    """
    Factory for creating entity visualizers.

    This factory manages the registration and creation of different
    visualizer types for entities.
    """

    # Registry of available visualizers
    _visualizers = {
        "pdf_report": PDFVisualizer,
        "pdf": PDFVisualizer,
        "basic_pdf": PDFVisualizer,
        "signed_pdf": SignedPDFVisualizer,
        "signed_pdf_report": SignedPDFVisualizer,
        "digital_signature_pdf": SignedPDFVisualizer
    }

    @classmethod
    def register_visualizer(cls, name: str, visualizer_class):
        """
        Register a new visualizer type.

        Args:
            name: Name/alias for the visualizer
            visualizer_class: Class that implements EntityVisualizer
        """
        if not issubclass(visualizer_class, EntityVisualizer):
            raise ValueError(f"Visualizer class must inherit from EntityVisualizer")

        cls._visualizers[name] = visualizer_class
        logger.info(f"Registered visualizer: {name} -> {visualizer_class.__name__}")

    @classmethod
    def get_visualizer(
        cls,
        visualizer_type: str,
        config: Optional[Dict[str, Any]] = None
    ) -> Optional[EntityVisualizer]:
        """
        Create a visualizer instance.

        Args:
            visualizer_type: Type of visualizer to create
            config: Configuration for the visualizer

        Returns:
            Visualizer instance or None if not found
        """
        try:
            visualizer_class = cls._visualizers.get(visualizer_type)

            if not visualizer_class:
                logger.error(f"Unknown visualizer type: {visualizer_type}")
                return None

            return visualizer_class(config=config)

        except Exception as e:
            logger.error(f"Failed to create visualizer {visualizer_type}: {e}")
            return None

    @classmethod
    def get_available_visualizers(cls) -> List[str]:
        """
        Get list of available visualizer types.

        Returns:
            List of registered visualizer names
        """
        return list(cls._visualizers.keys())

    @classmethod
    def get_visualizer_info(cls, visualizer_type: str) -> Optional[Dict[str, Any]]:
        """
        Get information about a specific visualizer.

        Args:
            visualizer_type: Type of visualizer

        Returns:
            Information dictionary or None if not found
        """
        try:
            visualizer_class = cls._visualizers.get(visualizer_type)

            if not visualizer_class:
                return None

            # Create temporary instance to get info
            temp_visualizer = visualizer_class()
            return temp_visualizer.get_visualizer_info()

        except Exception as e:
            logger.error(f"Failed to get visualizer info for {visualizer_type}: {e}")
            return None

    @classmethod
    def get_all_visualizer_info(cls) -> Dict[str, Any]:
        """
        Get information about all registered visualizers.

        Returns:
            Dictionary with information about all visualizers
        """
        result = {}

        for name, visualizer_class in cls._visualizers.items():
            try:
                temp_visualizer = visualizer_class()
                result[name] = temp_visualizer.get_visualizer_info()
            except Exception as e:
                logger.error(f"Failed to get info for visualizer {name}: {e}")
                result[name] = {
                    "name": name,
                    "error": str(e),
                    "class": visualizer_class.__name__
                }

        return result

    @classmethod
    def supports_format(cls, visualizer_type: str, format_type: str) -> bool:
        """
        Check if a visualizer supports a specific format.

        Args:
            visualizer_type: Type of visualizer
            format_type: Format to check

        Returns:
            True if format is supported
        """
        try:
            visualizer_class = cls._visualizers.get(visualizer_type)

            if not visualizer_class:
                return False

            temp_visualizer = visualizer_class()
            return temp_visualizer.supports_format(format_type)

        except Exception as e:
            logger.error(f"Failed to check format support for {visualizer_type}: {e}")
            return False

    @classmethod
    def get_visualizers_for_format(cls, format_type: str) -> List[str]:
        """
        Get all visualizers that support a specific format.

        Args:
            format_type: Format type to check

        Returns:
            List of visualizer names that support the format
        """
        supported = []

        for name in cls._visualizers.keys():
            if cls.supports_format(name, format_type):
                supported.append(name)

        return supported

    @classmethod
    def get_default_visualizer(cls, entity_type: str = None) -> str:
        """
        Get the default visualizer for an entity type.

        Args:
            entity_type: Type of entity (optional)

        Returns:
            Default visualizer name
        """
        # For now, return basic PDF as default
        # This could be made more sophisticated based on entity_type
        return "pdf_report"

    @classmethod
    def validate_config(
        cls,
        visualizer_type: str,
        config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Validate configuration for a visualizer.

        Args:
            visualizer_type: Type of visualizer
            config: Configuration to validate

        Returns:
            Validation result with any errors or warnings
        """
        result = {
            "valid": True,
            "errors": [],
            "warnings": []
        }

        try:
            visualizer_class = cls._visualizers.get(visualizer_type)

            if not visualizer_class:
                result["valid"] = False
                result["errors"].append(f"Unknown visualizer type: {visualizer_type}")
                return result

            # Try to create visualizer with config
            visualizer = visualizer_class(config=config)

            # Get config options info
            info = visualizer.get_visualizer_info()
            config_options = info.get("config_options", [])

            # Validate against known options (basic validation)
            for key in config.keys():
                known_option = any(opt["name"] == key for opt in config_options)
                if not known_option:
                    result["warnings"].append(f"Unknown config option: {key}")

        except Exception as e:
            result["valid"] = False
            result["errors"].append(f"Configuration validation failed: {str(e)}")

        return result


# Auto-register default visualizers when module is imported
def _initialize_default_visualizers():
    """Initialize default visualizers in the factory"""
    # Default visualizers are already registered in the class definition
    logger.info(f"VisualizerFactory initialized with {len(VisualizerFactory._visualizers)} visualizers")


# Initialize when module is loaded
_initialize_default_visualizers()