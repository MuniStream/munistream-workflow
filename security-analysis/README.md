# Sistema de Análisis de Vulnerabilidades de Seguridad

**Proyecto:** PEMC 2025 - Proyecto Ejecutivo de Modernización Catastral
**Organización:** PAW AI S.A.S. DE C.V.
**Cliente:** Gobierno de la Ciudad de México

---

## 📋 Tabla de Contenidos

1. [Descripción](#descripción)
2. [Requisitos Previos](#requisitos-previos)
3. [Instalación](#instalación)
4. [Configuración](#configuración)
5. [Uso](#uso)
6. [Estructura del Proyecto](#estructura-del-proyecto)
7. [Herramientas Utilizadas](#herramientas-utilizadas)
8. [Interpretación de Resultados](#interpretación-de-resultados)
9. [Troubleshooting](#troubleshooting)
10. [FAQ](#faq)

---

## 📖 Descripción

Este sistema automatizado realiza análisis exhaustivos de seguridad sobre los repositorios del Proyecto de Modernización Catastral, siguiendo la metodología **OWASP Top 10 2021** y utilizando las mejores herramientas de la industria.

### Tipos de Análisis

- **SAST (Static Application Security Testing)**: Análisis estático del código fuente
- **DAST (Dynamic Application Security Testing)**: Análisis dinámico de aplicaciones en ejecución

### Alcance

- **munistream-platform**: Plataforma web (React/Next.js + Node.js)
- **puentecatastral**: API de integración (Spring Boot + Java)

---

## 🔧 Requisitos Previos

### Sistema Operativo

- **Linux** (Ubuntu 20.04+, Debian 10+, CentOS 8+)
- **macOS** (10.15+)
- **WSL2** en Windows

### Software Base

- **Git** 2.20+
- **curl** 7.0+
- **jq** 1.6+ (para procesamiento JSON)
- **Python 3.8+**
- **Node.js 18+** (si hay proyectos Node.js)
- **Java 11+** (si hay proyectos Java)
- **Docker** 20.10+ (opcional, para OWASP ZAP)

### Permisos

- Acceso de lectura a los repositorios de GitHub
- Token de GitHub con permisos `repo` (para repos privados)
- Permisos sudo (solo para instalación de herramientas)

---

## 🚀 Instalación

### Opción 1: Instalación Automática (Recomendada)

```bash
# 1. Clonar o navegar al directorio del proyecto
cd security-analysis

# 2. Ejecutar el instalador automático
./setup/install-tools.sh
```

El script instalará automáticamente:
- Semgrep
- Trivy
- Gitleaks
- Nikto
- npm (si no está instalado)
- Python dependencies
- ESLint con plugins de seguridad
- OWASP Dependency-Check

### Opción 2: Instalación Manual

#### Linux (Ubuntu/Debian)

```bash
# Actualizar repositorios
sudo apt-get update

# Python y pip
sudo apt-get install -y python3 python3-pip

# Node.js
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs

# Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# Semgrep
python3 -m pip install semgrep

# Trivy
wget -qO - https://aquasecurity.github.io/trivy-repo/deb/public.key | sudo apt-key add -
echo "deb https://aquasecurity.github.io/trivy-repo/deb $(lsb_release -sc) main" | sudo tee -a /etc/apt/sources.list.d/trivy.list
sudo apt-get update
sudo apt-get install -y trivy

# Gitleaks
wget https://github.com/gitleaks/gitleaks/releases/download/v8.18.1/gitleaks_8.18.1_linux_x64.tar.gz
tar -xzf gitleaks_8.18.1_linux_x64.tar.gz
sudo mv gitleaks /usr/local/bin/
rm gitleaks_8.18.1_linux_x64.tar.gz

# Nikto
sudo apt-get install -y nikto

# Python dependencies
pip3 install jinja2 markdown pandas matplotlib seaborn plotly pyyaml
```

#### macOS

```bash
# Homebrew (si no está instalado)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Instalar herramientas
brew install python node docker semgrep trivy gitleaks nikto jq

# Python dependencies
pip3 install jinja2 markdown pandas matplotlib seaborn plotly pyyaml
```

### Verificación de Instalación

```bash
# Verificar que todas las herramientas estén instaladas
./setup/install-tools.sh

# O verificar manualmente
semgrep --version
trivy --version
gitleaks version
nikto -Version
docker --version
python3 --version
node --version
```

---

## ⚙️ Configuración

### 1. Crear archivo de configuración

```bash
# Copiar plantilla
cp .env.example .env

# Editar configuración
nano .env
```

### 2. Configurar variables de entorno

Edita `.env` con tus valores:

```bash
# URLs de los repositorios
MUNISTREAM_REPO_URL="https://github.com/tu-org/munistream-platform.git"
PUENTECATASTRAL_REPO_URL="https://github.com/tu-org/puentecatastral.git"

# Token de GitHub (para repos privados)
GITHUB_TOKEN="ghp_tu_token_aqui"

# URLs de aplicaciones en ejecución (para DAST)
MUNISTREAM_URL="https://catastro.dev.munistream.com"
PUENTE_API_URL="https://catastro.dev.munistream.com/api/puente"
```

### 3. Configurar acceso a GitHub

Para repositorios privados:

```bash
# Opción 1: Usar HTTPS con token
git config --global url."https://${GITHUB_TOKEN}@github.com/".insteadOf "https://github.com/"

# Opción 2: Usar SSH (recomendado)
ssh-keygen -t ed25519 -C "tu-email@ejemplo.com"
cat ~/.ssh/id_ed25519.pub
# Agregar la clave pública a GitHub Settings > SSH Keys
```

---

## 🎯 Uso

### Ejecución Completa (Recomendada)

Para ejecutar el análisis completo de principio a fin:

```bash
# Ejecutar todo el pipeline
./scripts/run-full-analysis.sh
```

O paso a paso:

```bash
# 1. Análisis SAST (Estático)
./scripts/run-sast-full.sh

# 2. Análisis DAST (Dinámico)
./scripts/run-dast-full.sh

# 3. Consolidar reportes
./scripts/consolidate-reports.sh

# 4. Generar reporte final en Markdown
python3 ./scripts/generate-markdown.py
```

### Ejecución Individual

#### Solo SAST

```bash
./scripts/run-sast-full.sh
```

Ejecutará:
- Semgrep (análisis de código)
- Trivy (vulnerabilidades)
- Gitleaks (secretos)
- npm audit (dependencias Node.js)
- ESLint (código JavaScript/TypeScript)

#### Solo DAST

```bash
./scripts/run-dast-full.sh
```

Ejecutará:
- OWASP ZAP (web app scanning)
- Nikto (web server scanning)
- Análisis de headers de seguridad
- Testing de endpoints API

#### Solo Consolidación

```bash
./scripts/consolidate-reports.sh
```

Genera reportes consolidados en JSON.

#### Solo Reporte Final

```bash
python3 ./scripts/generate-markdown.py
```

Genera el reporte final en Markdown.

### Comando Único

Crea un script maestro:

```bash
#!/bin/bash
# run-full-analysis.sh

set -e

echo "🔒 Iniciando análisis de seguridad completo..."

./scripts/run-sast-full.sh
./scripts/run-dast-full.sh
./scripts/consolidate-reports.sh
python3 ./scripts/generate-markdown.py

echo "✅ Análisis completado. Reporte disponible en:"
echo "   reports/consolidated/security_report_latest.md"
```

---

## 📁 Estructura del Proyecto

```
security-analysis/
├── README.md                          # Este archivo
├── .env.example                       # Plantilla de configuración
├── .env                              # Configuración (no commitear)
│
├── setup/
│   ├── install-tools.sh              # Instalador de herramientas
│   └── config/                       # Configuraciones
│       ├── semgrep.yml
│       ├── trivy.yaml
│       └── eslintrc.json
│
├── scripts/
│   ├── run-sast-full.sh              # Análisis SAST completo
│   ├── run-dast-full.sh              # Análisis DAST completo
│   ├── consolidate-reports.sh        # Consolidación
│   └── generate-markdown.py          # Generador de reporte
│
├── reports/
│   ├── sast/
│   │   ├── munistream/               # Reportes de munistream
│   │   │   ├── semgrep_*.json
│   │   │   ├── trivy_*.json
│   │   │   ├── gitleaks_*.json
│   │   │   └── npm_audit_*.json
│   │   └── puentecatastral/          # Reportes de puentecatastral
│   │       └── ...
│   │
│   ├── dast/
│   │   ├── zap_*.html
│   │   ├── zap_*.json
│   │   ├── nikto_*.html
│   │   └── *_headers_*.txt
│   │
│   └── consolidated/
│       ├── master_report_*.json      # Reporte maestro JSON
│       ├── executive_summary_*.txt   # Resumen ejecutivo
│       ├── security_report_*.md      # Reporte final Markdown
│       └── security_report_latest.md # Último reporte
│
└── temp_repos/                        # Clones temporales (git ignored)
    ├── munistream/
    └── puentecatastral/
```

---

## 🛠️ Herramientas Utilizadas

### Análisis Estático (SAST)

| Herramienta | Propósito | Documentación |
|-------------|-----------|---------------|
| **Semgrep** | Análisis de patrones de seguridad, OWASP Top 10 | [semgrep.dev](https://semgrep.dev) |
| **Trivy** | Escaneo de vulnerabilidades en dependencias y contenedores | [aquasecurity.github.io/trivy](https://aquasecurity.github.io/trivy) |
| **Gitleaks** | Detección de secretos y credenciales expuestas | [github.com/gitleaks](https://github.com/gitleaks/gitleaks) |
| **npm audit** | Análisis de vulnerabilidades en paquetes npm | [docs.npmjs.com](https://docs.npmjs.com/cli/v8/commands/npm-audit) |
| **ESLint** | Análisis estático con plugins de seguridad | [eslint.org](https://eslint.org) |
| **OWASP Dependency Check** | Análisis de dependencias conocidas | [owasp.org](https://owasp.org/www-project-dependency-check/) |

### Análisis Dinámico (DAST)

| Herramienta | Propósito | Documentación |
|-------------|-----------|---------------|
| **OWASP ZAP** | Web application security scanner | [zaproxy.org](https://www.zaproxy.org) |
| **Nikto** | Web server scanner | [cirt.net/Nikto2](https://cirt.net/Nikto2) |
| **curl** | Testing manual de endpoints y headers | [curl.se](https://curl.se) |

---

## 📊 Interpretación de Resultados

### Niveles de Severidad

| Severidad | Color | Acción Requerida | Timeline |
|-----------|-------|------------------|----------|
| **CRITICAL** | 🔴 | Inmediata | 24-48 horas |
| **HIGH** | 🟠 | Alta prioridad | 1-2 semanas |
| **MEDIUM** | 🟡 | Media prioridad | 1 mes |
| **LOW** | 🟢 | Baja prioridad | 2-3 meses |
| **INFO** | 🔵 | Informativo | Backlog |

### Umbrales Aceptables

**Para despliegue a producción:**

- ✅ Vulnerabilidades **CRITICAL**: 0
- ✅ Vulnerabilidades **HIGH**: ≤ 2
- ⚠️ Vulnerabilidades **MEDIUM**: ≤ 10
- ℹ️ Vulnerabilidades **LOW**: Sin límite

**Secretos expuestos:**

- 🔴 Cualquier secreto encontrado = **CRÍTICO**
- Acción: Rotar inmediatamente

### Formato del Reporte

El reporte final incluye:

1. **Resumen Ejecutivo**: Métricas clave y estado general
2. **Tabla OWASP Top 10**: Cumplimiento por categoría
3. **Detalle de Vulnerabilidades**: Por severidad y herramienta
4. **Plan de Remediación**: Priorizado con timelines
5. **Recomendaciones Técnicas**: Código de ejemplo y mejores prácticas
6. **Conclusiones**: Evaluación general y próximos pasos

---

## 🐛 Troubleshooting

### Error: "Herramienta no encontrada"

```bash
# Verificar instalación
which semgrep
which trivy
which gitleaks

# Reinstalar herramienta específica
./setup/install-tools.sh
```

### Error: "Permission denied"

```bash
# Dar permisos de ejecución
chmod +x setup/*.sh
chmod +x scripts/*.sh
chmod +x scripts/*.py

# O dar permisos a todo
find . -name "*.sh" -exec chmod +x {} \;
find . -name "*.py" -exec chmod +x {} \;
```

### Error: "Authentication failed" (GitHub)

```bash
# Verificar token
echo $GITHUB_TOKEN

# Verificar acceso
curl -H "Authorization: token $GITHUB_TOKEN" https://api.github.com/user

# Regenerar token en GitHub Settings > Developer settings > Personal access tokens
```

### Error: Docker no está corriendo

```bash
# Linux
sudo systemctl start docker
sudo systemctl enable docker

# macOS
open -a Docker

# Verificar
docker ps
```

### Error: "No se pudo clonar el repositorio"

```bash
# Verificar URL
git ls-remote $MUNISTREAM_REPO_URL

# Clonar manualmente
git clone $MUNISTREAM_REPO_URL temp_repos/munistream

# O usar SSH en lugar de HTTPS
```

### Error: "jq: command not found"

```bash
# Ubuntu/Debian
sudo apt-get install jq

# macOS
brew install jq

# CentOS/RHEL
sudo yum install jq
```

### Análisis muy lento

```bash
# Reducir alcance de Semgrep
semgrep --config=p/owasp-top-ten --exclude='node_modules' --exclude='dist' .

# Ejecutar solo en archivos modificados
git diff --name-only | xargs semgrep --config=auto

# Usar cache de Trivy
trivy fs --cache-dir ~/.cache/trivy .
```

### Reportes vacíos

```bash
# Verificar que se ejecutaron los análisis
ls -la reports/sast/
ls -la reports/dast/

# Ver logs de ejecución
tail -f reports/sast/*/semgrep_*.log

# Re-ejecutar con verbose
bash -x ./scripts/run-sast-full.sh
```

---

## ❓ FAQ

### ¿Cuánto tiempo toma el análisis completo?

- **SAST**: 10-30 minutos (depende del tamaño del código)
- **DAST**: 15-45 minutos (depende de la aplicación)
- **Total**: ~30-75 minutos

### ¿Puedo ejecutar esto en CI/CD?

Sí, ejemplo para GitHub Actions:

```yaml
name: Security Analysis

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]
  schedule:
    - cron: '0 0 * * 0'  # Semanal

jobs:
  security:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Install tools
        run: ./security-analysis/setup/install-tools.sh

      - name: Run SAST
        run: ./security-analysis/scripts/run-sast-full.sh

      - name: Consolidate reports
        run: ./security-analysis/scripts/consolidate-reports.sh

      - name: Upload reports
        uses: actions/upload-artifact@v3
        with:
          name: security-reports
          path: security-analysis/reports/
```

### ¿Cómo manejo los falsos positivos?

Crear archivo de exclusiones:

```yaml
# .semgrep-ignore
# Ignorar false positives específicos
src/legacy/old-code.js:42  # Código legacy, se migrará
test/**/*                   # Archivos de test
```

### ¿Los reportes contienen información sensible?

**Sí**, los reportes pueden contener:
- Rutas de archivos
- Nombres de variables
- Snippets de código
- URLs de aplicaciones

**Recomendaciones:**
- No commitear reportes a Git
- Añadir `reports/` a `.gitignore`
- Almacenar en ubicación segura
- Compartir solo con personal autorizado

### ¿Puedo personalizar las reglas?

Sí, edita las configuraciones:

```bash
# Semgrep custom rules
security-analysis/setup/config/semgrep.yml

# Trivy custom policies
security-analysis/setup/config/trivy.yaml
```

### ¿Funciona con otros lenguajes?

Actualmente optimizado para:
- ✅ JavaScript/TypeScript
- ✅ Java
- ✅ Python
- ⚠️ Go (parcial)
- ⚠️ PHP (parcial)

Para añadir soporte, instalar analizadores específicos.

### ¿Qué hago si encuentro una vulnerabilidad crítica?

1. **Documentar**: Capturar evidencia del hallazgo
2. **Notificar**: Informar al equipo de seguridad
3. **Aislar**: Si es en producción, evaluar aislamiento
4. **Remediar**: Aplicar parche o mitigación
5. **Verificar**: Re-ejecutar análisis
6. **Comunicar**: Actualizar stakeholders

---

## 📞 Soporte

### Contacto

- **Organización**: PAW AI S.A.S. DE C.V.
- **Proyecto**: PEMC 2025
- **Email**: seguridad@pawai.mx

### Recursos

- [OWASP Top 10](https://owasp.org/Top10/)
- [CWE Top 25](https://cwe.mitre.org/top25/)
- [NIST Cybersecurity Framework](https://www.nist.gov/cyberframework)

### Actualizaciones

Este sistema se actualiza regularmente. Para obtener la última versión:

```bash
git pull origin main
./setup/install-tools.sh  # Re-instalar herramientas actualizadas
```

---

## 📝 Changelog

### v1.0.0 (2025-01-XX)

- ✅ Implementación inicial
- ✅ Análisis SAST con 5 herramientas
- ✅ Análisis DAST con 3 herramientas
- ✅ Consolidación de reportes JSON
- ✅ Generación de reporte Markdown
- ✅ Documentación completa

---

## 📄 Licencia

Este sistema es propiedad de PAW AI S.A.S. DE C.V. y está destinado exclusivamente para uso en el Proyecto Ejecutivo de Modernización Catastral 2025 del Gobierno de la Ciudad de México.

**Confidencial** - No distribuir sin autorización.

---

**¡Listo para analizar!** 🔒🔍

Para comenzar:

```bash
./setup/install-tools.sh
./scripts/run-sast-full.sh
./scripts/run-dast-full.sh
./scripts/consolidate-reports.sh
python3 ./scripts/generate-markdown.py
```
