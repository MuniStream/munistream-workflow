#!/bin/bash

################################################################################
# SCRIPT DE ANÁLISIS DAST (Dynamic Application Security Testing)
# PAW AI S.A.S. DE C.V. - PEMC 2025
# Ejecuta análisis dinámico en aplicaciones en ejecución
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
NC='\033[0m'

# Configuración
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPORTS_DIR="$PROJECT_ROOT/reports/dast"

# URLs a analizar
MUNISTREAM_URL="${MUNISTREAM_URL:-https://catastro.dev.munistream.com}"
PUENTE_API_URL="${PUENTE_API_URL:-https://catastro.dev.munistream.com/api/puente}"

# Timestamp
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

# Verificar herramientas
check_tools() {
    log "Verificando herramientas necesarias..."

    local missing_tools=()

    # Verificar Docker para OWASP ZAP
    if ! command -v docker &>/dev/null; then
        missing_tools+=("docker")
        log_warning "Docker no encontrado (necesario para OWASP ZAP)"
    else
        log_success "Docker disponible"
    fi

    # Verificar Nikto
    if ! command -v nikto &>/dev/null; then
        missing_tools+=("nikto")
        log_warning "Nikto no encontrado"
    else
        log_success "Nikto disponible"
    fi

    # Verificar curl
    if ! command -v curl &>/dev/null; then
        log_error "curl es requerido"
        exit 1
    else
        log_success "curl disponible"
    fi

    if [ ${#missing_tools[@]} -gt 0 ]; then
        log_warning "Algunas herramientas no están disponibles: ${missing_tools[*]}"
        log "El análisis continuará con las herramientas disponibles"
    else
        log_success "Todas las herramientas están disponibles"
    fi
}

# Verificar si una URL está activa
check_url_active() {
    local url=$1

    log "Verificando accesibilidad: $url"

    local response_code=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 10 "$url" 2>/dev/null || echo "000")

    if [ "$response_code" != "000" ] && [ "$response_code" != "404" ]; then
        log_success "URL accesible (HTTP $response_code)"
        return 0
    else
        log_error "URL no accesible o inactiva: $url"
        return 1
    fi
}

# Análisis de headers de seguridad
analyze_security_headers() {
    local url=$1
    local name=$2
    local output_file="$REPORTS_DIR/${name}_headers_${TIMESTAMP}.txt"

    log_section "ANÁLISIS DE HEADERS DE SEGURIDAD: $name"

    log "Analizando headers HTTP de: $url"

    {
        echo "========================================="
        echo "HEADERS DE SEGURIDAD - $name"
        echo "URL: $url"
        echo "Fecha: $(date +'%Y-%m-%d %H:%M:%S')"
        echo "========================================="
        echo ""

        # Obtener headers
        echo "--- Headers HTTP Completos ---"
        curl -I -s "$url" 2>/dev/null || echo "Error obteniendo headers"
        echo ""

        echo "--- Análisis de Headers de Seguridad ---"
        echo ""

        # Headers de seguridad importantes
        local headers=(
            "Strict-Transport-Security"
            "Content-Security-Policy"
            "X-Frame-Options"
            "X-Content-Type-Options"
            "X-XSS-Protection"
            "Referrer-Policy"
            "Permissions-Policy"
        )

        for header in "${headers[@]}"; do
            local value=$(curl -I -s "$url" 2>/dev/null | grep -i "^${header}:" || echo "")

            if [ -n "$value" ]; then
                echo "✓ $header: PRESENTE"
                echo "  $value"
            else
                echo "✗ $header: AUSENTE ⚠️"
            fi
            echo ""
        done

        echo "========================================="

    } | tee "$output_file"

    log_success "Análisis de headers guardado en: $output_file"
}

# Análisis con OWASP ZAP
run_zap_scan() {
    local url=$1
    local name=$2
    local output_dir="$REPORTS_DIR"

    if ! command -v docker &>/dev/null; then
        log_warning "Docker no disponible, saltando OWASP ZAP"
        return 1
    fi

    log_section "OWASP ZAP - Baseline Scan: $name"

    log "Ejecutando OWASP ZAP baseline scan en: $url"
    log "Esto puede tardar varios minutos..."

    mkdir -p "$output_dir"

    # Ejecutar ZAP baseline scan
    docker run --rm \
        -v "$output_dir":/zap/wrk/:rw \
        -t zaproxy/zap-stable \
        zap-baseline.py \
        -t "$url" \
        -r "zap_${name}_${TIMESTAMP}.html" \
        -J "zap_${name}_${TIMESTAMP}.json" \
        -w "zap_${name}_${TIMESTAMP}.md" \
        || {
            log_warning "ZAP completado con advertencias (normal si encontró vulnerabilidades)"
        }

    if [ -f "$output_dir/zap_${name}_${TIMESTAMP}.html" ]; then
        log_success "Reporte ZAP HTML generado: zap_${name}_${TIMESTAMP}.html"
    fi

    if [ -f "$output_dir/zap_${name}_${TIMESTAMP}.json" ]; then
        log_success "Reporte ZAP JSON generado: zap_${name}_${TIMESTAMP}.json"

        # Contar alertas por riesgo
        local high=$(jq '[.site[].alerts[] | select(.riskcode=="3")] | length' "$output_dir/zap_${name}_${TIMESTAMP}.json" 2>/dev/null || echo "0")
        local medium=$(jq '[.site[].alerts[] | select(.riskcode=="2")] | length' "$output_dir/zap_${name}_${TIMESTAMP}.json" 2>/dev/null || echo "0")
        local low=$(jq '[.site[].alerts[] | select(.riskcode=="1")] | length' "$output_dir/zap_${name}_${TIMESTAMP}.json" 2>/dev/null || echo "0")

        log "Alertas ZAP encontradas:"
        echo -e "  ${RED}High: $high${NC}"
        echo -e "  ${YELLOW}Medium: $medium${NC}"
        echo -e "  ${BLUE}Low: $low${NC}"
    fi

    log_success "Escaneo ZAP completado"
}

# Análisis con Nikto
run_nikto_scan() {
    local url=$1
    local name=$2
    local output_dir="$REPORTS_DIR"

    if ! command -v nikto &>/dev/null; then
        log_warning "Nikto no disponible, saltando"
        return 1
    fi

    log_section "NIKTO - Web Server Scan: $name"

    log "Ejecutando Nikto en: $url"
    log "Esto puede tardar varios minutos..."

    mkdir -p "$output_dir"

    # Ejecutar Nikto
    nikto -h "$url" \
        -Format htm \
        -output "$output_dir/nikto_${name}_${TIMESTAMP}.html" \
        2>&1 | tee "$output_dir/nikto_${name}_${TIMESTAMP}.log" || {
            log_warning "Nikto completado con advertencias"
        }

    # También generar reporte de texto
    nikto -h "$url" \
        -Format txt \
        -output "$output_dir/nikto_${name}_${TIMESTAMP}.txt" \
        2>&1 || true

    if [ -f "$output_dir/nikto_${name}_${TIMESTAMP}.html" ]; then
        log_success "Reporte Nikto generado"
    fi

    log_success "Escaneo Nikto completado"
}

# Testing de endpoints API comunes
test_api_endpoints() {
    local base_url=$1
    local name=$2
    local output_file="$REPORTS_DIR/${name}_api_test_${TIMESTAMP}.txt"

    log_section "TESTING DE ENDPOINTS API: $name"

    log "Probando endpoints comunes..."

    {
        echo "========================================="
        echo "TESTING DE ENDPOINTS API - $name"
        echo "Base URL: $base_url"
        echo "Fecha: $(date +'%Y-%m-%d %H:%M:%S')"
        echo "========================================="
        echo ""

        # Endpoints comunes a probar
        local endpoints=(
            "/api/health"
            "/api/status"
            "/api/version"
            "/api/docs"
            "/api/swagger"
            "/api/graphql"
            "/api/users"
            "/api/admin"
            "/.env"
            "/config.json"
            "/swagger.json"
            "/api-docs"
            "/health"
            "/actuator/health"
            "/actuator/info"
        )

        for endpoint in "${endpoints[@]}"; do
            local url="${base_url}${endpoint}"
            local response_code=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 "$url" 2>/dev/null || echo "000")

            echo -n "Testing: $endpoint ... "

            case "$response_code" in
                200|201)
                    echo -e "${GREEN}$response_code OK${NC} ✓"
                    ;;
                401|403)
                    echo -e "${YELLOW}$response_code Protected${NC} ⚠️"
                    ;;
                404)
                    echo "$response_code Not Found"
                    ;;
                500|502|503)
                    echo -e "${RED}$response_code Server Error${NC} ✗"
                    ;;
                *)
                    echo "$response_code"
                    ;;
            esac
        done

        echo ""
        echo "========================================="

    } | tee "$output_file"

    log_success "Testing de endpoints completado"
}

# Test de métodos HTTP
test_http_methods() {
    local url=$1
    local name=$2
    local output_file="$REPORTS_DIR/${name}_http_methods_${TIMESTAMP}.txt"

    log_section "TESTING DE MÉTODOS HTTP: $name"

    {
        echo "========================================="
        echo "MÉTODOS HTTP PERMITIDOS - $name"
        echo "URL: $url"
        echo "========================================="
        echo ""

        local methods=("GET" "POST" "PUT" "DELETE" "PATCH" "OPTIONS" "HEAD" "TRACE")

        for method in "${methods[@]}"; do
            local response_code=$(curl -s -o /dev/null -w "%{http_code}" -X "$method" --connect-timeout 5 "$url" 2>/dev/null || echo "000")

            echo -n "$method: "

            case "$response_code" in
                405)
                    echo "Not Allowed ✓"
                    ;;
                200|201|204)
                    echo -e "${YELLOW}$response_code Allowed ⚠️${NC}"
                    ;;
                401|403)
                    echo "$response_code Protected"
                    ;;
                *)
                    echo "$response_code"
                    ;;
            esac
        done

        echo ""

        # Test específico para TRACE (puede ser vulnerable)
        echo "Verificando método TRACE (XST vulnerability):"
        curl -s -X TRACE --connect-timeout 5 "$url" 2>/dev/null && \
            echo -e "${RED}⚠️  TRACE habilitado - Posible vulnerabilidad XST${NC}" || \
            echo "✓ TRACE deshabilitado"

        echo ""
        echo "========================================="

    } | tee "$output_file"

    log_success "Testing de métodos HTTP completado"
}

# Test de SSL/TLS
test_ssl_tls() {
    local url=$1
    local name=$2
    local output_file="$REPORTS_DIR/${name}_ssl_${TIMESTAMP}.txt"

    # Solo para HTTPS
    if [[ ! "$url" =~ ^https:// ]]; then
        log "No es HTTPS, saltando test SSL/TLS"
        return 0
    fi

    log_section "ANÁLISIS SSL/TLS: $name"

    local hostname=$(echo "$url" | sed -e 's|^https\?://||' -e 's|/.*||')

    {
        echo "========================================="
        echo "ANÁLISIS SSL/TLS - $name"
        echo "Host: $hostname"
        echo "========================================="
        echo ""

        echo "Verificando certificado SSL..."
        echo ""

        # Obtener información del certificado
        echo | openssl s_client -servername "$hostname" -connect "${hostname}:443" 2>/dev/null | \
            openssl x509 -noout -text 2>/dev/null || echo "Error obteniendo certificado"

        echo ""
        echo "Verificando protocolos SSL/TLS soportados..."
        echo ""

        # Test TLS 1.0 (obsoleto)
        echo -n "TLS 1.0: "
        if echo | openssl s_client -tls1 -connect "${hostname}:443" 2>/dev/null | grep -q "Protocol"; then
            echo -e "${RED}Soportado ✗ (obsoleto)${NC}"
        else
            echo "No soportado ✓"
        fi

        # Test TLS 1.1 (obsoleto)
        echo -n "TLS 1.1: "
        if echo | openssl s_client -tls1_1 -connect "${hostname}:443" 2>/dev/null | grep -q "Protocol"; then
            echo -e "${YELLOW}Soportado ⚠️ (obsoleto)${NC}"
        else
            echo "No soportado ✓"
        fi

        # Test TLS 1.2
        echo -n "TLS 1.2: "
        if echo | openssl s_client -tls1_2 -connect "${hostname}:443" 2>/dev/null | grep -q "Protocol"; then
            echo -e "${GREEN}Soportado ✓${NC}"
        else
            echo "No soportado"
        fi

        # Test TLS 1.3
        echo -n "TLS 1.3: "
        if echo | openssl s_client -tls1_3 -connect "${hostname}:443" 2>/dev/null | grep -q "Protocol"; then
            echo -e "${GREEN}Soportado ✓${NC}"
        else
            echo "No soportado"
        fi

        echo ""
        echo "========================================="

    } | tee "$output_file"

    log_success "Análisis SSL/TLS completado"
}

# Analizar aplicación
analyze_application() {
    local url=$1
    local name=$2

    log_section "ANÁLISIS DAST: $name"

    # Verificar accesibilidad
    if ! check_url_active "$url"; then
        log_error "La aplicación no está accesible. Saltando análisis de $name"
        return 1
    fi

    # Ejecutar análisis
    analyze_security_headers "$url" "$name"
    test_http_methods "$url" "$name"
    test_ssl_tls "$url" "$name"

    # Si es una API, hacer tests específicos
    if [[ "$url" =~ /api/ ]]; then
        test_api_endpoints "$url" "$name"
    fi

    # Análisis con herramientas externas
    run_zap_scan "$url" "$name"
    run_nikto_scan "$url" "$name"

    log_success "Análisis de $name completado"
}

# Generar resumen
generate_summary() {
    log_section "GENERANDO RESUMEN DEL ANÁLISIS DAST"

    local summary_file="$REPORTS_DIR/summary_${TIMESTAMP}.txt"

    {
        echo "========================================="
        echo "RESUMEN DE ANÁLISIS DAST - PEMC 2025"
        echo "Fecha: $(date +'%Y-%m-%d %H:%M:%S')"
        echo "========================================="
        echo ""

        echo "URLs analizadas:"
        echo "  - MuniStream: $MUNISTREAM_URL"
        echo "  - Puente API: $PUENTE_API_URL"
        echo ""

        echo "Reportes generados:"
        ls -1 "$REPORTS_DIR"/*_${TIMESTAMP}.* 2>/dev/null | while read -r file; do
            echo "  - $(basename "$file")"
        done

        echo ""
        echo "========================================="
        echo "Ubicación: $REPORTS_DIR"
        echo "========================================="

    } | tee "$summary_file"

    log_success "Resumen guardado en: $summary_file"
}

# Main
main() {
    echo ""
    echo "╔════════════════════════════════════════════════════════════╗"
    echo "║         ANÁLISIS DAST - PEMC 2025                          ║"
    echo "║          PAW AI S.A.S. DE C.V.                             ║"
    echo "╚════════════════════════════════════════════════════════════╝"
    echo ""

    log "Iniciando análisis DAST completo..."
    log "Timestamp: $TIMESTAMP"

    check_tools

    # Crear directorio de reportes
    mkdir -p "$REPORTS_DIR"

    # Analizar MuniStream Platform
    analyze_application "$MUNISTREAM_URL" "munistream"

    echo ""

    # Analizar Puente API
    analyze_application "$PUENTE_API_URL" "puente_api"

    generate_summary

    echo ""
    log_success "✅ ANÁLISIS DAST COMPLETADO"
    echo ""
    log "Siguiente paso: ./scripts/consolidate-reports.sh"
    echo ""
}

main "$@"
