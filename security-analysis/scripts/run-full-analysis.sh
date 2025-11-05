#!/bin/bash

################################################################################
# SCRIPT MAESTRO - ANÁLISIS COMPLETO DE SEGURIDAD
# PAW AI S.A.S. DE C.V. - PEMC 2025
# Ejecuta todo el pipeline de análisis SAST + DAST
################################################################################

set -e

# Colores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
NC='\033[0m'

# Configuración
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Tiempo de inicio
START_TIME=$(date +%s)

# Logging
log() {
    echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[✓]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[⚠]${NC} $1"
}

log_error() {
    echo -e "${RED}[✗]${NC} $1"
}

log_section() {
    echo ""
    echo -e "${CYAN}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║${NC} $1"
    echo -e "${CYAN}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

# Función para calcular duración
calculate_duration() {
    local start=$1
    local end=$(date +%s)
    local duration=$((end - start))
    local minutes=$((duration / 60))
    local seconds=$((duration % 60))
    echo "${minutes}m ${seconds}s"
}

# Banner inicial
show_banner() {
    echo ""
    echo -e "${MAGENTA}╔════════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${MAGENTA}║                                                                    ║${NC}"
    echo -e "${MAGENTA}║        🔒 ANÁLISIS COMPLETO DE VULNERABILIDADES DE SEGURIDAD       ║${NC}"
    echo -e "${MAGENTA}║                                                                    ║${NC}"
    echo -e "${MAGENTA}║            Proyecto: PEMC 2025 - Modernización Catastral           ║${NC}"
    echo -e "${MAGENTA}║                Organización: PAW AI S.A.S. DE C.V.                 ║${NC}"
    echo -e "${MAGENTA}║                                                                    ║${NC}"
    echo -e "${MAGENTA}╚════════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

# Verificar configuración
check_configuration() {
    log_section "VERIFICANDO CONFIGURACIÓN"

    if [ ! -f "$PROJECT_ROOT/.env" ]; then
        log_error ".env no encontrado"
        log "Crea el archivo .env basado en .env.example"
        echo ""
        echo "Pasos:"
        echo "  1. cp .env.example .env"
        echo "  2. Edita .env con tus valores"
        echo "  3. Ejecuta este script nuevamente"
        echo ""
        exit 1
    fi

    log_success "Archivo .env encontrado"

    # Cargar variables
    export $(cat "$PROJECT_ROOT/.env" | grep -v '^#' | xargs)

    # Verificar variables críticas
    if [ -z "$MUNISTREAM_REPO_URL" ] && [ -z "$PUENTECATASTRAL_REPO_URL" ]; then
        log_warning "No se configuraron URLs de repositorios"
        log_warning "El análisis se ejecutará sobre el código local si está disponible"
    fi

    log_success "Configuración verificada"
}

# Verificar herramientas
check_tools() {
    log_section "VERIFICANDO HERRAMIENTAS"

    local missing_tools=()
    local tools=(
        "semgrep:Semgrep"
        "trivy:Trivy"
        "gitleaks:Gitleaks"
        "python3:Python 3"
        "jq:jq"
    )

    for tool_info in "${tools[@]}"; do
        IFS=':' read -r cmd name <<< "$tool_info"

        if command -v "$cmd" &>/dev/null; then
            log_success "$name instalado"
        else
            log_warning "$name no encontrado"
            missing_tools+=("$name")
        fi
    done

    if [ ${#missing_tools[@]} -gt 0 ]; then
        log_warning "Faltan herramientas: ${missing_tools[*]}"
        log "Ejecutando instalador automático..."
        echo ""

        if [ -f "$PROJECT_ROOT/setup/install-tools.sh" ]; then
            "$PROJECT_ROOT/setup/install-tools.sh"
        else
            log_error "No se encontró install-tools.sh"
            exit 1
        fi
    else
        log_success "Todas las herramientas necesarias están instaladas"
    fi
}

# Ejecutar SAST
run_sast() {
    log_section "FASE 1: ANÁLISIS ESTÁTICO (SAST)"

    local sast_start=$(date +%s)

    if [ -f "$SCRIPT_DIR/run-sast-full.sh" ]; then
        log "Ejecutando análisis SAST..."
        echo ""

        if "$SCRIPT_DIR/run-sast-full.sh"; then
            log_success "Análisis SAST completado"
        else
            log_error "Error en análisis SAST"
            log_warning "Continuando con siguiente fase..."
        fi
    else
        log_error "Script run-sast-full.sh no encontrado"
        return 1
    fi

    local duration=$(calculate_duration $sast_start)
    log "Duración SAST: $duration"
}

# Ejecutar DAST
run_dast() {
    log_section "FASE 2: ANÁLISIS DINÁMICO (DAST)"

    local dast_start=$(date +%s)

    if [ -f "$SCRIPT_DIR/run-dast-full.sh" ]; then
        log "Ejecutando análisis DAST..."
        echo ""

        if "$SCRIPT_DIR/run-dast-full.sh"; then
            log_success "Análisis DAST completado"
        else
            log_warning "Análisis DAST completado con advertencias"
        fi
    else
        log_error "Script run-dast-full.sh no encontrado"
        return 1
    fi

    local duration=$(calculate_duration $dast_start)
    log "Duración DAST: $duration"
}

# Consolidar reportes
consolidate_reports() {
    log_section "FASE 3: CONSOLIDACIÓN DE REPORTES"

    local consolidate_start=$(date +%s)

    if [ -f "$SCRIPT_DIR/consolidate-reports.sh" ]; then
        log "Consolidando reportes..."
        echo ""

        if "$SCRIPT_DIR/consolidate-reports.sh"; then
            log_success "Reportes consolidados"
        else
            log_error "Error en consolidación"
            return 1
        fi
    else
        log_error "Script consolidate-reports.sh no encontrado"
        return 1
    fi

    local duration=$(calculate_duration $consolidate_start)
    log "Duración consolidación: $duration"
}

# Generar reporte final
generate_report() {
    log_section "FASE 4: GENERACIÓN DE REPORTE FINAL"

    local report_start=$(date +%s)

    if [ -f "$SCRIPT_DIR/generate-markdown.py" ]; then
        log "Generando reporte en Markdown..."
        echo ""

        if python3 "$SCRIPT_DIR/generate-markdown.py"; then
            log_success "Reporte Markdown generado"
        else
            log_error "Error generando reporte"
            return 1
        fi
    else
        log_error "Script generate-markdown.py no encontrado"
        return 1
    fi

    local duration=$(calculate_duration $report_start)
    log "Duración generación: $duration"
}

# Mostrar resumen final
show_summary() {
    local total_duration=$(calculate_duration $START_TIME)

    log_section "RESUMEN DEL ANÁLISIS"

    echo -e "${GREEN}╔════════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║                   ✅ ANÁLISIS COMPLETADO                            ║${NC}"
    echo -e "${GREEN}╚════════════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    echo "📊 Estadísticas:"
    echo "   ⏱️  Duración total: $total_duration"
    echo "   📁 Directorio de reportes: $PROJECT_ROOT/reports/"
    echo ""

    # Buscar reporte final
    local latest_report="$PROJECT_ROOT/reports/consolidated/security_report_latest.md"

    if [ -f "$latest_report" ]; then
        echo "📄 Reporte final disponible en:"
        echo "   $latest_report"
        echo ""

        # Mostrar primeras líneas del resumen
        log "Vista previa del reporte:"
        echo ""
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        head -n 20 "$latest_report"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo ""

        echo "Para ver el reporte completo:"
        echo "  cat $latest_report"
        echo ""
        echo "O abrirlo en tu editor favorito:"
        echo "  code $latest_report"
        echo "  vim $latest_report"
    else
        log_warning "No se encontró el reporte final"
    fi

    echo ""
    echo "Próximos pasos recomendados:"
    echo "  1. Revisar el reporte de seguridad"
    echo "  2. Priorizar vulnerabilidades críticas y altas"
    echo "  3. Implementar plan de remediación"
    echo "  4. Re-ejecutar análisis después de correcciones"
    echo ""

    # Verificar si hay alertas críticas
    local consolidated_dir="$PROJECT_ROOT/reports/consolidated"

    if [ -d "$consolidated_dir" ]; then
        local gitleaks_file=$(ls -t "$consolidated_dir"/gitleaks_consolidated.json 2>/dev/null | head -1)

        if [ -f "$gitleaks_file" ]; then
            local secrets_count=$(jq '.total_secrets' "$gitleaks_file" 2>/dev/null || echo "0")

            if [ "$secrets_count" -gt 0 ]; then
                echo -e "${RED}⚠️  ALERTA CRÍTICA: $secrets_count secretos expuestos encontrados!${NC}"
                echo "   Revisa el reporte inmediatamente y rota las credenciales."
                echo ""
            fi
        fi
    fi

    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}                  Análisis de seguridad finalizado                  ${NC}"
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
}

# Manejo de errores
handle_error() {
    log_error "El script falló en la línea $1"
    echo ""
    echo "Para debugging, ejecuta las fases manualmente:"
    echo "  ./scripts/run-sast-full.sh"
    echo "  ./scripts/run-dast-full.sh"
    echo "  ./scripts/consolidate-reports.sh"
    echo "  python3 ./scripts/generate-markdown.py"
    echo ""
    exit 1
}

trap 'handle_error $LINENO' ERR

# Main
main() {
    show_banner

    log "Iniciando análisis completo de seguridad..."
    log "Timestamp: $(date +'%Y-%m-%d %H:%M:%S')"
    echo ""

    # Cambiar al directorio del proyecto
    cd "$PROJECT_ROOT"

    # Ejecutar pipeline completo
    check_configuration
    check_tools

    echo ""
    log "Pipeline de análisis:"
    echo "  1️⃣  Análisis Estático (SAST)"
    echo "  2️⃣  Análisis Dinámico (DAST)"
    echo "  3️⃣  Consolidación de Reportes"
    echo "  4️⃣  Generación de Reporte Final"
    echo ""

    read -p "¿Continuar con el análisis completo? (y/n): " -n 1 -r
    echo ""

    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        log "Análisis cancelado por el usuario"
        exit 0
    fi

    echo ""

    # Ejecutar fases
    run_sast
    run_dast
    consolidate_reports
    generate_report

    # Mostrar resumen
    show_summary
}

main "$@"
