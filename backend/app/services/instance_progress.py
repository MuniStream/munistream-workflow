"""Pasos de una instancia, contando solo el camino que el tramite recorrio.

Un flujo con ramas declara mas pasos de los que cualquier tramite llega a
ejecutar: las compuertas (`ShortCircuitOperator`) eligen una rama y la otra queda
sin recorrer. Contar todas las tareas del DAG como denominador hace que un
tramite terminado nunca llegue al cien por cien, y que "3 de 12 pasos" sea falso
para quien eligio persona fisica en un flujo que tambien contempla persona moral.

El calculo estaba resuelto en el endpoint del portal ciudadano y solo ahi, asi
que el administrador veia otro numero para el mismo tramite. Vive aqui para que
los dos lados respondan lo mismo.
"""

from typing import Any, Dict, List, Optional, Set, Tuple

from ..core.i18n import t as translate
from ..workflows.operators.python import ShortCircuitOperator


def ramas_no_recorridas(dag_instance) -> Set[str]:
    """Pasos que quedaron fuera del camino que tomo el tramite.

    Una compuerta que no completo es una rama no tomada. No se puede mirar el
    estado "skipped" y ya: al reconstruir la instancia desde la base, los pasos de
    esa rama vuelven como "pending" —nunca se ejecutaron, no tienen registro— asi
    que se propaga desde las compuertas hacia abajo.

    Los pasos donde las ramas vuelven a juntarse sobreviven, porque tienen tambien
    un antecesor completado: solo se descarta el paso cuyos antecesores estan
    *todos* muertos.
    """
    if not dag_instance or not getattr(dag_instance, "dag", None):
        return set()

    def estado(tid: str) -> str:
        return dag_instance.task_states.get(tid, {}).get("status", "pending")

    muertos = {
        tid
        for tid, task in dag_instance.dag.tasks.items()
        if isinstance(task, ShortCircuitOperator) and estado(tid) != "completed"
    }

    cambio = True
    while cambio:
        cambio = False
        for tid in dag_instance.dag.tasks:
            if tid in muertos or estado(tid) == "completed":
                continue
            antecesores = list(dag_instance.dag.graph.predecessors(tid))
            if antecesores and all(a in muertos for a in antecesores):
                muertos.add(tid)
                cambio = True

    return muertos


def pasos_recorridos(
    dag_instance, *, ocultar_internos: bool, locale: str = "es"
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Los pasos del camino recorrido, y la etiqueta de la rama que se tomo.

    ``ocultar_internos`` quita los pasos marcados `visible=False` —la validacion
    administrativa que lanza un sub-tramite, por ejemplo—. El ciudadano no los
    tiene que ver; el administrador si, que es su trabajo.

    Las compuertas nunca aparecen en ninguno de los dos: no son un paso del
    tramite sino la bifurcacion misma. De la que se ejecuto sale el nombre de la
    rama, que es lo unico que interesa de ella.
    """
    if not dag_instance:
        return [], None

    muertos = ramas_no_recorridas(dag_instance)
    pasos: List[Dict[str, Any]] = []
    rama: Optional[str] = None

    for task_id, state in dag_instance.task_states.items():
        estado = state.get("status", "pending")
        task = dag_instance.dag.tasks.get(task_id) if getattr(dag_instance, "dag", None) else None

        if ocultar_internos and task is not None and not getattr(task, "visible", True):
            continue

        if isinstance(task, ShortCircuitOperator):
            if estado == "completed" and rama is None:
                nombre = getattr(task, "name", None) or ""
                rama = nombre.replace("Rama ", "").strip() or None
            continue

        if estado == "skipped" or task_id in muertos:
            continue

        nombre = getattr(task, "name", None)
        if not nombre:
            clave = f"steps.{task_id}"
            traducido = translate(clave, locale=locale)
            nombre = traducido if traducido != clave else task_id.replace("_", " ").title()

        paso = {
            "step_id": task_id,
            "name": nombre,
            "description": f"Step {task_id}",
            "status": estado,
            "started_at": state.get("started_at"),
            "completed_at": state.get("completed_at"),
        }
        grupo = getattr(task, "group", None)
        if grupo:
            paso["group"] = grupo
        pasos.append(paso)

    return pasos, rama


def avance(pasos: List[Dict[str, Any]]) -> Tuple[int, int, float]:
    """Completados, total y porcentaje sobre los pasos recorridos."""
    total = len(pasos)
    completados = sum(1 for p in pasos if p.get("status") == "completed")
    return completados, total, (completados / total * 100) if total else 0.0
