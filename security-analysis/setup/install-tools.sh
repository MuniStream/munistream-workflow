#!/bin/bash

################################################################################
# SCRIPT DE INSTALACIÓN DE HERRAMIENTAS DE SEGURIDAD
# PAW AI S.A.S. DE C.V. - PEMC 2025
# Instala todas las herramientas necesarias para análisis SAST/DAST
################################################################################

set -e  # Exit on error

# Colores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Función de logging
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

# Detectar sistema operativo
detect_os() {
    log "Detectando sistema operativo..."

    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        OS="linux"
        if [ -f /etc/debian_version ]; then
            DISTRO="debian"
        elif [ -f /etc/redhat-release ]; then
            DISTRO="redhat"
        else
            DISTRO="unknown"
        fi
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        OS="macos"
        DISTRO="macos"
    else
        OS="unknown"
        DISTRO="unknown"
    fi

    log_success "Sistema detectado: $OS ($DISTRO)"
}

# Verificar si un comando existe
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Instalar Node.js y npm (si no existe)
install_nodejs() {
    log "Verificando Node.js..."

    if command_exists node; then
        NODE_VERSION=$(node --version)
        log_success "Node.js ya instalado: $NODE_VERSION"
        return 0
    fi

    log "Instalando Node.js..."

    if [ "$OS" == "linux" ]; then
        if [ "$DISTRO" == "debian" ]; then
            curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
            sudo apt-get install -y nodejs
        elif [ "$DISTRO" == "redhat" ]; then
            curl -fsSL https://rpm.nodesource.com/setup_20.x | sudo bash -
            sudo yum install -y nodejs
        fi
    elif [ "$OS" == "macos" ]; then
        if command_exists brew; then
            brew install node
        else
            log_error "Homebrew no encontrado. Instala Node.js manualmente desde https://nodejs.org"
            return 1
        fi
    fi

    log_success "Node.js instalado correctamente"
}

# Instalar Python 3 y pip (si no existe)
install_python() {
    log "Verificando Python 3..."

    if command_exists python3; then
        PYTHON_VERSION=$(python3 --version)
        log_success "Python 3 ya instalado: $PYTHON_VERSION"
    else
        log "Instalando Python 3..."

        if [ "$OS" == "linux" ]; then
            if [ "$DISTRO" == "debian" ]; then
                sudo apt-get update
                sudo apt-get install -y python3 python3-pip
            elif [ "$DISTRO" == "redhat" ]; then
                sudo yum install -y python3 python3-pip
            fi
        elif [ "$OS" == "macos" ]; then
            if command_exists brew; then
                brew install python3
            else
                log_error "Homebrew no encontrado. Instala Python manualmente"
                return 1
            fi
        fi

        log_success "Python 3 instalado correctamente"
    fi

    # Actualizar pip
    log "Actualizando pip..."
    python3 -m pip install --upgrade pip
}

# Instalar Docker (si no existe)
install_docker() {
    log "Verificando Docker..."

    if command_exists docker; then
        DOCKER_VERSION=$(docker --version)
        log_success "Docker ya instalado: $DOCKER_VERSION"
        return 0
    fi

    log_warning "Docker no encontrado. Para OWASP ZAP, instala Docker desde https://docs.docker.com/get-docker/"
    log_warning "Continuando con otras herramientas..."
}

# Instalar Semgrep
install_semgrep() {
    log "Instalando Semgrep..."

    if command_exists semgrep; then
        log_success "Semgrep ya instalado"
        return 0
    fi

    python3 -m pip install semgrep

    if command_exists semgrep; then
        SEMGREP_VERSION=$(semgrep --version)
        log_success "Semgrep instalado: $SEMGREP_VERSION"
    else
        log_error "Error instalando Semgrep"
        return 1
    fi
}

# Instalar Trivy
install_trivy() {
    log "Instalando Trivy..."

    if command_exists trivy; then
        log_success "Trivy ya instalado"
        return 0
    fi

    if [ "$OS" == "linux" ]; then
        if [ "$DISTRO" == "debian" ]; then
            wget -qO - https://aquasecurity.github.io/trivy-repo/deb/public.key | sudo apt-key add -
            echo "deb https://aquasecurity.github.io/trivy-repo/deb $(lsb_release -sc) main" | sudo tee -a /etc/apt/sources.list.d/trivy.list
            sudo apt-get update
            sudo apt-get install -y trivy
        elif [ "$DISTRO" == "redhat" ]; then
            sudo rpm -ivh https://github.com/aquasecurity/trivy/releases/download/v0.48.0/trivy_0.48.0_Linux-64bit.rpm
        fi
    elif [ "$OS" == "macos" ]; then
        if command_exists brew; then
            brew install trivy
        fi
    fi

    if command_exists trivy; then
        TRIVY_VERSION=$(trivy --version)
        log_success "Trivy instalado: $TRIVY_VERSION"
    else
        log_warning "No se pudo instalar Trivy automáticamente"
        log_warning "Descarga desde: https://github.com/aquasecurity/trivy/releases"
    fi
}

# Instalar Gitleaks (para detectar secretos)
install_gitleaks() {
    log "Instalando Gitleaks..."

    if command_exists gitleaks; then
        log_success "Gitleaks ya instalado"
        return 0
    fi

    if [ "$OS" == "linux" ]; then
        wget -q https://github.com/gitleaks/gitleaks/releases/download/v8.18.1/gitleaks_8.18.1_linux_x64.tar.gz
        tar -xzf gitleaks_8.18.1_linux_x64.tar.gz
        sudo mv gitleaks /usr/local/bin/
        rm gitleaks_8.18.1_linux_x64.tar.gz
    elif [ "$OS" == "macos" ]; then
        if command_exists brew; then
            brew install gitleaks
        fi
    fi

    if command_exists gitleaks; then
        GITLEAKS_VERSION=$(gitleaks version)
        log_success "Gitleaks instalado: $GITLEAKS_VERSION"
    else
        log_warning "No se pudo instalar Gitleaks automáticamente"
    fi
}

# Instalar Nikto
install_nikto() {
    log "Instalando Nikto..."

    if command_exists nikto; then
        log_success "Nikto ya instalado"
        return 0
    fi

    if [ "$OS" == "linux" ]; then
        if [ "$DISTRO" == "debian" ]; then
            sudo apt-get install -y nikto
        elif [ "$DISTRO" == "redhat" ]; then
            sudo yum install -y nikto
        fi
    elif [ "$OS" == "macos" ]; then
        if command_exists brew; then
            brew install nikto
        fi
    fi

    if command_exists nikto; then
        log_success "Nikto instalado correctamente"
    else
        log_warning "No se pudo instalar Nikto automáticamente"
    fi
}

# Instalar dependencias de Python para el generador de reportes
install_python_deps() {
    log "Instalando dependencias de Python..."

    python3 -m pip install --upgrade \
        jinja2 \
        markdown \
        pandas \
        matplotlib \
        seaborn \
        plotly \
        requests \
        pyyaml

    log_success "Dependencias de Python instaladas"
}

# Instalar ESLint con plugins de seguridad (global)
install_eslint() {
    log "Instalando ESLint con plugins de seguridad..."

    if command_exists eslint; then
        log_success "ESLint ya instalado"
    else
        npm install -g eslint \
            eslint-plugin-security \
            eslint-plugin-no-secrets \
            @typescript-eslint/parser \
            @typescript-eslint/eslint-plugin

        log_success "ESLint instalado con plugins de seguridad"
    fi
}

# Instalar OWASP Dependency-Check
install_dependency_check() {
    log "Instalando OWASP Dependency-Check..."

    if command_exists dependency-check; then
        log_success "OWASP Dependency-Check ya instalado"
        return 0
    fi

    # Verificar Java
    if ! command_exists java; then
        log_warning "Java no encontrado. Dependency-Check requiere Java."
        if [ "$OS" == "linux" ]; then
            if [ "$DISTRO" == "debian" ]; then
                sudo apt-get install -y default-jdk
            elif [ "$DISTRO" == "redhat" ]; then
                sudo yum install -y java-11-openjdk
            fi
        elif [ "$OS" == "macos" ]; then
            log_warning "Instala Java manualmente desde https://adoptium.net/"
        fi
    fi

    # Descargar Dependency-Check
    DEPCHECK_VERSION="9.0.7"
    wget -q "https://github.com/jeremylong/DependencyCheck/releases/download/v${DEPCHECK_VERSION}/dependency-check-${DEPCHECK_VERSION}-release.zip"
    unzip -q "dependency-check-${DEPCHECK_VERSION}-release.zip"
    sudo mv dependency-check /opt/
    sudo ln -sf /opt/dependency-check/bin/dependency-check.sh /usr/local/bin/dependency-check
    rm "dependency-check-${DEPCHECK_VERSION}-release.zip"

    log_success "OWASP Dependency-Check instalado"
}

# Verificar instalaciones
verify_installations() {
    log ""
    log "========================================="
    log "VERIFICANDO INSTALACIONES"
    log "========================================="

    declare -A tools=(
        ["node"]="Node.js"
        ["npm"]="npm"
        ["python3"]="Python 3"
        ["pip3"]="pip3"
        ["docker"]="Docker"
        ["semgrep"]="Semgrep"
        ["trivy"]="Trivy"
        ["gitleaks"]="Gitleaks"
        ["nikto"]="Nikto"
        ["eslint"]="ESLint"
        ["dependency-check"]="OWASP Dependency-Check"
    )

    INSTALLED_COUNT=0
    TOTAL_COUNT=${#tools[@]}

    for cmd in "${!tools[@]}"; do
        if command_exists "$cmd"; then
            log_success "${tools[$cmd]}: Instalado"
            ((INSTALLED_COUNT++))
        else
            log_warning "${tools[$cmd]}: No instalado"
        fi
    done

    log ""
    log "========================================="
    log "RESUMEN: $INSTALLED_COUNT/$TOTAL_COUNT herramientas instaladas"
    log "========================================="

    if [ $INSTALLED_COUNT -lt 8 ]; then
        log_warning "Algunas herramientas no están instaladas. El análisis puede ser incompleto."
    else
        log_success "Sistema listo para ejecutar análisis de seguridad"
    fi
}

# Main
main() {
    echo ""
    echo "╔════════════════════════════════════════════════════════════╗"
    echo "║     INSTALADOR DE HERRAMIENTAS DE SEGURIDAD - PEMC 2025    ║"
    echo "║                  PAW AI S.A.S. DE C.V.                     ║"
    echo "╚════════════════════════════════════════════════════════════╝"
    echo ""

    detect_os

    log "Iniciando instalación de herramientas..."
    echo ""

    install_nodejs || log_warning "Falló instalación de Node.js"
    install_python || log_warning "Falló instalación de Python"
    install_docker || log_warning "Docker no instalado"
    install_semgrep || log_warning "Falló instalación de Semgrep"
    install_trivy || log_warning "Falló instalación de Trivy"
    install_gitleaks || log_warning "Falló instalación de Gitleaks"
    install_nikto || log_warning "Falló instalación de Nikto"
    install_python_deps || log_warning "Falló instalación de dependencias Python"
    install_eslint || log_warning "Falló instalación de ESLint"
    install_dependency_check || log_warning "Falló instalación de Dependency-Check"

    echo ""
    verify_installations

    echo ""
    log_success "Instalación completada. Revisa el resumen arriba."
    echo ""
}

main "$@"
