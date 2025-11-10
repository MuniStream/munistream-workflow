"""
Data Formatting Utilities for Entity Visualization
"""

from typing import Any, Dict, List, Union
from datetime import datetime, date
import json


class DataFormatter:
    """Format entity data for display in templates and PDFs"""

    def __init__(self):
        """Initialize data formatter"""
        self.field_formatters = {
            'date': self._format_date,
            'datetime': self._format_datetime,
            'currency': self._format_currency,
            'percentage': self._format_percentage,
            'phone': self._format_phone,
            'email': self._format_email,
            'address': self._format_address,
            'boolean': self._format_boolean,
            'list': self._format_list,
            'dict': self._format_dict,
        }

    def format_entity_data(self, data: Dict[str, Any]) -> Dict[str, str]:
        """
        Format all fields in entity data for display

        Args:
            data: Raw entity data dictionary

        Returns:
            Formatted data dictionary
        """
        formatted = {}
        for key, value in data.items():
            formatted[key] = self.format_value(value, field_name=key)
        return formatted

    def format_value(
        self,
        value: Any,
        field_name: str = None,
        format_type: str = None
    ) -> str:
        """
        Format a single value for display

        Args:
            value: Value to format
            field_name: Name of the field (for context-based formatting)
            format_type: Explicit format type to use

        Returns:
            Formatted string
        """
        if value is None:
            return "-"

        # Determine format type
        if not format_type:
            format_type = self._detect_format_type(value, field_name)

        # Apply formatter
        formatter = self.field_formatters.get(format_type, self._format_default)
        try:
            return formatter(value)
        except Exception:
            # Fall back to default formatting if specialized formatter fails
            return self._format_default(value)

    def _detect_format_type(self, value: Any, field_name: str = None) -> str:
        """
        Detect the appropriate format type for a value

        Args:
            value: Value to analyze
            field_name: Field name for context

        Returns:
            Format type string
        """
        # Check field name patterns first
        if field_name:
            field_lower = field_name.lower()

            if any(word in field_lower for word in ['date', 'fecha']):
                if 'time' in field_lower or 'datetime' in field_lower:
                    return 'datetime'
                return 'date'

            if any(word in field_lower for word in ['phone', 'tel', 'telefono']):
                return 'phone'

            if any(word in field_lower for word in ['email', 'correo']):
                return 'email'

            if any(word in field_lower for word in ['address', 'direccion']):
                return 'address'

            if any(word in field_lower for word in ['price', 'cost', 'amount', 'precio', 'costo']):
                return 'currency'

            if 'percent' in field_lower or 'porcentaje' in field_lower:
                return 'percentage'

        # Check value type and content
        if isinstance(value, bool):
            return 'boolean'

        if isinstance(value, list):
            return 'list'

        if isinstance(value, dict):
            return 'dict'

        if isinstance(value, (datetime, date)):
            return 'datetime' if isinstance(value, datetime) else 'date'

        # Check string patterns
        if isinstance(value, str):
            # ISO date pattern
            if self._is_iso_date(value):
                return 'datetime' if 'T' in value else 'date'

            # Email pattern
            if '@' in value and '.' in value:
                return 'email'

            # Phone pattern (basic)
            if self._is_phone_number(value):
                return 'phone'

        return 'default'

    def _format_date(self, value: Union[str, date, datetime]) -> str:
        """Format date value"""
        if isinstance(value, str):
            try:
                if 'T' in value:
                    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
                    return dt.strftime('%Y-%m-%d')
                else:
                    return value  # Already in date format
            except ValueError:
                return value
        elif isinstance(value, datetime):
            return value.strftime('%Y-%m-%d')
        elif isinstance(value, date):
            return value.strftime('%Y-%m-%d')
        return str(value)

    def _format_datetime(self, value: Union[str, datetime]) -> str:
        """Format datetime value"""
        if isinstance(value, str):
            try:
                dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
                return dt.strftime('%Y-%m-%d %H:%M:%S')
            except ValueError:
                return value
        elif isinstance(value, datetime):
            return value.strftime('%Y-%m-%d %H:%M:%S')
        return str(value)

    def _format_currency(self, value: Union[str, int, float]) -> str:
        """Format currency value"""
        try:
            if isinstance(value, str):
                # Remove currency symbols and parse
                clean_value = value.replace('$', '').replace(',', '').strip()
                amount = float(clean_value)
            else:
                amount = float(value)

            return f"${amount:,.2f}"
        except (ValueError, TypeError):
            return str(value)

    def _format_percentage(self, value: Union[str, int, float]) -> str:
        """Format percentage value"""
        try:
            if isinstance(value, str):
                clean_value = value.replace('%', '').strip()
                pct = float(clean_value)
            else:
                pct = float(value)

            return f"{pct:.1f}%"
        except (ValueError, TypeError):
            return str(value)

    def _format_phone(self, value: str) -> str:
        """Format phone number"""
        if not isinstance(value, str):
            return str(value)

        # Remove non-numeric characters
        digits = ''.join(filter(str.isdigit, value))

        if len(digits) == 10:
            # Format as (XXX) XXX-XXXX
            return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
        elif len(digits) == 11 and digits[0] == '1':
            # Format as +1 (XXX) XXX-XXXX
            return f"+1 ({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
        else:
            return value  # Return original if can't format

    def _format_email(self, value: str) -> str:
        """Format email (mostly just validation)"""
        if isinstance(value, str) and '@' in value:
            return value.lower().strip()
        return str(value)

    def _format_address(self, value: Union[str, dict]) -> str:
        """Format address value"""
        if isinstance(value, dict):
            # Structured address
            parts = []

            # Street and number
            street = value.get('street', '')
            number = value.get('number', '')
            if street:
                parts.append(f"{street} {number}".strip())

            # Colony/neighborhood
            if value.get('colony'):
                parts.append(value['colony'])

            # City, State ZIP
            city_state = []
            if value.get('city'):
                city_state.append(value['city'])
            if value.get('state'):
                city_state.append(value['state'])
            if value.get('zip_code'):
                city_state.append(value['zip_code'])

            if city_state:
                parts.append(', '.join(city_state))

            return ', '.join(parts)

        return str(value)

    def _format_boolean(self, value: bool) -> str:
        """Format boolean value"""
        return "Yes" if value else "No"

    def _format_list(self, value: list) -> str:
        """Format list value"""
        if not value:
            return "-"

        # Format each item and join
        formatted_items = [self.format_value(item) for item in value]
        return ", ".join(formatted_items)

    def _format_dict(self, value: dict) -> str:
        """Format dictionary value"""
        if not value:
            return "-"

        # For simple dicts, show key: value pairs
        if len(value) <= 3:
            pairs = []
            for k, v in value.items():
                formatted_key = k.replace('_', ' ').title()
                formatted_value = self.format_value(v)
                pairs.append(f"{formatted_key}: {formatted_value}")
            return "; ".join(pairs)
        else:
            # For complex dicts, just show the count
            return f"Complex data ({len(value)} fields)"

    def _format_default(self, value: Any) -> str:
        """Default formatter for any value"""
        if isinstance(value, (int, float)):
            if isinstance(value, float) and value.is_integer():
                return str(int(value))
            return str(value)

        return str(value)

    def _is_iso_date(self, value: str) -> bool:
        """Check if string is an ISO date"""
        try:
            if 'T' in value:
                datetime.fromisoformat(value.replace('Z', '+00:00'))
            else:
                datetime.strptime(value, '%Y-%m-%d')
            return True
        except ValueError:
            return False

    def _is_phone_number(self, value: str) -> bool:
        """Check if string looks like a phone number"""
        digits = ''.join(filter(str.isdigit, value))
        return len(digits) >= 10 and len(digits) <= 15

    def format_field_name(self, field_name: str) -> str:
        """
        Format field name for display

        Args:
            field_name: Raw field name

        Returns:
            Formatted field name
        """
        # Replace underscores with spaces and title case
        formatted = field_name.replace('_', ' ').title()

        # Handle common abbreviations
        replacements = {
            'Id': 'ID',
            'Url': 'URL',
            'Api': 'API',
            'Http': 'HTTP',
            'Https': 'HTTPS',
            'Xml': 'XML',
            'Json': 'JSON',
            'Uuid': 'UUID',
        }

        for old, new in replacements.items():
            formatted = formatted.replace(old, new)

        return formatted