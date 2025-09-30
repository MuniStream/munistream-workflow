#!/bin/bash

# H5 Smart Deployment Script for MuniStream Workflow API
# This script performs intelligent, non-destructive updates to API containers

set -e

# Parse command line arguments
FORCE_REBUILD=${1:-false}
TARGET_CLIENT=${2:-"all"}

echo "🚀 Starting H5 Smart Deployment for Workflow API..."
echo "   Target Client: $TARGET_CLIENT"
echo "   Force Rebuild: $FORCE_REBUILD"

# Environment validation
if [ -z "$AURORA_ENDPOINT" ]; then
    echo "❌ Error: AURORA_ENDPOINT is not set"
    exit 1
fi

if [ -z "$DB_PASSWORD" ]; then
    echo "❌ Error: DB_PASSWORD is not set"
    exit 1
fi

# Function to check if container needs update
check_container_needs_update() {
    local service_name=$1
    local container_name="api-${service_name}"

    # Check if container exists and is running
    if docker ps --format "table {{.Names}}" | grep -q "^${container_name}$"; then
        echo "✅ Container $container_name is running"

        if [ "$FORCE_REBUILD" = "true" ]; then
            echo "🔄 Force rebuild requested for $container_name"
            return 0
        else
            echo "ℹ️ Container $container_name exists and running - skipping rebuild"
            return 1
        fi
    else
        echo "🆕 Container $container_name not found - will create"
        return 0
    fi
}

# Function to check database connectivity
check_database_connectivity() {
    local client_name=$1
    local db_name="munistream_${client_name}"

    echo "🔍 Checking database connectivity for $client_name..."

    # Use psql to check connectivity
    if command -v psql >/dev/null 2>&1; then
        if PGPASSWORD="$DB_PASSWORD" psql -h "$AURORA_ENDPOINT" -U postgres -d "$db_name" -c "SELECT 1;" >/dev/null 2>&1; then
            echo "✅ Database $db_name is accessible"
            return 0
        else
            echo "⚠️ Database $db_name connectivity issue"
            return 1
        fi
    else
        echo "ℹ️ psql not available, skipping DB connectivity check"
        return 0
    fi
}

# Function to run database migrations for specific client
run_database_migrations() {
    local service_name=$1
    local container_name="api-${service_name}"

    echo "🗃️ Running database migrations for $service_name..."

    # Wait for container to be ready
    sleep 5

    # Run migrations inside the container
    if docker exec "$container_name" python -m alembic upgrade head 2>/dev/null; then
        echo "✅ Database migrations completed for $service_name"
    else
        echo "⚠️ Database migrations failed or not configured for $service_name"
    fi
}

# Function to deploy specific service
deploy_service() {
    local service_name=$1
    local service_full_name="api-${service_name}"

    echo "🔧 Deploying API service: $service_full_name"

    # Check database connectivity first
    check_database_connectivity "$service_name"

    # Create network if it doesn't exist
    docker network create munistream-network 2>/dev/null || echo "Network already exists"

    # Build only the specific service
    echo "🏗️ Building $service_full_name..."
    docker-compose -f docker-compose.h5.yml build "$service_full_name"

    # Stop only the target service (graceful)
    echo "🛑 Gracefully stopping $service_full_name..."
    docker-compose -f docker-compose.h5.yml stop "$service_full_name" || true

    # Remove the old container
    docker-compose -f docker-compose.h5.yml rm -f "$service_full_name" || true

    # Start the specific service
    echo "▶️ Starting $service_full_name..."
    docker-compose -f docker-compose.h5.yml up -d "$service_full_name"

    # Wait for container to be ready
    echo "⏳ Waiting for $service_full_name to be ready..."
    sleep 10

    # Run database migrations
    run_database_migrations "$service_name"

    # Health check
    local port
    case $service_name in
        "core") port=8000 ;;
        "conapesca") port=8001 ;;
        "teso") port=8002 ;;
    esac

    echo "🔍 Performing health check for $service_full_name on port $port..."

    # Try health check endpoint
    if curl -f http://localhost:$port/health >/dev/null 2>&1; then
        echo "✅ $service_full_name API is healthy on port $port"
    elif curl -f http://localhost:$port >/dev/null 2>&1; then
        echo "✅ $service_full_name API is responding on port $port (no health endpoint)"
    else
        echo "⚠️ $service_full_name API may need more time to start"

        # Show recent logs for debugging
        echo "📋 Recent logs for $service_full_name:"
        docker-compose -f docker-compose.h5.yml logs --tail=10 "$service_full_name"
    fi
}

# Function to start shared services if needed
start_shared_services() {
    echo "🔧 Starting shared services if needed..."

    # Start Redis if cache profile is enabled
    if docker-compose -f docker-compose.h5.yml --profile cache ps redis-shared >/dev/null 2>&1; then
        echo "ℹ️ Redis cache already running"
    else
        echo "🚀 Starting Redis cache..."
        docker-compose -f docker-compose.h5.yml --profile cache up -d redis-shared || echo "Redis not configured"
    fi
}

# Main deployment logic
echo "🔧 Preparing H5 deployment environment..."
start_shared_services

case $TARGET_CLIENT in
    "core")
        if check_container_needs_update "core" || [ "$FORCE_REBUILD" = "true" ]; then
            deploy_service "core"
        fi
        ;;
    "conapesca")
        if check_container_needs_update "conapesca" || [ "$FORCE_REBUILD" = "true" ]; then
            deploy_service "conapesca"
        fi
        ;;
    "tesoreriacdmx"|"teso")
        if check_container_needs_update "teso" || [ "$FORCE_REBUILD" = "true" ]; then
            deploy_service "teso"
        fi
        ;;
    "all")
        echo "🔄 Checking all API clients for updates..."

        # Deploy only containers that need updates
        for client in core conapesca teso; do
            if check_container_needs_update "$client" || [ "$FORCE_REBUILD" = "true" ]; then
                deploy_service "$client"
                echo "⏳ Brief pause between API deployments..."
                sleep 3
            fi
        done
        ;;
    *)
        echo "❌ Invalid target client: $TARGET_CLIENT"
        echo "Valid options: core, conapesca, tesoreriacdmx, all"
        exit 1
        ;;
esac

# Final status check
echo ""
echo "📊 Final API Container Status:"
docker-compose -f docker-compose.h5.yml ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}"

echo ""
echo "✅ H5 Workflow API deployment completed!"
echo ""
echo "🌐 API Access URLs:"
echo "  - Core API: http://localhost:8000"
echo "  - Conapesca API: http://localhost:8001"
echo "  - Tesoreriacdmx API: http://localhost:8002"
echo ""
echo "🔗 ALB API URLs (when DNS is configured):"
echo "  - Core: https://core-dev.munistream.local/api/"
echo "  - Conapesca: https://conapesca-dev.munistream.local/api/"
echo "  - Tesoreriacdmx: https://tesoreriacdmx-dev.munistream.local/api/"
echo ""
echo "🗃️ Database Connections:"
echo "  - Core: munistream_core"
echo "  - Conapesca: munistream_conapesca"
echo "  - Tesoreriacdmx: munistream_tesoreriacdmx"

# Show recent container logs
echo ""
echo "📋 Recent API container logs:"
docker-compose -f docker-compose.h5.yml logs --tail=3