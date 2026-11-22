"""
Escalation of reviews that outlive their deadline.

A `WorkflowStartOperator` that waits on a child (usually the administrative
validation a reviewer has to resolve) used to fail the citizen's request when
`timeout_minutes` elapsed: the citizen lost the request because staff did not
get to it in time. Now the request keeps waiting and the review is escalated
once per elapsed period, up to `max_escalations`:

- the child jumps to the top priority and records the escalation in its
  assignment history (`escalation_level`, `escalated_at`), without touching
  `assignment_status`, so the reviewer can keep working it;
- staff are emailed: the assigned reviewer, the members of the assigned team,
  the members of the `escalate_to` team when the step declares one, and the
  tenant's report recipients when traffic-light alerts are enabled;
- on the first escalation the citizen is told the request is delayed
  (`EventType.REVIEW_OVERDUE`, system notification `revision_demorada`).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

TOP_PRIORITY = 10


def period_label(minutes: int) -> str:
    """'24 horas', '2 días', '1.5 horas': the deadline as staff and citizens read it."""
    hours = minutes / 60
    if hours < 48 or hours % 24:
        return f"{hours:g} horas"
    return f"{hours / 24:g} días"


def timeout_failure_message(step: str, minutes: int) -> str:
    """Reason shown to the citizen when a step declared `on_timeout="fail"` expires."""
    return (f"«{step}» no se completó en el plazo de {period_label(minutes)}. El trámite se cerró sin "
            "resolverse; puedes iniciarlo de nuevo.")


def due_escalation_level(elapsed_minutes: float, timeout_minutes: int, current_level: int,
                         max_escalations: int) -> Optional[int]:
    """The level to escalate to now, or None when nothing is due.

    One level per whole elapsed period: after 1x the deadline it is level 1,
    after 2x level 2, and so on up to `max_escalations`. A level already
    reached is never notified again, even if polls are delayed.
    """
    if timeout_minutes <= 0 or elapsed_minutes <= timeout_minutes:
        return None
    level = min(int(elapsed_minutes // timeout_minutes), max_escalations)
    return level if level > current_level else None


def _dedupe(values: Iterable[Optional[str]]) -> List[str]:
    out: List[str] = []
    for value in values:
        value = (value or "").strip().lower()
        if value and "@" in value and value not in out:
            out.append(value)
    return out


async def _team_members(team_id: Optional[str]) -> List[Dict[str, Any]]:
    """Reviewers in the team's Keycloak group, plus the active members of the
    team stored in Mongo (the team administration screen edits those)."""
    if not team_id:
        return []
    members: List[Dict[str, Any]] = []
    try:
        from .user_teams import team_members

        members += await team_members(team_id) or []
    except Exception as exc:  # noqa: BLE001 - recipients are best effort
        logger.warning("could not list Keycloak members of team %s: %s", team_id, exc)
    try:
        from bson import ObjectId

        from ..models.team import TeamModel
        from ..models.user import UserModel

        team = await TeamModel.find_one(TeamModel.team_id == team_id)
        ids = [m.user_id for m in (getattr(team, "members", None) or []) if m.is_active]
        oids = [ObjectId(i) for i in ids if ObjectId.is_valid(i)]
        if oids:
            for user in await UserModel.find({"_id": {"$in": oids}}).to_list():
                members.append({"id": str(user.id), "email": user.email})
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not list stored members of team %s: %s", team_id, exc)
    return members


async def _user_email(user_id: Optional[str], known_members: List[Dict[str, Any]]) -> Optional[str]:
    """Assigned users are stored as Keycloak ids (auto-assignment) or as local
    user ids (manual assignment); both are resolved."""
    if not user_id:
        return None
    for member in known_members:
        if user_id in (member.get("id"), member.get("username"), member.get("email")):
            return member.get("email")
    if "@" in user_id:
        return user_id
    try:
        from bson import ObjectId

        from ..models.user import UserModel

        if ObjectId.is_valid(user_id):
            user = await UserModel.get(ObjectId(user_id))
            if user:
                return user.email
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not resolve email of user %s: %s", user_id, exc)
    return None


def _procedure_name(workflow_id: str) -> str:
    try:
        from .workflow_service import workflow_service

        dag = workflow_service.dag_bag.get_dag(workflow_id)
        return (dag.name if dag else None) or workflow_id
    except Exception:  # noqa: BLE001
        return workflow_id


async def staff_recipients(child, escalate_to_team: Optional[str]) -> List[str]:
    from .reportes import configuracion

    team = await _team_members(child.assigned_team_id)
    supervisors = await _team_members(escalate_to_team) if escalate_to_team != child.assigned_team_id else []
    assigned = await _user_email(child.assigned_user_id, team + supervisors)
    report_recipients: List[str] = []
    try:
        cfg = await configuracion()
        if cfg.alertas:
            report_recipients = list(cfg.destinatarios)
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not read report settings: %s", exc)
    return _dedupe([assigned]
                   + [m.get("email") for m in team]
                   + [m.get("email") for m in supervisors]
                   + report_recipients)


def staff_email(*, procedure: str, step: str, folio: str, level: int, max_escalations: int,
                timeout_minutes: int, elapsed_minutes: float, assigned_to: Optional[str],
                link: str) -> Dict[str, str]:
    waited = period_label(int(elapsed_minutes))
    subject = f"Revisión vencida ({level}.º aviso): {procedure}, folio {folio}"
    lines = [
        f"La revisión «{step}» del trámite «{procedure}» (folio {folio}) lleva {waited} sin resolverse; "
        f"su plazo es de {period_label(timeout_minutes)}.",
        "",
        "El trámite del ciudadano sigue abierto esperando la revisión. Se le dio la prioridad más alta "
        "en la bandeja y se le avisó al ciudadano que está demorado.",
        "",
        f"Asignada a: {assigned_to or 'nadie todavía'}.",
    ]
    if link:
        lines += ["", f"Abrir la revisión: {link}"]
    if level >= max_escalations:
        lines += ["", "Este es el último aviso automático; el trámite seguirá marcado como escalado."]
    return {"subject": subject, "body": "\n".join(lines) + "\n"}


async def escalate_overdue_review(
    *,
    child,
    parent_context: Dict[str, Any],
    step_id: str,
    step_name: str,
    level: int,
    max_escalations: int,
    timeout_minutes: int,
    elapsed_minutes: float,
    escalate_to_team: Optional[str] = None,
) -> Dict[str, Any]:
    """Escalate `child` to `level`, email staff and, on the first level, tell
    the citizen. Never raises: a failed notice must not break the poll."""
    from .reportes import _admin_base_url, _enviar_correo

    reason = (f"Plazo de {period_label(timeout_minutes)} vencido en «{step_name}» "
              f"({level}.º aviso automático)")
    child.mark_overdue(level=level, reason=reason, priority=TOP_PRIORITY)
    await child.save()

    sent: List[Dict[str, Any]] = []
    try:
        recipients = await staff_recipients(child, escalate_to_team)
        base_url = _admin_base_url()
        email = staff_email(
            procedure=_procedure_name(parent_context.get("workflow_id") or child.workflow_id),
            step=step_name,
            # Same short reference the citizen sees in "My requests".
            folio=(parent_context.get("instance_id") or child.instance_id)[:8],
            level=level,
            max_escalations=max_escalations,
            timeout_minutes=timeout_minutes,
            elapsed_minutes=elapsed_minutes,
            assigned_to=child.assigned_user_id or child.assigned_team_id,
            link=f"{base_url}/instances/{child.instance_id}" if base_url else "",
        )
        sent = await _enviar_correo(recipients, email["subject"], email["body"])
        if not recipients:
            logger.warning("overdue review %s escalated with no staff email to notify", child.instance_id)
    except Exception:
        logger.exception("could not email staff about overdue review %s", child.instance_id)

    citizen_notified = False
    if level == 1 and parent_context.get("instance_id"):
        try:
            from ..models.workflow import EventType
            from .workflow_service import workflow_service

            await workflow_service.executor.event_manager.publish_event(
                event_type=EventType.REVIEW_OVERDUE,
                workflow_id=parent_context.get("workflow_id"),
                instance_id=parent_context.get("instance_id"),
                user_id=parent_context.get("user_id"),
                event_data={
                    "step_id": step_id,
                    "review_instance_id": child.instance_id,
                    "escalation_level": level,
                    "plazo": period_label(timeout_minutes),
                },
            )
            citizen_notified = True
        except Exception:
            logger.exception("could not publish REVIEW_OVERDUE for %s", parent_context.get("instance_id"))

    return {
        "escalation_level": level,
        "escalated_at": datetime.utcnow().isoformat(),
        "staff_notified": [s["destinatario"] for s in sent if s.get("enviado")],
        "citizen_notified": citizen_notified,
    }
