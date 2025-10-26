"""
AI Validation Operator for intelligent data validation and verification.
Uses AI models to validate extracted data against business rules and external sources.
"""
from typing import Dict, Any, List, Optional, Callable
import asyncio
import json
import logging
from datetime import datetime
import re

from .base import BaseOperator, TaskResult
from .external_api import ExternalAPIOperator
from ...core.config import settings

logger = logging.getLogger(__name__)


class AIValidationOperator(BaseOperator):
    """
    AI-powered operator for validating and verifying extracted data.
    Performs rule-based validation, format checking, and external API verification.
    """

    def __init__(
        self,
        task_id: str,
        data_context_key: str,
        validation_rules: Dict[str, Any],
        external_apis: Optional[List[Dict[str, Any]]] = None,
        ai_model: Optional[str] = None,
        confidence_threshold: float = 0.8,
        **kwargs
    ):
        """
        Initialize AI validation operator.

        Args:
            task_id: Unique task identifier
            data_context_key: Context key containing data to validate
            validation_rules: Validation rules and business logic
            external_apis: External API configurations for verification
            ai_model: Specific AI model to use
            confidence_threshold: Minimum confidence for validation pass
        """
        super().__init__(task_id, **kwargs)
        self.data_context_key = data_context_key
        self.validation_rules = validation_rules
        self.external_apis = external_apis or []
        self.ai_model = ai_model or settings.AI_MODEL_NAME
        self.confidence_threshold = confidence_threshold

    def execute(self, context: Dict[str, Any]) -> TaskResult:
        """Synchronous wrapper for async execution."""
        return asyncio.run(self.execute_async(context))

    async def execute_async(self, context: Dict[str, Any]) -> TaskResult:
        """Execute AI validation on extracted data."""
        try:
            logger.info(f"Starting AI validation for task {self.task_id}")

            # Get data to validate from context
            data_to_validate = context.get(self.data_context_key)
            if not data_to_validate:
                return TaskResult.failure(f"No data found in context key: {self.data_context_key}")

            validation_results = []

            # Handle multiple extractions
            if isinstance(data_to_validate, list):
                for i, extraction in enumerate(data_to_validate):
                    result = await self._validate_single_extraction(extraction, context)
                    validation_results.append({
                        'extraction_index': i,
                        'filename': extraction.get('filename', f'extraction_{i}'),
                        'validation_result': result,
                        'validation_timestamp': datetime.utcnow().isoformat()
                    })
            else:
                result = await self._validate_single_extraction(data_to_validate, context)
                validation_results.append({
                    'extraction_index': 0,
                    'filename': 'single_extraction',
                    'validation_result': result,
                    'validation_timestamp': datetime.utcnow().isoformat()
                })

            # Calculate overall validation metrics
            total_validations = len(validation_results)
            passed_validations = len([r for r in validation_results if r['validation_result'].get('overall_valid', False)])
            validation_rate = passed_validations / total_validations if total_validations > 0 else 0

            # Store results in context
            context.update({
                f'{self.task_id}_validations': validation_results,
                f'{self.task_id}_validation_rate': validation_rate,
                f'{self.task_id}_passed_count': passed_validations,
                f'{self.task_id}_total_count': total_validations
            })

            if validation_rate >= self.confidence_threshold:
                logger.info(f"AI validation completed with {validation_rate:.1%} success rate")
                return TaskResult.success(f"Validated {passed_validations}/{total_validations} extractions")
            else:
                return TaskResult.failure(f"Low validation success rate: {validation_rate:.1%}")

        except Exception as e:
            logger.error(f"AI validation failed: {str(e)}")
            return TaskResult.failure(f"AI validation error: {str(e)}")

    async def _validate_single_extraction(self, extraction_data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Validate a single data extraction."""
        extracted_data = extraction_data.get('extraction_result', {})
        if not extracted_data:
            return {
                'overall_valid': False,
                'error': 'No extraction result to validate',
                'field_validations': {},
                'external_validations': {},
                'ai_validation': {}
            }

        validation_result = {
            'overall_valid': True,
            'field_validations': {},
            'external_validations': {},
            'ai_validation': {},
            'confidence_score': 1.0
        }

        try:
            # 1. Rule-based field validation
            field_results = await self._validate_fields(extracted_data)
            validation_result['field_validations'] = field_results

            # 2. External API validation
            if self.external_apis:
                external_results = await self._validate_external_apis(extracted_data, context)
                validation_result['external_validations'] = external_results

            # 3. AI-powered validation
            ai_results = await self._ai_validate_data(extracted_data)
            validation_result['ai_validation'] = ai_results

            # Calculate overall validity and confidence
            overall_valid, confidence = self._calculate_overall_validity(
                field_results,
                validation_result['external_validations'],
                ai_results
            )

            validation_result['overall_valid'] = overall_valid
            validation_result['confidence_score'] = confidence

            return validation_result

        except Exception as e:
            logger.error(f"Single extraction validation failed: {str(e)}")
            return {
                'overall_valid': False,
                'error': str(e),
                'field_validations': {},
                'external_validations': {},
                'ai_validation': {}
            }

    async def _validate_fields(self, data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """Validate individual fields using rule-based validation."""
        field_results = {}

        for field_name, field_rules in self.validation_rules.get('fields', {}).items():
            field_value = data.get(field_name)
            field_result = {
                'valid': True,
                'errors': [],
                'warnings': [],
                'formatted_value': field_value
            }

            # Required field check
            if field_rules.get('required', False) and not field_value:
                field_result['valid'] = False
                field_result['errors'].append('Field is required but missing')

            if field_value is not None:
                # Data type validation
                expected_type = field_rules.get('type')
                if expected_type:
                    if not self._validate_type(field_value, expected_type):
                        field_result['valid'] = False
                        field_result['errors'].append(f'Expected type {expected_type}, got {type(field_value).__name__}')

                # Format validation
                format_pattern = field_rules.get('format')
                if format_pattern and isinstance(field_value, str):
                    if not re.match(format_pattern, field_value):
                        field_result['valid'] = False
                        field_result['errors'].append(f'Does not match required format: {format_pattern}')

                # Value range validation
                min_value = field_rules.get('min')
                max_value = field_rules.get('max')
                if min_value is not None or max_value is not None:
                    try:
                        numeric_value = float(field_value) if isinstance(field_value, str) else field_value
                        if min_value is not None and numeric_value < min_value:
                            field_result['valid'] = False
                            field_result['errors'].append(f'Value {numeric_value} is below minimum {min_value}')
                        if max_value is not None and numeric_value > max_value:
                            field_result['valid'] = False
                            field_result['errors'].append(f'Value {numeric_value} is above maximum {max_value}')
                    except (ValueError, TypeError):
                        field_result['warnings'].append('Could not validate numeric range')

                # Custom validation functions
                custom_validator = field_rules.get('custom_validator')
                if custom_validator and callable(custom_validator):
                    try:
                        custom_result = custom_validator(field_value)
                        if not custom_result:
                            field_result['valid'] = False
                            field_result['errors'].append('Failed custom validation')
                    except Exception as e:
                        field_result['warnings'].append(f'Custom validator error: {str(e)}')

            field_results[field_name] = field_result

        return field_results

    async def _validate_external_apis(self, data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """Validate data using external APIs."""
        external_results = {}

        for api_config in self.external_apis:
            api_name = api_config.get('name', 'unnamed_api')
            try:
                # Create temporary API operator
                api_operator = ExternalAPIOperator(
                    task_id=f"temp_validation_{api_name}",
                    endpoint=api_config['endpoint'],
                    method=api_config.get('method', 'GET'),
                    headers=api_config.get('headers', {}),
                    context_to_payload=api_config.get('context_mapping', {}),
                    timeout=api_config.get('timeout', 30)
                )

                # Prepare context with extracted data
                temp_context = {**context, **data}

                # Execute API call
                api_result = await api_operator.execute(temp_context)

                if api_result.status == 'success':
                    # Process API response based on validation rules
                    validator_func = api_config.get('response_validator')
                    if validator_func and callable(validator_func):
                        validation_result = validator_func(api_result.data, data)
                    else:
                        validation_result = {'valid': True, 'response': api_result.data}

                    external_results[api_name] = validation_result
                else:
                    external_results[api_name] = {
                        'valid': False,
                        'error': f"API call failed: {api_result.message}"
                    }

            except Exception as e:
                logger.error(f"External API validation failed for {api_name}: {str(e)}")
                external_results[api_name] = {
                    'valid': False,
                    'error': str(e)
                }

        return external_results

    async def _ai_validate_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Use AI to validate data consistency and detect anomalies."""
        try:
            validation_prompt = self._build_validation_prompt(data)

            if settings.AI_MODEL_PROVIDER == "openai":
                result = await self._openai_validate(validation_prompt, data)
            elif settings.AI_MODEL_PROVIDER == "anthropic":
                result = await self._anthropic_validate(validation_prompt, data)
            else:
                raise ValueError(f"Unsupported AI provider: {settings.AI_MODEL_PROVIDER}")

            return result

        except Exception as e:
            logger.error(f"AI validation failed: {str(e)}")
            return {
                'valid': False,
                'error': str(e),
                'confidence': 0.0
            }

    def _build_validation_prompt(self, data: Dict[str, Any]) -> str:
        """Build AI validation prompt."""
        data_json = json.dumps(data, indent=2)
        validation_rules_json = json.dumps(self.validation_rules, indent=2)

        return f"""
You are a data validation expert. Analyze the following extracted data for consistency, accuracy, and potential issues.

EXTRACTED DATA:
{data_json}

VALIDATION RULES:
{validation_rules_json}

Please analyze the data and return a JSON response with the following structure:
{{
    "valid": true/false,
    "confidence": 0.0-1.0,
    "issues": ["list of identified issues"],
    "suggestions": ["list of improvement suggestions"],
    "consistency_check": {{
        "cross_field_validation": "analysis of field relationships",
        "format_consistency": "analysis of format consistency",
        "logical_consistency": "analysis of logical consistency"
    }}
}}

Focus on:
1. Cross-field consistency (do related fields make sense together?)
2. Format and pattern consistency
3. Logical validity (dates, numbers, relationships)
4. Potential data entry errors or OCR mistakes
5. Missing or suspicious information

Return only valid JSON, no additional text.
"""

    async def _openai_validate(self, prompt: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate using OpenAI API."""
        try:
            import openai

            if not settings.OPENAI_API_KEY:
                raise ValueError("OpenAI API key not configured")

            client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

            response = await client.chat.completions.create(
                model=self.ai_model,
                messages=[
                    {"role": "system", "content": "You are a data validation expert. Analyze data for accuracy and consistency."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=settings.AI_MAX_TOKENS,
                temperature=0.1,  # Low temperature for validation consistency
                timeout=settings.AI_REQUEST_TIMEOUT
            )

            result_text = response.choices[0].message.content.strip()
            return json.loads(result_text)

        except Exception as e:
            logger.error(f"OpenAI validation failed: {str(e)}")
            raise

    async def _anthropic_validate(self, prompt: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate using Anthropic Claude API."""
        try:
            import anthropic

            if not settings.ANTHROPIC_API_KEY:
                raise ValueError("Anthropic API key not configured")

            client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

            message = await client.messages.create(
                model=self.ai_model,
                max_tokens=settings.AI_MAX_TOKENS,
                temperature=0.1,  # Low temperature for validation consistency
                messages=[{"role": "user", "content": prompt}]
            )

            result_text = message.content[0].text.strip()
            return json.loads(result_text)

        except Exception as e:
            logger.error(f"Anthropic validation failed: {str(e)}")
            raise

    def _validate_type(self, value: Any, expected_type: str) -> bool:
        """Validate if value matches expected type."""
        type_mapping = {
            'string': str,
            'integer': int,
            'float': float,
            'boolean': bool,
            'list': list,
            'dict': dict
        }

        expected_python_type = type_mapping.get(expected_type.lower())
        if not expected_python_type:
            return True  # Unknown type, skip validation

        return isinstance(value, expected_python_type)

    def _calculate_overall_validity(
        self,
        field_results: Dict[str, Dict[str, Any]],
        external_results: Dict[str, Dict[str, Any]],
        ai_results: Dict[str, Any]
    ) -> tuple[bool, float]:
        """Calculate overall validity and confidence score."""
        # Field validation score
        field_valid_count = sum(1 for result in field_results.values() if result.get('valid', False))
        field_total = len(field_results)
        field_score = field_valid_count / field_total if field_total > 0 else 1.0

        # External validation score
        external_valid_count = sum(1 for result in external_results.values() if result.get('valid', False))
        external_total = len(external_results)
        external_score = external_valid_count / external_total if external_total > 0 else 1.0

        # AI validation score
        ai_score = ai_results.get('confidence', 0.0) if ai_results.get('valid', False) else 0.0

        # Weighted average (adjust weights as needed)
        weights = {'field': 0.4, 'external': 0.3, 'ai': 0.3}
        overall_confidence = (
            field_score * weights['field'] +
            external_score * weights['external'] +
            ai_score * weights['ai']
        )

        # Overall validity requires minimum thresholds
        overall_valid = (
            field_score >= 0.8 and  # 80% of fields must be valid
            external_score >= 0.7 and  # 70% of external validations must pass
            ai_score >= 0.6 and  # AI confidence must be >= 60%
            overall_confidence >= self.confidence_threshold
        )

        return overall_valid, overall_confidence