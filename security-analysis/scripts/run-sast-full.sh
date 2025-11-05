#!/bin/bash

################################################################################
# SCRIPT DE ANÁLISIS SAST (Static Application Security Testing)
# PAW AI S.A.S. DE C.V. - PEMC 2025
# Ejecuta análisis estático completo en ambos repositorios
################################################################################

set -e

# Cargar variables de entorno
if [ -f "../.env" ]; then
    export $(cat ../.env | grep -v '^#' | xargs)
fi

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
REPORTS_DIR="$PROJECT_ROOT/reports/sast"
TEMP_REPOS="$PROJECT_ROOT/temp_repos"

# Repositorios a analizar
MUNISTREAM_REPO="${MUNISTREAM_REPO_URL:-}"
PUENTECATASTRAL_REPO="${PUENTECATASTRAL_REPO_URL:-}"

# Timestamp para reportes
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

# Verificar herramientas necesarias
check_tools() {
    log "Verificando herramientas necesarias..."

    local missing_tools=()

    for tool in semgrep trivy gitleaks; do
        if ! command -v "$tool" &>/dev/null; then
            missing_tools+=("$tool")
            log_warning "$tool no encontrado"
        else
            log_success "$tool disponible"
        fi
    done

    if [ ${#missing_tools[@]} -gt 0 ]; then
        log_error "Faltan herramientas: ${missing_tools[*]}"
        log "Ejecuta: ./setup/install-tools.sh"
        exit 1
    fi

    log_success "Todas las herramientas necesarias están instaladas"
}

# Clonar o actualizar repositorio
clone_or_update_repo() {
    local repo_url=$1
    local repo_name=$2
    local target_dir="$TEMP_REPOS/$repo_name"

    log "Procesando repositorio: $repo_name"

    if [ -z "$repo_url" ]; then
        log_warning "URL no configurada para $repo_name. Usando directorio actual."
        return 1
    fi

    mkdir -p "$TEMP_REPOS"

    if [ -d "$target_dir" ]; then
        log "Actualizando repositorio existente..."
        cd "$target_dir"
        git fetch origin || log_warning "No se pudo actualizar el repositorio"
        git pull origin main || git pull origin master || log_warning "No se pudo hacer pull"
        cd - > /dev/null
    else
        log "Clonando repositorio..."
        git clone "$repo_url" "$target_dir" || {
            log_error "No se pudo clonar $repo_url"
            return 1
        }
    fi

    log_success "Repositorio $repo_name listo en: $target_dir"
    return 0
}

# Análisis con Semgrep (OWASP Top 10)
run_semgrep() {
    local repo_path=$1
    local repo_name=$2
    local output_dir="$REPORTS_DIR/$repo_name"

    log_section "SEMGREP - Análisis de Seguridad: $repo_name"

    mkdir -p "$output_dir"

    cd "$repo_path"

    log "Ejecutando Semgrep con reglas OWASP Top 10..."

    # Análisis con múltiples rulesets
    semgrep --config=auto \
        --config="p/owasp-top-ten" \
        --config="p/security-audit" \
        --config="p/secrets" \
        --config="p/javascript" \
        --config="p/typescript" \
        --config="p/java" \
        --json \
        --output="$output_dir/semgrep_${TIMESTAMP}.json" \
        . 2>&1 | tee "$output_dir/semgrep_${TIMESTAMP}.log" || {
            log_warning "Semgrep completado con advertencias"
        }

    # Generar también reporte de texto
    semgrep --config=auto \
        --config="p/owasp-top-ten" \
        --config="p/security-audit" \
        --text \
        --output="$output_dir/semgrep_${TIMESTAMP}.txt" \
        . 2>&1 || true

    cd - > /dev/null

    if [ -f "$output_dir/semgrep_${TIMESTAMP}.json" ]; then
        local findings=$(jq '.results | length' "$output_dir/semgrep_${TIMESTAMP}.json" 2>/dev/null || echo "0")
        log_success "Semgrep completado: $findings hallazgos encontrados"
    else
        log_error "No se generó el reporte de Semgrep"
    fi
}

# Análisis con Trivy (Vulnerabilidades y secretos)
run_trivy() {
    local repo_path=$1
    local repo_name=$2
    local output_dir="$REPORTS_DIR/$repo_name"

    log_section "TRIVY - Escaneo de Vulnerabilidades: $repo_name"

    mkdir -p "$output_dir"

    cd "$repo_path"

    log "Ejecutando Trivy filesystem scan..."

    # Escaneo de filesystem
    trivy fs \
        --security-checks vuln,config,secret \
        --severity CRITICAL,HIGH,MEDIUM,LOW \
        --format json \
        --output "$output_dir/trivy_fs_${TIMESTAMP}.json" \
        . 2>&1 | tee "$output_dir/trivy_${TIMESTAMP}.log" || {
            log_warning "Trivy completado con advertencias"
        }

    # Reporte en tabla
    trivy fs \
        --security-checks vuln,config,secret \
        --severity CRITICAL,HIGH,MEDIUM,LOW \
        --format table \
        --output "$output_dir/trivy_fs_${TIMESTAMP}.txt" \
        . 2>&1 || true

    # Si hay package-lock.json, escanear dependencias npm
    if [ -f "package-lock.json" ]; then
        log "Escaneando dependencias npm..."
        trivy fs \
            --security-checks vuln \
            --severity CRITICAL,HIGH,MEDIUM \
            --format json \
            --output "$output_dir/trivy_npm_${TIMESTAMP}.json" \
            package-lock.json 2>&1 || true
    fi

    # Si hay pom.xml o build.gradle, escanear dependencias Java
    if [ -f "pom.xml" ] || [ -f "build.gradle" ]; then
        log "Escaneando dependencias Java..."
        trivy fs \
            --security-checks vuln \
            --severity CRITICAL,HIGH,MEDIUM \
            --format json \
            --output "$output_dir/trivy_java_${TIMESTAMP}.json" \
            . 2>&1 || true
    fi

    cd - > /dev/null

    log_success "Trivy completado"
}

# Análisis con Gitleaks (Secretos expuestos)
run_gitleaks() {
    local repo_path=$1
    local repo_name=$2
    local output_dir="$REPORTS_DIR/$repo_name"

    log_section "GITLEAKS - Detección de Secretos: $repo_name"

    mkdir -p "$output_dir"

    cd "$repo_path"

    log "Ejecutando Gitleaks..."

    # Escanear todo el historial de git
    gitleaks detect \
        --source . \
        --report-format json \
        --report-path "$output_dir/gitleaks_${TIMESTAMP}.json" \
        --verbose \
        --no-git 2>&1 | tee "$output_dir/gitleaks_${TIMESTAMP}.log" || {
            log_warning "Gitleaks completado (puede tener hallazgos)"
        }

    cd - > /dev/null

    if [ -f "$output_dir/gitleaks_${TIMESTAMP}.json" ]; then
        local secrets=$(jq '. | length' "$output_dir/gitleaks_${TIMESTAMP}.json" 2>/dev/null || echo "0")
        if [ "$secrets" -gt 0 ]; then
            log_error "⚠️  CRÍTICO: $secrets secretos potenciales encontrados!"
        else
            log_success "No se encontraron secretos expuestos"
        fi
    fi
}

# Análisis npm audit (para proyectos Node.js)
run_npm_audit() {
    local repo_path=$1
    local repo_name=$2
    local output_dir="$REPORTS_DIR/$repo_name"

    if [ ! -f "$repo_path/package.json" ]; then
        log "No es un proyecto Node.js, saltando npm audit"
        return 0
    fi

    log_section "NPM AUDIT - Vulnerabilidades en Dependencias: $repo_name"

    mkdir -p "$output_dir"

    cd "$repo_path"

    log "Ejecutando npm audit..."

    # Instalar dependencias si es necesario
    if [ ! -d "node_modules" ]; then
        log "Instalando dependencias..."
        npm install --package-lock-only 2>&1 || true
    fi

    # Ejecutar audit
    npm audit --json > "$output_dir/npm_audit_${TIMESTAMP}.json" 2>&1 || {
        log_warning "npm audit encontró vulnerabilidades"
    }

    npm audit > "$output_dir/npm_audit_${TIMESTAMP}.txt" 2>&1 || true

    cd - > /dev/null

    if [ -f "$output_dir/npm_audit_${TIMESTAMP}.json" ]; then
        local critical=$(jq '.metadata.vulnerabilities.critical // 0' "$output_dir/npm_audit_${TIMESTAMP}.json" 2>/dev/null || echo "0")
        local high=$(jq '.metadata.vulnerabilities.high // 0' "$output_dir/npm_audit_${TIMESTAMP}.json" 2>/dev/null || echo "0")
        local medium=$(jq '.metadata.vulnerabilities.moderate // 0' "$output_dir/npm_audit_${TIMESTAMP}.json" 2>/dev/null || echo "0")
        local low=$(jq '.metadata.vulnerabilities.low // 0' "$output_dir/npm_audit_${TIMESTAMP}.json" 2>/dev/null || echo "0")

        log "Vulnerabilidades encontradas:"
        echo -e "  ${RED}Critical: $critical${NC}"
        echo -e "  ${YELLOW}High: $high${NC}"
        echo -e "  ${BLUE}Medium: $medium${NC}"
        echo -e "  Low: $low"

        if [ "$critical" -gt 0 ]; then
            log_error "⚠️  CRÍTICO: Se encontraron vulnerabilidades críticas en dependencias!"
        fi
    fi

    log_success "npm audit completado"
}

# Análisis ESLint con plugins de seguridad
run_eslint_security() {
    local repo_path=$1
    local repo_name=$2
    local output_dir="$REPORTS_DIR/$repo_name"

    if [ ! -f "$repo_path/package.json" ]; then
        log "No es un proyecto Node.js, saltando ESLint"
        return 0
    fi

    log_section "ESLINT - Análisis de Código JavaScript/TypeScript: $repo_name"

    mkdir -p "$output_dir"

    cd "$repo_path"

    # Crear configuración temporal de ESLint si no existe
    if [ ! -f ".eslintrc.json" ]; then
        log "Creando configuración temporal de ESLint..."
        cat > .eslintrc.temp.json <<EOF
{
  "plugins": ["security", "no-secrets"],
  "extends": ["plugin:security/recommended"],
  "rules": {
    "no-secrets/no-secrets": "error"
  }
}
EOF
        ESLINT_CONFIG=".eslintrc.temp.json"
    else
        ESLINT_CONFIG=".eslintrc.json"
    fi

    log "Ejecutando ESLint con plugins de seguridad..."

    # Ejecutar ESLint
    npx eslint \
        --config "$ESLINT_CONFIG" \
        --ext .js,.jsx,.ts,.tsx \
        --format json \
        --output-file "$output_dir/eslint_${TIMESTAMP}.json" \
        . 2>&1 | tee "$output_dir/eslint_${TIMESTAMP}.log" || {
            log_warning "ESLint completado con advertencias"
        }

    # Limpiar configuración temporal
    if [ -f ".eslintrc.temp.json" ]; then
        rm .eslintrc.temp.json
    fi

    cd - > /dev/null

    log_success "ESLint completado"
}

# Análisis de un repositorio completo
analyze_repository() {
    local repo_url=$1
    local repo_name=$2

    log_section "ANALIZANDO REPOSITORIO: $repo_name"

    local repo_path

    # Intentar clonar/actualizar repositorio
    if clone_or_update_repo "$repo_url" "$repo_name"; then
        repo_path="$TEMP_REPOS/$repo_name"
    else
        # Si falla, buscar en el directorio actual
        if [ -d "../$repo_name" ]; then
            repo_path="$(cd ../$repo_name && pwd)"
            log "Usando repositorio local: $repo_path"
        elif [ -d "../../$repo_name" ]; then
            repo_path="$(cd ../../$repo_name && pwd)"
            log "Usando repositorio local: $repo_path"
        else
            log_error "No se encontró el repositorio $repo_name"
            return 1
        fi
    fi

    # Ejecutar todas las herramientas de análisis
    run_semgrep "$repo_path" "$repo_name"
    run_trivy "$repo_path" "$repo_name"
    run_gitleaks "$repo_path" "$repo_name"
    run_npm_audit "$repo_path" "$repo_name"
    run_eslint_security "$repo_path" "$repo_name"

    log_success "Análisis de $repo_name completado"
}

# Generar resumen
generate_summary() {
    log_section "GENERANDO RESUMEN DEL ANÁLISIS"

    local summary_file="$REPORTS_DIR/summary_${TIMESTAMP}.txt"

    {
        echo "========================================="
        echo "RESUMEN DE ANÁLISIS SAST - PEMC 2025"
        echo "Fecha: $(date +'%Y-%m-%d %H:%M:%S')"
        echo "========================================="
        echo ""

        for repo in munistream puentecatastral; do
            echo "--- $repo ---"

            if [ -d "$REPORTS_DIR/$repo" ]; then
                local latest_semgrep=$(ls -t "$REPORTS_DIR/$repo"/semgrep_*.json 2>/dev/null | head -1)
                local latest_trivy=$(ls -t "$REPORTS_DIR/$repo"/trivy_fs_*.json 2>/dev/null | head -1)
                local latest_gitleaks=$(ls -t "$REPORTS_DIR/$repo"/gitleaks_*.json 2>/dev/null | head -1)
                local latest_npm=$(ls -t "$REPORTS_DIR/$repo"/npm_audit_*.json 2>/dev/null | head -1)

                if [ -f "$latest_semgrep" ]; then
                    local semgrep_count=$(jq '.results | length' "$latest_semgrep" 2>/dev/null || echo "0")
                    echo "  Semgrep: $semgrep_count hallazgos"
                fi

                if [ -f "$latest_gitleaks" ]; then
                    local secrets_count=$(jq '. | length' "$latest_gitleaks" 2>/dev/null || echo "0")
                    echo "  Gitleaks: $secrets_count secretos potenciales"
                fi

                if [ -f "$latest_npm" ]; then
                    local critical=$(jq '.metadata.vulnerabilities.critical // 0' "$latest_npm" 2>/dev/null || echo "0")
                    local high=$(jq '.metadata.vulnerabilities.high // 0' "$latest_npm" 2>/dev/null || echo "0")
                    echo "  npm audit: Critical=$critical, High=$high"
                fi
            else
                echo "  No se encontraron reportes"
            fi

            echo ""
        done

        echo "========================================="
        echo "Reportes guardados en: $REPORTS_DIR"
        echo "========================================="

    } | tee "$summary_file"

    log_success "Resumen guardado en: $summary_file"
}

# Main
main() {
    echo ""
    echo "╔════════════════════════════════════════════════════════════╗"
    echo "║         ANÁLISIS SAST - PEMC 2025                          ║"
    echo "║          PAW AI S.A.S. DE C.V.                             ║"
    echo "╚════════════════════════════════════════════════════════════╝"
    echo ""

    log "Iniciando análisis SAST completo..."
    log "Timestamp: $TIMESTAMP"

    check_tools

    # Crear directorios de reportes
    mkdir -p "$REPORTS_DIR"/{munistream,puentecatastral}

    # Analizar MuniStream Platform
    if [ -n "$MUNISTREAM_REPO" ]; then
        analyze_repository "$MUNISTREAM_REPO" "munistream"
    else
        log_warning "MUNISTREAM_REPO_URL no configurado, saltando..."
    fi

    echo ""

    # Analizar Puente Catastral
    if [ -n "$PUENTECATASTRAL_REPO" ]; then
        analyze_repository "$PUENTECATASTRAL_REPO" "puentecatastral"
    else
        log_warning "PUENTECATASTRAL_REPO_URL no configurado, saltando..."
    fi

    generate_summary

    echo ""
    log_success "✅ ANÁLISIS SAST COMPLETADO"
    echo ""
    log "Siguiente paso: ./scripts/run-dast-full.sh"
    echo ""
}

main "$@"
