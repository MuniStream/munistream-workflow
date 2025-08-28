#!/bin/bash

echo "🔐 Testing Entity Workflow with Authentication"
echo "=============================================="

# Test user credentials
TOKEN="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJjdXN0b21lcl8xMjMiLCJuYW1lIjoiSm9obiBEb2UiLCJlbWFpbCI6ImpvaG5AZXhhbXBsZS5jb20iLCJleHAiOjE5NzUzODI2MDAsImlhdCI6MTcyODM4MjYwMCwidHlwZSI6ImN1c3RvbWVyIn0.BddQMKLb8Ig4Q-nQOPOXXvDLBSvXeQgHC-yvB4tBv98"

# 1. Start workflow
echo -e "\n1️⃣ Starting workflow..."
RESPONSE=$(curl -s -X POST "http://localhost:8000/api/v1/public/instances" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "workflow_id": "test_entity_workflow_v1"
  }')

echo "Response: $RESPONSE"
INSTANCE_ID=$(echo $RESPONSE | jq -r '.instance_id')

if [ "$INSTANCE_ID" == "null" ] || [ -z "$INSTANCE_ID" ]; then
    echo "❌ Failed to create instance"
    exit 1
fi

echo "✅ Instance created: $INSTANCE_ID"

# 2. Check status
echo -e "\n2️⃣ Checking instance status..."
sleep 2
curl -s "http://localhost:8000/api/v1/public/track/$INSTANCE_ID" \
  -H "Authorization: Bearer $TOKEN" | jq '.'

# 3. Submit person data
echo -e "\n3️⃣ Submitting person data..."
curl -X POST "http://localhost:8000/api/v1/public/instances/$INSTANCE_ID/submit-data" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "task_id": "collect_person",
    "data": {
      "first_name": "Juan",
      "last_name": "Pérez",
      "email": "juan@example.com"
    }
  }' | jq '.'

# 4. Check status after person data
echo -e "\n4️⃣ Checking status after person data..."
sleep 2
curl -s "http://localhost:8000/api/v1/public/track/$INSTANCE_ID" \
  -H "Authorization: Bearer $TOKEN" | jq '.'

# 5. Submit property data
echo -e "\n5️⃣ Submitting property data..."
curl -X POST "http://localhost:8000/api/v1/public/instances/$INSTANCE_ID/submit-data" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "task_id": "collect_property",
    "data": {
      "address": "Calle Principal 123, Ciudad",
      "property_type": "house"
    }
  }' | jq '.'

# 6. Final status check
echo -e "\n6️⃣ Final status check..."
sleep 3
FINAL_STATUS=$(curl -s "http://localhost:8000/api/v1/public/track/$INSTANCE_ID" \
  -H "Authorization: Bearer $TOKEN")

echo "$FINAL_STATUS" | jq '.'

# 7. Check if completed
STATUS=$(echo "$FINAL_STATUS" | jq -r '.status')
if [ "$STATUS" == "completed" ]; then
    echo -e "\n✅ Workflow completed successfully!"
else
    echo -e "\n⚠️ Workflow status: $STATUS (expected: completed)"
fi

# 8. Check instance logs
echo -e "\n7️⃣ Checking instance logs..."
curl -s "http://localhost:8000/api/v1/instances/$INSTANCE_ID/logs" \
  -H "Authorization: Bearer $TOKEN" | jq '.[:5]'

echo -e "\n=============================================="
echo "Test complete!"