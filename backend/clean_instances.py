#!/usr/bin/env python3
"""
Script para limpiar completamente todas las instancias de workflow con enums obsoletos
"""
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient

MONGO_URL = "mongodb://admin:munistream123@localhost:27017/munistream?authSource=admin"

async def clean_instances():
    """Limpiar todas las instancias de workflow"""
    
    client = AsyncIOMotorClient(MONGO_URL)
    db = client.munistream
    
    print("🧹 Limpiando todas las instancias de workflow...")
    
    # Eliminar TODAS las instancias
    result = await db.workflow_instances.delete_many({})
    print(f"✅ Eliminadas {result.deleted_count} instancias")
    
    # Verificar que esté limpio
    count = await db.workflow_instances.count_documents({})
    print(f"📊 Instancias restantes: {count}")
    
    if count == 0:
        print("🎉 Base de datos limpia!")
    else:
        print("⚠️  Aún hay instancias en la base de datos")
    
    client.close()
    
    return count == 0

if __name__ == "__main__":
    result = asyncio.run(clean_instances())
    if result:
        print("✅ Migración completada exitosamente")
        exit(0)
    else:
        print("❌ Error en la migración")
        exit(1)