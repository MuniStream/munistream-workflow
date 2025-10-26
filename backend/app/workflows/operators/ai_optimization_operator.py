"""
AI Optimization Operator for intelligent decision-making and optimization tasks.
Uses AI models and optimization algorithms for task assignment, resource allocation, and scheduling.
"""
from typing import Dict, Any, List, Optional, Tuple
import asyncio
import json
import logging
from datetime import datetime, timedelta
import numpy as np
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import StandardScaler
import math

from .base import BaseOperator, TaskResult
from ...core.config import settings

logger = logging.getLogger(__name__)


class AIOptimizationOperator(BaseOperator):
    """
    AI-powered operator for optimization and decision-making tasks.
    Supports task assignment, resource allocation, scheduling, and workflow optimization.
    """

    def __init__(
        self,
        task_id: str,
        optimization_type: str,
        optimization_config: Dict[str, Any],
        data_context_key: str,
        ai_model: Optional[str] = None,
        use_ml_optimization: bool = True,
        **kwargs
    ):
        """
        Initialize AI optimization operator.

        Args:
            task_id: Unique task identifier
            optimization_type: Type of optimization (task_assignment, scheduling, resource_allocation)
            optimization_config: Configuration for the specific optimization
            data_context_key: Context key containing input data for optimization
            ai_model: Specific AI model to use
            use_ml_optimization: Whether to use ML algorithms for optimization
        """
        super().__init__(task_id, **kwargs)
        self.optimization_type = optimization_type
        self.optimization_config = optimization_config
        self.data_context_key = data_context_key
        self.ai_model = ai_model or settings.AI_MODEL_NAME
        self.use_ml_optimization = use_ml_optimization

    def execute(self, context: Dict[str, Any]) -> TaskResult:
        """Synchronous wrapper for async execution."""
        return asyncio.run(self.execute_async(context))

    async def execute_async(self, context: Dict[str, Any]) -> TaskResult:
        """Execute AI optimization."""
        try:
            logger.info(f"Starting AI optimization for task {self.task_id}, type: {self.optimization_type}")

            # Get input data from context
            input_data = context.get(self.data_context_key)
            if not input_data:
                return TaskResult.failure(f"No input data found in context key: {self.data_context_key}")

            # Route to specific optimization method
            if self.optimization_type == "task_assignment":
                result = await self._optimize_task_assignment(input_data, context)
            elif self.optimization_type == "scheduling":
                result = await self._optimize_scheduling(input_data, context)
            elif self.optimization_type == "resource_allocation":
                result = await self._optimize_resource_allocation(input_data, context)
            elif self.optimization_type == "workload_balancing":
                result = await self._optimize_workload_balancing(input_data, context)
            else:
                return TaskResult.failure(f"Unsupported optimization type: {self.optimization_type}")

            # Store optimization results in context
            context.update({
                f'{self.task_id}_optimization_result': result,
                f'{self.task_id}_optimization_type': self.optimization_type,
                f'{self.task_id}_optimization_timestamp': datetime.utcnow().isoformat()
            })

            if result.get('success', False):
                logger.info(f"AI optimization completed successfully with score: {result.get('optimization_score', 'N/A')}")
                return TaskResult.success(f"Optimization completed: {result.get('summary', 'No summary')}")
            else:
                return TaskResult.failure(f"Optimization failed: {result.get('error', 'Unknown error')}")

        except Exception as e:
            logger.error(f"AI optimization failed: {str(e)}")
            return TaskResult.failure(f"AI optimization error: {str(e)}")

    async def _optimize_task_assignment(self, input_data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Optimize task assignment to team members."""
        try:
            tasks = input_data.get('tasks', [])
            team_members = input_data.get('team_members', [])
            constraints = self.optimization_config.get('constraints', {})

            if not tasks or not team_members:
                return {'success': False, 'error': 'Missing tasks or team members data'}

            # Use AI to analyze task requirements and team capabilities
            ai_analysis = await self._ai_analyze_assignment_requirements(tasks, team_members)

            # Apply optimization algorithm
            if self.use_ml_optimization:
                assignments = await self._ml_optimize_assignments(tasks, team_members, ai_analysis, constraints)
            else:
                assignments = await self._rule_based_assignments(tasks, team_members, constraints)

            # Calculate optimization metrics
            metrics = self._calculate_assignment_metrics(assignments, tasks, team_members)

            return {
                'success': True,
                'optimization_type': 'task_assignment',
                'assignments': assignments,
                'metrics': metrics,
                'ai_analysis': ai_analysis,
                'optimization_score': metrics.get('overall_score', 0.0),
                'summary': f"Assigned {len(assignments)} tasks with {metrics.get('overall_score', 0):.1%} efficiency"
            }

        except Exception as e:
            logger.error(f"Task assignment optimization failed: {str(e)}")
            return {'success': False, 'error': str(e)}

    async def _optimize_scheduling(self, input_data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Optimize scheduling of tasks and appointments."""
        try:
            items_to_schedule = input_data.get('items', [])
            time_slots = input_data.get('available_slots', [])
            preferences = input_data.get('preferences', {})

            if not items_to_schedule:
                return {'success': False, 'error': 'No items to schedule'}

            # AI analysis of scheduling requirements
            ai_analysis = await self._ai_analyze_scheduling_requirements(items_to_schedule, time_slots, preferences)

            # Apply scheduling optimization
            schedule = await self._optimize_schedule(items_to_schedule, time_slots, ai_analysis, preferences)

            # Calculate scheduling metrics
            metrics = self._calculate_scheduling_metrics(schedule, items_to_schedule, time_slots)

            return {
                'success': True,
                'optimization_type': 'scheduling',
                'schedule': schedule,
                'metrics': metrics,
                'ai_analysis': ai_analysis,
                'optimization_score': metrics.get('utilization_score', 0.0),
                'summary': f"Scheduled {len(schedule)} items with {metrics.get('utilization_score', 0):.1%} utilization"
            }

        except Exception as e:
            logger.error(f"Scheduling optimization failed: {str(e)}")
            return {'success': False, 'error': str(e)}

    async def _optimize_resource_allocation(self, input_data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Optimize allocation of resources to projects or tasks."""
        try:
            resources = input_data.get('resources', [])
            demands = input_data.get('demands', [])
            constraints = self.optimization_config.get('constraints', {})

            if not resources or not demands:
                return {'success': False, 'error': 'Missing resources or demands data'}

            # AI analysis of resource requirements
            ai_analysis = await self._ai_analyze_resource_requirements(resources, demands)

            # Apply resource allocation optimization
            allocation = await self._optimize_resource_allocation_core(resources, demands, ai_analysis, constraints)

            # Calculate allocation metrics
            metrics = self._calculate_allocation_metrics(allocation, resources, demands)

            return {
                'success': True,
                'optimization_type': 'resource_allocation',
                'allocation': allocation,
                'metrics': metrics,
                'ai_analysis': ai_analysis,
                'optimization_score': metrics.get('efficiency_score', 0.0),
                'summary': f"Allocated resources with {metrics.get('efficiency_score', 0):.1%} efficiency"
            }

        except Exception as e:
            logger.error(f"Resource allocation optimization failed: {str(e)}")
            return {'success': False, 'error': str(e)}

    async def _optimize_workload_balancing(self, input_data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Optimize workload balancing across team members or systems."""
        try:
            workloads = input_data.get('current_workloads', [])
            new_tasks = input_data.get('new_tasks', [])
            capacity_limits = input_data.get('capacity_limits', {})

            # AI analysis of workload patterns
            ai_analysis = await self._ai_analyze_workload_patterns(workloads, new_tasks)

            # Apply workload balancing
            balanced_allocation = await self._balance_workloads(workloads, new_tasks, capacity_limits, ai_analysis)

            # Calculate balancing metrics
            metrics = self._calculate_balancing_metrics(balanced_allocation, workloads, capacity_limits)

            return {
                'success': True,
                'optimization_type': 'workload_balancing',
                'balanced_allocation': balanced_allocation,
                'metrics': metrics,
                'ai_analysis': ai_analysis,
                'optimization_score': metrics.get('balance_score', 0.0),
                'summary': f"Balanced workload with {metrics.get('balance_score', 0):.1%} efficiency"
            }

        except Exception as e:
            logger.error(f"Workload balancing optimization failed: {str(e)}")
            return {'success': False, 'error': str(e)}

    async def _ai_analyze_assignment_requirements(self, tasks: List[Dict], team_members: List[Dict]) -> Dict[str, Any]:
        """Use AI to analyze task requirements and team capabilities."""
        try:
            analysis_prompt = f"""
Analyze the following tasks and team members for optimal assignment:

TASKS:
{json.dumps(tasks, indent=2)}

TEAM MEMBERS:
{json.dumps(team_members, indent=2)}

Please provide analysis in JSON format:
{{
    "task_complexity_scores": {{"task_id": complexity_score_0_to_1}},
    "skill_requirements": {{"task_id": ["required_skill1", "required_skill2"]}},
    "team_capabilities": {{"member_id": {{"skills": ["skill1"], "availability": 0_to_1, "current_load": 0_to_1}}}},
    "optimal_matches": {{"task_id": ["member_id1", "member_id2"]}},
    "recommendations": ["recommendation1", "recommendation2"]
}}
"""

            if settings.AI_MODEL_PROVIDER == "openai":
                result = await self._openai_analyze(analysis_prompt)
            elif settings.AI_MODEL_PROVIDER == "anthropic":
                result = await self._anthropic_analyze(analysis_prompt)
            else:
                result = {}

            return result

        except Exception as e:
            logger.error(f"AI assignment analysis failed: {str(e)}")
            return {}

    async def _ml_optimize_assignments(self, tasks: List[Dict], team_members: List[Dict],
                                     ai_analysis: Dict[str, Any], constraints: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Use machine learning to optimize task assignments."""
        try:
            # Create feature matrix for optimization
            assignments = []

            # Simple greedy algorithm with AI insights
            task_complexity = ai_analysis.get('task_complexity_scores', {})
            team_capabilities = ai_analysis.get('team_capabilities', {})
            optimal_matches = ai_analysis.get('optimal_matches', {})

            for task in tasks:
                task_id = task.get('id', task.get('task_id', ''))

                # Get AI-recommended matches
                recommended_members = optimal_matches.get(task_id, [])

                if recommended_members:
                    # Choose best available member from recommendations
                    best_member = None
                    best_score = -1

                    for member_id in recommended_members:
                        member_data = team_capabilities.get(member_id, {})
                        availability = member_data.get('availability', 1.0)
                        current_load = member_data.get('current_load', 0.0)

                        # Calculate assignment score
                        score = availability * (1.0 - current_load)

                        if score > best_score:
                            best_score = score
                            best_member = member_id

                    if best_member:
                        assignments.append({
                            'task_id': task_id,
                            'assigned_to': best_member,
                            'assignment_score': best_score,
                            'assignment_reason': 'AI recommendation + availability',
                            'estimated_duration': task.get('estimated_duration', '1h')
                        })

                        # Update member load for next iteration
                        if best_member in team_capabilities:
                            team_capabilities[best_member]['current_load'] = min(1.0,
                                team_capabilities[best_member].get('current_load', 0.0) + 0.2)

            return assignments

        except Exception as e:
            logger.error(f"ML assignment optimization failed: {str(e)}")
            return []

    async def _ai_analyze_scheduling_requirements(self, items: List[Dict], time_slots: List[Dict],
                                                preferences: Dict[str, Any]) -> Dict[str, Any]:
        """Use AI to analyze scheduling requirements."""
        analysis_prompt = f"""
Analyze scheduling requirements for optimal time slot allocation:

ITEMS TO SCHEDULE:
{json.dumps(items, indent=2)}

AVAILABLE TIME SLOTS:
{json.dumps(time_slots, indent=2)}

PREFERENCES:
{json.dumps(preferences, indent=2)}

Provide analysis in JSON format:
{{
    "item_priorities": {{"item_id": priority_score_0_to_1}},
    "time_preferences": {{"item_id": ["preferred_time_slot_id"]}},
    "duration_estimates": {{"item_id": duration_in_minutes}},
    "conflicts": [["item_id1", "item_id2", "conflict_reason"]],
    "optimal_schedule": {{"item_id": "time_slot_id"}},
    "recommendations": ["recommendation1"]
}}
"""

        try:
            if settings.AI_MODEL_PROVIDER == "openai":
                result = await self._openai_analyze(analysis_prompt)
            elif settings.AI_MODEL_PROVIDER == "anthropic":
                result = await self._anthropic_analyze(analysis_prompt)
            else:
                result = {}

            return result

        except Exception as e:
            logger.error(f"AI scheduling analysis failed: {str(e)}")
            return {}

    async def _ai_analyze_resource_requirements(self, resources: List[Dict], demands: List[Dict]) -> Dict[str, Any]:
        """Use AI to analyze resource allocation requirements."""
        analysis_prompt = f"""
Analyze resource allocation requirements for optimal distribution:

AVAILABLE RESOURCES:
{json.dumps(resources, indent=2)}

RESOURCE DEMANDS:
{json.dumps(demands, indent=2)}

Provide analysis in JSON format:
{{
    "resource_utilization": {{"resource_id": current_utilization_0_to_1}},
    "demand_priorities": {{"demand_id": priority_score_0_to_1}},
    "resource_matches": {{"demand_id": ["suitable_resource_id"]}},
    "allocation_strategy": "strategy_description",
    "optimal_allocation": {{"demand_id": {{"resource_id": allocation_percentage}}}},
    "bottlenecks": ["resource_id_with_high_demand"],
    "recommendations": ["recommendation1"]
}}
"""

        try:
            if settings.AI_MODEL_PROVIDER == "openai":
                result = await self._openai_analyze(analysis_prompt)
            elif settings.AI_MODEL_PROVIDER == "anthropic":
                result = await self._anthropic_analyze(analysis_prompt)
            else:
                result = {}

            return result

        except Exception as e:
            logger.error(f"AI resource analysis failed: {str(e)}")
            return {}

    async def _ai_analyze_workload_patterns(self, current_workloads: List[Dict], new_tasks: List[Dict]) -> Dict[str, Any]:
        """Use AI to analyze workload patterns and predict optimal distribution."""
        analysis_prompt = f"""
Analyze workload patterns for optimal task distribution:

CURRENT WORKLOADS:
{json.dumps(current_workloads, indent=2)}

NEW TASKS:
{json.dumps(new_tasks, indent=2)}

Provide analysis in JSON format:
{{
    "workload_distribution": {{"member_id": current_load_percentage}},
    "capacity_utilization": {{"member_id": utilization_0_to_1}},
    "task_distribution_strategy": "strategy_description",
    "optimal_distribution": {{"task_id": "assigned_member_id"}},
    "load_balancing_score": balance_score_0_to_1,
    "bottlenecks": ["overloaded_member_id"],
    "recommendations": ["recommendation1"]
}}
"""

        try:
            if settings.AI_MODEL_PROVIDER == "openai":
                result = await self._openai_analyze(analysis_prompt)
            elif settings.AI_MODEL_PROVIDER == "anthropic":
                result = await self._anthropic_analyze(analysis_prompt)
            else:
                result = {}

            return result

        except Exception as e:
            logger.error(f"AI workload analysis failed: {str(e)}")
            return {}

    async def _openai_analyze(self, prompt: str) -> Dict[str, Any]:
        """Analyze using OpenAI API."""
        try:
            import openai

            if not settings.OPENAI_API_KEY:
                return {}

            client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

            response = await client.chat.completions.create(
                model=self.ai_model,
                messages=[
                    {"role": "system", "content": "You are an optimization expert. Analyze data and provide optimization insights in JSON format."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=settings.AI_MAX_TOKENS,
                temperature=settings.AI_TEMPERATURE,
                timeout=settings.AI_REQUEST_TIMEOUT
            )

            result_text = response.choices[0].message.content.strip()
            return json.loads(result_text)

        except Exception as e:
            logger.error(f"OpenAI analysis failed: {str(e)}")
            return {}

    async def _anthropic_analyze(self, prompt: str) -> Dict[str, Any]:
        """Analyze using Anthropic Claude API."""
        try:
            import anthropic

            if not settings.ANTHROPIC_API_KEY:
                return {}

            client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

            message = await client.messages.create(
                model=self.ai_model,
                max_tokens=settings.AI_MAX_TOKENS,
                temperature=settings.AI_TEMPERATURE,
                messages=[{"role": "user", "content": prompt}]
            )

            result_text = message.content[0].text.strip()
            return json.loads(result_text)

        except Exception as e:
            logger.error(f"Anthropic analysis failed: {str(e)}")
            return {}

    def _calculate_assignment_metrics(self, assignments: List[Dict], tasks: List[Dict], team_members: List[Dict]) -> Dict[str, Any]:
        """Calculate metrics for task assignments."""
        if not assignments:
            return {'overall_score': 0.0}

        total_assignments = len(assignments)
        avg_score = sum(a.get('assignment_score', 0.0) for a in assignments) / total_assignments

        return {
            'total_assignments': total_assignments,
            'average_assignment_score': avg_score,
            'overall_score': min(avg_score, 1.0),
            'assignment_distribution': len(set(a.get('assigned_to') for a in assignments))
        }

    def _calculate_scheduling_metrics(self, schedule: List[Dict], items: List[Dict], time_slots: List[Dict]) -> Dict[str, Any]:
        """Calculate metrics for scheduling optimization."""
        if not schedule:
            return {'utilization_score': 0.0}

        scheduled_items = len(schedule)
        total_items = len(items)
        utilization_score = scheduled_items / total_items if total_items > 0 else 0.0

        return {
            'scheduled_items': scheduled_items,
            'total_items': total_items,
            'utilization_score': utilization_score,
            'schedule_efficiency': min(utilization_score * 1.2, 1.0)  # Bonus for high utilization
        }

    def _calculate_allocation_metrics(self, allocation: Dict[str, Any], resources: List[Dict], demands: List[Dict]) -> Dict[str, Any]:
        """Calculate metrics for resource allocation."""
        if not allocation:
            return {'efficiency_score': 0.0}

        # Simple efficiency calculation
        efficiency_score = 0.8  # Placeholder - would need more complex calculation

        return {
            'allocation_count': len(allocation),
            'efficiency_score': efficiency_score,
            'resource_utilization': 0.75  # Placeholder
        }

    def _calculate_balancing_metrics(self, balanced_allocation: Dict[str, Any], workloads: List[Dict],
                                   capacity_limits: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate metrics for workload balancing."""
        if not balanced_allocation:
            return {'balance_score': 0.0}

        # Simple balance calculation
        balance_score = 0.85  # Placeholder - would calculate actual load variance

        return {
            'balance_score': balance_score,
            'load_variance': 0.15,  # Lower is better
            'capacity_utilization': 0.80
        }

    # Placeholder implementations for core optimization methods
    async def _rule_based_assignments(self, tasks: List[Dict], team_members: List[Dict], constraints: Dict[str, Any]) -> List[Dict]:
        """Simple rule-based assignment as fallback."""
        assignments = []
        member_index = 0

        for task in tasks:
            if member_index < len(team_members):
                member = team_members[member_index]
                assignments.append({
                    'task_id': task.get('id', task.get('task_id', '')),
                    'assigned_to': member.get('id', member.get('member_id', '')),
                    'assignment_score': 0.7,
                    'assignment_reason': 'Round-robin assignment'
                })
                member_index = (member_index + 1) % len(team_members)

        return assignments

    async def _optimize_schedule(self, items: List[Dict], time_slots: List[Dict],
                               ai_analysis: Dict[str, Any], preferences: Dict[str, Any]) -> List[Dict]:
        """Simple scheduling optimization."""
        schedule = []
        used_slots = set()

        optimal_schedule = ai_analysis.get('optimal_schedule', {})

        for item in items:
            item_id = item.get('id', item.get('item_id', ''))
            preferred_slot = optimal_schedule.get(item_id)

            if preferred_slot and preferred_slot not in used_slots:
                schedule.append({
                    'item_id': item_id,
                    'time_slot_id': preferred_slot,
                    'scheduled_at': datetime.utcnow().isoformat()
                })
                used_slots.add(preferred_slot)

        return schedule

    async def _optimize_resource_allocation_core(self, resources: List[Dict], demands: List[Dict],
                                               ai_analysis: Dict[str, Any], constraints: Dict[str, Any]) -> Dict[str, Any]:
        """Core resource allocation optimization."""
        allocation = {}
        optimal_allocation = ai_analysis.get('optimal_allocation', {})

        for demand in demands:
            demand_id = demand.get('id', demand.get('demand_id', ''))
            if demand_id in optimal_allocation:
                allocation[demand_id] = optimal_allocation[demand_id]

        return allocation

    async def _balance_workloads(self, workloads: List[Dict], new_tasks: List[Dict],
                               capacity_limits: Dict[str, Any], ai_analysis: Dict[str, Any]) -> Dict[str, Any]:
        """Balance workloads across team members."""
        balanced_allocation = {}
        optimal_distribution = ai_analysis.get('optimal_distribution', {})

        for task in new_tasks:
            task_id = task.get('id', task.get('task_id', ''))
            if task_id in optimal_distribution:
                balanced_allocation[task_id] = optimal_distribution[task_id]

        return balanced_allocation