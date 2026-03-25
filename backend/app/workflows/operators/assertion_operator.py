"""
AssertionOperator - Evaluates configurable assertions on workflow context data.

Presents assertion results to the user for confirmation or override.
Example: context.curp == user.curp → system shows true/false, user confirms or corrects.
"""

import re
from typing import Dict, Any, List, Optional
from datetime import datetime

from .base import BaseOperator, TaskResult, TaskStatus
from ...core.logging_config import get_workflow_logger

logger = get_workflow_logger(__name__)

SUPPORTED_OPERATORS = ("==", "!=", ">", "<", ">=", "<=", "contains", "not_contains", "startswith", "endswith", "matches")


def _resolve_path(context: Dict[str, Any], path: str) -> Any:
    """Resolve a dot-path against the workflow context. Returns None if not found."""
    parts = path.split(".")
    current = context
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _evaluate_operator(left: Any, operator: str, right: Any) -> bool:
    """Evaluate a single comparison between two values."""
    try:
        if operator == "==":
            return str(left).strip() == str(right).strip()
        elif operator == "!=":
            return str(left).strip() != str(right).strip()
        elif operator == ">":
            return float(left) > float(right)
        elif operator == "<":
            return float(left) < float(right)
        elif operator == ">=":
            return float(left) >= float(right)
        elif operator == "<=":
            return float(left) <= float(right)
        elif operator == "contains":
            if isinstance(left, list):
                return right in left
            return str(right) in str(left)
        elif operator == "not_contains":
            if isinstance(left, list):
                return right not in left
            return str(right) not in str(left)
        elif operator == "startswith":
            return str(left).startswith(str(right))
        elif operator == "endswith":
            return str(left).endswith(str(right))
        elif operator == "matches":
            return bool(re.match(str(right), str(left)))
    except (TypeError, ValueError):
        return False
    return False


class AssertionOperator(BaseOperator):
    """
    Operator that evaluates a list of assertions on the workflow context
    and asks the user to confirm or override each result.

    Each assertion compares two context values (resolved via dot-path) using
    a configurable operator. The user always reviews the results before
    the workflow continues.

    Usage example::

        AssertionOperator(
            task_id="verify_identity",
            title="Verificación de Datos de Identidad",
            description="Confirme que los datos del formulario coinciden",
            assertions=[
                {
                    "id": "curp_match",
                    "label": "CURP coincide con el sistema",
                    "left_path": "collect_data_data.curp",
                    "right_path": "customer_curp",
                    "operator": "==",
                    "description": "La CURP capturada debe coincidir con la registrada",
                    "critical": False,
                },
            ],
            on_failure="review",
        )
    """

    def __init__(
        self,
        task_id: str,
        assertions: List[Dict[str, Any]],
        title: str = "Verificación de Aserciones",
        description: str = "Revise y confirme los resultados de verificación",
        on_failure: str = "review",
        **kwargs,
    ):
        """
        Args:
            task_id: Unique task identifier.
            assertions: List of assertion dicts, each with keys:
                - id (str): Unique assertion identifier.
                - label (str): Human-readable label.
                - left_path (str): Dot-path in context for the left value.
                - right_path (str): Dot-path in context for the right value.
                - operator (str): Comparison operator.
                - description (str, optional): Extra description shown to user.
                - critical (bool, optional): If True, cannot be overridden on failure.
            title: Title shown in the UI step.
            description: Description shown in the UI step.
            on_failure: "review" (always shows review, default) or "fail" (fails immediately if any assertion fails).
        """
        super().__init__(task_id=task_id, **kwargs)
        self.assertions = assertions
        self.title = title
        self.description = description
        self.on_failure = on_failure

    def execute(self, context: Dict[str, Any]) -> TaskResult:
        """Main execution: evaluate assertions and request user review, or process user decisions."""
        input_key = f"{self.task_id}_input"

        if input_key in context:
            return self._process_user_decisions(context)

        return self._request_user_review(context)

    def _evaluate_all(self, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Evaluate all assertions against the current context."""
        results = []
        for assertion in self.assertions:
            assertion_id = assertion.get("id", "")
            operator = assertion.get("operator", "==")
            left_path = assertion.get("left_path", "")
            right_path = assertion.get("right_path", "")

            left_value = _resolve_path(context, left_path)
            right_value = _resolve_path(context, right_path)

            system_result = _evaluate_operator(left_value, operator, right_value)

            results.append({
                "id": assertion_id,
                "label": assertion.get("label", assertion_id),
                "description": assertion.get("description", ""),
                "left_path": left_path,
                "right_path": right_path,
                "left_value": left_value,
                "right_value": right_value,
                "operator": operator,
                "system_result": system_result,
                "critical": assertion.get("critical", False),
            })

        logger.info(
            "Assertion evaluation complete",
            task_id=self.task_id,
            total=len(results),
            passed=sum(1 for r in results if r["system_result"]),
        )
        return results

    def _request_user_review(self, context: Dict[str, Any]) -> TaskResult:
        """First execution: evaluate and present results for user review."""
        assertion_results = self._evaluate_all(context)

        all_passed = all(r["system_result"] for r in assertion_results)

        # If on_failure == "fail" and there are failures, fail immediately (no review).
        if self.on_failure == "fail" and not all_passed:
            failed = [r["id"] for r in assertion_results if not r["system_result"]]
            logger.warning("Assertions failed with on_failure=fail", failed=failed)
            return TaskResult(
                status=TaskStatus.FAILED,
                data={
                    "error": "One or more assertions failed",
                    "failed_assertions": failed,
                    "assertion_results": assertion_results,
                },
            )

        form_config = {
            "title": self.title,
            "description": self.description,
            "type": "assertion_review",
            "current_step_id": self.task_id,
            "assertions": assertion_results,
            "all_passed": all_passed,
            "summary": {
                "total": len(assertion_results),
                "passed": sum(1 for r in assertion_results if r["system_result"]),
                "failed": sum(1 for r in assertion_results if not r["system_result"]),
            },
        }

        self.state.waiting_for = "assertion_review"

        return TaskResult(
            status=TaskStatus.WAITING,
            data={
                "waiting_for": "assertion_review",
                "form_config": form_config,
            },
        )

    def _process_user_decisions(self, context: Dict[str, Any]) -> TaskResult:
        """Second execution: process user confirm/override decisions."""
        input_data = context.get(f"{self.task_id}_input", {})
        decisions = input_data.get("decisions", {})  # {assertion_id: {decision, comment}}

        # Re-evaluate to get original system results (stateless approach).
        assertion_results = self._evaluate_all(context)

        final_assertions = []
        overrides_count = 0
        blocked_critical = []

        for result in assertion_results:
            assertion_id = result["id"]
            user_decision_data = decisions.get(assertion_id, {})
            user_decision = user_decision_data.get("decision", "confirm")
            user_comment = user_decision_data.get("comment", "")

            is_override = user_decision == "override"
            if is_override:
                overrides_count += 1

            # Critical assertions that failed cannot be overridden.
            if result["critical"] and not result["system_result"] and is_override:
                blocked_critical.append(assertion_id)

            final_result = result["system_result"] or is_override

            final_assertions.append({
                "id": assertion_id,
                "system_result": result["system_result"],
                "left_value": result["left_value"],
                "right_value": result["right_value"],
                "user_decision": user_decision,
                "user_comment": user_comment,
                "final_result": final_result,
                "critical": result["critical"],
            })

        if blocked_critical:
            logger.warning("Critical assertions failed and cannot be overridden", blocked=blocked_critical)
            return TaskResult(
                status=TaskStatus.FAILED,
                data={
                    "error": "Critical assertions failed and cannot be overridden",
                    "blocked_assertions": blocked_critical,
                    f"{self.task_id}_assertions_result": {
                        "all_passed": False,
                        "assertions": final_assertions,
                        "overrides_count": overrides_count,
                        "confirmed_at": datetime.utcnow().isoformat(),
                    },
                },
            )

        all_final_passed = all(a["final_result"] for a in final_assertions)

        assertions_result = {
            "all_passed": all_final_passed,
            "assertions": final_assertions,
            "overrides_count": overrides_count,
            "confirmed_at": datetime.utcnow().isoformat(),
        }

        logger.info(
            "Assertion review completed",
            task_id=self.task_id,
            all_passed=all_final_passed,
            overrides=overrides_count,
        )

        return TaskResult(
            status=TaskStatus.CONTINUE,
            data={f"{self.task_id}_assertions_result": assertions_result},
        )
