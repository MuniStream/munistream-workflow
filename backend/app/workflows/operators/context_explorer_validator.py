"""
ContextExplorerValidator - Operator for exploring and validating workflow context.

This operator displays comprehensive information from any specified workflow context,
including selected entities with their visualizers, form data, and other contextual information.
Designed for administrative workflows to provide full visibility into any workflow data.
"""

from typing import Dict, Any, List, Optional
import logging
from datetime import datetime

from .base import BaseOperator, TaskResult
from ...services.entity_service import EntityService
from ...core.logging_config import get_workflow_logger

logger = get_workflow_logger(__name__)


class ContextExplorerValidator(BaseOperator):
    """
    Operator that displays specified workflow context information for validation.

    Shows selected entities with their visualizers, form data, and other contextual
    information from the specified context path. Provides a comprehensive view for
    administrative validation and review.
    """

    def __init__(
        self,
        task_id: str,
        context_path: str = "_parent_context",
        title: str = "Context Explorer - Workflow Information",
        description: str = "Review and validate workflow information",
        show_raw_context: bool = True,
        show_selected_entities: bool = True,
        show_form_data: bool = True,
        show_user_info: bool = True,
        show_s3_uploads: bool = True,
        entity_display_fields: Optional[List[str]] = None,
        **kwargs
    ):
        """
        Initialize the ContextExplorerValidator.

        Args:
            task_id: Unique task identifier
            context_path: Path to context data (e.g., "_parent_context", "original_context")
            title: Title for the explorer interface
            description: Description of what's being reviewed
            show_raw_context: Whether to show raw context data
            show_selected_entities: Whether to show selected entities with visualizers
            show_form_data: Whether to show form submission data
            show_user_info: Whether to show user/customer information
            show_s3_uploads: Whether to show uploaded files from S3
            entity_display_fields: Fields to display for each entity
            **kwargs: Additional arguments passed to BaseOperator
        """
        super().__init__(task_id=task_id, **kwargs)
        self.context_path = context_path
        self.title = title
        self.description = description
        self.show_raw_context = show_raw_context
        self.show_selected_entities = show_selected_entities
        self.show_form_data = show_form_data
        self.show_user_info = show_user_info
        self.show_s3_uploads = show_s3_uploads
        self.entity_display_fields = entity_display_fields or [
            "name", "entity_type", "upload_date", "file_size", "status"
        ]

    def execute(self, context: Dict[str, Any]) -> TaskResult:
        """
        Synchronous wrapper for async execution.
        Used when the executor doesn't call execute_async directly.
        """
        import asyncio
        return asyncio.run(self.execute_async(context))

    async def execute_async(self, context: Dict[str, Any]) -> TaskResult:
        """Async execution for entity loading"""

        logger.info("ContextExplorerValidator async execution started",
                   context_path=self.context_path)

        # Check if user has submitted validation decision
        input_key = f"{self.task_id}_input"
        if input_key in context:
            return self._process_validation_decision(context)

        # Display context explorer interface with async entity loading
        return await self._display_context_explorer(context)

    def _process_validation_decision(self, context: Dict[str, Any]) -> TaskResult:
        """Process the validation decision from the user"""

        input_data = context.get(f"{self.task_id}_input", {})
        decision = input_data.get("validation_decision")
        comments = input_data.get("validation_comments", "")

        logger.info("Processing validation decision",
                   decision=decision,
                   has_comments=bool(comments))

        if decision == "approved":
            return TaskResult(
                status="continue",
                data={
                    "validation_decision": "approved",
                    "validation_comments": comments,
                    "validated_at": datetime.utcnow().isoformat(),
                    "validated_by": context.get("user_id", "system")
                }
            )
        elif decision == "rejected":
            return TaskResult(
                status="failed",
                data={
                    "validation_decision": "rejected",
                    "validation_comments": comments,
                    "rejected_at": datetime.utcnow().isoformat(),
                    "rejected_by": context.get("user_id", "system"),
                    "error": f"Context validation rejected: {comments}"
                }
            )
        else:
            # Invalid decision, show form again
            self.state.waiting_for = "context_validation"
            return TaskResult(
                status="waiting",
                data={
                    "waiting_for": "context_validation",
                    "validation_errors": ["Please select a validation decision"]
                }
            )

    async def _display_context_explorer(
        self,
        context: Dict[str, Any],
        validation_errors: Optional[List[str]] = None
    ) -> TaskResult:
        """Display the context explorer interface"""

        logger.info("Displaying context explorer interface",
                   context_path=self.context_path)

        # Get specified context
        target_context = self._get_context_data(context)

        if not target_context:
            logger.warning("No context found at specified path",
                          context_path=self.context_path)
            return TaskResult(
                status="failed",
                data={
                    "error": f"No workflow context available at path: {self.context_path}",
                    "available_keys": list(context.keys())
                }
            )

        # Build the explorer interface
        form_config = {
            "title": self.title,
            "description": f"{self.description} (Context: {self.context_path})",
            "type": "context_explorer_validation",
            "sections": []
        }

        # Add validation errors if any
        if validation_errors:
            form_config["validation_errors"] = validation_errors

        # Section 1: User Information
        if self.show_user_info:
            user_section = self._build_user_info_section(target_context)
            if user_section:
                form_config["sections"].append(user_section)

        # Section 2: Selected Entities
        if self.show_selected_entities:
            entities_section = await self._build_selected_entities_section(target_context)
            if entities_section:
                form_config["sections"].append(entities_section)

        # Section 3: Form Data
        if self.show_form_data:
            form_section = self._build_form_data_section(target_context)
            if form_section:
                form_config["sections"].append(form_section)

        # Section 4: S3 Uploaded Files
        if self.show_s3_uploads:
            s3_section = await self._build_s3_uploads_section(target_context)
            if s3_section:
                form_config["sections"].append(s3_section)

        # Section 5: Validation Results
        validation_section = self._build_validation_results_section(target_context)
        if validation_section:
            form_config["sections"].append(validation_section)

        # Section 6: Raw Context (optional)
        if self.show_raw_context:
            context_section = self._build_raw_context_section(target_context)
            if context_section:
                form_config["sections"].append(context_section)

        # Add validation decision fields
        form_config["validation_fields"] = [
            {
                "name": "validation_decision",
                "label": "Validation Decision",
                "type": "radio",
                "required": True,
                "options": [
                    {"value": "approved", "label": "Approve - Context is valid and complete"},
                    {"value": "rejected", "label": "Reject - Context has issues or is incomplete"}
                ]
            },
            {
                "name": "validation_comments",
                "label": "Validation Comments",
                "type": "textarea",
                "required": False,
                "placeholder": "Optional comments about the validation decision..."
            }
        ]


        # Set waiting_for in task state (like SelfieOperator does)
        self.state.waiting_for = "context_validation"

        task_result = TaskResult(
            status="waiting",
            data={
                "waiting_for": "context_validation",
                "form_config": form_config
            }
        )


        return task_result

    def _get_context_data(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Get context data from the specified path"""

        # Support nested paths like "parent.grandparent.context"
        if "." in self.context_path:
            current = context
            for part in self.context_path.split("."):
                if isinstance(current, dict) and part in current:
                    current = current[part]
                else:
                    return None
            return current if isinstance(current, dict) else None
        else:
            # Simple path
            return context.get(self.context_path)

    def _build_user_info_section(self, target_context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Build user information section"""

        user_info = {}

        # Collect user-related fields
        user_fields = [
            ("customer_name", "Customer Name"),
            ("customer_email", "Customer Email"),
            ("customer_id", "Customer ID"),
            ("user_id", "User ID"),
            ("parent_user_id", "Parent User ID"),
            ("parent_customer_email", "Parent Customer Email")
        ]

        for field_key, field_label in user_fields:
            if field_key in target_context:
                user_info[field_label] = target_context[field_key]

        if not user_info:
            return None

        return {
            "title": "👤 User Information",
            "type": "info_display",
            "data": user_info
        }

    async def _build_selected_entities_section(
        self,
        target_context: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Build selected entities section with visualizers"""

        selected_entities = target_context.get("selected_entities", {})

        if not selected_entities:
            return {
                "title": "📎 Selected Entities",
                "type": "info_display",
                "data": {"message": "No entities were selected in this workflow"}
            }

        entities_data = {}

        for entity_group, entity_ids in selected_entities.items():
            if not entity_ids:
                continue

            # Get entity details
            try:
                entities = []
                for entity_id in entity_ids:
                    entity = await EntityService.get_entity(entity_id)
                    if entity:
                        entity_info = {
                            "entity_id": entity.entity_id,
                            "name": entity.name,
                            "entity_type": entity.entity_type,
                            "created_at": entity.created_at.isoformat() if entity.created_at else None
                        }

                        # Add display fields
                        for field in self.entity_display_fields:
                            if hasattr(entity, field):
                                value = getattr(entity, field)
                                if value is not None:
                                    entity_info[field] = value
                            elif field in entity.data:
                                entity_info[field] = entity.data[field]

                        # Add visualizer if available
                        if hasattr(entity, 'visualizer') and entity.visualizer:
                            entity_info["visualizer"] = entity.visualizer

                        entities.append(entity_info)

                if entities:
                    entities_data[entity_group] = entities

            except Exception as e:
                logger.error(f"Error loading entities for {entity_group}: {str(e)}")
                entities_data[entity_group] = [{"error": f"Failed to load entities: {str(e)}"}]

        return {
            "title": "📎 Selected Entities",
            "type": "entities_display",
            "data": entities_data
        }

    def _build_form_data_section(self, target_context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Build form data section"""

        form_data = {}

        # Look for common form data patterns
        form_patterns = [
            ("collect_concession_data_data", "Concession Data"),
            ("collect_property_data_data", "Property Data"),
            ("collect_permit_data_data", "Permit Data"),
            ("form_data", "Form Data"),
            ("user_input", "User Input")
        ]

        for pattern_key, pattern_label in form_patterns:
            if pattern_key in target_context:
                data = target_context[pattern_key]
                if isinstance(data, dict) and data:
                    form_data[pattern_label] = data

        # Also look for any field ending with '_data'
        for key, value in target_context.items():
            if key.endswith('_data') and isinstance(value, dict) and value:
                if key not in [p[0] for p in form_patterns]:
                    label = key.replace('_', ' ').title()
                    form_data[label] = value

        if not form_data:
            return None

        return {
            "title": "📝 Form Submission Data",
            "type": "form_data_display",
            "data": form_data
        }

    def _build_raw_context_section(self, target_context: Dict[str, Any]) -> Dict[str, Any]:
        """Build raw context section"""

        # Filter out sensitive or unnecessary fields
        filtered_context = {}
        skip_fields = {
            '_parent_context', 'password', 'secret', 'token', 'key',
            'digital_signature_private_key', 'digital_signature_password'
        }

        for key, value in target_context.items():
            if not any(skip in key.lower() for skip in skip_fields):
                filtered_context[key] = value

        return {
            "title": f"🔍 Raw Context Data ({self.context_path})",
            "type": "json_display",
            "data": filtered_context,
            "collapsible": True,
            "collapsed": True
        }

    async def _build_s3_uploads_section(self, target_context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Build S3 uploads section with file previews"""

        s3_uploads = {}

        # Find all S3 upload results in context
        for key, value in target_context.items():

            # Look for S3 upload patterns
            pattern_matches = [pattern for pattern in ['_s3_result', '_s3_upload', '_result'] if pattern in key]
            has_upload_or_s3 = 'upload' in key or 's3' in key.lower()


            if pattern_matches and has_upload_or_s3:

                if isinstance(value, dict) and 'url' in value:
                    # Process single upload
                    file_info = await self._process_s3_file(value, key)
                    if file_info:
                        s3_uploads[key] = file_info
                elif isinstance(value, dict) and 'uploaded_files' in value:
                    # Process uploaded_files array structure
                    uploaded_files = value.get('uploaded_files', [])
                    processed_files = []
                    for item in uploaded_files:
                        if isinstance(item, dict) and 'url' in item:
                            file_info = await self._process_s3_file(item, key)
                            if file_info:
                                processed_files.append(file_info)
                    if processed_files:
                        s3_uploads[key] = processed_files
                elif isinstance(value, list):
                    # Process multiple uploads
                    processed_files = []
                    for item in value:
                        if isinstance(item, dict) and 'url' in item:
                            file_info = await self._process_s3_file(item, key)
                            if file_info:
                                processed_files.append(file_info)
                    if processed_files:
                        s3_uploads[key] = processed_files

        if not s3_uploads:
            return None

        return {
            "title": "📁 Uploaded Files",
            "type": "s3_files_display",
            "data": s3_uploads
        }

    async def _process_s3_file(self, upload_result: Dict[str, Any], source_key: str) -> Optional[Dict[str, Any]]:
        """Process a single S3 file for display"""
        try:
            from app.services.file_conversion_service import FileConversionService

            file_url = upload_result.get('url')
            if not file_url:
                return None

            # Extract meaningful task name from source key
            task_name = source_key.replace('_s3_result', '').replace('_s3_upload', '').replace('_result', '')
            task_name = task_name.replace('upload_', '').replace('_', ' ').title()

            # Initialize conversion service
            conversion_service = FileConversionService()

            # Convert to preview (reusing FileConversionService logic)
            conversion_result = await conversion_service.convert_file(
                file_url=file_url,
                convert_format='png',
                max_width=400,
                thumbnail=True
            )

            # Prepare the result with proper field mapping for frontend
            result = {
                'url': file_url,
                'filename': upload_result.get('filename', 'unknown'),
                's3_key': upload_result.get('s3_key'),
                'source_task': task_name,
                'size': upload_result.get('size'),
                'bucket': upload_result.get('bucket'),
            }

            # Map conversion result fields to frontend expectations
            if conversion_result:
                if 'data' in conversion_result:
                    result['preview_data'] = conversion_result['data']  # Map data -> preview_data
                if 'format' in conversion_result:
                    result['file_type'] = conversion_result['format']
                if 'type' in conversion_result:
                    result['conversion_type'] = conversion_result['type']
                if 'error' in conversion_result:
                    result['error'] = conversion_result['error']

            return result

        except Exception as e:
            logger.warning(f"Could not process S3 file from {source_key}: {e}")
            return {
                'url': upload_result.get('url', ''),
                'filename': upload_result.get('filename', 'unknown'),
                'source_task': source_key.replace('_s3_result', '').replace('_s3_upload', '').replace('_result', ''),
                'error': str(e)
            }

    def _build_validation_results_section(self, target_context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Build validation results section for SelfieOperator and IDCaptureOperator"""

        validation_results = {}

        # Look for validation patterns
        validation_patterns = {
            'capture_selfie': {
                'validation_key': 'capture_selfie_validation_score',
                'provenance_key': 'capture_selfie_provenance',
                'capture_key': 'selfie_capture',
                'display_name': 'Selfie Validation'
            },
            'capture_id_document': {
                'validation_key': 'capture_id_document_quality_score',
                'provenance_key': 'capture_id_document_provenance',
                'capture_key': 'captured_document',
                'display_name': 'ID Document Validation'
            }
        }

        for pattern_key, pattern_info in validation_patterns.items():
            validation_data = {}

            # Get validation score
            if pattern_info['validation_key'] in target_context:
                validation_data['score'] = target_context[pattern_info['validation_key']]

            # Get provenance information
            if pattern_info['provenance_key'] in target_context:
                provenance = target_context[pattern_info['provenance_key']]
                validation_data['provenance'] = {
                    'capture_method': provenance.get('capture_method'),
                    'platform': provenance.get('platform'),
                    'user_agent': provenance.get('user_agent', '').split(' ')[0] if provenance.get('user_agent') else None,
                    'capture_timestamp': provenance.get('capture_timestamp'),
                    'quality_score': provenance.get('quality_score'),
                    'validation_checks_passed': provenance.get('validation_checks_passed'),
                    'all_validations_passed': provenance.get('all_validations_passed')
                }

            # Get capture/validation details
            if pattern_info['capture_key'] in target_context:
                capture_data = target_context[pattern_info['capture_key']]

                if 'validation' in capture_data:
                    validation_info = capture_data['validation']
                    validation_data['validation_details'] = {
                        'valid': validation_info.get('valid'),
                        'quality_score': validation_info.get('quality_score'),
                        'errors': validation_info.get('errors', []),
                        'validation_timestamp': validation_info.get('validation_timestamp')
                    }

                    # Specific validations for selfie
                    if pattern_key == 'capture_selfie':
                        validation_data['validation_details'].update({
                            'face_detected': validation_info.get('face_detected'),
                            'face_confidence': validation_info.get('face_confidence'),
                            'face_count': validation_info.get('face_count')
                        })

                    # Specific validations for ID document
                    elif pattern_key == 'capture_id_document':
                        validation_data['validation_details'].update({
                            'front_quality': validation_info.get('front_quality'),
                            'back_quality': validation_info.get('back_quality'),
                            'detected_elements': validation_info.get('detected_elements', {})
                        })

            # Add to results if we have data
            if validation_data:
                validation_results[pattern_info['display_name']] = validation_data

        if not validation_results:
            return None

        return {
            "title": "🔍 Validation Results",
            "type": "validation_results_display",
            "data": validation_results
        }