// MongoDB initialization script for CivicStream
// This script runs when the MongoDB container starts for the first time

// Switch to the civicstream database
db = db.getSiblingDB('civicstream');

// Create collections with validation
db.createCollection('workflow_definitions', {
  validator: {
    $jsonSchema: {
      bsonType: 'object',
      required: ['workflow_id', 'name', 'status'],
      properties: {
        workflow_id: {
          bsonType: 'string',
          description: 'Unique workflow identifier'
        },
        name: {
          bsonType: 'string',
          description: 'Workflow name'
        },
        status: {
          bsonType: 'string',
          enum: ['draft', 'active', 'inactive', 'archived'],
          description: 'Workflow status'
        }
      }
    }
  }
});

db.createCollection('workflow_instances', {
  validator: {
    $jsonSchema: {
      bsonType: 'object',
      required: ['instance_id', 'workflow_id', 'user_id'],
      properties: {
        instance_id: {
          bsonType: 'string',
          description: 'Unique instance identifier'
        },
        workflow_id: {
          bsonType: 'string',
          description: 'Parent workflow ID'
        },
        user_id: {
          bsonType: 'string',
          description: 'User who initiated the instance'
        }
      }
    }
  }
});

// Create indexes for better performance
db.workflow_definitions.createIndex({ 'workflow_id': 1 }, { unique: true });
db.workflow_definitions.createIndex({ 'name': 1 });
db.workflow_definitions.createIndex({ 'status': 1 });
db.workflow_definitions.createIndex({ 'created_at': -1 });

db.workflow_instances.createIndex({ 'instance_id': 1 }, { unique: true });
db.workflow_instances.createIndex({ 'workflow_id': 1 });
db.workflow_instances.createIndex({ 'user_id': 1 });
db.workflow_instances.createIndex({ 'status': 1 });
db.workflow_instances.createIndex({ 'started_at': -1 });

db.workflow_steps.createIndex({ 'workflow_id': 1, 'step_id': 1 }, { unique: true });
db.workflow_steps.createIndex({ 'workflow_id': 1 });

db.step_executions.createIndex({ 'instance_id': 1, 'step_id': 1 });
db.step_executions.createIndex({ 'instance_id': 1 });
db.step_executions.createIndex({ 'workflow_id': 1 });

db.approval_requests.createIndex({ 'approval_id': 1 }, { unique: true });
db.approval_requests.createIndex({ 'instance_id': 1 });
db.approval_requests.createIndex({ 'status': 1 });

// Create a user for the application
db.createUser({
  user: 'civicstream_app',
  pwd: 'civicstream_app_password',
  roles: [
    {
      role: 'readWrite',
      db: 'civicstream'
    }
  ]
});

print('CivicStream MongoDB initialization completed successfully!');
print('Database: civicstream');
print('Application user: civicstream_app');
print('Collections created with indexes and validation rules.');