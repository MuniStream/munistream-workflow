#!/bin/bash

echo "🔐 Testing Entity Workflow with Customer Authentication"
echo "======================================================="

BASE_URL="http://localhost:8000/api/v1/public"

# 1. Register a new customer
echo -e "\n1️⃣ Registering new customer..."
REGISTER_RESPONSE=$(curl -s -X POST "$BASE_URL/auth/register" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "test_'$(date +%s)'@example.com",
    "password": "TestPassword123",
    "full_name": "Test Customer",
    "phone": "+1234567890",
    "document_number": "12345678"
  }')

echo "Response: $REGISTER_RESPONSE" | jq '.'
TOKEN=$(echo $REGISTER_RESPONSE | jq -r '.access_token')

if [ "$TOKEN" == "null" ] || [ -z "$TOKEN" ]; then
    # Try login with existing user
    echo -e "\n📝 Registration failed, trying login with existing user..."
    LOGIN_RESPONSE=$(curl -s -X POST "$BASE_URL/auth/login" \
      -H "Content-Type: application/json" \
      -d '{
        "email": "test@example.com",
        "password": "TestPassword123"
      }')
    
    echo "Login Response: $LOGIN_RESPONSE" | jq '.'
    TOKEN=$(echo $LOGIN_RESPONSE | jq -r '.access_token')
    
    if [ "$TOKEN" == "null" ] || [ -z "$TOKEN" ]; then
        echo "❌ Failed to authenticate"
        exit 1
    fi
fi

echo "✅ Authenticated successfully"
echo "Token: ${TOKEN:0:50}..."

# 2. Create workflow instance
echo -e "\n2️⃣ Creating workflow instance..."
INSTANCE_RESPONSE=$(curl -s -X POST "$BASE_URL/workflows/start" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "workflow_id": "test_entity_workflow_v1"
  }')

echo "Instance Response: $INSTANCE_RESPONSE" | jq '.'
INSTANCE_ID=$(echo $INSTANCE_RESPONSE | jq -r '.instance_id')

if [ "$INSTANCE_ID" == "null" ] || [ -z "$INSTANCE_ID" ]; then
    echo "❌ Failed to create instance"
    exit 1
fi

echo "✅ Instance created: $INSTANCE_ID"

# 3. Check initial status
echo -e "\n3️⃣ Checking initial status..."
sleep 2
STATUS_RESPONSE=$(curl -s "$BASE_URL/track/$INSTANCE_ID" \
  -H "Authorization: Bearer $TOKEN")
echo "$STATUS_RESPONSE" | jq '.'

# 4. Submit person data
echo -e "\n4️⃣ Submitting person data..."
PERSON_RESPONSE=$(curl -s -X POST "$BASE_URL/instances/$INSTANCE_ID/submit-data" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "first_name": "Juan",
    "last_name": "Pérez",
    "email": "juan@example.com"
  }')
echo "$PERSON_RESPONSE" | jq '.'

# 5. Check status after person data
echo -e "\n5️⃣ Checking status after person data..."
sleep 5
STATUS_RESPONSE=$(curl -s "$BASE_URL/track/$INSTANCE_ID" \
  -H "Authorization: Bearer $TOKEN")
echo "$STATUS_RESPONSE" | jq '.'

# Wait for workflow to reach property collection step
echo "⏳ Waiting for workflow to process..."
sleep 3

# 6. Submit property data
echo -e "\n6️⃣ Submitting property data..."
PROPERTY_RESPONSE=$(curl -s -X POST "$BASE_URL/instances/$INSTANCE_ID/submit-data" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "address": "Calle Principal 123, Ciudad",
    "property_type": "house"
  }')
echo "$PROPERTY_RESPONSE" | jq '.'

# 7. Final status check
echo -e "\n7️⃣ Final status check..."
sleep 3
FINAL_STATUS=$(curl -s "$BASE_URL/track/$INSTANCE_ID" \
  -H "Authorization: Bearer $TOKEN")

echo "$FINAL_STATUS" | jq '.'

# 8. Check if completed
STATUS=$(echo "$FINAL_STATUS" | jq -r '.status')
if [ "$STATUS" == "completed" ]; then
    echo -e "\n✅ Workflow completed successfully!"
else
    echo -e "\n⚠️ Workflow status: $STATUS (expected: completed)"
    
    # Wait a bit more and check again
    echo "⏳ Waiting for workflow to complete..."
    sleep 5
    
    FINAL_STATUS2=$(curl -s "$BASE_URL/track/$INSTANCE_ID" \
      -H "Authorization: Bearer $TOKEN")
    echo -e "\n8️⃣ Final status after additional wait:"
    echo "$FINAL_STATUS2" | jq '.'
    
    STATUS2=$(echo "$FINAL_STATUS2" | jq -r '.status')
    if [ "$STATUS2" == "completed" ]; then
        echo -e "\n✅ Workflow completed successfully after delay!"
    else
        echo -e "\n❌ Workflow still not completed. Status: $STATUS2"
    fi
fi

# 9. Check instance logs (using admin API)
echo -e "\n8️⃣ Checking instance logs..."
curl -s "http://localhost:8000/api/v1/instances/$INSTANCE_ID/logs" | jq '.[:5]'

echo -e "\n======================================================="
echo "Instance ID: $INSTANCE_ID"
echo "Final Status: $STATUS"
echo "Test complete!"