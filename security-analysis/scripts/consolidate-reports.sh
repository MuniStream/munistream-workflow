#!/bin/bash

################################################################################
# SCRIPT DE CONSOLIDACIÓN DE REPORTES
# PAW AI S.A.S. DE C.V. - PEMC 2025
# Consolida todos los reportes SAST y DAST en un formato unificado
################################################################################

set -e

# Colores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Configuración
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPORTS_DIR="$PROJECT_ROOT/reports"
CONSOLIDATED_DIR="$REPORTS_DIR/consolidated"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

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

# Verificar que existan reportes
check_reports() {
    log "Verificando existencia de reportes..."

    if [ ! -d "$REPORTS_DIR/sast" ] && [ ! -d "$REPORTS_DIR/dast" ]; then
        log_error "No se encontraron directorios de reportes"
        log "Ejecuta primero: ./scripts/run-sast-full.sh y ./scripts/run-dast-full.sh"
        exit 1
    fi

    local sast_count=$(find "$REPORTS_DIR/sast" -type f 2>/dev/null | wc -l)
    local dast_count=$(find "$REPORTS_DIR/dast" -type f 2>/dev/null | wc -l)

    log "Reportes SAST encontrados: $sast_count"
    log "Reportes DAST encontrados: $dast_count"

    if [ "$sast_count" -eq 0 ] && [ "$dast_count" -eq 0 ]; then
        log_error "No se encontraron reportes para consolidar"
        exit 1
    fi

    log_success "Reportes disponibles para consolidación"
}

# Procesar reportes de Semgrep
process_semgrep() {
    local consolidated_file="$CONSOLIDATED_DIR/semgrep_consolidated.json"

    log "Consolidando reportes de Semgrep..."

    # Encontrar todos los reportes de Semgrep
    local semgrep_files=$(find "$REPORTS_DIR/sast" -name "semgrep_*.json" 2>/dev/null)

    if [ -z "$semgrep_files" ]; then
        log_warning "No se encontraron reportes de Semgrep"
        return 0
    fi

    # Inicializar JSON consolidado
    echo '{"repositories": {}, "total_findings": 0, "by_severity": {}}' > "$consolidated_file"

    local total_findings=0
    local critical=0
    local high=0
    local medium=0
    local low=0

    for file in $semgrep_files; do
        local repo_name=$(basename "$(dirname "$file")")

        log "Procesando: $repo_name - $(basename "$file")"

        # Extraer hallazgos
        local findings=$(jq '.results | length' "$file" 2>/dev/null || echo "0")
        total_findings=$((total_findings + findings))

        # Contar por severidad
        local repo_critical=$(jq '[.results[] | select(.extra.severity=="ERROR")] | length' "$file" 2>/dev/null || echo "0")
        local repo_high=$(jq '[.results[] | select(.extra.severity=="WARNING")] | length' "$file" 2>/dev/null || echo "0")
        local repo_medium=$(jq '[.results[] | select(.extra.severity=="INFO")] | length' "$file" 2>/dev/null || echo "0")

        critical=$((critical + repo_critical))
        high=$((high + repo_high))
        medium=$((medium + repo_medium))

        # Agregar al JSON consolidado
        jq --arg repo "$repo_name" \
           --argjson findings "$findings" \
           --argjson critical "$repo_critical" \
           --argjson high "$repo_high" \
           --argjson medium "$repo_medium" \
           --slurpfile results "$file" \
           '.repositories[$repo] = {
               "findings": $findings,
               "critical": $critical,
               "high": $high,
               "medium": $medium,
               "results": $results[0].results
           }' "$consolidated_file" > "${consolidated_file}.tmp" && mv "${consolidated_file}.tmp" "$consolidated_file"
    done

    # Actualizar totales
    jq --argjson total "$total_findings" \
       --argjson critical "$critical" \
       --argjson high "$high" \
       --argjson medium "$medium" \
       --argjson low "$low" \
       '.total_findings = $total |
        .by_severity = {
            "critical": $critical,
            "high": $high,
            "medium": $medium,
            "low": $low
        }' "$consolidated_file" > "${consolidated_file}.tmp" && mv "${consolidated_file}.tmp" "$consolidated_file"

    log_success "Semgrep consolidado: $total_findings hallazgos"
}

# Procesar reportes de Trivy
process_trivy() {
    local consolidated_file="$CONSOLIDATED_DIR/trivy_consolidated.json"

    log "Consolidando reportes de Trivy..."

    local trivy_files=$(find "$REPORTS_DIR/sast" -name "trivy_*.json" 2>/dev/null)

    if [ -z "$trivy_files" ]; then
        log_warning "No se encontraron reportes de Trivy"
        return 0
    fi

    echo '{"repositories": {}, "total_vulnerabilities": 0, "by_severity": {}}' > "$consolidated_file"

    local total_vulns=0
    local critical=0
    local high=0
    local medium=0
    local low=0

    for file in $trivy_files; do
        local repo_name=$(basename "$(dirname "$file")")

        log "Procesando: $repo_name - $(basename "$file")"

        # Contar vulnerabilidades por severidad
        if jq -e '.Results' "$file" >/dev/null 2>&1; then
            local repo_critical=$(jq '[.Results[]?.Vulnerabilities[]? | select(.Severity=="CRITICAL")] | length' "$file" 2>/dev/null || echo "0")
            local repo_high=$(jq '[.Results[]?.Vulnerabilities[]? | select(.Severity=="HIGH")] | length' "$file" 2>/dev/null || echo "0")
            local repo_medium=$(jq '[.Results[]?.Vulnerabilities[]? | select(.Severity=="MEDIUM")] | length' "$file" 2>/dev/null || echo "0")
            local repo_low=$(jq '[.Results[]?.Vulnerabilities[]? | select(.Severity=="LOW")] | length' "$file" 2>/dev/null || echo "0")

            local repo_total=$((repo_critical + repo_high + repo_medium + repo_low))

            critical=$((critical + repo_critical))
            high=$((high + repo_high))
            medium=$((medium + repo_medium))
            low=$((low + repo_low))
            total_vulns=$((total_vulns + repo_total))

            # Agregar al consolidado
            jq --arg repo "$repo_name" \
               --argjson total "$repo_total" \
               --argjson critical "$repo_critical" \
               --argjson high "$repo_high" \
               --argjson medium "$repo_medium" \
               --argjson low "$repo_low" \
               '.repositories[$repo] = {
                   "total": $total,
                   "critical": $critical,
                   "high": $high,
                   "medium": $medium,
                   "low": $low
               }' "$consolidated_file" > "${consolidated_file}.tmp" && mv "${consolidated_file}.tmp" "$consolidated_file"
        fi
    done

    # Actualizar totales
    jq --argjson total "$total_vulns" \
       --argjson critical "$critical" \
       --argjson high "$high" \
       --argjson medium "$medium" \
       --argjson low "$low" \
       '.total_vulnerabilities = $total |
        .by_severity = {
            "critical": $critical,
            "high": $high,
            "medium": $medium,
            "low": $low
        }' "$consolidated_file" > "${consolidated_file}.tmp" && mv "${consolidated_file}.tmp" "$consolidated_file"

    log_success "Trivy consolidado: $total_vulns vulnerabilidades"
}

# Procesar reportes de Gitleaks
process_gitleaks() {
    local consolidated_file="$CONSOLIDATED_DIR/gitleaks_consolidated.json"

    log "Consolidando reportes de Gitleaks..."

    local gitleaks_files=$(find "$REPORTS_DIR/sast" -name "gitleaks_*.json" 2>/dev/null)

    if [ -z "$gitleaks_files" ]; then
        log_warning "No se encontraron reportes de Gitleaks"
        return 0
    fi

    echo '{"repositories": {}, "total_secrets": 0}' > "$consolidated_file"

    local total_secrets=0

    for file in $gitleaks_files; do
        local repo_name=$(basename "$(dirname "$file")")

        log "Procesando: $repo_name - $(basename "$file")"

        local secrets_count=$(jq '. | length' "$file" 2>/dev/null || echo "0")
        total_secrets=$((total_secrets + secrets_count))

        if [ "$secrets_count" -gt 0 ]; then
            log_error "⚠️  CRÍTICO: $secrets_count secretos encontrados en $repo_name"
        fi

        jq --arg repo "$repo_name" \
           --argjson count "$secrets_count" \
           --slurpfile secrets "$file" \
           '.repositories[$repo] = {
               "count": $count,
               "secrets": $secrets[0]
           }' "$consolidated_file" > "${consolidated_file}.tmp" && mv "${consolidated_file}.tmp" "$consolidated_file"
    done

    jq --argjson total "$total_secrets" \
       '.total_secrets = $total' "$consolidated_file" > "${consolidated_file}.tmp" && mv "${consolidated_file}.tmp" "$consolidated_file"

    if [ "$total_secrets" -gt 0 ]; then
        log_error "⚠️  TOTAL DE SECRETOS EXPUESTOS: $total_secrets"
    else
        log_success "Gitleaks: No se encontraron secretos expuestos"
    fi
}

# Procesar reportes de npm audit
process_npm_audit() {
    local consolidated_file="$CONSOLIDATED_DIR/npm_audit_consolidated.json"

    log "Consolidando reportes de npm audit..."

    local npm_files=$(find "$REPORTS_DIR/sast" -name "npm_audit_*.json" 2>/dev/null)

    if [ -z "$npm_files" ]; then
        log_warning "No se encontraron reportes de npm audit"
        return 0
    fi

    echo '{"repositories": {}, "total_vulnerabilities": 0, "by_severity": {}}' > "$consolidated_file"

    local total_vulns=0
    local critical=0
    local high=0
    local moderate=0
    local low=0

    for file in $npm_files; do
        local repo_name=$(basename "$(dirname "$file")")

        log "Procesando: $repo_name - $(basename "$file")"

        # Extraer vulnerabilidades por severidad
        local repo_critical=$(jq '.metadata.vulnerabilities.critical // 0' "$file" 2>/dev/null || echo "0")
        local repo_high=$(jq '.metadata.vulnerabilities.high // 0' "$file" 2>/dev/null || echo "0")
        local repo_moderate=$(jq '.metadata.vulnerabilities.moderate // 0' "$file" 2>/dev/null || echo "0")
        local repo_low=$(jq '.metadata.vulnerabilities.low // 0' "$file" 2>/dev/null || echo "0")

        local repo_total=$((repo_critical + repo_high + repo_moderate + repo_low))

        critical=$((critical + repo_critical))
        high=$((high + repo_high))
        moderate=$((moderate + repo_moderate))
        low=$((low + repo_low))
        total_vulns=$((total_vulns + repo_total))

        jq --arg repo "$repo_name" \
           --argjson total "$repo_total" \
           --argjson critical "$repo_critical" \
           --argjson high "$repo_high" \
           --argjson moderate "$repo_moderate" \
           --argjson low "$repo_low" \
           '.repositories[$repo] = {
               "total": $total,
               "critical": $critical,
               "high": $high,
               "moderate": $moderate,
               "low": $low
           }' "$consolidated_file" > "${consolidated_file}.tmp" && mv "${consolidated_file}.tmp" "$consolidated_file"
    done

    jq --argjson total "$total_vulns" \
       --argjson critical "$critical" \
       --argjson high "$high" \
       --argjson moderate "$moderate" \
       --argjson low "$low" \
       '.total_vulnerabilities = $total |
        .by_severity = {
            "critical": $critical,
            "high": $high,
            "moderate": $moderate,
            "low": $low
        }' "$consolidated_file" > "${consolidated_file}.tmp" && mv "${consolidated_file}.tmp" "$consolidated_file"

    log_success "npm audit consolidado: $total_vulns vulnerabilidades"
}

# Procesar reportes DAST
process_dast() {
    local consolidated_file="$CONSOLIDATED_DIR/dast_consolidated.json"

    log "Consolidando reportes DAST..."

    if [ ! -d "$REPORTS_DIR/dast" ]; then
        log_warning "No se encontró directorio DAST"
        return 0
    fi

    echo '{"applications": {}, "total_issues": 0}' > "$consolidated_file"

    # Procesar reportes ZAP si existen
    local zap_files=$(find "$REPORTS_DIR/dast" -name "zap_*.json" 2>/dev/null)

    if [ -n "$zap_files" ]; then
        for file in $zap_files; do
            local app_name=$(basename "$file" | sed 's/zap_\(.*\)_[0-9]*.json/\1/')

            log "Procesando ZAP: $app_name"

            local high=$(jq '[.site[].alerts[] | select(.riskcode=="3")] | length' "$file" 2>/dev/null || echo "0")
            local medium=$(jq '[.site[].alerts[] | select(.riskcode=="2")] | length' "$file" 2>/dev/null || echo "0")
            local low=$(jq '[.site[].alerts[] | select(.riskcode=="1")] | length' "$file" 2>/dev/null || echo "0")
            local info=$(jq '[.site[].alerts[] | select(.riskcode=="0")] | length' "$file" 2>/dev/null || echo "0")

            local total=$((high + medium + low + info))

            jq --arg app "$app_name" \
               --argjson total "$total" \
               --argjson high "$high" \
               --argjson medium "$medium" \
               --argjson low "$low" \
               --argjson info "$info" \
               '.applications[$app] = {
                   "zap": {
                       "total": $total,
                       "high": $high,
                       "medium": $medium,
                       "low": $low,
                       "info": $info
                   }
               }' "$consolidated_file" > "${consolidated_file}.tmp" && mv "${consolidated_file}.tmp" "$consolidated_file"
        done

        log_success "Reportes ZAP consolidados"
    fi

    # Contar headers de seguridad faltantes
    local header_files=$(find "$REPORTS_DIR/dast" -name "*_headers_*.txt" 2>/dev/null)

    if [ -n "$header_files" ]; then
        for file in $header_files; do
            local app_name=$(basename "$file" | sed 's/\(.*\)_headers_.*/\1/')

            log "Analizando headers: $app_name"

            local missing_headers=$(grep -c "AUSENTE" "$file" 2>/dev/null || echo "0")

            jq --arg app "$app_name" \
               --argjson missing "$missing_headers" \
               '.applications[$app].headers_missing = $missing' "$consolidated_file" > "${consolidated_file}.tmp" && mv "${consolidated_file}.tmp" "$consolidated_file" 2>/dev/null || true
        done
    fi

    log_success "Reportes DAST consolidados"
}

# Generar reporte maestro consolidado
generate_master_report() {
    local master_file="$CONSOLIDATED_DIR/master_report_${TIMESTAMP}.json"

    log_section "GENERANDO REPORTE MAESTRO"

    {
        echo "{"
        echo "  \"report_metadata\": {"
        echo "    \"project\": \"PEMC 2025 - Modernización Catastral\","
        echo "    \"organization\": \"PAW AI S.A.S. DE C.V.\","
        echo "    \"timestamp\": \"$(date -Iseconds)\","
        echo "    \"analysis_type\": \"SAST + DAST\""
        echo "  },"

        # SAST Summary
        echo "  \"sast\": {"

        # Semgrep
        if [ -f "$CONSOLIDATED_DIR/semgrep_consolidated.json" ]; then
            echo -n "    \"semgrep\": "
            jq -c . "$CONSOLIDATED_DIR/semgrep_consolidated.json"
            echo ","
        fi

        # Trivy
        if [ -f "$CONSOLIDATED_DIR/trivy_consolidated.json" ]; then
            echo -n "    \"trivy\": "
            jq -c . "$CONSOLIDATED_DIR/trivy_consolidated.json"
            echo ","
        fi

        # Gitleaks
        if [ -f "$CONSOLIDATED_DIR/gitleaks_consolidated.json" ]; then
            echo -n "    \"gitleaks\": "
            jq -c . "$CONSOLIDATED_DIR/gitleaks_consolidated.json"
            echo ","
        fi

        # npm audit
        if [ -f "$CONSOLIDATED_DIR/npm_audit_consolidated.json" ]; then
            echo -n "    \"npm_audit\": "
            jq -c . "$CONSOLIDATED_DIR/npm_audit_consolidated.json"
        fi

        echo "  },"

        # DAST Summary
        echo "  \"dast\": "
        if [ -f "$CONSOLIDATED_DIR/dast_consolidated.json" ]; then
            jq -c . "$CONSOLIDATED_DIR/dast_consolidated.json"
        else
            echo "{}"
        fi

        echo "}"
    } | jq . > "$master_file"

    log_success "Reporte maestro generado: $master_file"
}

# Generar resumen ejecutivo
generate_executive_summary() {
    log_section "RESUMEN EJECUTIVO"

    local summary_file="$CONSOLIDATED_DIR/executive_summary_${TIMESTAMP}.txt"

    {
        echo "╔════════════════════════════════════════════════════════════════════╗"
        echo "║      RESUMEN EJECUTIVO - ANÁLISIS DE SEGURIDAD PEMC 2025          ║"
        echo "║                 PAW AI S.A.S. DE C.V.                              ║"
        echo "╚════════════════════════════════════════════════════════════════════╝"
        echo ""
        echo "Fecha: $(date +'%Y-%m-%d %H:%M:%S')"
        echo ""
        echo "========================================================================"
        echo "MÉTRICAS GLOBALES"
        echo "========================================================================"
        echo ""

        # Extraer métricas de archivos consolidados
        if [ -f "$CONSOLIDATED_DIR/semgrep_consolidated.json" ]; then
            local semgrep_total=$(jq '.total_findings' "$CONSOLIDATED_DIR/semgrep_consolidated.json")
            local semgrep_critical=$(jq '.by_severity.critical' "$CONSOLIDATED_DIR/semgrep_consolidated.json")
            local semgrep_high=$(jq '.by_severity.high' "$CONSOLIDATED_DIR/semgrep_consolidated.json")
            echo "SEMGREP (Análisis de Código):"
            echo "  Total hallazgos: $semgrep_total"
            echo "  - Críticos: $semgrep_critical"
            echo "  - Altos: $semgrep_high"
            echo ""
        fi

        if [ -f "$CONSOLIDATED_DIR/trivy_consolidated.json" ]; then
            local trivy_total=$(jq '.total_vulnerabilities' "$CONSOLIDATED_DIR/trivy_consolidated.json")
            local trivy_critical=$(jq '.by_severity.critical' "$CONSOLIDATED_DIR/trivy_consolidated.json")
            local trivy_high=$(jq '.by_severity.high' "$CONSOLIDATED_DIR/trivy_consolidated.json")
            echo "TRIVY (Vulnerabilidades):"
            echo "  Total vulnerabilidades: $trivy_total"
            echo "  - Críticas: $trivy_critical"
            echo "  - Altas: $trivy_high"
            echo ""
        fi

        if [ -f "$CONSOLIDATED_DIR/gitleaks_consolidated.json" ]; then
            local secrets_total=$(jq '.total_secrets' "$CONSOLIDATED_DIR/gitleaks_consolidated.json")
            echo "GITLEAKS (Secretos Expuestos):"
            echo "  Total secretos: $secrets_total"
            if [ "$secrets_total" -gt 0 ]; then
                echo "  ⚠️  CRÍTICO: Se encontraron secretos expuestos"
            fi
            echo ""
        fi

        if [ -f "$CONSOLIDATED_DIR/npm_audit_consolidated.json" ]; then
            local npm_total=$(jq '.total_vulnerabilities' "$CONSOLIDATED_DIR/npm_audit_consolidated.json")
            local npm_critical=$(jq '.by_severity.critical' "$CONSOLIDATED_DIR/npm_audit_consolidated.json")
            local npm_high=$(jq '.by_severity.high' "$CONSOLIDATED_DIR/npm_audit_consolidated.json")
            echo "NPM AUDIT (Dependencias):"
            echo "  Total vulnerabilidades: $npm_total"
            echo "  - Críticas: $npm_critical"
            echo "  - Altas: $npm_high"
            echo ""
        fi

        echo "========================================================================"
        echo "ANÁLISIS OWASP TOP 10"
        echo "========================================================================"
        echo ""
        echo "Pendiente de clasificación detallada (requiere generate-markdown.py)"
        echo ""

        echo "========================================================================"
        echo "PRÓXIMOS PASOS"
        echo "========================================================================"
        echo ""
        echo "1. Ejecutar: python3 scripts/generate-markdown.py"
        echo "2. Revisar reporte detallado en: reports/consolidated/"
        echo "3. Priorizar remediación de vulnerabilidades críticas y altas"
        echo ""

    } | tee "$summary_file"

    log_success "Resumen ejecutivo guardado en: $summary_file"
}

# Main
main() {
    echo ""
    echo "╔════════════════════════════════════════════════════════════╗"
    echo "║     CONSOLIDACIÓN DE REPORTES - PEMC 2025                  ║"
    echo "║          PAW AI S.A.S. DE C.V.                             ║"
    echo "╚════════════════════════════════════════════════════════════╝"
    echo ""

    log "Iniciando consolidación de reportes..."
    log "Timestamp: $TIMESTAMP"

    check_reports

    # Crear directorio consolidado
    mkdir -p "$CONSOLIDATED_DIR"

    # Procesar todos los reportes
    process_semgrep
    process_trivy
    process_gitleaks
    process_npm_audit
    process_dast

    # Generar reportes finales
    generate_master_report
    generate_executive_summary

    echo ""
    log_success "✅ CONSOLIDACIÓN COMPLETADA"
    echo ""
    log "Siguiente paso: python3 ./scripts/generate-markdown.py"
    echo ""
}

main "$@"
